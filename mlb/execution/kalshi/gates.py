"""
mlb.execution.kalshi.gates -- the pure decision function (design doc s3 S2-S5).

evaluate(leg, book, cons, k_prev, now, cfg, bankroll, budget_cap) -> Decision

Stages, each a gate that ends the candidate with a reason code:
  game started? -> Kalshi book fresh/liquid/sane? -> consensus fresh/thick/tight?
  -> per side: price band -> gross edge -> Kalshi-off (news) -> lag signature
  -> shrunk p_hat -> net-of-fee EV gate + Kelly sizing (sizing.plan_order).

Pure: no I/O, no clock reads (now is passed in), so every branch is unit-tested. Both sides of the
contract are evaluated (buy YES / buy NO); at most one can be +EV.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.consensus import ConsPoint
from mlb.execution.kalshi.mapping import Leg
from mlb.execution.kalshi.sizing import Plan, Reject, plan_order

STRATEGY_VERSION = "v1"

CONSENSUS_MOVED = "CONSENSUS_MOVED"
KALSHI_INFORMED = "KALSHI_INFORMED"
PERSISTENT = "PERSISTENT"
UNKNOWN = "UNKNOWN"


@dataclass
class Decision:
    outcome: str                         # "PLAN" or a REJ_* code
    reason: str = ""
    side: str | None = None
    plan: Plan | None = None
    lag_class: str | None = None
    p_cons: float | None = None
    p_hat: float | None = None
    n_books: int | None = None
    ask: float | None = None
    intent_id: str | None = None
    selection_bought: str | None = None
    inputs: dict = field(default_factory=dict)


def intent_id(leg: Leg, selection_bought: str) -> str:
    """One intent per ECONOMIC bet per day: HOME-yes on one team's market and NO on the other
    team's market are the same bet, so they share an id (and the order dedup)."""
    key = f"{leg.market}|{leg.game_pk}|{leg.line}|{selection_bought}|{leg.game_date}|{STRATEGY_VERSION}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def lag_signature(dp_cons, dp_kalshi, move: float) -> str:
    """How did the gap open? Probabilities are for the side being BOUGHT.

    dp_cons   = change in book consensus over the lookback
    dp_kalshi = change in Kalshi's mid over the lookback
      consensus moved up while Kalshi did not follow -> Kalshi is LAGGING       (the thesis)
      Kalshi fell while consensus stayed flat        -> informed selling / news (reject)
      otherwise                                      -> gap is persistent
    """
    if dp_cons is None or dp_kalshi is None:
        return UNKNOWN
    if dp_cons >= move and dp_kalshi < move / 2:
        return CONSENSUS_MOVED
    if dp_kalshi <= -move and abs(dp_cons) < move / 2:
        return KALSHI_INFORMED
    return PERSISTENT


def shrink(cons: ConsPoint, now: datetime, cfg: ExecConfig) -> float:
    """Haircut h: base + staleness + dispersion. Large apparent edges are dominated by reference
    error (CLV flips sign with edge size -- profit review 2026-07-23), so we shade p_B down."""
    age_min = max(0.0, (now - cons.ts).total_seconds() / 60.0)
    return cfg.h0 + cfg.h_age_per_min * max(0.0, age_min - cfg.h_age0_min) + cfg.h_disp * cons.disp


def evaluate(leg: Leg, book: Book, cons: ConsPoint | None, k_prev: float | None, now: datetime,
             cfg: ExecConfig, bankroll: float, budget_cap: float) -> Decision:
    inputs = {"ticker": leg.ticker, "market": leg.market, "game_pk": leg.game_pk,
              "line": leg.line, "start_utc": leg.start_utc.isoformat()}

    def rej(code: str, detail: str = "", **kw) -> Decision:
        return Decision(code, detail, inputs=dict(inputs, **kw.pop("extra", {})), **kw)

    # ---- game state ----
    if now >= leg.start_utc - timedelta(minutes=cfg.close_buffer_min):
        return rej("REJ_GAME_STARTED", f"start {leg.start_utc.isoformat()}")

    # ---- S2: Kalshi book ----
    age_s = now.timestamp() - book.fetched_at
    inputs["book_age_s"] = round(age_s, 2)
    if age_s > cfg.book_max_age_s:
        return rej("REJ_BOOK_STALE", f"book age {age_s:.1f}s > {cfg.book_max_age_s}s")
    # allow_wide_book_on_consensus: the reference price is an EXTERNAL book consensus, not this venue's mid, so a
    # wide / one-sided venue book does not make the reference noisy -- only the executable ask matters (it is
    # checked against the consensus below instead of the mid). Default False keeps Kalshi behavior unchanged.
    wide_ok = cfg.allow_wide_book_on_consensus
    if not wide_ok and not book.is_two_sided():
        return rej("REJ_BOOK_ILLIQUID", "one-sided book")
    if book.is_crossed():
        return rej("REJ_BOOK_ILLIQUID", "crossed book")
    spread = book.yes_spread()
    inputs["yes_spread"] = spread
    wide = spread is None or spread > cfg.spread_max
    if wide and not wide_ok:
        return rej("REJ_BOOK_ILLIQUID", f"spread {spread} > {cfg.spread_max}")
    inputs["wide_book"] = wide
    yes_mid = book.yes_mid()
    inputs["kalshi_yes_mid"] = yes_mid

    # ---- S3: consensus ----
    if cons is None:
        return rej("REJ_CONS_THIN", "no consensus for this proposition")
    cons_age_min = (now - cons.ts).total_seconds() / 60.0
    inputs.update(cons_age_min=round(cons_age_min, 1), n_books=cons.n_books, disp=round(cons.disp, 4),
                  p_cons_yes=round(cons.p, 4))
    if cons_age_min > cfg.cons_max_age_min:
        return rej("REJ_CONS_STALE", f"consensus age {cons_age_min:.1f}min > {cfg.cons_max_age_min}")
    if cons.n_books < cfg.n_min_books or cons.disp > cfg.disp_max:
        return rej("REJ_CONS_THIN", f"n_books={cons.n_books} disp={cons.disp:.3f}")

    # ---- per side ----
    h = shrink(cons, now, cfg)
    inputs["shrink_h"] = round(h, 4)
    results: list[tuple[float, Decision]] = []
    for side in ("yes", "no"):
        p_side = cons.p if side == "yes" else 1.0 - cons.p
        mid_side = None if yes_mid is None else (yes_mid if side == "yes" else 1.0 - yes_mid)
        sel = leg.selection_yes if side == "yes" else leg.selection_no
        ask = book.best_ask(side)
        side_inputs = dict(inputs, side=side, p_cons_side=round(p_side, 4), ask=ask)
        base = dict(side=side, p_cons=round(p_side, 4), n_books=cons.n_books, ask=ask,
                    selection_bought=sel, intent_id=intent_id(leg, sel))

        def srej(code, detail="", lag=None, **extra):
            return Decision(code, detail, lag_class=lag, inputs=dict(side_inputs, **extra), **base)

        if ask is None:
            results.append((-1.0, srej("REJ_BOOK_ILLIQUID", "no ask")))
            continue
        gap = p_side - ask
        if not (cfg.price_min <= ask <= cfg.price_max):
            results.append((gap, srej("REJ_PRICE_BAND", f"ask {ask} outside [{cfg.price_min},{cfg.price_max}]")))
            continue
        if gap <= 0:
            results.append((gap, srej("REJ_EV_LOW", f"ask {ask} >= consensus {p_side:.4f}")))
            continue
        if wide:
            # wide/one-sided book (wide_ok only): the mid is meaningless, so bound the ask's distance to consensus
            if gap > cfg.kdev_max:
                results.append((gap, srej("REJ_KALSHI_OFF", f"wide book: cons {p_side:.3f} - ask {ask:.3f} > {cfg.kdev_max}")))
                continue
        elif abs(mid_side - p_side) > cfg.kdev_max:
            results.append((gap, srej("REJ_KALSHI_OFF", f"|mid {mid_side:.3f} - cons {p_side:.3f}| > {cfg.kdev_max}")))
            continue

        dp_cons = None if cons.prev_p is None else (p_side - (cons.prev_p if side == "yes" else 1.0 - cons.prev_p))
        dp_k = None if k_prev is None or mid_side is None else (mid_side - (k_prev if side == "yes" else 1.0 - k_prev))
        lag = lag_signature(dp_cons, dp_k, cfg.lag_move)
        if lag == KALSHI_INFORMED:
            results.append((gap, srej("REJ_KALSHI_INFORMED", f"dp_kalshi={dp_k:.3f} dp_cons={dp_cons:.3f}",
                                      lag=lag)))
            continue
        lam = cfg.kelly_lambda if lag == CONSENSUS_MOVED else cfg.kelly_lambda_persistent
        p_hat = p_side - h

        plan = plan_order(book, side, p_hat, cfg, lam, bankroll, budget_cap, list(leg.ranges))
        extra = dict(dp_cons=dp_cons, dp_kalshi=dp_k, lam=lam, p_hat=round(p_hat, 4))
        if isinstance(plan, Reject):
            d = srej(plan.reason, plan.detail, lag=lag, **extra)
            d.p_hat = round(p_hat, 4)
            results.append((gap, d))
            continue
        d = Decision("PLAN", "", side=side, plan=plan, lag_class=lag, p_cons=round(p_side, 4),
                     p_hat=round(p_hat, 4), n_books=cons.n_books, ask=ask,
                     intent_id=base["intent_id"], selection_bought=sel,
                     inputs=dict(side_inputs, **extra))
        results.append((gap + 1e6, d))                 # a plan always outranks any reject

    return max(results, key=lambda t: t[0])[1]
