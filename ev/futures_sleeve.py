"""ev/futures_sleeve.py -- Module 8: Slow Markets / Futures Swing Sleeve.

Pure functions over supplied market snapshots and positions.
Maintains the two-sleeve bankroll allocation (Sleeve A: daily turnover, Sleeve B: swing futures),
n-way Power devig for outright markets, entry evaluation (|P_sim - q| >= 0.08 and EV >= 12%),
and mark-to-market 75% take-profit limit exit calculation.

NO venue client imports, NO order calls, NO database writes. Pure data and decision logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence


@dataclass(frozen=True)
class FuturesSnapshot:
    """A raw snapshot row of a futures/outright quote."""
    venue: str
    market: str
    outcome: str
    q: float  # implied probability
    quote_ts: datetime | None = None


@dataclass(frozen=True)
class FuturesBoardRow:
    """A normalized futures board row with Power-devigged fair probability."""
    venue: str
    market: str
    outcome: str
    q_market: float
    p_fair_power: float
    quote_ts: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FuturesPosition:
    """A swing sleeve position record (data-only, no venue interaction)."""
    market: str
    side: str
    q_entry: float
    p_sim: float
    exit_price: float
    sleeve: str = "B"
    notional: float = 0.0
    status: str = "OPEN"
    created_at: datetime | None = None


def sleeve_allocation(
    total_bankroll: float,
    sleeve_a_share: float = 0.825,
) -> tuple[float, float]:
    """Calculate bankroll split between Sleeve A (daily turnover) and Sleeve B (swing futures).

    Sleeve A: 80% - 85% of total bankroll (default 82.5%).
    Sleeve B: 15% - 20% of total bankroll (1.0 - sleeve_a_share).

    Returns:
        (sleeve_a_dollars, sleeve_b_dollars)
    Raises:
        ValueError: if sleeve_a_share is outside [0.80, 0.85] or bankroll < 0.
    """
    if total_bankroll < 0:
        raise ValueError(f"total_bankroll must be non-negative, got {total_bankroll}")
    if not (0.80 <= sleeve_a_share <= 0.85):
        raise ValueError(f"sleeve_a_share must be in [0.80, 0.85], got {sleeve_a_share}")

    a_dollars = round(total_bankroll * sleeve_a_share, 2)
    b_dollars = round(total_bankroll * (1.0 - sleeve_a_share), 2)
    return a_dollars, b_dollars


def devig_n_way_power(probs: Sequence[float]) -> list[float]:
    """N-way Power devig solver: solves sum(q_i^k) == 1.0 on k in [0.5, 15.0].

    Allocates overround to small probabilities proportionally more than proportional devig,
    correcting the favorite-longshot bias in multi-way outright markets.
    """
    p_list = [float(p) for p in probs]
    if any(p <= 0.0 or math.isnan(p) for p in p_list):
        raise ValueError(f"devig_n_way_power: all implied probs must be positive non-NaN, got {p_list}")

    s = sum(p_list)
    if s <= 1.0 + 1e-12:
        return [p / s for p in p_list]

    lo, hi = 0.5, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(p ** mid for p in p_list) > 1.0:
            lo = mid
        else:
            hi = mid

    k = (lo + hi) / 2.0
    raw = [p ** k for p in p_list]
    tot = sum(raw)
    return [r / tot for r in raw]


def bank_futures_board(snapshots: Sequence[dict | FuturesSnapshot]) -> list[FuturesBoardRow]:
    """Group futures snapshots by (venue, market) and apply n-way Power devig.

    Accepts dicts or FuturesSnapshot objects.
    Dict format: {'venue': str, 'market': str, 'outcome': str, 'q': float, 'ts': ...}
    """
    # Group items by (venue, market)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in snapshots:
        if isinstance(item, FuturesSnapshot):
            venue = item.venue
            market = item.market
            outcome = item.outcome
            q = item.q
            ts = item.quote_ts
            extra = {}
        elif isinstance(item, dict):
            venue = item.get("venue", "")
            market = item.get("market") or item.get("series", "")
            outcome = item.get("outcome", "")
            q = float(item.get("q") if item.get("q") is not None else item.get("implied_prob", 0.0))
            ts = item.get("quote_ts") or item.get("ts")
            extra = {k: v for k, v in item.items() if k not in ("venue", "market", "series", "outcome", "q", "implied_prob", "quote_ts", "ts")}
        else:
            continue

        key = (venue, market)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({"outcome": outcome, "q": q, "ts": ts, "extra": extra})

    board_rows: list[FuturesBoardRow] = []
    for (v, m), rows in grouped.items():
        q_vals = [r["q"] for r in rows]
        try:
            fair_vals = devig_n_way_power(q_vals)
        except Exception:
            # Fallback to proportional if n-way solver encounters degenerate input
            s = sum(q_vals)
            fair_vals = [q / s for q in q_vals] if s > 0 else [1.0 / len(q_vals)] * len(q_vals)

        for r, fair in zip(rows, fair_vals):
            board_rows.append(
                FuturesBoardRow(
                    venue=v,
                    market=m,
                    outcome=r["outcome"],
                    q_market=r["q"],
                    p_fair_power=fair,
                    quote_ts=r["ts"],
                    extra=r["extra"],
                )
            )

    return board_rows


def futures_ev_net(p_sim: float, q_market: float, side: str = "YES") -> float:
    """Compute decimal-payout net EV relative to market implied probability.

    YES buy:
        EV_net = p_sim / q_market - 1.0

    NO lay (symmetric form):
        Taking NO at q_no = 1.0 - q_market with true fair p_no = 1.0 - p_sim:
        EV_net, NO = (1.0 - p_sim) / (1.0 - q_market) - 1.0
    """
    if side.upper() == "NO":
        q_no = 1.0 - q_market
        if q_no <= 0.0:
            return -1.0
        return (1.0 - p_sim) / q_no - 1.0

    if q_market <= 0.0:
        return -1.0
    return p_sim / q_market - 1.0


def evaluate_futures_entry(
    p_sim: float,
    q_market: float,
    ev_net: float | None = None,
    side: str = "YES",
) -> bool:
    """Evaluate entry gate for futures swing position.

    Fires iff:
        |P_sim - q_market| >= 0.080 AND EV_net >= 0.12 (+12.0%)
    """
    if ev_net is None:
        ev_net = futures_ev_net(p_sim, q_market, side=side)

    dislocation = abs(p_sim - q_market)
    return dislocation >= 0.080 and ev_net >= 0.12


def take_profit_exit_price(q_entry: float, p_sim: float) -> float:
    """Calculate take-profit limit order exit price.

    Rule: P_exit = q_entry + 0.75 * (P_sim - q_entry).
    Locks in 75% convergence toward fair value.
    """
    return round(q_entry + 0.75 * (p_sim - q_entry), 4)
