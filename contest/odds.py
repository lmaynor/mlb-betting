"""American odds math in exact decimals; money in integer cents, rounded half-up once at the end."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable


def american_to_decimal(american: int) -> Decimal:
    """-110 -> 1.9090..., +150 -> 2.5. American odds have |a| >= 100; anything else is a bad price and raises."""
    a = int(american)
    if abs(a) < 100:
        raise ValueError(f"not an American price: {american!r}")
    return Decimal(1) + (Decimal(100) / Decimal(-a) if a < 0 else Decimal(a) / Decimal(100))


def parlay_decimal(prices: Iterable[int]) -> Decimal:
    total = Decimal(1)
    for p in prices:
        total *= american_to_decimal(p)
    return total


def payout_cents(stake_cents: int, decimal_odds: Decimal) -> int:
    """Total return (stake included) for a winning bet, rounded half-up to the cent."""
    return int((Decimal(int(stake_cents)) * decimal_odds).quantize(Decimal(1), rounding=ROUND_HALF_UP))
