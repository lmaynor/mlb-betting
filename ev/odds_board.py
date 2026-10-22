"""
ev.odds_board -- the full per-book price matrix behind the /ev/odds screen (NFL / NHL / NBA player props).

Every other prop surface exports one price (the best) or only the books of a FLAGGED outlier (`all_books`). The screen
needs every real book's two-sided price for every posted line, +EV or not, so this module keeps what
`ev.props.build_consensus` already computes and then throws away: the per-(game, player, stat, point) list of
`{book, over, under}` quotes from the same unioned ParlayAPI + BettingPros events the projection board reads.

No fair value is computed here. The reference books and the de-vig method are the viewer's choice on the screen, so the
payload carries raw American prices only. One-sided quotes (an anytime-TD "Yes" ladder) are kept with `under: null`.

Written to EVSweep/{sport}_props/odds_board_latest.json by ev.daily_projections.run_sport (the `daily-projections` job),
served by mlb/runners/public_api.py::get_odds_board at GET /api/public/odds/board. Never computed on a web request.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ev.props import DFS_BOOKS, OFFSHORE_BOOKS, parse_event_props

log = logging.getLogger("ev.odds_board")

SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "EVSweep/{sport}_props/odds_board_latest.json"
MAX_ROWS = 3000                      # nearest game first; ~1 MB of JSON at a 10-book average
MIN_BOOKS = 2                        # a one-book line is not a comparison
CACHE_TTL_SEC = 5 * 60               # the job rewrites the file every 30 min
STALE_MINUTES = 90

_CACHE: dict = {}                    # sport -> (time.monotonic() loaded, payload)


def build_board(sport: str, events: list | None, now: datetime, meta: dict | None = None) -> dict:
    """Banked events -> the odds-board payload for one sport (rows sorted nearest game first, then player, then stat).

    Alternate-line markets are skipped (a ladder of 6 lines per player would bury the main lines), DFS / pick'em apps are
    skipped (they carry the number, not a price), and a game that has started is dropped."""
    meta = meta or {}
    # imported here, not at module top: ev.daily_projections imports this module lazily from run_sport
    from ev.daily_projections import canon_team, game_key_of

    rows: dict = {}
    n_events = n_started = 0
    for ev in events or []:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            n_started += 1
            continue
        n_events += 1
        away, home = canon_team(sport, ev.get("away_team")), canon_team(sport, ev.get("home_team"))
        gk = game_key_of(sport, start, away, home)
        quotes, _ = parse_event_props(ev, sport)
        for q in quotes:
            if q.alt or q.book in DFS_BOOKS or (q.over is None and q.under is None):
                continue
            key = f"{gk}|{q.player_norm}|{q.stat}|{q.point}"
            row = rows.get(key)
            if row is None:
                row = rows[key] = {"key": key, "game_key": gk, "commence": start.astimezone(timezone.utc).isoformat(),
                                   "away": away, "home": home, "player": q.player, "player_norm": q.player_norm,
                                   "player_id": q.player_id, "team": q.team_hint, "stat": q.stat, "line": q.point,
                                   "books": {}}
            row["books"].setdefault(q.book, {"book": q.book, "over": q.over, "under": q.under})
    out = []
    for row in rows.values():
        books = sorted(row["books"].values(), key=lambda b: b["book"])
        if len(books) >= MIN_BOOKS:
            out.append({**row, "books": books})
    out.sort(key=lambda r: (r["commence"], r["player_norm"], r["stat"], r["line"]))
    cut = max(len(out) - MAX_ROWS, 0)
    out = out[:MAX_ROWS]
    book_keys = sorted({b["book"] for r in out for b in r["books"]})
    return {
        "sport": sport, "ts": now.isoformat(), "validated": False, "count": len(out), "rows": out,
        "books": book_keys, "offshore_books": sorted(b for b in book_keys if b in OFFSHORE_BOOKS),
        "stats": sorted({r["stat"] for r in out}),
        "games": [{"game_key": g[0], "start": g[1], "away": g[2], "home": g[3]}
                  for g in sorted({(r["game_key"], r["commence"], r["away"], r["home"]) for r in out},
                                  key=lambda g: (g[1], g[0]))],
        "status": "live" if out else "idle",
        "lines_age_minutes": meta.get("age_min"), "lines_source": list(meta.get("lines_source") or []),
        "summary": {"events": n_events, "events_started": n_started, "rows_cut": cut},
    }


def write_board(payload: dict, sport: str) -> None:
    """Best-effort write of one sport's board: a failed write never fails the projections compute."""
    if payload.get("status") == "unavailable":
        return
    try:
        from mlb_core import storage
        storage.write_bytes(json.dumps(payload, default=str).encode(), OUT_KEY.format(sport=sport))
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("odds board write failed for %s: %s", sport, exc)


def read_board(sport: str) -> dict | None:
    """The stored board for one sport, or None when it has not been produced yet. Process-cached for CACHE_TTL_SEC."""
    hit = _CACHE.get(sport)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    from mlb_core import storage
    try:
        payload = json.loads(storage.read_bytes(OUT_KEY.format(sport=sport)))
    except Exception:                                                                  # noqa: BLE001
        return None
    _CACHE[sport] = (time.monotonic(), payload)
    return payload


def board_age_min(payload: dict | None, now: datetime | None = None) -> float | None:
    try:
        return ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(str(payload.get("ts")))).total_seconds() / 60.0
    except Exception:                                                                  # noqa: BLE001
        return None
