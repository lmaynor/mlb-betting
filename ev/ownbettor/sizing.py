"""
ev.ownbettor.sizing -- Kelly staking (Luc's Nov 16, 2026 amendment; supersedes flat $5).

For a binary contract bought at price p (dollars per contract, 0-1) with model
probability q of the side, the full-Kelly fraction of bankroll is (q - p) / (1 - p) --
the same form the AUT-01 shadow uses.  The bettor stakes

    stake = bankroll * kelly_fraction * kelly_full,   capped at per_bet_cap_usd,

against the configured bankroll ($1,000 shipped; in dry-run the bankroll is virtual).
The stake is computed for EVERY signal, before any gate runs, so the audit log can
record what the bettor would have staked even on signals the rule gate rejects.
Contracts are whole: contracts = floor(stake / price), and the all-in cost
(contracts * price + taker fee) is re-checked against the per-bet cap at intent time.
"""

from __future__ import annotations

from mlb.execution.kalshi.fees import taker_fee

from ev.ownbettor.config import BettorConfig


def kelly_full(p_hat: float, price: float) -> float:
    """Full-Kelly bankroll fraction for buying `side` at `price`; 0 when no edge."""
    if price <= 0.0 or price >= 1.0 or p_hat <= price:
        return 0.0
    return (p_hat - price) / (1.0 - price)


def planned_stake(cfg: BettorConfig, p_hat: float, price: float) -> float:
    """Dollars the bettor plans to stake on a signal priced at `price` (0 = no bet)."""
    stake = cfg.bankroll * cfg.kelly_fraction * kelly_full(p_hat, price)
    return round(min(stake, cfg.per_bet_cap_usd), 4)


def contracts_for(stake: float, price: float, cfg: BettorConfig) -> tuple[int, float]:
    """(contracts, all-in cost) for `stake` dollars at `price`, per-bet cap enforced.

    Reduces the contract count until price*contracts + taker fee fits BOTH the planned
    stake and the per-bet hard cap.  (0, 0.0) when not even one contract fits.
    """
    if stake <= 0 or price <= 0.0 or price >= 1.0:
        return 0, 0.0
    budget = min(stake, cfg.per_bet_cap_usd)
    n = int(budget // price)
    while n > 0:
        cost = n * price + taker_fee(price, n, cfg.taker_multiplier)
        if cost <= budget + 1e-9:
            return n, round(cost, 4)
        n -= 1
    return 0, 0.0
