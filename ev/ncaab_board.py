"""
ev.ncaab_board -- the NCAAB Game Lines board, built from the three banked feeds (ev.ncaab_banker + BestBook's cbb crawl).

    Odds/bettingpros_live/ncaab/game_lines_latest.json   ev.ncaab_banker   (anchor: start time + team names, ~20 books)
    Odds/bestbook_live/cbb/game_lines_latest.json        ev.bestbook_events (~14 venues, full ladders)
    Odds/kalshi_live/ncaab/game_lines_latest.json        ev.ncaab_banker   (exchange ladders; date only, no start time)

The three sources use different team codes for the same school (BettingPros 'CSF' / 'HP' / 'BUT', BestBook and Kalshi 'CSUF' / 'HPU' /
'BTLR') and there is no cross-source D1 code map, so games are JOINED, not looked up: BettingPros games are the anchors (the only feed
with an exact start time and team names); a BestBook game joins the anchor starting within JOIN_WINDOW whose BOTH team codes look alike
(equal, prefix, or SequenceMatcher >= MIN_SIM), a Kalshi game joins the anchor on the same ET date by the same rule. A game no anchor
claims keeps its own source's codes (BestBook alone has ~14 books, enough for a board row); a Kalshi-only game has no start time and one
book, so it never reaches the board. The board then goes through ev.game_odds_board.build_game_board unchanged (same de-vig, flipped-
book / rogue-quote guards, ladder cap, history), so NCAAB rows have exactly the NBA / NHL shape.

Per (market, line, book) the FIRST source wins (newest file first): a row never mixes one side from one snapshot and the other side from
another. Nothing here is a model: it is a price screen, unvalidated.
"""

from __future__ import annotations

import difflib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("ev.ncaab_board")

SPORT = "ncaab"
SOURCES = {
    "bettingpros": "Odds/bettingpros_live/ncaab/game_lines_latest.json",
    "bestbook": "Odds/bestbook_live/cbb/game_lines_latest.json",
    "kalshi": "Odds/kalshi_live/ncaab/game_lines_latest.json",
}
MAX_AGE_MIN = 180.0                  # the crawls are hourly: three misses and a feed is not live
JOIN_WINDOW = timedelta(hours=3)
MIN_SIM = 0.6
# Known code pairs the fuzzy match cannot see (extend as the board shows split games): BestBook / Kalshi code -> BettingPros code.
ALIASES = {"BTLR": "BUT"}
# The same book under two spellings across feeds (BettingPros' catalog name vs BestBook's venue code): one book on the board.
BOOK_FIX = {"hard rock": "hardrock", "thescore bet": "thescore", "polymarket us": "polymarket", "bally bet": "ballybet",
            "draftkings predictions": "draftkings"}
_ET = ZoneInfo("America/New_York")


def team_code(name) -> Optional[str]:
    """The board's team resolver for NCAAB: the banked feeds already carry a code, so it is the code, upper-cased."""
    s = str(name or "").strip().upper()
    return s or None


def sim(a: str, b: str) -> float:
    a, b = ALIASES.get(a.upper(), a.upper()), ALIASES.get(b.upper(), b.upper())
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def _ts(v) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def read_source(source: str, now: datetime, max_age_min: float = MAX_AGE_MIN) -> tuple[Optional[dict], dict]:
    """One banked file -> (file dict, meta); the dict is None (with meta['reason']) when missing, undated, stale or empty."""
    from mlb_core import storage
    try:
        obj = json.loads(storage.read_bytes(SOURCES[source]))
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": f"no {source} NCAAB file yet ({type(exc).__name__})"}
    ts = _ts(obj.get("fetched_at"))
    if ts is None:
        return None, {"reason": f"{source} NCAAB file has no fetched_at"}
    age = (now - ts).total_seconds() / 60.0
    meta = {"age_min": round(age, 1), "fetched_at": ts.isoformat(), "games": len(obj.get("games") or [])}
    if age > max_age_min:
        return None, {**meta, "reason": f"{source} NCAAB file is {age:.0f} min old (> {max_age_min:.0f})"}
    if not obj.get("games"):
        return None, {**meta, "reason": f"{source} NCAAB file has no games"}
    return obj, meta


def _match(g: dict, anchors: list[dict], by_date: bool) -> Optional[dict]:
    """The anchor game `g` is the same game as: both teams alike, and (start within JOIN_WINDOW | same ET date). Best score wins."""
    best, best_sc = None, 0.0
    gs = _ts(g.get("commence_time"))
    for a in anchors:
        as_ = _ts(a.get("commence_time"))
        if as_ is None:
            continue
        if by_date:
            if as_.astimezone(_ET).date().isoformat() != g.get("date"):
                continue
        elif gs is None or abs(as_ - gs) > JOIN_WINDOW:
            continue
        sc = min(sim(str(g.get("away_team", "")), str(a["away_team"])), sim(str(g.get("home_team", "")), str(a["home_team"])))
        if sc >= MIN_SIM and sc > best_sc:
            best, best_sc = a, sc
    return best


def unify(files: dict[str, dict]) -> tuple[list[dict], dict[str, str], dict]:
    """{source: banked file} -> (games in the BestBook shape build_game_board takes, {code: team name}, counts).

    Sources are folded newest file first and the first quote per (market, line, book) wins."""
    order = sorted(files, key=lambda s: str(files[s].get("fetched_at")), reverse=True)
    bp = files.get("bettingpros")
    anchors: list[dict] = []
    names: dict[str, str] = {}
    counts = {"anchors": 0, "joined": 0, "own": 0, "dropped": 0}
    claimed: dict[int, dict] = {}                           # id(anchor) -> {(market, line, book): quote}

    def _anchor(g: dict) -> dict:
        a = {"away_team": str(g["away_team"]).upper(), "home_team": str(g["home_team"]).upper(),
             "commence_time": g.get("commence_time"), "quotes": []}
        claimed[id(a)] = {}
        for side, nm in (("away_team", g.get("away_name")), ("home_team", g.get("home_name"))):
            if nm:
                names[a[side]] = str(nm)
        return a

    def _fold(a: dict, quotes: list) -> None:
        seen = claimed[id(a)]
        for q in quotes or []:
            book = BOOK_FIX.get(str(q.get("book") or "").lower(), q.get("book"))
            k = (q.get("market"), q.get("line"), book)
            if k not in seen:
                seen[k] = {**q, "book": book}
                a["quotes"].append(seen[k])

    bp_games: list[tuple[dict, dict]] = []                  # (banked game, its anchor)
    if bp:
        for g in bp["games"]:
            if g.get("away_team") and g.get("home_team") and _ts(g.get("commence_time")):
                a = _anchor(g)
                anchors.append(a)
                bp_games.append((g, a))
        counts["anchors"] = len(anchors)
    bp_anchors = list(anchors)
    for s in order:                                          # newest file first: its quote wins a (market, line, book) tie
        if s == "bettingpros":
            for g, a in bp_games:
                _fold(a, g.get("quotes"))
            continue
        for g in files[s]["games"]:
            a = _match(g, bp_anchors, by_date=(s == "kalshi")) if bp_anchors else None
            if a is not None:
                counts["joined"] += 1
                _fold(a, g.get("quotes"))
            elif s == "kalshi" or _ts(g.get("commence_time")) is None:
                counts["dropped"] += 1                       # a Kalshi-only game has no start time and one book
            else:
                own = next((x for x in anchors if x["away_team"] == str(g["away_team"]).upper()
                            and x["home_team"] == str(g["home_team"]).upper() and x["commence_time"] == g.get("commence_time")), None)
                if own is None:
                    own = _anchor(g)
                    anchors.append(own)
                    counts["own"] += 1
                _fold(own, g.get("quotes"))
    return [a for a in anchors if a["quotes"]], names, counts


def read_unified(now: datetime) -> tuple[Optional[list], Optional[dict], dict[str, str], dict]:
    """(games, meta, team names, counts) from every fresh banked file; games is None when no source is fresh."""
    files: dict[str, dict] = {}
    reasons: dict[str, str] = {}
    ages: list[float] = []
    stamps: list[str] = []
    for s in SOURCES:
        obj, meta = read_source(s, now)
        if obj is None:
            reasons[s] = meta.get("reason", "")
            continue
        files[s] = obj
        ages.append(meta["age_min"])
        stamps.append(meta["fetched_at"])
    if not files:
        return None, {"reason": "; ".join(reasons.values()) or "no NCAAB files"}, {}, {}
    games, names, counts = unify(files)
    counts["sources"] = sorted(files)
    return games, {"fetched_at": max(stamps), "age_min": min(ages)}, names, counts


def run(now: Optional[datetime] = None, write: bool = True) -> dict:
    """Build and (by default) write the NCAAB board; with no fresh banked source returns an 'unavailable' payload and leaves the
    previous good file untouched (ev.game_odds_board.write_game_board refuses it)."""
    from ev import game_odds_board as gob
    now = now or datetime.now(timezone.utc)
    games, meta, names, counts = read_unified(now)
    if not games:
        return {"sport": SPORT, "ts": now.isoformat(), "as_of": None, "validated": False, "status": "unavailable",
                "lines_source": gob.BESTBOOK_SOURCE, "count": 0, "rows": [], "books": [], "offshore_books": [], "games": [],
                "history": {}, "lines_age_minutes": None, "reason": (meta or {}).get("reason") or "no priced NCAAB games",
                "summary": {}}
    payload = gob.build_game_board(SPORT, None, now, None, games, meta, prev_history=gob.read_previous_history(SPORT))
    payload["lines_source"] = "+".join(counts.get("sources", [])) or payload.get("lines_source")
    payload["team_names"] = {c: n for c, n in names.items() if any(c in (g["away"], g["home"]) for g in payload["games"])}
    payload["summary"]["ncaab_join"] = counts
    if write:
        gob.write_game_board(payload, SPORT)
    return payload


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = run()
    log.info("NCAAB_BOARD %s", json.dumps({"status": p.get("status"), "games": len(p.get("games") or []), "rows": p.get("count"),
                                           "books": len(p.get("books") or []), "join": (p.get("summary") or {}).get("ncaab_join")}))
    return 0 if p.get("status") in ("live", "awaiting_lines") else 1


if __name__ == "__main__":
    sys.exit(main())
