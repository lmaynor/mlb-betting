"""
ev.props -- sportsbook player-prop quotes (ParlayAPI) -> per-(player, stat, point) fair-probability consensus.

Shapes observed in a REAL ParlayAPI NFL event payload (2026-10-04; fixture tests/fixtures/ev/):
  * market KEYS differ by book for the same stat (player_rec_yds / player_receiving_yards /
    player_reception_yds, player_rush_yards / _rush_yds / _rushing_yards, ...); aliases below.
  * outcome name "Over <player>" / "Under <player>" with description = player (two-sided), or a one-sided
    "Yes" outcome at a ladder `point` (bovada pass TDs, anytime TD) -- treated as Over at that point.
  * some books append a team to the description: "Jonathan Taylor (IND)".
  * DFS / pick'em apps (sleeper, underdog, ...) quote ~-112/-112 everywhere: they carry the NUMBER, not a
    price, so they are EXCLUDED from the fair-probability median (counted in n_dfs for visibility).

Fair probability = median over real sportsbooks of each book's own 2-way de-vigged P(over). One-sided
quotes are counted (n_onesided) but NOT used: de-vigging them needs an assumed vig that has no NFL
calibration yet, so they are not decision-grade.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

DFS_BOOKS = frozenset({"sleeper", "underdog", "prizepicks", "dabble", "betr", "fliff"})

# canonical stat -> every ParlayAPI market key seen (or requested) for it
STAT_KEYS = {
    "pass_yds": {"player_pass_yds", "player_passing_yards"},
    "rush_yds": {"player_rush_yds", "player_rush_yards", "player_rushing_yards"},
    "rec_yds": {"player_reception_yds", "player_rec_yds", "player_receiving_yards"},
    "receptions": {"player_receptions"},
    "pass_tds": {"player_pass_tds", "player_passing_touchdowns"},
    "anytime_td": {"player_anytime_td", "player_anytime_touchdown_scorer"},
}
_KEY_TO_STAT = {k: s for s, ks in STAT_KEYS.items() for k in ks}

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
_TEAM_TAIL = re.compile(r"\s*\(([A-Za-z]{2,4})\)\s*$")


def stat_of(market_key: str):
    """ParlayAPI market key -> (canonical stat | None, is_alternate)."""
    alt = market_key.endswith("_alternate")
    base = market_key[: -len("_alternate")] if alt else market_key
    return _KEY_TO_STAT.get(base), alt


def split_player(raw: str):
    """'Jonathan Taylor (IND)' -> ('Jonathan Taylor', 'IND'); no team -> (raw, None)."""
    raw = (raw or "").strip()
    m = _TEAM_TAIL.search(raw)
    return (raw[: m.start()].strip(), m.group(1).upper()) if m else (raw, None)


def norm_name(name: str) -> str:
    """Accent-fold, lowercase, drop punctuation and generational suffixes: 'Ja’Marr Chase Jr.' -> 'jamarr chase'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[-_]", " ", s)
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if t not in _SUFFIX_TOKENS]
    return " ".join(toks)


@dataclass(frozen=True)
class Quote:
    book: str
    stat: str
    alt: bool
    player: str            # display name (team suffix stripped)
    player_norm: str
    team_hint: str | None
    point: float
    over: int | None       # american price; for a one-sided 'Yes' ladder this is the Yes price
    under: int | None

    @property
    def two_sided(self) -> bool:
        return self.over is not None and self.under is not None


def parse_event_props(event_obj: dict) -> tuple[list[Quote], dict]:
    """One ParlayAPI single-event odds object -> ([Quote], {'unmapped_keys': {...}})."""
    quotes: list[Quote] = []
    unmapped: dict = {}
    for book in (event_obj or {}).get("bookmakers", []) or []:
        bkey = book.get("key", "")
        for market in book.get("markets", []) or []:
            mkey = market.get("key", "")
            stat, alt = stat_of(mkey)
            if stat is None:
                if mkey.startswith("player_"):
                    unmapped[mkey] = unmapped.get(mkey, 0) + 1
                continue
            cell: dict = {}
            for o in market.get("outcomes", []) or []:
                raw, price, point = o.get("description"), o.get("price"), o.get("point")
                name = (o.get("name") or "").strip()
                if not raw or price is None or point is None or "{" in raw or " @ " in raw:
                    continue
                low = name.lower()
                side = "over" if (low.startswith("over") or low == "yes") else \
                       "under" if low.startswith("under") else None
                if side is None:
                    continue
                player, team = split_player(raw)
                rec = cell.setdefault((norm_name(player), float(point)),
                                      {"player": player, "team": team, "over": None, "under": None})
                if rec[side] is None:                       # first price wins; a book repeats a point rarely
                    rec[side] = int(price)
            for (pn, pt), rec in cell.items():
                quotes.append(Quote(bkey, stat, alt, rec["player"], pn, rec["team"], pt, rec["over"], rec["under"]))
    return quotes, {"unmapped_keys": unmapped}


@dataclass(frozen=True)
class PropConsensus:
    p_over: float
    n_books: int           # two-sided real sportsbooks used in the median
    disp: float            # MAD of per-book fair P(over)
    books: tuple
    n_onesided: int
    n_dfs: int
    team_hints: tuple = ()  # distinct team codes any book attached to this player's quote ('IND')


def _mad(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(np.median(np.abs(x - np.median(x)))) if len(x) else 0.0


def build_consensus(quotes: list[Quote], devig_method: str = "proportional") -> dict:
    """{(player_norm, stat, point): PropConsensus} for every point with >= 1 usable two-sided real book."""
    groups: dict = {}
    for q in quotes:
        groups.setdefault((q.player_norm, q.stat, q.point), []).append(q)
    out = {}
    for key, qs in groups.items():
        per_book: dict = {}
        n_dfs = 0
        onesided = set()
        hints = {q.team_hint for q in qs if q.team_hint}
        for q in qs:
            if q.book in DFS_BOOKS:
                n_dfs += 1
                continue
            if not q.two_sided:
                onesided.add(q.book)
                continue
            fair_over, _ = devig_two_way(american_to_implied_prob(q.over), american_to_implied_prob(q.under),
                                         devig_method)
            per_book[q.book] = float(fair_over)
        if not per_book:
            continue
        vals = list(per_book.values())
        out[key] = PropConsensus(
            p_over=float(np.median(vals)), n_books=len(vals), disp=_mad(vals), books=tuple(sorted(per_book)),
            n_onesided=len(onesided - set(per_book)), n_dfs=n_dfs, team_hints=tuple(sorted(hints)))
    return out
