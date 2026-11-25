"""Shared settlement and fee-capture module for the three-lane maker program (MAK-01).

Implements RUL-20261124-maker-lane section (e): venue fee/credit modeling
(Novig, Kalshi, ProphetX), offline outcome resolution (NHL SOG box-score
master and DictResolver plug point), dual-track markout vs. realized-cash
ledger records, calibration Z-score (Z_cal), and the unmerged dual-block
scorecard report.
"""

from __future__ import annotations

import csv
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

STATUS_WIN = "SETTLED_WIN"
STATUS_LOSS = "SETTLED_LOSS"
STATUS_VOID = "VOID_PUSH"
STATUS_PENDING = "PENDING"
STATUS_UNRECONCILED = "UNRECONCILED"

_SETTLED_STATUSES = frozenset({STATUS_WIN, STATUS_LOSS, STATUS_VOID})
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_BLOCK_A_FOOTNOTE = "Model-expected value assuming unbiased closing consensus; NOT cash profit."
_FREEZE_LINE = "FREEZE: Z_cal < -2.5 on >=100 settled fills — audit the consensus anchor."


def norm_name(name: str) -> str:
    """Normalize player name: lowercase, strip diacritics, collapse non-alnum to spaces."""
    raw = unicodedata.normalize("NFKD", str(name or ""))
    no_marks = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", no_marks.lower()).strip()


def _ceil_to_cent(amount_usd: float) -> float:
    if amount_usd <= 0.0:
        return 0.0
    return math.ceil(amount_usd * 100.0 - 1e-9) / 100.0


@dataclass(frozen=True)
class FeeBreakdown:
    """Fee and maker-credit breakdown for a single fill."""

    fee_usd: float
    credit_usd: float
    source: str
    model: str
    note: str


def novig_fees(fill_price: float, contracts: float, window_type: str = "PREGAME") -> FeeBreakdown:
    """Compute Novig maker fee (always 0) and live maker credit (0.25 * 0.06 * P * (1-P) * C)."""
    is_live = str(window_type or "").upper() == "LIVE"
    if is_live:
        credit = 0.25 * 0.06 * fill_price * (1.0 - fill_price) * contracts
        note = "live maker credit"
    else:
        credit = 0.0
        note = "pregame: 0 fee, 0 credit"
    return FeeBreakdown(
        fee_usd=0.0,
        credit_usd=credit,
        source="SIMULATED",
        model="novig_maker",
        note=note,
    )


def kalshi_fees(fill_price: float, contracts: float, is_combo: bool = False) -> FeeBreakdown:
    """Compute Kalshi maker fee: ceil_to_cent(coef * C * P * (1-P))."""
    coef = 0.035 if is_combo else 0.0175
    raw_fee = coef * contracts * fill_price * (1.0 - fill_price)
    fee = _ceil_to_cent(raw_fee)
    model = "kalshi_maker_combo" if is_combo else "kalshi_maker_single"
    return FeeBreakdown(
        fee_usd=fee,
        credit_usd=0.0,
        source="SIMULATED",
        model=model,
        note=f"coef={coef}",
    )


def prophetx_fees(
    fill_price: float,
    contracts: float,
    settlement_price: float | None,
) -> FeeBreakdown:
    """Compute ProphetX per-fill simulated 2%-of-net-gains fee (UNVERIFIED secondhand rule)."""
    if settlement_price is None:
        return FeeBreakdown(
            fee_usd=0.0,
            credit_usd=0.0,
            source="SIMULATED",
            model="prophetx_net_gains",
            note="unsettled; fee not assessable (UNVERIFIED 2% net-gains model)",
        )
    gain_per_contract = settlement_price - fill_price
    fee = 0.02 * max(0.0, gain_per_contract) * contracts
    return FeeBreakdown(
        fee_usd=fee,
        credit_usd=0.0,
        source="SIMULATED",
        model="prophetx_net_gains",
        note="UNVERIFIED secondhand 2%-of-net-gains per-fill approximation",
    )


def venue_fees(
    venue: str,
    fill_price: float,
    contracts: float,
    window_type: str = "PREGAME",
    is_combo: bool = False,
    settlement_price: float | None = None,
) -> FeeBreakdown:
    """Dispatch fee/credit calculation by venue name."""
    v = str(venue or "").lower()
    if v == "novig":
        return novig_fees(fill_price, contracts, window_type)
    if v == "kalshi":
        return kalshi_fees(fill_price, contracts, is_combo=is_combo)
    if v == "prophetx":
        return prophetx_fees(fill_price, contracts, settlement_price)
    raise ValueError(f"Unsupported venue: {venue!r}")


@dataclass(frozen=True)
class Outcome:
    """Resolved outcome for a single fill."""

    settlement_price: float | None
    status: str
    source: str
    detail: str
    stat_value: float | None


class Resolver(Protocol):
    """Protocol for offline fill outcome resolvers."""

    def resolve(self, fill: dict[str, Any]) -> Outcome:
        ...


class NHLSogResolver:
    """Resolve NHL shots-on-goal fills against the local NHL skater box-score master CSV."""

    SOURCE_NAME = "nhl_skater_boxscores_master"

    def __init__(self, boxscores_csv_path: str | os.PathLike[str]) -> None:
        self._index: dict[tuple[str, str], list[float]] = {}
        self.max_date: str | None = None
        with open(boxscores_csv_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                date_str = str(row.get("date") or "").strip()
                name_str = str(row.get("name") or "").strip()
                sog_raw = row.get("sog")
                if date_str:
                    if self.max_date is None or date_str > self.max_date:
                        self.max_date = date_str
                if not date_str or not name_str or sog_raw is None or str(sog_raw).strip() == "":
                    continue
                key = (norm_name(name_str), date_str)
                self._index.setdefault(key, []).append(float(sog_raw))

    def resolve(self, fill: dict[str, Any]) -> Outcome:
        sport = str(fill.get("sport") or "").upper()
        stat = str(fill.get("stat") or "").lower()
        if sport != "NHL" or stat != "sog":
            return Outcome(
                settlement_price=None,
                status=STATUS_UNRECONCILED,
                source=self.SOURCE_NAME,
                detail=f"unsupported family: {fill.get('sport')}:{fill.get('stat')}",
                stat_value=None,
            )

        raw_pnorm = fill.get("player_norm")
        pname = norm_name(raw_pnorm) if raw_pnorm else norm_name(str(fill.get("player") or ""))
        game_day = str(fill.get("game_day") or "").strip()
        key = (pname, game_day)
        sogs = self._index.get(key)
        if not sogs:
            return Outcome(
                settlement_price=None,
                status=STATUS_UNRECONCILED,
                source=self.SOURCE_NAME,
                detail=f"missing boxscore for {key}; master max_date={self.max_date}",
                stat_value=None,
            )

        distinct = set(sogs)
        if len(distinct) > 1:
            return Outcome(
                settlement_price=None,
                status=STATUS_UNRECONCILED,
                source=self.SOURCE_NAME,
                detail=f"ambiguous boxscore rows for {key}: sog={sogs}",
                stat_value=None,
            )

        sog = sogs[0]
        line = float(fill["line"])
        p_fill = float(fill["p_fill"])
        side = str(fill.get("side") or "").lower()

        if sog == line:
            return Outcome(
                settlement_price=p_fill,
                status=STATUS_VOID,
                source=self.SOURCE_NAME,
                detail=f"push: sog={sog} == line={line}",
                stat_value=sog,
            )

        over_hit = sog > line
        if side == "yes":
            won = over_hit
        elif side == "no":
            won = not over_hit
        else:
            return Outcome(
                settlement_price=None,
                status=STATUS_UNRECONCILED,
                source=self.SOURCE_NAME,
                detail=f"unknown side: {side!r}",
                stat_value=sog,
            )

        return Outcome(
            settlement_price=1.0 if won else 0.0,
            status=STATUS_WIN if won else STATUS_LOSS,
            source=self.SOURCE_NAME,
            detail=f"sog={sog} vs line={line} side={side}",
            stat_value=sog,
        )


class DictResolver:
    """Lookup resolver backed by an order_id -> Outcome mapping."""

    def __init__(self, mapping: Mapping[str, Outcome]) -> None:
        self._mapping = dict(mapping)

    def resolve(self, fill: dict[str, Any]) -> Outcome:
        oid = fill.get("order_id")
        if oid in self._mapping:
            return self._mapping[oid]
        return Outcome(
            settlement_price=None,
            status=STATUS_UNRECONCILED,
            source="dict_resolver",
            detail="no outcome supplied",
            stat_value=None,
        )


def settle_fill(
    fill: dict[str, Any],
    resolver: Resolver | None = None,
    venue: str = "novig",
    lane_id: str | None = None,
    rung_id: str | None = None,
    mode: str = "PAPER",
    fee_source_override: str | None = None,
) -> dict[str, Any]:
    """Produce a section-(e) ledger record dict for a single filled order."""
    order_id = fill.get("order_id")
    fill_id = fill.get("fill_id") or order_id
    sport = str(fill.get("sport") or "").lower()
    stat = str(fill.get("stat") or "").lower()
    family = f"{sport}:{stat}"
    window_type = fill.get("window_type") or "PREGAME"

    fill_price = float(fill["p_fill"])
    contracts = float(fill["fill_qty_units"])
    notional_cost_usd = round(fill_price * contracts, 6)

    cons_at_post = fill.get("p_cons_at_post")
    cons_at_fill = (
        fill.get("p_cons_at_fill")
        if fill.get("p_cons_at_fill") is not None
        else cons_at_post
    )
    cons_close = fill.get("p_cons_close")

    pickoff_drift_pp = (
        (cons_at_post - cons_close) * 100.0
        if cons_at_post is not None and cons_close is not None
        else None
    )
    gross_closing_markout_c = (
        (cons_close - fill_price) * 100.0
        if cons_close is not None
        else None
    )

    if resolver is None:
        settlement_status = STATUS_PENDING
        outcome_source = None
        settlement_price = None
        stat_value = None
        unreconciled_reason = None
    else:
        outcome = resolver.resolve(fill)
        settlement_status = outcome.status
        outcome_source = outcome.source
        settlement_price = outcome.settlement_price
        stat_value = outcome.stat_value
        unreconciled_reason = (
            outcome.detail if settlement_status == STATUS_UNRECONCILED else None
        )

    fb = venue_fees(
        venue=venue,
        fill_price=fill_price,
        contracts=contracts,
        window_type=window_type,
        is_combo=bool(fill.get("is_combo", False)),
        settlement_price=settlement_price,
    )
    fee_source = fee_source_override if fee_source_override is not None else fb.source

    gross_realized_pnl_usd = (
        (settlement_price - fill_price) * contracts
        if settlement_price is not None
        else None
    )
    net_realized_pnl_usd = (
        gross_realized_pnl_usd - fb.fee_usd + fb.credit_usd
        if gross_realized_pnl_usd is not None
        else None
    )
    net_closing_markout_usd = (
        ((cons_close - fill_price) * contracts) - fb.fee_usd + fb.credit_usd
        if cons_close is not None
        else None
    )

    return {
        "fill_id": fill_id,
        "order_id": order_id,
        "venue": venue,
        "lane_id": lane_id,
        "rung_id": rung_id,
        "event_id": fill.get("game_key"),
        "market_ticker": fill.get("market_ticker") or fill.get("intent_id"),
        "family": family,
        "side": fill.get("side"),
        "window_type": window_type,
        "mode": mode,
        "post_ts": fill.get("t_post"),
        "fill_ts": fill.get("t_fill"),
        "fill_price": fill_price,
        "filled_contracts": contracts,
        "notional_cost_usd": notional_cost_usd,
        "consensus_at_post": cons_at_post,
        "consensus_at_fill": cons_at_fill,
        "venue_touch_at_post": {
            "bid": fill.get("novig_bid_at_post"),
            "ask": fill.get("novig_ask_at_post"),
        },
        "venue_spread_at_post": fill.get("novig_spread_at_post"),
        "consensus_book_count": fill.get("consensus_book_count"),
        "consensus_plus_5m": fill.get("p_cons_fill_plus_5m"),
        "consensus_plus_30m": fill.get("p_cons_fill_plus_30m"),
        "consensus_close": cons_close,
        "venue_close_mid": fill.get("p_novig_close"),
        "pickoff_drift_pp": pickoff_drift_pp,
        "gross_closing_markout_c": gross_closing_markout_c,
        "settlement_status": settlement_status,
        "settlement_ts": fill.get("settlement_ts"),
        "outcome_source": outcome_source,
        "settlement_price": settlement_price,
        "stat_value": stat_value,
        "gross_realized_pnl_usd": gross_realized_pnl_usd,
        "venue_fee_assessed_usd": fb.fee_usd,
        "venue_credit_or_rebate_usd": fb.credit_usd,
        "fee_source": fee_source,
        "fee_model": fb.model,
        "net_realized_pnl_usd": net_realized_pnl_usd,
        "net_closing_markout_usd": net_closing_markout_usd,
        "unreconciled_reason": unreconciled_reason,
    }


def z_cal(records: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    """Compute calibration Z-score over settled binary fills with closing consensus."""
    numer = 0.0
    var_sum = 0.0
    n = 0
    for rec in records:
        if rec.get("settlement_status") == STATUS_VOID:
            continue
        sp = rec.get("settlement_price")
        cc = rec.get("consensus_close")
        if sp in (0.0, 1.0) and cc is not None:
            sp_f = float(sp)
            cc_f = float(cc)
            numer += sp_f - cc_f
            var_sum += cc_f * (1.0 - cc_f)
            n += 1
    if n == 0 or var_sum <= 0.0:
        return (None, 0)
    return (numer / math.sqrt(var_sum), n)


def _mean(vals: Sequence[float]) -> float | None:
    if not vals:
        return None
    return sum(vals) / len(vals)


def _fmt_opt(val: float | None, fmt: str, suffix: str = "") -> str:
    if val is None:
        return "n/a"
    return f"{format(val, fmt)}{suffix}"


def scorecard(
    records: Sequence[Mapping[str, Any]],
    unfilled_quotes: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Render the dual-track scorecard (Block A markout vs. Block B cash settlement)."""
    with_close = [r for r in records if r.get("consensus_close") is not None]
    n_close = len(with_close)

    gross_c_vals = [
        float(r["gross_closing_markout_c"])
        for r in with_close
        if r.get("gross_closing_markout_c") is not None
    ]
    fee_load_c_vals: list[float] = []
    net_c_vals: list[float] = []
    for r in with_close:
        contracts = float(r.get("filled_contracts") or 0.0)
        if contracts <= 0.0:
            continue
        fee = float(r.get("venue_fee_assessed_usd") or 0.0)
        credit = float(r.get("venue_credit_or_rebate_usd") or 0.0)
        fee_load_c_vals.append(((fee - credit) / contracts) * 100.0)
        if r.get("net_closing_markout_usd") is not None:
            net_c_vals.append((float(r["net_closing_markout_usd"]) / contracts) * 100.0)

    turnover_close = sum(float(r.get("notional_cost_usd") or 0.0) for r in with_close)
    net_markout_usd_sum = sum(
        float(r["net_closing_markout_usd"])
        for r in with_close
        if r.get("net_closing_markout_usd") is not None
    )
    net_markout_pct = (
        (net_markout_usd_sum / turnover_close) * 100.0
        if turnover_close > 0.0 and with_close
        else None
    )

    drift_vals = [
        float(r["pickoff_drift_pp"])
        for r in records
        if r.get("pickoff_drift_pp") is not None
    ]
    filled_drift = _mean(drift_vals)

    block_a_lines = [
        "BLOCK A — PRICE QUALITY (MARKOUT)",
        "-" * 42,
        f"  Fills with close       : {n_close} / {len(records)}",
        f"  Gross closing markout  : {_fmt_opt(_mean(gross_c_vals), '+.3f', ' c/contract')}",
        f"  Fee/credit load        : {_fmt_opt(_mean(fee_load_c_vals), '+.3f', ' c/contract')}",
        (
            f"  NET closing markout    : {_fmt_opt(_mean(net_c_vals), '+.3f', ' c/contract')} "
            f"({_fmt_opt(net_markout_pct, '+.2f', '% of turnover')})"
        ),
        f"  Pickoff drift          : {_fmt_opt(filled_drift, '+.3f', ' pp')}",
    ]

    if unfilled_quotes is not None:
        unfilled_drifts = [
            (float(q["p_cons_at_post"]) - float(q["p_cons_close"])) * 100.0
            for q in unfilled_quotes
            if q.get("p_cons_at_post") is not None and q.get("p_cons_close") is not None
        ]
        unfilled_drift = _mean(unfilled_drifts)
        gap = (
            filled_drift - unfilled_drift
            if filled_drift is not None and unfilled_drift is not None
            else None
        )
        block_a_lines.append(
            f"  Winner's-curse gap     : {_fmt_opt(gap, '+.3f', ' pp')} "
            f"(unfilled drift={_fmt_opt(unfilled_drift, '+.3f', ' pp')})"
        )

    block_a_lines.append(f"  {_BLOCK_A_FOOTNOTE}")

    n_total = len(records)
    settled = [r for r in records if r.get("settlement_status") in _SETTLED_STATUSES]
    n_settled = len(settled)
    n_win = sum(1 for r in settled if r.get("settlement_status") == STATUS_WIN)
    settled_pct = (n_settled / n_total) * 100.0 if n_total > 0 else None
    actual_win_rate = (n_win / n_settled) * 100.0 if n_settled > 0 else None

    settled_closes = [
        float(r["consensus_close"]) * 100.0
        for r in settled
        if r.get("consensus_close") is not None
    ]
    expected_win_rate = _mean(settled_closes)

    gross_pnl = sum(
        float(r["gross_realized_pnl_usd"])
        for r in settled
        if r.get("gross_realized_pnl_usd") is not None
    )
    total_fees = sum(float(r.get("venue_fee_assessed_usd") or 0.0) for r in settled)
    total_credits = sum(float(r.get("venue_credit_or_rebate_usd") or 0.0) for r in settled)
    net_pnl = sum(
        float(r["net_realized_pnl_usd"])
        for r in settled
        if r.get("net_realized_pnl_usd") is not None
    )
    total_turnover = sum(float(r.get("notional_cost_usd") or 0.0) for r in records)
    net_pnl_pct = (
        (net_pnl / total_turnover) * 100.0
        if total_turnover > 0.0 and n_settled > 0
        else None
    )

    z_val, z_n = z_cal(records)
    z_str = f"{z_val:+.4f} (n={z_n})" if z_val is not None else f"n/a (n={z_n})"

    block_b_lines = [
        "BLOCK B — CASH REALITY (SETTLEMENT)",
        "-" * 42,
        f"  Settled fills          : {n_settled} / {n_total} ({_fmt_opt(settled_pct, '.2f', '%')})",
        f"  Actual win rate        : {_fmt_opt(actual_win_rate, '.2f', '%')}",
        f"  Expected win rate      : {_fmt_opt(expected_win_rate, '.2f', '%')}",
        f"  Gross realized P&L     : ${gross_pnl:+.4f}",
        f"  Total fees             : ${total_fees:.4f}",
        f"  Total credits          : ${total_credits:.4f}",
        f"  NET realized P&L       : ${net_pnl:+.4f} ({_fmt_opt(net_pnl_pct, '+.2f', '% of turnover')})",
        f"  Z_cal                  : {z_str}",
    ]
    if z_val is not None and z_val < -2.5 and z_n >= 100:
        block_b_lines.append(f"  {_FREEZE_LINE}")

    return "\n".join(block_a_lines + ["", "=" * 42, ""] + block_b_lines)
