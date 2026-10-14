"""
mlb.execution.kalshi.book -- order-book snapshot and executable ask ladders.

Kalshi's orderbook endpoint returns RESTING BIDS on each side:
    orderbook_fp = {"yes_dollars": [[price, size], ...],   # bids to buy YES, ascending price
                    "no_dollars":  [[price, size], ...]}   # bids to buy NO,  ascending price
There are no explicit asks: the ask to BUY YES at price p exists iff someone bids NO at 1-p.
    yes ask ladder = [(1 - p_no_bid, size)] for no bids, cheapest first (= highest no bid first)
    no  ask ladder = [(1 - p_yes_bid, size)] for yes bids, cheapest first
This is verified against the market object (yes_ask == 1 - best no bid) in the tests/probe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALID_SIDES = ("yes", "no")


@dataclass(frozen=True)
class Level:
    price: float   # dollars per contract, in the terms of the side being BOUGHT
    size: float    # contracts available at this price


def _levels(raw) -> list[tuple[float, float]]:
    out = []
    for item in raw or []:
        try:
            p, s = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0 and 0.0 < p < 1.0:
            out.append((p, s))
    return sorted(out)


@dataclass
class Book:
    ticker: str
    yes_bids: list[tuple[float, float]]
    no_bids: list[tuple[float, float]]
    fetched_at: float = field(default=0.0)   # epoch seconds when the HTTP response arrived

    @classmethod
    def from_response(cls, ticker: str, resp: dict, fetched_at: float) -> "Book":
        ob = (resp or {}).get("orderbook_fp") or {}
        return cls(ticker, _levels(ob.get("yes_dollars")), _levels(ob.get("no_dollars")), fetched_at)

    def asks(self, side: str) -> list[Level]:
        """Executable ladder for BUYING `side`, cheapest first."""
        if side not in VALID_SIDES:
            raise ValueError(f"side must be yes|no, got {side!r}")
        opposite = self.no_bids if side == "yes" else self.yes_bids
        return [Level(round(1.0 - p, 4), s) for p, s in sorted(opposite, reverse=True)]

    def best_bid(self, side: str) -> float | None:
        bids = self.yes_bids if side == "yes" else self.no_bids
        return bids[-1][0] if bids else None

    def best_ask(self, side: str) -> float | None:
        a = self.asks(side)
        return a[0].price if a else None

    def top_size(self, side: str) -> float:
        a = self.asks(side)
        return a[0].size if a else 0.0

    def is_two_sided(self) -> bool:
        return bool(self.yes_bids) and bool(self.no_bids)

    def is_crossed(self) -> bool:
        """best yes bid + best no bid > 1 means a free-money cross: bad/stale data."""
        by, bn = self.best_bid("yes"), self.best_bid("no")
        return by is not None and bn is not None and (by + bn) > 1.0 + 1e-9

    def yes_spread(self) -> float | None:
        ask, bid = self.best_ask("yes"), self.best_bid("yes")
        return None if ask is None or bid is None else round(ask - bid, 4)

    def yes_mid(self) -> float | None:
        ask, bid = self.best_ask("yes"), self.best_bid("yes")
        return None if ask is None or bid is None else round((ask + bid) / 2.0, 6)
