"""
mlb.execution.kalshi.venue -- one order interface, two implementations.

    PaperVenue  -- shadow mode: simulates an IOC order against a FRESH real order book
                   (fetched at submit time), consuming only levels at or below the limit.
                   No optimistic fills at stale prices.
    KalshiVenue -- Kalshi DEMO: real signed orders with fake money.

Both take the same OrderIntent so the decision code is identical in shadow and demo (the
"seam" idea from ~/market-maker's ExecutionVenue). TAKER ONLY: every order is
immediate_or_cancel; there is no resting-order path.

Kalshi v2 prices everything in YES terms: side 'bid' = buy YES at price p; side 'ask' = sell
YES at p, which is the same exposure as buying NO at 1 - p. So:
    buy YES, max price L  -> bid  @ snap_down(L)
    buy NO,  max price L  -> ask  @ snap_up(1 - L)   (a higher yes-price == a lower NO cost)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Protocol

from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.client import KalshiClient
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.price_grid import PriceRange, snap_down, snap_up

log = logging.getLogger("kalshi_exec.venue")


def client_order_id(intent_id: str) -> str:
    """Deterministic: a retried submit for the same intent can never create a second order."""
    return "kx" + hashlib.sha256(intent_id.encode()).hexdigest()[:30]


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    ticker: str
    side: str                     # 'yes' | 'no' -- the side being BOUGHT
    contracts: int
    limit_price: float            # max price in the terms of `side`
    ranges: tuple = ()            # PriceRange tuple for grid snapping
    exchange_index: int | None = None
    fee_multiplier: float | None = None   # paper venue only: the series' taker multiplier


@dataclass(frozen=True)
class FillRec:
    fill_id: str
    order_id: str
    ticker: str
    side: str
    count: float
    price: float                  # in the terms of the side bought
    fee: float
    ts: str


@dataclass
class Ack:
    order_id: str | None
    client_order_id: str
    status: str                   # FILLED | PARTIAL | NO_FILL | ERROR
    fill_count: float = 0.0
    filled_cost: float = 0.0      # price*count + fee, all-in
    fee: float = 0.0
    fills: list = field(default_factory=list)
    raw: dict | None = None
    error: str | None = None


class Venue(Protocol):
    def submit(self, intent: OrderIntent) -> Ack: ...


def _status(filled: float, wanted: int) -> str:
    if filled <= 0:
        return "NO_FILL"
    return "FILLED" if filled >= wanted - 1e-9 else "PARTIAL"


class PaperVenue:
    """IOC against the live book. `book_fn(ticker) -> Book` must return a FRESH snapshot."""

    def __init__(self, book_fn: Callable[[str], Book], multiplier: float = 1.0):
        self._book_fn, self._mult = book_fn, multiplier
        self._n = 0

    def submit(self, intent: OrderIntent) -> Ack:
        coid = client_order_id(intent.intent_id)
        book = self._book_fn(intent.ticker)
        remaining, fills = float(intent.contracts), []
        for lv in book.asks(intent.side):
            if remaining <= 0 or lv.price > intent.limit_price + 1e-9:
                break
            take = min(remaining, lv.size)
            self._n += 1
            mult = self._mult if intent.fee_multiplier is None else intent.fee_multiplier
            fills.append(FillRec(f"paper-{coid}-{self._n}", f"paper-{coid}", intent.ticker, intent.side,
                                 take, lv.price, taker_fee(lv.price, take, mult), "paper"))
            remaining -= take
        filled = sum(f.count for f in fills)
        cost = sum(f.count * f.price + f.fee for f in fills)
        return Ack(f"paper-{coid}", coid, _status(filled, intent.contracts), filled, round(cost, 4),
                   round(sum(f.fee for f in fills), 4), fills, raw={"simulated": True})


class KalshiVenue:
    def __init__(self, client: KalshiClient):
        self.client = client

    @staticmethod
    def to_api(intent: OrderIntent) -> tuple[str, float]:
        ranges = list(intent.ranges)
        if intent.side == "yes":
            return "bid", snap_down(intent.limit_price, ranges)
        return "ask", snap_up(1.0 - intent.limit_price, ranges)

    def submit(self, intent: OrderIntent) -> Ack:
        coid = client_order_id(intent.intent_id)
        book_side, price = self.to_api(intent)
        try:
            resp = self.client.create_order(
                ticker=intent.ticker, client_order_id=coid, book_side=book_side,
                count=float(intent.contracts), price=price, time_in_force="immediate_or_cancel",
                exchange_index=intent.exchange_index)
        except Exception as e:                                        # noqa: BLE001
            return Ack(None, coid, "ERROR", error=repr(e)[:400])

        order_id = resp.get("order_id")
        fills = self._fills_for(order_id, intent) if order_id else []
        filled = sum(f.count for f in fills) or float(resp.get("fill_count") or 0)
        cost = sum(f.count * f.price + f.fee for f in fills)
        return Ack(order_id, coid, _status(filled, intent.contracts), filled, round(cost, 4),
                   round(sum(f.fee for f in fills), 4), fills, raw=resp)

    def _fills_for(self, order_id: str, intent: OrderIntent) -> list[FillRec]:
        out = []
        for f in self.client.get_fills(order_id=order_id):
            side = f.get("outcome_side") or intent.side
            price = float(f["yes_price_dollars"] if side == "yes" else f["no_price_dollars"])
            out.append(FillRec(str(f["fill_id"]), str(f["order_id"]), f["ticker"], side,
                               float(f["count_fp"]), price, float(f.get("fee_cost") or 0.0),
                               str(f.get("created_time") or "")))
        return out
