"""
ev.bestbook_game_lines -- BestBook full-game MONEYLINE / SPREAD / TOTAL ladders as per-book two-way quotes.

BestBook's per-game snapshot (the same multi-MB stream the props crawl already pulls, so this costs no extra request) carries
the game's line board in section 'lines': one binary yes/no row per rung, with a price from every venue that quotes it.
Probed live 2026-11-01 (NBA LAC@GSW: 17 venues, ~100 totals rungs and ~90 spread rungs; NHL OTT@ANA: 18 venues, ~10 rungs):

  match:{game}|ml|{TEAM}                    yes = TEAM wins                      no = the other team wins
  match:{game}|spread|full|{TEAM}|{x}       yes = TEAM covers its rung ('TEAM -x')  no = the OTHER team covers +x
  match:{game}|total|full|{x}               yes = Over x                         no = Under x

The row's `line` is NOT the rung team's handicap (it is the AWAY team's), so the handicap is parsed from the `rung` label
('LAC -7.5') instead. Period markets (1h / 1p ...), team totals, three-way and 'wins by' families are ignored: only full game.

Output quote (the shape ev.game_odds_board.merge_bestbook consumes), spread `line` = the HOME team's handicap, a = AWAY / OVER,
b = HOME / UNDER, exactly the Odds board's own convention:
    {"market": "ml"|"spread"|"total", "line": float | None, "book": str, "a": int | None, "b": int | None}
"""

from __future__ import annotations

import re
from typing import Optional

from mlb_core.odds import bestbook as bb

# An exchange quotes 1c / 99c placeholders where nobody is bidding; a book's long-shot rung (+3500 / -10000) is real. Only
# exchange sides outside this probability band are dropped.
EXCHANGE_MIN_PROB = 0.03
EXCHANGE_MAX_PROB = 0.97

_MINUS = chr(0x2212)                                  # BestBook labels rungs with a real minus sign
_RUNG_HANDICAP = re.compile(r"([+\-" + _MINUS + r"])\s*(\d+(?:\.\d+)?)\s*$")


def _handicap(rung) -> Optional[float]:
    """'LAC -7.5' (real minus sign) -> -7.5, 'GSW +3' -> 3.0; None when the label has no signed number."""
    m = _RUNG_HANDICAP.search(str(rung or "").strip())
    if not m:
        return None
    v = float(m.group(2))
    return -v if m.group(1) in ("-", _MINUS) else v


def _family_kind(row: dict) -> Optional[str]:
    """'ml' / 'spread' / 'total' for a full-game line row, else None (periods, team totals, 3-way, combos, props)."""
    if not isinstance(row, dict) or row.get("section") != "lines" or row.get("player"):
        return None
    fam = str(row.get("family") or "")
    if fam.endswith("|ml"):
        return "ml"
    if fam.endswith("|spread|full"):
        return "spread"
    if fam.endswith("|total|full"):
        return "total"
    return None


def _side_price(q: dict, side: str) -> Optional[int]:
    """One side's American price from a normalized quote, or None. Books keep their native price (it is exactly what the book
    shows); exchanges use the probability-derived price and drop the 1c / 99c placeholder sides."""
    prob, derived, native = q.get(f"{side}_prob"), q.get(f"{side}_american"), q.get(f"{side}_native")
    if prob is None or derived is None:
        return None
    if q.get("kind") == "exchange":
        return derived if EXCHANGE_MIN_PROB <= prob <= EXCHANGE_MAX_PROB else None
    if native:
        try:
            v = int(str(native).replace(_MINUS, "-").replace("+", ""))
            if abs(v) >= 100:
                return v
        except ValueError:
            pass
    return derived


def game_line_quotes(rows: list) -> list[dict]:
    """Raw BestBook snapshot rows of ONE game -> the per-book two-way quotes of its full-game ML / spread / total ladders.

    Each rung is read from BOTH of its orientations by the caller's merge (the spread 'LAC -1.5' row and the 'GSW +1.5' row are the
    same market), so a book/line seen twice is folded here with gaps filled, never overwritten. DFS / pick'em venues (not
    bettable) are skipped. A malformed row is skipped and never aborts the game."""
    out: dict[tuple, dict] = {}
    for row in rows or []:
        kind = _family_kind(row)
        if kind is None:
            continue
        teams = row.get("gameTeams") or []
        if len(teams) < 2:
            continue
        away_bb, home_bb = teams[0], teams[1]
        try:
            if kind == "total":
                line = float(row.get("line"))
                side_of = None
            else:
                rung_team = row.get("rungTeam")
                if rung_team not in (away_bb, home_bb):
                    continue
                if kind == "ml":
                    line = None
                else:
                    h = _handicap(row.get("rung"))
                    if h is None:
                        continue
                    line = h if rung_team == home_bb else -h        # the board stores the HOME handicap
                side_of = rung_team == home_bb
            recs = bb.normalize_row(row)
        except (TypeError, ValueError):
            continue
        for q in recs:
            if not q.get("bettable"):
                continue
            yes, no = _side_price(q, "yes"), _side_price(q, "no")
            if yes is None and no is None:
                continue
            if kind == "total":
                a, b = yes, no                                       # a = Over, b = Under
            elif side_of:                                            # the rung team is HOME: yes = home, no = away
                a, b = no, yes
            else:                                                    # the rung team is AWAY: yes = away, no = home
                a, b = yes, no
            key = (kind, line, q.get("book"))
            old = out.get(key)
            if old is None:
                out[key] = {"market": kind, "line": line, "book": q.get("book"), "a": a, "b": b}
            else:
                if old["a"] is None:
                    old["a"] = a
                if old["b"] is None:
                    old["b"] = b
    return list(out.values())


def prune_single_book(quotes: list[dict]) -> list[dict]:
    """Drop every (market, line) that only one venue quotes: the price board needs >= 2 books per row, and the long tail of a
    ladder (a 30.5-point spread, a 10.5-goal total) is mostly one-book rungs. Keeps the stored crawl payload small."""
    seen: dict[tuple, int] = {}
    for q in quotes:
        k = (q["market"], q["line"])
        seen[k] = seen.get(k, 0) + 1
    return [q for q in quotes if seen[(q["market"], q["line"])] >= 2]
