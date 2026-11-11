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

DERIVATIVE ARCHIVE (Workstream B, derivative-archive-plan-2026-11-11.md): the same snapshots also carry period markets
(1H / Q1 / ...) and team totals, which game_line_quotes above discards by design. derivative_quotes() below is the sibling
parser that banks them instead, one row per book quote, in the archive schema of that plan:

    {"market": "h2h"|"spreads"|"totals"|"team_totals", "period": "full"|"q1".."q4"|"h1"|"h2"|"p1".."p3",
     "period_scope": "standalone"|"cumulative", "team_scope": "game"|<TEAM code>, "line": float | None,
     "book": str, "book_kind": "book"|"exchange", "price_a": int | None, "price_b": int | None,
     "price_draw": int | None (3-way ML only), "n_outcomes": 2|3, "source": "bestbook", "raw_family": <verbatim>}

price_a / price_b follow the plan: totals and team totals a = Over, b = Under; ML / spread a = away, b = home; spread `line`
is the HOME team's handicap (the convention above). `period_scope` is stored explicitly because feeds mix standalone
(quarters, hockey periods: that segment only) and cumulative (halves: 1H = Q1+Q2) silently. `raw_family` is the feed's own
family label verbatim, so a label-mapping bug is a re-derivation, not a data loss. Unknown derivative families are skipped
and COUNTED (pass a `stats` dict), never guessed.
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


# ---------------------------------------------------------------------------
# Derivative quotes: period markets + team totals (archive schema, see header)
# ---------------------------------------------------------------------------

# BestBook period tokens -> (archive period, period_scope). Halves are cumulative by definition (1H = Q1+Q2);
# quarters and hockey periods are standalone (that segment only). "full" / "rt" (regulation time) are the full game.
_PERIOD_TOKENS: dict[str, tuple[str, str]] = {
    "full": ("full", "standalone"), "rt": ("full", "standalone"),
    "1h": ("h1", "cumulative"), "2h": ("h2", "cumulative"),
    "1q": ("q1", "standalone"), "2q": ("q2", "standalone"), "3q": ("q3", "standalone"), "4q": ("q4", "standalone"),
    "1p": ("p1", "standalone"), "2p": ("p2", "standalone"), "3p": ("p3", "standalone"),
}
_MARKET_TOKENS = {"ml": "h2h", "h2h": "h2h", "spread": "spreads", "spreads": "spreads",
                  "total": "totals", "totals": "totals"}
_TEAM_TOTAL_TOKENS = {"teamtotal", "teamtotals", "team_total", "team_totals", "tt"}
_THREE_WAY_TOKENS = {"pwin3", "win3", "ml3", "3way", "h2h3"}
_SECTION_PERIOD = re.compile(r"[:@](1q|2q|3q|4q|1h|2h|1p|2p|3p)\b", re.IGNORECASE)


def _family_segments(row: dict) -> list[str]:
    """The family label's segments AFTER the game key: family is '{game}|token|token...', and the game key itself
    contains no '|', so segment 0 is the game and the rest are the label we map. Lowercased for matching only;
    the verbatim label is preserved separately as raw_family."""
    fam = str(row.get("family") or "")
    return [s.strip().lower() for s in fam.split("|")[1:] if s.strip()]


def _derivative_shape(row: dict):
    """(market, period, period_scope, team_scope, n_outcomes) for a derivative row, else None.

    A row is a derivative when it is a non-player 'lines' (or 'periods:*') row that is NOT a full-game row
    (game_line_quotes owns those) and whose family names a period market or a team total. Three-way ML families
    (pwin3 / win3 / ml3 / 3way) map to h2h with n_outcomes=3; period team totals carry both a team and a period."""
    if not isinstance(row, dict) or row.get("player"):
        return None
    sec = str(row.get("section") or "")
    if sec != "lines" and not sec.startswith("periods"):
        return None
    if _family_kind(row) is not None:
        return None                                    # full-game ML / spread / total: not a derivative
    segs = _family_segments(row)
    if not segs:
        return None
    teams = [str(t) for t in (row.get("gameTeams") or [])]
    period, scope = "full", "standalone"
    period_seen = False
    for s in segs:
        if s in _PERIOD_TOKENS:
            period, scope = _PERIOD_TOKENS[s]
            period_seen = True
    if not period_seen:
        m = _SECTION_PERIOD.search(sec)
        if m:
            period, scope = _PERIOD_TOKENS[m.group(1).lower()]
    # Team totals: a team-total token, with the team named by a family segment, rungTeam, or the row's team field.
    if any(s in _TEAM_TOTAL_TOKENS for s in segs):
        team = next((t for t in teams if t.lower() in segs), None) or row.get("rungTeam") or row.get("team")
        if team not in teams:
            return None
        return "team_totals", period, scope, str(team), 2
    # Three-way ML (a draw price exists): never silently merged with the 2-way shape.
    if any(s in _THREE_WAY_TOKENS for s in segs):
        return "h2h", period, scope, "game", 3
    market = next((_MARKET_TOKENS[s] for s in segs if s in _MARKET_TOKENS), None)
    if market is None or (period == "full" and not period_seen):
        return None                                    # a non-full-game market token with no period is not mappable
    if market == "h2h" and period == "full":
        return None
    return market, period, scope, "game", 2


def derivative_quotes(rows: list, stats: dict | None = None) -> list[dict]:
    """Raw BestBook snapshot rows of ONE game -> its derivative quotes (period markets + team totals), archive schema.

    Folding mirrors game_line_quotes: each ML / spread rung is read from both orientations and folded with gaps
    filled, never overwritten; DFS venues are skipped; exchange placeholder sides are dropped by the same
    probability band. A 3-way ML book quote folds its away / draw / home rows into ONE record (price_draw set,
    n_outcomes=3) so the two shapes can never be merged downstream by accident. Unmapped derivative-shaped rows
    increment stats['unknown_family'] (and stats['derivative_rows'] counts every row this parser claimed or
    counted); a malformed row is skipped and never aborts the game."""
    out: dict[tuple, dict] = {}
    for row in rows or []:
        shape = _derivative_shape(row)
        if shape is None:
            # Count, never guess: a lines / periods row that is neither full-game nor a mapped derivative.
            if stats is not None and isinstance(row, dict) and not row.get("player"):
                sec = str(row.get("section") or "")
                if (sec == "lines" or sec.startswith("periods")) and _family_kind(row) is None:
                    stats["unknown_family"] = stats.get("unknown_family", 0) + 1
            continue
        if stats is not None:
            stats["derivative_rows"] = stats.get("derivative_rows", 0) + 1
        market, period, scope, team_scope, n_outcomes = shape
        teams = row.get("gameTeams") or []
        if len(teams) < 2:
            continue
        away_bb, home_bb = teams[0], teams[1]
        try:
            if market in ("totals", "team_totals"):
                line = float(row.get("line"))
                side_of = None
            elif market == "spreads":
                rung_team = row.get("rungTeam")
                if rung_team not in (away_bb, home_bb):
                    continue
                h = _handicap(row.get("rung"))
                if h is None:
                    continue
                line = h if rung_team == home_bb else -h        # the board stores the HOME handicap
                side_of = rung_team == home_bb
            else:                                                # h2h
                line = None
                rung_team = row.get("rungTeam")
                rung = str(row.get("rung") or "").lower()
                if n_outcomes == 3 and ("draw" in rung or "tie" in rung):
                    side_of = "draw"
                elif rung_team in (away_bb, home_bb):
                    side_of = rung_team == home_bb
                else:
                    continue
            recs = bb.normalize_row(row)
        except (TypeError, ValueError):
            continue
        for q in recs:
            if not q.get("bettable"):
                continue
            yes, no = _side_price(q, "yes"), _side_price(q, "no")
            if yes is None and no is None:
                continue
            key = (market, period, team_scope, line, q.get("book"), n_outcomes)
            old = out.get(key)
            if old is None:
                old = out[key] = {"market": market, "period": period, "period_scope": scope,
                                  "team_scope": team_scope, "line": line, "book": q.get("book"),
                                  "book_kind": q.get("kind"), "price_a": None, "price_b": None,
                                  "price_draw": None, "n_outcomes": n_outcomes, "source": "bestbook",
                                  "raw_family": row.get("family")}
            if market in ("totals", "team_totals"):
                a, b = yes, no                                   # a = Over, b = Under
                if old["price_a"] is None:
                    old["price_a"] = a
                if old["price_b"] is None:
                    old["price_b"] = b
            elif side_of == "draw":
                if old["price_draw"] is None:
                    old["price_draw"] = yes
            elif n_outcomes == 3:
                # A 3-way row prices ONE outcome (yes = that outcome happens); its binary `no` is the
                # other-two-outcomes bundle, NOT the opposite team's price, so only `yes` is folded.
                if side_of:
                    if old["price_b"] is None:
                        old["price_b"] = yes
                elif old["price_a"] is None:
                    old["price_a"] = yes
            elif side_of:                                        # rung team is HOME: yes = home = b
                if old["price_b"] is None:
                    old["price_b"] = yes
                if old["price_a"] is None:
                    old["price_a"] = no
            else:                                                # rung team is AWAY: yes = away = a
                if old["price_a"] is None:
                    old["price_a"] = yes
                if old["price_b"] is None:
                    old["price_b"] = no
    return list(out.values())


def prune_single_book(quotes: list[dict]) -> list[dict]:
    """Drop every (market, line) that only one venue quotes: the price board needs >= 2 books per row, and the long tail of a
    ladder (a 30.5-point spread, a 10.5-goal total) is mostly one-book rungs. Keeps the stored crawl payload small."""
    seen: dict[tuple, int] = {}
    for q in quotes:
        k = (q["market"], q["line"])
        seen[k] = seen.get(k, 0) + 1
    return [q for q in quotes if seen[(q["market"], q["line"])] >= 2]
