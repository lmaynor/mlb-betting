"""
ev.ownbettor.venues.kalshi -- the Kalshi adapter (venue 1, standing venue order AUT-08).

Two forms behind the same seam:

  KalshiPaperAdapter -- dry-run.  Wraps mlb.execution.kalshi.venue.PaperVenue: an
      IOC limit order simulated against a FRESH book snapshot (recorded books in
      tests; the read-only public production data host for live dry-runs -- public
      endpoints only, no credentials, per the AUT-09 build scope).

  KalshiLiveAdapter -- real money.  Construction is a gauntlet of refusals, in order:
      1. OWNBETTOR_LIVE_ARMED off            -> LiveRefused (the hard switch ships OFF)
      2. kalshi_env != "production"          -> LiveRefused (demo is not real money)
      3. no credentials (env / GCP vault)    -> LiveNotProvisioned
      4. mlb.execution.kalshi.client refuses production in code today
         (ProductionNotEnabled)              -> LiveNotProvisioned
      Only when all four clear does it wrap KalshiVenue, submitting RESTING
      post-only limit orders (submit_resting: GTC, post_only -- the live form of
      "limit orders only").  Step 4 is Luc's provisioning step, not this build's.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.client import KalshiClient, ProductionNotEnabled
from mlb.execution.kalshi.venue import KalshiVenue, OrderIntent, PaperVenue

from ev.ownbettor.config import BettorConfig
from ev.ownbettor.keys import load_kalshi_credentials
from ev.ownbettor.venues.base import BettorIntent, VenueAck


class LiveRefused(RuntimeError):
    """The hard switch (or a mode guard) refuses live placement."""


class LiveNotProvisioned(RuntimeError):
    """Live was requested but a provisioning step Luc owns has not happened yet."""


class BookSource(Protocol):
    def book(self, ticker: str) -> Book | None: ...


class StaticBookSource:
    """Recorded books, in memory (tests, offline replays)."""

    def __init__(self, books: dict):
        self.books = books

    def book(self, ticker: str) -> Book | None:
        return self.books.get(ticker)


class RecordedBookSource:
    """Recorded books from a JSON file: {ticker: {"yes_dollars": [[p, s], ...],
    "no_dollars": [[p, s], ...]}} in Kalshi orderbook_fp shape."""

    def __init__(self, books: dict):
        self._books = {t: Book.from_response(t, {"orderbook_fp": ob}, 0.0)
                       for t, ob in books.items()}

    @classmethod
    def from_file(cls, path: str) -> "RecordedBookSource":
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return cls(json.load(f))

    def book(self, ticker: str) -> Book | None:
        return self._books.get(ticker)


class PublicBookSource:
    """Fresh books from Kalshi's PUBLIC production data host: unsigned, read-only,
    no credentials.  This is a connectivity/book source for dry-runs only."""

    def __init__(self):
        self._client = KalshiClient("public-data", None, None)

    def book(self, ticker: str) -> Book | None:
        try:
            return self._client.get_orderbook(ticker)
        except Exception:                                        # noqa: BLE001 -- unknown ticker / outage = no book
            return None


def _to_order_intent(intent: BettorIntent) -> OrderIntent:
    return OrderIntent(intent_id=intent.intent_id, ticker=intent.ticker, side=intent.side,
                       contracts=intent.contracts, limit_price=intent.limit_price,
                       ranges=intent.ranges, exchange_index=intent.exchange_index,
                       fee_multiplier=intent.fee_multiplier)


def _to_ack(ack, simulated: bool) -> VenueAck:
    return VenueAck(order_id=ack.order_id, client_order_id=ack.client_order_id,
                    status=ack.status, fill_count=ack.fill_count,
                    filled_cost=ack.filled_cost, fee=ack.fee, simulated=simulated,
                    error=ack.error, raw=ack.raw)


class KalshiPaperAdapter:
    name = "kalshi"
    simulated = True

    def __init__(self, book_source: BookSource, cfg: BettorConfig):
        self._source = book_source
        self._venue = PaperVenue(self._book_fn, multiplier=cfg.taker_multiplier)

    def _book_fn(self, ticker: str) -> Book:
        book = self._source.book(ticker)
        if book is None:
            raise LookupError(f"no book for {ticker}")
        return book

    def best_ask(self, ticker: str, side: str) -> float | None:
        book = self._source.book(ticker)
        return book.best_ask(side) if book else None

    def submit(self, intent: BettorIntent) -> VenueAck:
        return _to_ack(self._venue.submit(_to_order_intent(intent)), simulated=True)

    def cancel(self, order_id: str, ticker: str) -> dict:
        # Paper orders are IOC: nothing rests, so there is nothing to cancel.
        return {"status": "not_resting", "simulated": True}


class KalshiLiveAdapter:
    name = "kalshi"
    simulated = False

    def __init__(self, cfg: BettorConfig):
        if not cfg.live_armed:
            raise LiveRefused("OWNBETTOR_LIVE_ARMED is off: live placement refused "
                              "(the switch ships OFF and is Luc's later decision)")
        if cfg.kalshi_env != "production":
            raise LiveRefused(f"live mode requires kalshi_env='production', got {cfg.kalshi_env!r} "
                              "(demo is not real money)")
        creds = load_kalshi_credentials()
        if creds is None:
            raise LiveNotProvisioned("no Kalshi credentials provisioned "
                                     "(runtime env or the beezy-vault-prod vault)")
        try:
            client = KalshiClient("production", creds.key_id, creds.private_key_pem)
        except ProductionNotEnabled as e:
            raise LiveNotProvisioned(
                "the Kalshi client refuses production in code until the owner provisions "
                f"it (design s8 Phase 3): {e}") from e
        self._client = client
        self._venue = KalshiVenue(client)

    def best_ask(self, ticker: str, side: str) -> float | None:
        return self._client.get_orderbook(ticker).best_ask(side)

    def submit(self, intent: BettorIntent) -> VenueAck:
        # The live form of "limit orders only": a resting post-only GTC limit order.
        return _to_ack(self._venue.submit_resting(_to_order_intent(intent)), simulated=False)

    def cancel(self, order_id: str, ticker: str) -> dict:
        return self._client.cancel_order(order_id, ticker=ticker)
