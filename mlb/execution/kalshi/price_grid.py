"""
mlb.execution.kalshi.price_grid -- snap prices onto a market's tick grid.

Kalshi enforces a per-market, sometimes tapered price grid (market["price_ranges"]:
a list of {start, end, step}). An unsnapped price is rejected live ("invalid_price",
learned the hard way in ~/market-maker). For a BUY limit we must never exceed our
maximum acceptable price, so we snap DOWN; for a sell-yes order that expresses a
buy-NO we snap UP (a higher yes-price means a LOWER no cost).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class PriceRange:
    start: float
    end: float
    step: float


def parse_price_ranges(raw) -> list[PriceRange]:
    """market["price_ranges"] -> sorted [PriceRange]. Empty/invalid -> []."""
    out = []
    for r in raw or []:
        try:
            out.append(PriceRange(float(r["start"]), float(r["end"]), float(r["step"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: r.start)


def _range_for(price: float, ranges: list[PriceRange]) -> PriceRange:
    for r in ranges:
        if r.start - _EPS <= price <= r.end + _EPS:
            return r
    return ranges[-1] if price > ranges[-1].end else ranges[0]


def snap_down(price: float, ranges: list[PriceRange]) -> float:
    """Largest grid price <= `price` (clamped to the grid). Falls back to 4dp rounding."""
    if not ranges:
        return round(math.floor(price * 10000 + _EPS) / 10000, 4)
    p = min(max(price, ranges[0].start), ranges[-1].end)
    r = _range_for(p, ranges)
    steps = math.floor((p - r.start) / r.step + _EPS)
    return round(min(r.start + steps * r.step, r.end), 4)


def snap_up(price: float, ranges: list[PriceRange]) -> float:
    """Smallest grid price >= `price` (clamped to the grid). Falls back to 4dp rounding."""
    if not ranges:
        return round(math.ceil(price * 10000 - _EPS) / 10000, 4)
    p = min(max(price, ranges[0].start), ranges[-1].end)
    r = _range_for(p, ranges)
    steps = math.ceil((p - r.start) / r.step - _EPS)
    return round(min(r.start + steps * r.step, r.end), 4)
