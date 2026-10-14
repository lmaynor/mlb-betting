"""
mlb.execution.kalshi.sizing -- net-of-fee EV and fractional-Kelly order sizing.

For a binary contract with all-in cost c per contract (price + fee/contract) that pays
$1 on a win, and a win probability p_hat:

    ev = p_hat / c - 1                  # net EV per dollar staked
    f* = (p_hat - c) / (1 - c)          # full-Kelly fraction of bankroll spent on cost
    f  = lambda * f*                    # fractional Kelly

Derivation: b = (1-c)/c; f* = (b*p - q)/b = (p - c)/(1 - c). Probability and price MUST
share one basis -- here the all-in executable cost, never a mid or a devigged price
(same rule as mlb_core.odds.utils.kelly_stake).

c depends on how deep the order sweeps the ask ladder, so plan_order() binary-searches
the largest whole-contract N for which BOTH the EV gate and the Kelly/cap budget hold
at the VWAP all-in cost of those N contracts. (ev falls and N*c rises as N grows, so
feasibility is a prefix and binary search is exact.)
"""

from __future__ import annotations

from dataclasses import dataclass

from mlb.execution.kalshi.book import Book, Level
from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.price_grid import PriceRange, snap_down


def kelly_fraction(p_hat: float, cost: float) -> float:
    """Full-Kelly fraction of bankroll for a $1-payout binary at all-in cost `cost`."""
    if not (0.0 < cost < 1.0) or p_hat <= cost:
        return 0.0
    return (p_hat - cost) / (1.0 - cost)


def net_ev(p_hat: float, cost: float) -> float:
    return p_hat / cost - 1.0 if cost > 0 else float("-inf")


@dataclass(frozen=True)
class Sweep:
    contracts: int
    price_cost: float     # sum(price * qty)
    fee: float
    last_price: float     # price of the deepest level touched (the IOC limit)

    @property
    def total_cost(self) -> float:
        return self.price_cost + self.fee

    @property
    def cost_per_contract(self) -> float:
        return self.total_cost / self.contracts

    @property
    def vwap(self) -> float:
        return self.price_cost / self.contracts


def sweep(levels: list[Level], n: int, multiplier: float = 1.0) -> Sweep | None:
    """Cost of buying exactly `n` contracts walking `levels` (cheapest first).

    Fee is summed per level (Kalshi charges on each matched trade). Returns None if the
    ladder lacks the depth.
    """
    if n <= 0:
        return None
    remaining, price_cost, fee, last = float(n), 0.0, 0.0, 0.0
    for lv in levels:
        if remaining <= 0:
            break
        take = min(remaining, lv.size)
        price_cost += take * lv.price
        fee += taker_fee(lv.price, take, multiplier)
        last = lv.price
        remaining -= take
    if remaining > 1e-9:
        return None
    return Sweep(n, round(price_cost, 6), round(fee, 6), last)


@dataclass(frozen=True)
class Plan:
    side: str
    contracts: int
    limit_price: float          # max price (in the terms of the side bought), grid-snapped DOWN
    vwap: float
    fee: float
    cost_per_contract: float    # c: all-in
    ev: float
    f_star: float
    f_applied: float
    stake: float                # total all-in dollars at risk
    p_hat: float


@dataclass(frozen=True)
class Reject:
    reason: str                 # REJ_* reason code
    detail: str = ""


def plan_order(
    book: Book,
    side: str,
    p_hat: float,
    cfg: ExecConfig,
    lam: float,
    bankroll: float,
    budget_cap: float,
    ranges: list[PriceRange] | None = None,
) -> Plan | Reject:
    """Size one taker order.

    budget_cap = the tightest REMAINING dollar budget among per-game / daily-deploy caps
    (the caller computes it from the risk ledger). The per-bet cap is applied here.
    """
    levels = [lv for lv in book.asks(side) if lv.price <= cfg.extreme_max]
    if not levels:
        return Reject("REJ_BOOK_ILLIQUID", "no executable ask")

    depth = int(sum(lv.size for lv in levels))
    first = sweep(levels, 1, cfg.taker_multiplier)
    if first is None or depth < 1:
        return Reject("REJ_BOOK_ILLIQUID", "depth < 1 contract")

    ev1 = net_ev(p_hat, first.cost_per_contract)
    if ev1 > cfg.ev_cap:
        return Reject("REJ_EV_TOO_HIGH", f"ev={ev1:.4f} > cap {cfg.ev_cap}")
    if ev1 < cfg.ev_min:
        return Reject("REJ_EV_LOW", f"ev={ev1:.4f} < min {cfg.ev_min}")

    stake_cap = min(cfg.per_bet_cap, budget_cap)

    def feasible(n: int) -> Sweep | None:
        sw = sweep(levels, n, cfg.taker_multiplier)
        if sw is None:
            return None
        c = sw.cost_per_contract
        if net_ev(p_hat, c) < cfg.ev_min:
            return None
        budget = min(lam * kelly_fraction(p_hat, c) * bankroll, stake_cap)
        return sw if sw.total_cost <= budget + 1e-9 else None

    if feasible(cfg.min_contracts) is None:
        return Reject("REJ_SIZE_TOO_SMALL", f"budget cannot buy {cfg.min_contracts} contract(s)")

    lo, hi = cfg.min_contracts, depth     # lo is feasible; find the largest feasible n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if feasible(mid) is not None:
            lo = mid
        else:
            hi = mid - 1
    sw = feasible(lo)
    assert sw is not None

    if sw.total_cost < cfg.min_stake:
        return Reject("REJ_SIZE_TOO_SMALL", f"stake ${sw.total_cost:.2f} < min ${cfg.min_stake:.2f}")

    c = sw.cost_per_contract
    fs = kelly_fraction(p_hat, c)
    limit = snap_down(sw.last_price, ranges or [])
    return Plan(
        side=side, contracts=sw.contracts, limit_price=limit, vwap=round(sw.vwap, 6),
        fee=sw.fee, cost_per_contract=round(c, 6), ev=round(net_ev(p_hat, c), 6),
        f_star=round(fs, 6), f_applied=round(lam * fs, 6), stake=round(sw.total_cost, 4),
        p_hat=round(p_hat, 6),
    )
