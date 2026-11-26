"""Kalshi wide-spread NBA prop quoter rules and configuration.

RUL-20261125 Half 2 / MAK-01, shadow-only.
Pure, stateless quoting, cancellation, fee-schedule, and risk-cap evaluation
functions. No I/O and no internal wall-clock reads (`now` is always passed in).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ACTION_QUOTE_BOTH = "QUOTE_BOTH"
ACTION_QUOTE_YES = "QUOTE_YES"
ACTION_QUOTE_NO = "QUOTE_NO"
ACTION_SKIP = "SKIP"
ACTION_CANCEL = "CANCEL"
ACTION_HALT = "HALT"

MODE_IMPROVE_1C = "IMPROVE_1C"
MODE_JOIN_TOP = "JOIN_TOP"

# Verified 2026-11-25 against Kalshi published fee schedule (eff. 2026-07-07:
# maker fees = ceil_centicent(M * 0.0175 * C * P * (1 - P)), M defaults to 0 for makers)
# AND Kalshi public API series objects: KXNBAREB, KXNBA3PT, KXNBAPTS all carry
# fee_type="quadratic", fee_multiplier=1 (makers pay 0.0).
MAKER_FEE_COEF = 0.0175
SERIES_FEE_TYPES: dict[str, str] = {
    "KXNBAREB": "quadratic",
    "KXNBA3PT": "quadratic",
    "KXNBAPTS": "quadratic",
}
INJURY_PULL_STATUSES = frozenset({"Questionable", "Doubtful", "Out"})


@dataclass(frozen=True)
class WideSpreadConfig:
    """Single source of truth for all RUL-20261125 Half 2 parameters and assumptions."""

    # Quoter spread / edge / tail / sizing / cadence rules
    spread_min_c: int = 5
    spread_max_c: int = 25
    min_net_edge_c: float = 1.5
    tail_min_c: int = 10
    tail_max_c: int = 90
    quote_size: int = 10
    window_open_min: int = 240
    window_close_min: int = 45
    poll_interval_s: int = 30
    feed_poll_interval_s: int = 30
    fv_jump_pp: float = 3.0
    fv_jump_window_min: int = 5
    cooldown_min: int = 15

    # Simulated inventory & risk caps
    cap_market_net_contracts: int = 20
    cap_player_risk_usd: float = 25.0
    cap_game_risk_usd: float = 50.0
    cap_daily_open_risk_usd: float = 150.0
    cap_daily_stop_loss_usd: float = 50.0

    # Fair-value anchor weights, hold limits, quorum, staleness, dispersion
    sharp_hold_max: float = 0.055
    retail_hold_max: float = 0.075
    hold_exclude_above: float = 0.08
    weight_sharp: float = 2.0
    weight_retail: float = 1.0
    quorum_with_sharp: int = 3
    quorum_without_sharp: int = 4
    staleness_base_s: int = 180
    staleness_cap_s: int = 300
    dispersion_max_pp: float = 4.0

    # Shadow simulator latency & graduation / kill thresholds
    latency_denial_s: int = 5
    markout_go_c: float = 1.50
    markout_t_stat_min: float = 1.65
    markout_kill_c: float = 0.50
    markout_series_drop_c: float = 0.0
    drift_go_pp: float = -1.50
    drift_kill_early_pp: float = -3.00
    drift_kill_early_slates: int = 3
    drift_drop_pp: float = -2.50
    min_slates: int = 7
    min_fills_pooled: int = 60
    min_fills_series: int = 25
    fills_per_slate_go: int = 8
    fills_kill_total: int = 20
    skip_go: float = 0.25
    skip_kill: float = 0.60
    zcal_floor: float = -2.0


def maker_fee_cents(
    price_cents: float,
    fee_type: str = "quadratic",
    multiplier: float = 1.0,
) -> float:
    """Per-contract Kalshi maker fee in cents, rounded up to the centicent (0.01c).

    Verified 2026-11-25 against Kalshi's published fee schedule (eff. 2026-07-07)
    and Kalshi's public API series objects (KXNBAREB, KXNBA3PT, KXNBAPTS all have
    fee_type='quadratic', fee_multiplier=1 -> 0.0 maker fee).

    Note: unlike ev.maker_settlement.kalshi_fees (which also uses coef=0.0175 but
    ceils to $0.01 per multi-contract fill), this function computes the per-contract
    centicent-rounded fee in cents for stateless per-contract quoting math.
    """
    if price_cents <= 0.0 or price_cents >= 100.0 or multiplier <= 0.0:
        return 0.0
    ft = str(fee_type or "").strip().lower()
    if ft == "quadratic":
        return 0.0
    if ft in ("quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"):
        # p in dollars is price_cents / 100, so 100 * coef * M * P * (1 - P)
        # simplifies to coef * M * price_cents * (100 - price_cents) / 100 cents.
        raw_cents = MAKER_FEE_COEF * multiplier * price_cents * (100.0 - price_cents) / 100.0
        if raw_cents <= 0.0:
            return 0.0
        return math.ceil(raw_cents * 100.0 - 1e-9) / 100.0
    raise ValueError(f"Unsupported Kalshi fee_type: {fee_type!r}")


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _book_metrics(best_yes_bid_c: float | None, best_no_bid_c: float | None) -> tuple[bool, float | None]:
    if best_yes_bid_c is None or best_no_bid_c is None:
        return False, None
    if best_yes_bid_c < 1 or best_no_bid_c < 1 or best_yes_bid_c >= 100 or best_no_bid_c >= 100:
        return False, None
    best_yes_ask_c = 100.0 - best_no_bid_c
    spread_c = best_yes_ask_c - best_yes_bid_c
    if spread_c <= 0:
        return False, spread_c
    return True, spread_c


@dataclass(frozen=True)
class BookState:
    """Top-of-book snapshot in integer/float cents."""

    best_yes_bid_c: int | float | None
    best_no_bid_c: int | float | None
    yes_bid_size: float = 0.0
    no_bid_size: float = 0.0


@dataclass(frozen=True)
class QuoterState:
    """Simulated inventory, open-risk, realized-loss, and cooldown state."""

    market_net_contracts: int = 0
    player_open_risk_usd: float = 0.0
    game_open_risk_usd: float = 0.0
    daily_open_risk_usd: float = 0.0
    daily_realized_loss_usd: float = 0.0
    cooldown_until: datetime | None = None
    series: str = "KXNBAPTS"


@dataclass(frozen=True)
class QuoteDecision:
    """Output of evaluate_quote."""

    action: str
    reason_code: str
    yes_bid_c: int | None = None
    no_bid_c: int | None = None
    yes_mode: str | None = None
    no_mode: str | None = None
    size: int = 0
    ex_ante_net_edge_yes: float | None = None
    ex_ante_net_edge_no: float | None = None


@dataclass(frozen=True)
class ActiveQuoteView:
    """Current book + resting quote view passed into evaluate_cancel."""

    best_yes_bid_c: int | float | None
    best_no_bid_c: int | float | None
    yes_bid_c: int | None = None
    no_bid_c: int | None = None


@dataclass(frozen=True)
class CancelDecision:
    """Output of evaluate_cancel."""

    cancel: bool
    reason_code: str
    cooldown_until: datetime | None = None


def _price_side(
    fv_c: float,
    best_bid_c: float,
    best_ask_c: float,
    fee_type: str,
    cfg: WideSpreadConfig,
) -> tuple[int | None, str | None, float | None, str | None]:
    """Compute target bid, queue mode, and ex-ante edge for one side, or a skip reason."""
    max_bid = math.floor(fv_c - maker_fee_cents(fv_c, fee_type=fee_type) - cfg.min_net_edge_c)
    best_bid_int = int(best_bid_c)
    best_ask_int = int(best_ask_c)
    target = min(best_bid_int + 1, max_bid)
    if target < best_bid_int:
        return None, None, None, "no_edge"
    if target > best_ask_int - 1:
        return None, None, None, "no_edge"
    if target < cfg.tail_min_c or target > cfg.tail_max_c:
        return None, None, None, "tail_guard"
    mode = MODE_IMPROVE_1C if target == best_bid_int + 1 else MODE_JOIN_TOP
    ex_ante = fv_c - target - maker_fee_cents(float(target), fee_type=fee_type)
    return target, mode, ex_ante, None


def _side_blocked_by_caps(
    side: str,
    price_c: int,
    state: QuoterState,
    cfg: WideSpreadConfig,
) -> bool:
    """Return True if placing `side` adds risk and breaches market/player/game/daily caps."""
    if side == "yes":
        if state.market_net_contracts < 0:
            return False  # Inventory-reducing side stays quotable.
        if state.market_net_contracts >= cfg.cap_market_net_contracts:
            return True
    else:
        if state.market_net_contracts > 0:
            return False  # Inventory-reducing side stays quotable.
        if -state.market_net_contracts >= cfg.cap_market_net_contracts:
            return True

    quote_risk_usd = cfg.quote_size * (price_c / 100.0)
    if state.player_open_risk_usd + quote_risk_usd > cfg.cap_player_risk_usd:
        return True
    if state.game_open_risk_usd + quote_risk_usd > cfg.cap_game_risk_usd:
        return True
    if state.daily_open_risk_usd + quote_risk_usd > cfg.cap_daily_open_risk_usd:
        return True
    return False


def evaluate_quote(
    book: BookState,
    fv_yes_prob: float | None,
    state: QuoterState,
    now: datetime,
    tip_ts: datetime,
    cfg: WideSpreadConfig,
    series: str | None = None,
    fee_type: str | None = None,
) -> QuoteDecision:
    """Evaluate wide-spread quoting rules for a single market at timestamp `now`."""
    now_utc = _to_utc(now)
    tip_utc = _to_utc(tip_ts)

    if state.daily_realized_loss_usd >= cfg.cap_daily_stop_loss_usd:
        return QuoteDecision(action=ACTION_HALT, reason_code="daily_stop")

    if state.cooldown_until is not None and now_utc < _to_utc(state.cooldown_until):
        return QuoteDecision(action=ACTION_SKIP, reason_code="cooldown")

    minutes_to_tip = (tip_utc - now_utc).total_seconds() / 60.0
    if minutes_to_tip > cfg.window_open_min or minutes_to_tip <= cfg.window_close_min:
        return QuoteDecision(action=ACTION_SKIP, reason_code="outside_window")

    if fv_yes_prob is None or not (0.0 < fv_yes_prob < 1.0):
        return QuoteDecision(action=ACTION_SKIP, reason_code="no_anchor")

    two_sided, spread_c = _book_metrics(book.best_yes_bid_c, book.best_no_bid_c)
    if not two_sided or spread_c is None or spread_c < cfg.spread_min_c or spread_c > cfg.spread_max_c:
        return QuoteDecision(action=ACTION_SKIP, reason_code="spread_gate")

    resolved_series = series if series is not None else state.series
    resolved_fee_type = fee_type if fee_type is not None else SERIES_FEE_TYPES.get(resolved_series, "quadratic")

    best_yes_bid = float(book.best_yes_bid_c)  # type: ignore[arg-type]
    best_no_bid = float(book.best_no_bid_c)    # type: ignore[arg-type]
    best_yes_ask = 100.0 - best_no_bid
    best_no_ask = 100.0 - best_yes_bid

    q_yes, yes_mode, ex_yes, yes_skip = _price_side(
        100.0 * fv_yes_prob, best_yes_bid, best_yes_ask, resolved_fee_type, cfg
    )
    q_no, no_mode, ex_no, no_skip = _price_side(
        100.0 * (1.0 - fv_yes_prob), best_no_bid, best_no_ask, resolved_fee_type, cfg
    )

    cap_blocked = False
    if q_yes is not None and _side_blocked_by_caps("yes", q_yes, state, cfg):
        q_yes, yes_mode, ex_yes = None, None, None
        cap_blocked = True
    if q_no is not None and _side_blocked_by_caps("no", q_no, state, cfg):
        q_no, no_mode, ex_no = None, None, None
        cap_blocked = True

    if q_yes is not None and q_no is not None:
        return QuoteDecision(
            action=ACTION_QUOTE_BOTH,
            reason_code="ok",
            yes_bid_c=q_yes,
            no_bid_c=q_no,
            yes_mode=yes_mode,
            no_mode=no_mode,
            size=cfg.quote_size,
            ex_ante_net_edge_yes=ex_yes,
            ex_ante_net_edge_no=ex_no,
        )
    if q_yes is not None:
        return QuoteDecision(
            action=ACTION_QUOTE_YES,
            reason_code="ok",
            yes_bid_c=q_yes,
            yes_mode=yes_mode,
            size=cfg.quote_size,
            ex_ante_net_edge_yes=ex_yes,
        )
    if q_no is not None:
        return QuoteDecision(
            action=ACTION_QUOTE_NO,
            reason_code="ok",
            no_bid_c=q_no,
            no_mode=no_mode,
            size=cfg.quote_size,
            ex_ante_net_edge_no=ex_no,
        )

    if cap_blocked:
        reason = "inventory_cap"
    elif yes_skip == "tail_guard" or no_skip == "tail_guard":
        reason = "tail_guard"
    else:
        reason = "no_edge"
    return QuoteDecision(action=ACTION_SKIP, reason_code=reason)


def _norm_status(status: str | None) -> str | None:
    if status is None:
        return None
    s = str(status).strip()
    return s if s else None


def evaluate_cancel(
    active: ActiveQuoteView,
    anchor_ok: bool,
    anchor_reason: str,
    prev_status: str | None,
    status: str | None,
    fv_move_pp_5m: float | None,
    now: datetime,
    tip_ts: datetime,
    cfg: WideSpreadConfig,
) -> CancelDecision:
    """Evaluate immediate cancel triggers in spec order."""
    # 1. Spread / one-sided trigger
    two_sided, spread_c = _book_metrics(active.best_yes_bid_c, active.best_no_bid_c)
    if not two_sided or spread_c is None or spread_c < cfg.spread_min_c or spread_c > cfg.spread_max_c:
        return CancelDecision(cancel=True, reason_code="spread_gate")

    # 2. Anchor stale / dispersion / quorum trigger
    if not anchor_ok:
        return CancelDecision(cancel=True, reason_code=anchor_reason or "anchor_invalid")

    # 3. Injury flip trigger
    cur_s = _norm_status(status)
    prev_s = _norm_status(prev_status)
    if cur_s in INJURY_PULL_STATUSES and prev_s not in INJURY_PULL_STATUSES:
        return CancelDecision(cancel=True, reason_code="injury_flip")

    # 4. Consensus FV jump >= 3.0pp within 5 min -> pull + 15-min player cooldown
    now_utc = _to_utc(now)
    if fv_move_pp_5m is not None and abs(fv_move_pp_5m) >= cfg.fv_jump_pp:
        return CancelDecision(
            cancel=True,
            reason_code="fv_jump",
            cooldown_until=now_utc + timedelta(minutes=cfg.cooldown_min),
        )

    # 5. Clock reaches T-45m
    minutes_to_tip = (_to_utc(tip_ts) - now_utc).total_seconds() / 60.0
    if minutes_to_tip <= cfg.window_close_min:
        return CancelDecision(cancel=True, reason_code="t_minus_45m")

    return CancelDecision(cancel=False, reason_code="hold")
