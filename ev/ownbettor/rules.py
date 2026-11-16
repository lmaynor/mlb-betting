"""
ev.ownbettor.rules -- the signal rule gate (RUL-20261115-autopilot-design s3/s5).

Every signal passes this gate before any order is intended:
  * net EV >= 1.5% (the signal's own shrunk p_hat against its signal price,
    fee-adjusted at the executable book price again at intent time),
  * net EV <= 10% cap (reject, never clamp -- an implausible edge is a bad input),
  * odds ceiling +300: never bet a price below 0.25,
  * limit orders only: a signal without an upstream limit price ceiling is refused,
    and every order this package intends is a limit order by construction.

The gate is pure: it takes the signal and the stake sizing already computed for it,
and returns a decision.  Recording the decision (with the stake) is the runner's job,
so a rejected signal still leaves its computed stake in the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass

from ev.ownbettor.config import BettorConfig
from ev.ownbettor.signals import Signal


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""             # REJ_* code when not allowed
    ev_net_pct: float | None = None


def net_ev_pct(p_hat: float, price: float) -> float | None:
    """Net EV in percent of buying at `price` with model prob `p_hat` (pre-fee form;
    the executable re-check at intent time deducts the Kalshi taker fee)."""
    if price <= 0:
        return None
    return (p_hat / price - 1.0) * 100.0


def evaluate(signal: Signal, cfg: BettorConfig) -> GateDecision:
    ev = net_ev_pct(signal.p_hat, signal.signal_price)
    if signal.signal_price < cfg.price_min:
        return GateDecision(False, "REJ_PRICE_BAND", ev)
    if ev is None or ev < cfg.ev_floor * 100.0:
        return GateDecision(False, "REJ_EV_LOW", ev)
    if ev > cfg.ev_cap * 100.0:
        return GateDecision(False, "REJ_EV_CAP", ev)
    if signal.limit_price_ceiling is None or signal.limit_price_ceiling <= 0:
        return GateDecision(False, "REJ_NO_LIMIT", ev)
    return GateDecision(True, "", ev)
