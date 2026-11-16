"""
ev.ownbettor.venues.base -- the venue seam.

One adapter interface so Novig and ProphetX own-account adapters can follow Kalshi
without touching the gate / sizing / risk / audit pipeline.  An adapter only knows how
to (a) quote the current best ask for a side, (b) submit a LIMIT order intent, and
(c) cancel a resting order.  Market orders do not exist in this interface -- limit
orders only, by construction (RUL-20261115-autopilot-design).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol


def intent_id_for(signal_id: str) -> str:
    """Deterministic intent id: reprocessing a signal can never intend a second order."""
    return "ownbet-" + hashlib.sha256(signal_id.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class BettorIntent:
    intent_id: str
    signal_id: str
    ticker: str
    side: str                     # 'yes' | 'no' -- the side being BOUGHT
    contracts: int
    limit_price: float            # max price in the terms of `side`
    planned_stake_usd: float
    ranges: tuple = ()
    exchange_index: int | None = None
    fee_multiplier: float | None = None


@dataclass
class VenueAck:
    order_id: str | None
    client_order_id: str
    status: str                   # FILLED | PARTIAL | NO_FILL | RESTING | ERROR
    fill_count: float = 0.0
    filled_cost: float = 0.0      # price*count + fee, all-in
    fee: float = 0.0
    simulated: bool = True
    error: str | None = None
    raw: dict | None = field(default=None)


class VenueAdapter(Protocol):
    name: str
    simulated: bool               # True = paper/dry-run; False = real-money venue

    def best_ask(self, ticker: str, side: str) -> float | None: ...
    def submit(self, intent: BettorIntent) -> VenueAck: ...
    def cancel(self, order_id: str, ticker: str) -> dict: ...
