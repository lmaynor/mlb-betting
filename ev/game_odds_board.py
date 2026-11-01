"""
ev.game_odds_board -- the per-book GAME-LINE price matrix (moneyline / spread / total) for NBA and NHL.

The game-line analogue of ev.odds_board. It reads the ALREADY-BANKED ParlayAPI game-line snapshot through
nba.game.odds_feed.read_banked_game_lines (the latest_game_lines.json pointer -> the raw game_lines_{HHMM}.json event list), so
it costs no ParlayAPI credits, and keeps every real book's two-sided American price for every posted line: raw prices only,
no fair value (the screen de-vigs the viewer's chosen reference books). Every row is unvalidated.

Parsing mirrors the game engines (nba.game.run_daily / nhl.game.run_daily extract_game_market_odds) so the two never disagree:
  * team names -> the engines' own codes (nba.teams.team_code / nhl.config.team_abbrev), so `game_key` equals the engine's
    "{sport}:{ET date}:{away}@{home}" and the frontend can join board rows to the model feed.
  * spreads: the line is the HOME outcome's point (home -3.5 => -3.5; the away side is the opposite sign).
  * totals: outcomes are matched by the names 'over' / 'under'; the line is their point.
  * h2h: outcomes matched to home / away by team code.
Side convention of a row's books[]: a = AWAY side (ml, spread) or OVER (total); b = HOME side or UNDER.

Written to EVSweep/{sport}_games/odds_board_latest.json by ev.daily_projections (right after the game-model refresh),
served by mlb/runners/public_api.py::get_game_odds_board at GET /api/public/games/odds. Never computed on a web request.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ev.odds_board import ROGUE_MIN_BOOKS, ROGUE_RATIO, _safe_decimal
from ev.props import DFS_BOOKS, OFFSHORE_BOOKS
from mlb_core.alerts.discord_routes import canonical_book

log = logging.getLogger("ev.game_odds_board")
_ET = ZoneInfo("America/New_York")

SPORTS = ("nba", "nhl")
PARLAY_KEY = {"nba": "basketball_nba", "nhl": "icehockey_nhl"}
OUT_KEY = "EVSweep/{sport}_games/odds_board_latest.json"
LINES_SOURCE = "oddsaccum_game_lines"
MARKETS = ("ml", "spread", "total")
MIN_BOOKS = 2                       # a one-book line is not a comparison
# The game-lines banker runs ~6x a day (daytime gaps up to 4 h) and a snapshot older than odds_feed.MAX_AGE_MIN (360) is refused
# outright, so 'stale' means older than the longest normal daytime gap (4 h) plus margin, not the props board's 90 minutes.
STALE_MINUTES = 270
# A book whose two-way quote has the OPPOSITE favourite to the consensus (and sits > FLIP_DEVIATION from the median no-vig side-a
# probability) is a swapped-sides feed error, not a price: seen live 2026-11-01 (betmgm quoted Philadelphia -185 / Utah +150 while
# eleven books had Utah -180 / Philadelphia +150), which would show as a ~28% "arbitrage". Needs FLIP_MIN_BOOKS two-sided books.
FLIP_DEVIATION = 0.15
FLIP_MIN_BOOKS = 4
# ParlayAPI market key -> board market. The alternate_* keys are accepted in case the feed carries them (unverified).
_MARKET_OF = {"h2h": "ml", "spreads": "spread", "totals": "total",
              "alternate_spreads": "spread", "alternate_totals": "total"}


def _resolver(sport: str):
    """The engine's own team-name -> code function for the sport (None / unknown names resolve to None)."""
    if sport == "nba":
        from nba.teams import team_code
        return team_code
    if sport == "nhl":
        from nhl.config import team_abbrev
        return team_abbrev
    raise ValueError(f"unknown game odds sport {sport!r}; expected one of {SPORTS}")


def _price(p: Any) -> Optional[int]:
    """American price as int, or None for a missing / zero / NaN / non-numeric / impossible (|p| < 100) price."""
    from nba.game.odds_feed import safe_price
    v = safe_price(p)
    if v is None or abs(v) < 100:
        return None
    return v


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v == v and abs(v) != float("inf") else None


def _parse_market(mk_key: str, outcomes: list, h_code: str, a_code: str, code_of) -> Optional[Tuple[Optional[float], Optional[int], Optional[int]]]:
    """One bookmaker market -> (line, a_price, b_price) or None when nothing usable. a = away / over, b = home / under."""
    kind = _MARKET_OF[mk_key]
    a_p = b_p = None
    if kind == "total":
        points = []
        for o in outcomes:
            if not isinstance(o, dict):
                continue
            side = str(o.get("name", "")).strip().lower()
            pt = _num(o.get("point"))
            if side == "over":
                a_p = _price(o.get("price"))
            elif side == "under":
                b_p = _price(o.get("price"))
            else:
                continue
            if pt is not None:
                points.append(pt)
        if not points or len(set(points)) != 1 or (a_p is None and b_p is None):
            return None
        return points[0], a_p, b_p
    home_pt = away_pt = None
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        code = code_of(str(o.get("name")))
        if code == h_code:
            b_p, home_pt = _price(o.get("price")), _num(o.get("point"))
        elif code == a_code:
            a_p, away_pt = _price(o.get("price")), _num(o.get("point"))
    if a_p is None and b_p is None:
        return None
    if kind == "ml":
        return None, a_p, b_p
    if home_pt is not None and away_pt is not None and abs(home_pt + away_pt) > 1e-6:
        return None                                              # sides disagree on the number: malformed
    line = home_pt if home_pt is not None else (-away_pt if away_pt is not None else None)
    if line is None:
        return None
    return line, a_p, b_p


def _game_key(sport: str, start: datetime, away: str, home: str) -> str:
    return f"{sport}:{start.astimezone(_ET).date().isoformat()}:{away}@{home}"


def _fmt_line(line: Optional[float]) -> str:
    return "ml" if line is None else f"{line:g}"


def _devig_a(a_p: Optional[int], b_p: Optional[int]) -> Optional[float]:
    da, db = _safe_decimal(a_p), _safe_decimal(b_p)
    if da is None or db is None:
        return None
    ia, ib = 1.0 / da, 1.0 / db
    return ia / (ia + ib)


def _drop_flipped_books(sport: str, row: dict) -> int:
    """Remove (both sides of) every book whose two-way quote favours the opposite side to the consensus. Returns how many."""
    probs = {name: _devig_a(b["a"], b["b"]) for name, b in row["books"].items()}
    probs = {n: p for n, p in probs.items() if p is not None}
    if len(probs) < FLIP_MIN_BOOKS:
        return 0
    dropped = 0
    for name, p in probs.items():
        others = [q for n, q in probs.items() if n != name]
        med = float(median(others))
        if (p - 0.5) * (med - 0.5) < 0 and abs(p - med) > FLIP_DEVIATION:
            log.warning("game_odds_board %s: dropping %s from %s, sides look swapped (no-vig a-prob %.2f vs peer median %.2f)",
                        sport, name, row["key"], p, med)
            row["books"][name]["a"] = row["books"][name]["b"] = None
            dropped += 1
    return dropped


def build_game_board(sport: str, events: Optional[list], now: datetime, meta: Optional[dict] = None) -> dict:
    """Banked game-line events -> the board payload for one sport (see the module docstring for the row shape).

    One row per (game, market, line); a row needs >= MIN_BOOKS books (a book counts when at least one side is priced). DFS /
    pick'em apps are skipped, a game that has started is dropped, a malformed price is skipped and never aborts the build, and
    a side quote whose decimal payout is above ROGUE_RATIO x the side's median is dropped when >= ROGUE_MIN_BOOKS books quote it."""
    meta = meta or {}
    code_of = _resolver(sport)
    rows: Dict[str, dict] = {}
    n_events = n_started = n_unmapped = 0
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time") or ev.get("start_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            n_started += 1
            continue
        away, home = code_of(str(ev.get("away_team", ""))), code_of(str(ev.get("home_team", "")))
        if not away or not home:
            n_unmapped += 1
            continue
        n_events += 1
        gk = _game_key(sport, start, away, home)
        for bm in ev.get("bookmakers") or ev.get("books") or []:
            if not isinstance(bm, dict):
                continue
            book = canonical_book(str(bm.get("key") or bm.get("name") or "")).strip()
            if not book or book in DFS_BOOKS:
                continue
            for mk in bm.get("markets") or []:
                if not isinstance(mk, dict) or mk.get("key") not in _MARKET_OF:
                    continue
                try:
                    parsed = _parse_market(mk["key"], mk.get("outcomes") or [], home, away, code_of)
                except Exception:                                                       # noqa: BLE001
                    log.debug("game_odds_board %s: skipped malformed %s market of %s", sport, mk.get("key"), book)
                    continue
                if parsed is None:
                    continue
                line, a_p, b_p = parsed
                market = _MARKET_OF[mk["key"]]
                key = f"{gk}|{market}|{_fmt_line(line)}"
                row = rows.get(key)
                if row is None:
                    row = rows[key] = {"key": key, "sport": sport, "game_key": gk, "away": away, "home": home,
                                       "commence": start.astimezone(timezone.utc).isoformat(), "market": market,
                                       "line": line, "books": {}}
                old = row["books"].get(book)
                if old is None:
                    row["books"][book] = {"book": book, "a": a_p, "b": b_p}
                else:                                   # a clone folded onto its twin (or a repeated market): fill gaps only
                    if old["a"] is None:
                        old["a"] = a_p
                    if old["b"] is None:
                        old["b"] = b_p

    n_flipped = 0
    for row in rows.values():
        n_flipped += _drop_flipped_books(sport, row)
    for row in rows.values():
        for side in ("a", "b"):
            quotes = [(name, b[side]) for name, b in row["books"].items() if b[side] is not None]
            if len(quotes) < ROGUE_MIN_BOOKS:
                continue
            decs = {name: _safe_decimal(p) for name, p in quotes}
            valid = [d for d in decs.values() if d is not None]
            if len(valid) < ROGUE_MIN_BOOKS:
                continue
            med = float(median(valid))
            for name, p in quotes:
                if decs[name] is not None and decs[name] > med * ROGUE_RATIO:
                    log.warning("game_odds_board %s: dropping rogue %s quote at %s for %s (%s vs peer median dec %.2f)",
                                sport, side, name, row["key"], p, med)
                    row["books"][name][side] = None

    kept: List[dict] = []
    for row in rows.values():
        books = sorted((b for b in row["books"].values() if b["a"] is not None or b["b"] is not None), key=lambda b: b["book"])
        if len(books) >= MIN_BOOKS:
            kept.append({**row, "books": books})

    # modal line per (game, market): the line most books quote (ties: nearest pick'em / lowest total)
    modal: Dict[tuple, float] = {}
    for r in kept:
        if r["market"] == "ml":
            continue
        gm = (r["game_key"], r["market"])
        cand = (-len(r["books"]), abs(r["line"]) if r["market"] == "spread" else r["line"], r["line"])
        if gm not in modal or cand < modal[gm][0]:
            modal[gm] = (cand, r["line"])
    mkt_order = {m: i for i, m in enumerate(MARKETS)}
    kept.sort(key=lambda r: (r["commence"], r["game_key"], mkt_order[r["market"]],
                             0.0 if r["market"] == "ml" else abs(r["line"] - modal[(r["game_key"], r["market"])][1]),
                             r["line"] if r["line"] is not None else 0.0))

    per_game: Dict[str, dict] = {}
    for r in kept:
        g = per_game.setdefault(r["game_key"], {"game_key": r["game_key"], "away": r["away"], "home": r["home"],
                                                "start": r["commence"], "n_rows": 0})
        g["n_rows"] += 1
    book_keys = sorted({b["book"] for r in kept for b in r["books"]})
    return {
        "sport": sport, "ts": now.isoformat(), "as_of": meta.get("snapshot_ts") or now.isoformat(), "validated": False,
        "status": "live" if kept else "awaiting_lines", "lines_source": LINES_SOURCE,
        "lines_age_minutes": meta.get("age_min"), "count": len(kept), "rows": kept, "books": book_keys,
        "offshore_books": sorted(b for b in book_keys if b in OFFSHORE_BOOKS),
        "games": sorted(per_game.values(), key=lambda g: (g["start"], g["game_key"])),
        "summary": {"events": n_events, "events_started": n_started, "events_unmapped": n_unmapped,
                    "banked_events": meta.get("banked_events"), "flipped_quotes_dropped": n_flipped},
    }


def write_game_board(payload: dict, sport: str) -> bool:
    """Best-effort write of one sport's board -> True if written. An 'unavailable' / 'stale' payload (a failed refresh) is never
    written, so the previous good file stays in place; a failed write never fails the projections compute."""
    if payload.get("status") in ("unavailable", "stale"):
        return False
    try:
        from mlb_core import storage
        storage.write_bytes(json.dumps(payload, default=str).encode(), OUT_KEY.format(sport=sport))
        return True
    except Exception as exc:                                                            # noqa: BLE001
        log.warning("game odds board write failed for %s: %s", sport, exc)
        return False


def run(sport: str, now: Optional[datetime] = None, write: bool = True) -> dict:
    """Load the banked game-line snapshot, build and (by default) write the board. A refused / missing snapshot returns an
    'unavailable' (or 'stale', when the snapshot is merely too old) payload with rows [] and a `reason`, and leaves the
    previous good file untouched."""
    from nba.game import odds_feed
    if sport not in SPORTS:
        raise ValueError(f"unknown game odds sport {sport!r}; expected one of {SPORTS}")
    now = now or datetime.now(timezone.utc)
    events, meta = odds_feed.read_banked_game_lines(PARLAY_KEY[sport], now)
    if not events:
        old = meta.get("age_min") is not None and meta["age_min"] > odds_feed.MAX_AGE_MIN
        return {"sport": sport, "ts": now.isoformat(), "as_of": meta.get("snapshot_ts"), "validated": False,
                "status": "stale" if old else "unavailable", "lines_source": LINES_SOURCE, "count": 0, "rows": [],
                "books": [], "offshore_books": [], "games": [], "lines_age_minutes": meta.get("age_min"),
                "reason": meta.get("reason") or "no banked game-line snapshot", "summary": {}}
    payload = build_game_board(sport, events, now, meta)
    if write:
        write_game_board(payload, sport)
    return payload
