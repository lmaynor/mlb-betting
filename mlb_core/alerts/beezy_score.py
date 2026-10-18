"""
mlb_core.alerts.beezy_score -- Python port of beezy-vip/lib/beezy-score.ts (Beezy Score v2).

A 0-100 STRENGTH score for one flagged price (NOT a win probability):

    edge       up to 60 pts   4 pts per 1% of edge (full at 15%)
    consensus  up to 20 pts   books behind the fair price (3 books = 0, 12+ = 20); unknown count = neutral 10
    hit prob   up to 20 pts   chance the bet wins (15% = 0, 50%+ = 20); longshots are penalised

consensus and hit prob are scaled by min(edge / 2%, 1), so a price with no edge scores 0. A standalone HR model pick
is capped inside LEAN (HR_MODEL_ONLY_CAP) unless >= HR_MIN_BOOKS books back it.

The site stays the source of truth. tests/test_beezy_score.py pins the constants against the TypeScript file so the two
cannot drift silently. Used by the free-plays picker and the per-sport alerts channels.
"""
from __future__ import annotations

import math
import re

WEIGHTS = {"edge": 60, "consensus": 20, "hit": 20}
TIER_CUTS = {"strong": 70, "lean": 45}
HR_MODEL_ONLY_CAP = 64
HR_MIN_BOOKS = 4


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _edge_pct(e) -> float:
    """edge is a FRACTION (0.08) on some rows and already PERCENT (8) on others."""
    if e is None or e != e:           # None / NaN
        return 0.0
    e = float(e)
    return e * 100 if abs(e) < 2 else e


def _prob(p) -> float:
    if p is None or p != p:
        return 0.0
    p = float(p)
    return p if p <= 1.5 else p / 100


def books_from_notes(notes) -> int | None:
    """EV rows carry the reference-book count in notes as '(N books)'; model bets do not."""
    m = re.search(r"\((\d+) books\)", notes) if isinstance(notes, str) else None   # NaN from pandas is not a str
    return int(m.group(1)) if m else None


def beezy_score(edge, n_books: int | None = None, p_hit=None, system: str | None = None) -> int:
    edge_pct = max(_edge_pct(edge), 0.0)
    e_pts = min(edge_pct * 4, WEIGHTS["edge"])
    if n_books is None:
        cons_raw = WEIGHTS["consensus"] / 2
    else:
        cons_raw = WEIGHTS["consensus"] * _clamp((n_books - 3) / 9, 0, 1)
    hit_raw = WEIGHTS["hit"] * _clamp((_prob(p_hit) - 0.15) / 0.35, 0, 1)
    scale = min(edge_pct / 2, 1)
    total = math.floor(_clamp(e_pts + cons_raw * scale + hit_raw * scale, 0, 100) + 0.5)  # Math.round: half up
    if system == "HR" and (n_books is None or n_books < HR_MIN_BOOKS):
        total = min(total, HR_MODEL_ONLY_CAP)
    return total


def tier(score: int) -> str:
    if score >= TIER_CUTS["strong"]:
        return "strong"
    if score >= TIER_CUTS["lean"]:
        return "lean"
    return "watch"
