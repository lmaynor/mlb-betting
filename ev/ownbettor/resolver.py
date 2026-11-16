"""
ev.ownbettor.resolver -- signal -> venue market resolution.

The repo's mapping doctrine is strict (mlb.execution.kalshi.mapping): a wrong mapping
is a wrong BET, so resolution is a lookup in a VERIFIED table, never a guess.  Kalshi
player-prop mapping does not exist in the repo yet (the Kalshi executor's mapping is
MLB game markets; its docstring parks player props as v2 on thin depth), so v1 ships:

  * the resolver seam (any object with resolve(signal) -> ResolvedMarket | None), and
  * RecordedTableResolver: a JSON table built offline from verified market data,
    keyed by signal_id, falling back to a league|event|stat|line|side composite key.

An unresolved signal is an audited REJ_UNMAPPED, never an approximated order.  Building
the verified live prop-mapping table is switch-on work (see the AUT-09 memo).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from ev.ownbettor.signals import Signal


@dataclass(frozen=True)
class ResolvedMarket:
    ticker: str
    ranges: tuple = ()                 # Kalshi price-grid ranges for order snapping
    exchange_index: int | None = None
    fee_multiplier: float | None = None


class TickerResolver(Protocol):
    def resolve(self, signal: Signal) -> ResolvedMarket | None: ...


def composite_key(signal: Signal) -> str:
    return f"{signal.league}|{signal.event_id}|{signal.stat}|{signal.line}|{signal.side}"


class RecordedTableResolver:
    def __init__(self, table: dict):
        self.table = table or {}

    @classmethod
    def from_file(cls, path: str) -> "RecordedTableResolver":
        if not path or not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def resolve(self, signal: Signal) -> ResolvedMarket | None:
        entry = self.table.get(signal.signal_id) or self.table.get(composite_key(signal))
        if not entry:
            return None
        ranges = tuple(tuple(r) for r in entry.get("ranges") or ())
        return ResolvedMarket(
            ticker=str(entry["ticker"]), ranges=ranges,
            exchange_index=entry.get("exchange_index"),
            fee_multiplier=entry.get("fee_multiplier"))
