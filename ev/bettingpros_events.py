"""
ev.bettingpros_events -- LIVE BettingPros props as ParlayAPI-shaped event dicts, for the NFL / NHL / NBA picks boards.

ev.bettingpros_live turns BettingPros offers into `Quote` objects. The projection board (ev.daily_projections) and the
banked-odds sweeps consume ParlayAPI EVENT payloads instead, so this module adds the missing layer: it crawls a day
window of events + offers, keeps only real two-sided sportsbook / exchange prices (ev.bettingpros_live.LIVE_BOOKS: DFS
apps, Kalshi, the BettingPros 'consensus' pseudo-book and opening-line rows never get in), and re-emits them as

    {id, sport_key, commence_time, away_team, home_team, source: 'bettingpros',
     bookmakers: [{key, markets: [{key: 'player_points', outcomes: [{name: 'Over', description: <player (TEAM)>,
                                                                       price, point}, {name: 'Under', ...}]}]}]}

so ev.props.parse_event_props, ev.daily_projections.build_line_rows, the consensus and Sink A work UNCHANGED. Team names
are the sport's canonical team codes (ev.daily_projections.canon_team accepts them as-is).

Market keys: ONE canonical ParlayAPI key per stat (PARLAY_KEY) so a BettingPros quote and a ParlayAPI quote land in one
consensus group. A line the book does not flag as its main line gets the `_alternate` suffix, which ev.props.stat_of
understands and ev.daily_projections.pick_main_line ignores whenever a main-line quote exists.

Storage: Odds/bettingpros_live/{sport}/latest.json = {sport, fetched_at, window, events, counts}. The file is only
replaced by a crawl that produced data (or truthfully found no upcoming games), never by a failed one.

Cost/pace: BettingPros is free but the offers endpoint caps a page at 10 offers, so a busy slate is 100-250 requests per
sport. fetch_offers_paged already sleeps ~0.8 s (+-40%) between pages; this module adds a pause between markets.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev import bettingpros_live as bl
from ev.props import norm_name
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("ev.bettingpros_events")

_ET = ZoneInfo("America/New_York")
SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "Odds/bettingpros_live/{sport}/latest.json"

# Days AHEAD of today (ET) to crawl. NBA opening night is quoted ~4 days out; the NFL slate is weekly; NHL plays most
# nights and BettingPros only prices the next ~2 days.
DEFAULT_DAYS = {"nfl": 7, "nhl": 3, "nba": 7}

# canonical stat -> THE ParlayAPI market key a BettingPros quote is filed under. Every value must be a key ev.props
# already maps back to the stat (tests/test_bettingpros_events.py pins it for all sports).
PARLAY_KEY = {
    "nfl": {"pass_yds": "player_pass_yds", "rush_yds": "player_rush_yds", "rec_yds": "player_reception_yds",
            "receptions": "player_receptions", "pass_tds": "player_pass_tds", "anytime_td": "player_anytime_td",
            "pass_attempts": "player_pass_attempts", "pass_completions": "player_pass_completions",
            "rush_attempts": "player_rush_attempts", "interceptions": "player_interceptions"},
    "nhl": {"goals": "player_goals", "sog": "player_shots_on_goal", "points": "player_points",
            "assists": "player_assists", "saves": "player_saves", "blocked_shots": "player_blocked_shots"},
    "nba": {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists",
            "threes": "player_threes", "pra": "player_points_rebounds_assists", "pr": "player_points_rebounds",
            "pa": "player_points_assists", "ra": "player_rebounds_assists", "steals": "player_steals",
            "blocks": "player_blocks", "triple_double": "player_triple_double", "double_double": "player_double_double"},
}

# BettingPros team codes ev.daily_projections.canon_team cannot resolve (found probing the 2026-27 NBA slate, 4 of 36
# events were dropped as unknown teams). NFL / NHL codes (JAC, LA, NJ, SJ, TB, ...) all resolve as they are.
BP_TEAM_FIX = {"nba": {"UTH": "UTA", "NOR": "NOP"}}


def bp_team(sport: str, code) -> str:
    c = str(code or "").strip().upper()
    return BP_TEAM_FIX.get(sport, {}).get(c, c)


# Books that are on BettingPros under a name different from the ParlayAPI key are already mapped in
# bl.LIVE_BOOKS (theScore Bet -> 'espnbet', Hard Rock -> 'hardrock'). The set of keys a crawl can emit:
EMITTED_BOOKS = frozenset(bl.LIVE_BOOKS.values())


class _CountingSession:
    """Wraps a requests session: counts HTTP calls (bp.api_get only uses .get / .headers)."""

    def __init__(self, sess):
        self._s = sess
        self.requests = 0

    @property
    def headers(self):
        return self._s.headers

    def get(self, *a, **k):
        self.requests += 1
        return self._s.get(*a, **k)


def _sleep(base: float) -> None:
    if base > 0:
        time.sleep(base * random.uniform(0.6, 1.4))


def window_dates(now: datetime, days: int) -> list[str]:
    """ET calendar dates today .. today + days inclusive (BettingPros' `date` filter is an ET day: a 20:15 ET Monday
    game is stamped 00:15 UTC Tuesday and is returned under Monday's date)."""
    d0 = now.astimezone(_ET).date()
    return [(d0 + timedelta(days=i)).isoformat() for i in range(0, max(int(days), 0) + 1)]


def parse_scheduled(s) -> datetime | None:
    """BettingPros 'scheduled' ('YYYY-MM-DD HH:MM:SS', UTC) -> aware datetime, None when unparseable."""
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _iso_z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def quotes_to_bookmakers(quotes: list, sport: str, teams: dict | None = None) -> list[dict]:
    """[Quote] -> ParlayAPI `bookmakers` list. `teams` = {player_norm: BettingPros team code}: appended to the player
    description as '(TEAM)', the form ParlayAPI books use, which parse_event_props splits back into a team hint."""
    keys = PARLAY_KEY[sport]
    teams = teams or {}
    by_book: dict = {}
    for q in quotes:
        mk = keys.get(q.stat)
        if mk is None:
            continue
        if q.alt:
            mk += "_alternate"
        t = teams.get(q.player_norm)
        desc = f"{q.player} ({t})" if t else q.player
        outs = by_book.setdefault(q.book, {}).setdefault(mk, [])
        if q.over is not None:
            outs.append({"name": "Over", "description": desc, "price": int(q.over), "point": float(q.point)})
        if q.under is not None:
            outs.append({"name": "Under", "description": desc, "price": int(q.under), "point": float(q.point)})
    return [{"key": b, "title": b, "markets": [{"key": mk, "outcomes": outs} for mk, outs in sorted(mkts.items())]}
            for b, mkts in sorted(by_book.items())]


# Markets where BettingPros files ONE offer per GAME with one selection per PLAYER (no over/under side; the selection
# `label` is the player and `participant` his id). bpp.build_prop_rows reads participants[0] and takes the selection label as
# the SIDE, so for these markets it yields the first player's name as a "side" and every quote is lost. NFL anytime TD is
# the one such market among the configured stats (verified 2026-10-17: 13 offers, 4,962 junk rows, zero quotes).
PER_GAME_PLAYER_MARKETS = {
    "nfl": frozenset({"anytime_td"}),
    "nba": frozenset({"td", "triple_double", "dd", "double_double"}),
}
_NOT_A_PLAYER_POSITIONS = {"DST", "DEF", "TEAM"}


def build_per_game_player_rows(sport: str, market: str, market_id: int, ds: str, offers: list, ev_map: dict) -> list:
    """Long rows (the bpp.build_prop_rows shape) for a per-game, per-player market. Every selection is an 'over' (the
    one-sided Yes price) at the canonical anytime point 0.5: BettingPros stamps line 1 ('1+'), and ev.daily_projections.p_over
    reads a line >= 1 as 'P(2 or more)', so the line is normalised exactly as ParlayAPI's Yes ladder is. Team-defence
    participants (position DST) are skipped."""
    rows: list = []
    for o in offers:
        parts = {str(p.get("id")): p for p in (o.get("participants") or [])}
        ev = ev_map.get(o.get("event_id"), {})
        for sel in o.get("selections") or []:
            p = parts.get(str(sel.get("participant")))
            if not p:
                continue
            pl = p.get("player") or {}
            if str(pl.get("position") or "").upper() in _NOT_A_PLAYER_POSITIONS:
                continue
            name = p.get("name") or sel.get("label") or ""
            if not name:
                continue
            base = {"date": ds, "sport": sport, "market": market, "market_id": market_id, "event_id": o.get("event_id"),
                    "scheduled": ev.get("scheduled", ""), "season_type": ev.get("season_type", ""),
                    "event_status": ev.get("status", ""), "away": ev.get("away", ""), "home": ev.get("home", ""),
                    "player_id": p.get("id"), "player": name, "team": pl.get("team") or "",
                    "position": pl.get("position") or "", "side": "over"}
            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("cost") is None or ln.get("line") is None or ln.get("is_off"):
                        continue
                    rows.append({**base, "line": 0.5, "book_id": bid, "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                                 "book_kind": bpp.book_kind(bid), "odds": ln["cost"], "is_main": ln.get("main"),
                                 "is_best": ln.get("best"), "is_open": False, "updated": ln.get("updated")})
    return rows


def build_events(sport: str, ev_map: dict, rows_by_event: dict, now: datetime, canon=None) -> tuple[list, dict]:
    """Event map + long rows (grouped by event id) -> (ParlayAPI-shaped events, counts). Events with no usable
    quote are omitted from the list (counted in `events_no_quotes`) so an unpriced game never looks like a feed."""
    if canon is None:
        from ev.daily_projections import canon_team as canon
    out, counts = [], {"events_seen": len(ev_map), "events_started": 0, "events_unknown_team": 0, "events_no_quotes": 0,
                       "events_priced": 0, "quotes": 0, "books": set()}
    for eid, e in ev_map.items():
        start = parse_scheduled(e.get("scheduled"))
        if start is None:
            counts["events_unknown_team"] += 1
            continue
        if start <= now:
            counts["events_started"] += 1
            continue
        away, home = canon(sport, bp_team(sport, e.get("away"))), canon(sport, bp_team(sport, e.get("home")))
        if not away or not home:
            counts["events_unknown_team"] += 1
            log.warning("bettingpros %s event %s: unknown team(s) %r @ %r", sport, eid, e.get("away"), e.get("home"))
            continue
        rows = rows_by_event.get(eid) or []
        quotes = bl.rows_to_quotes(rows, sport)
        if not quotes:
            counts["events_no_quotes"] += 1
            continue
        teams = {}
        for r in rows:
            if r.get("team") and r.get("player"):
                teams.setdefault(norm_name(r["player"]), bp_team(sport, r["team"]))
        books = quotes_to_bookmakers(quotes, sport, teams)
        counts["events_priced"] += 1
        counts["quotes"] += len(quotes)
        counts["books"].update(b["key"] for b in books)
        out.append({"id": f"bp{eid}", "bp_event_id": eid, "sport_key": sport, "source": "bettingpros",
                    "commence_time": _iso_z(start), "away_team": away, "home_team": home, "bookmakers": books})
    counts["books"] = sorted(counts["books"])
    out.sort(key=lambda x: (x["commence_time"], x["away_team"]))
    return out, counts


def crawl_sport(sport: str, now: datetime | None = None, days: int | None = None, delay: float = 0.8,
                market_sleep: float = 1.0, day_sleep: float = 0.4, markets: list | None = None, session=None) -> dict:
    """Crawl one sport's window. Network only through bpp (events / markets / offers). A market that errors is
    recorded in `errors` and skipped; a failure to list EVENTS raises (the caller must not publish an empty board).

    -> {sport, fetched_at, window, events, counts, per_market, errors, seconds, requests, newest_quote_update}"""
    sp = sport.lower()
    if sp not in bl.LABEL_TO_STAT:
        raise ValueError(f"unsupported sport {sport!r}; expected one of {sorted(bl.LABEL_TO_STAT)}")
    api = sp.upper()
    now = now or datetime.now(timezone.utc)
    t0 = time.monotonic()
    sess = _CountingSession(session or bpp.make_session(api))
    dates = window_dates(now, DEFAULT_DAYS[sp] if days is None else days)
    ev_map: dict = {}
    for i, ds in enumerate(dates):
        ev_map.update(bpp.fetch_events_detail(sess, api, ds))        # raises on an API failure: nothing is published
        if i + 1 < len(dates):
            _sleep(day_sleep)
    ev_map = bl._upcoming(ev_map, now)
    resolved = bpp.resolve_markets(sess, api, markets) if ev_map else {}
    rows_by_event: dict = {}
    per_market: dict = {}
    errors: list = []
    newest = ""
    for label, m in resolved.items():
        if label not in bl.LABEL_TO_STAT[sp]:
            continue
        r0 = sess.requests
        try:
            offers = bpp.fetch_offers_paged(sess, api, m["market_id"], list(ev_map), delay=delay)
        except Exception as exc:                                                        # noqa: BLE001
            errors.append({"market": label, "error": str(exc)[:200]})
            log.warning("bettingpros %s %s offers failed: %r", sp, label, str(exc)[:160])
            per_market[label] = {"offers": 0, "rows": 0, "requests": sess.requests - r0, "error": True}
            continue
        builder = (build_per_game_player_rows if label in PER_GAME_PLAYER_MARKETS.get(sp, ())
                   else bpp.build_prop_rows)
        rows = builder(api, label, m["market_id"], dates[0], offers, ev_map)
        for r in rows:
            rows_by_event.setdefault(r["event_id"], []).append(r)
            if r.get("updated") and not r.get("is_open") and str(r["updated"]) > newest:
                newest = str(r["updated"])
        per_market[label] = {"offers": len(offers), "rows": len(rows), "requests": sess.requests - r0}
        _sleep(market_sleep)
    events, counts = build_events(sp, ev_map, rows_by_event, now)
    counts["events_upcoming"] = len(ev_map)
    return {"sport": sp, "fetched_at": datetime.now(timezone.utc).isoformat(), "window": {"dates": dates},
            "events": events, "counts": counts, "per_market": per_market, "errors": errors,
            "markets_requested": len(resolved), "seconds": round(time.monotonic() - t0, 1),
            "requests": sess.requests, "newest_quote_update": newest or None}


def publishable(result: dict) -> tuple[bool, str]:
    """May this crawl replace latest.json? Yes when it priced at least one game, or when it truthfully saw no upcoming
    games (offseason / off day). No when games exist but nothing priced and every market failed -- that is an outage,
    and overwriting the last good file with it would blank the board."""
    c = result["counts"]
    if c.get("events_priced", 0) > 0:
        return True, "priced"
    if c.get("events_upcoming", 0) == 0:
        return True, "no upcoming games"
    if result["errors"] and len(result["errors"]) >= result.get("markets_requested", 0):
        return False, "every market failed"
    return True, "games listed but no prices posted yet"


def write_latest(result: dict, key: str | None = None) -> str:
    from mlb_core import storage
    key = key or OUT_KEY.format(sport=result["sport"])
    storage.write_bytes(json.dumps(result, default=str).encode(), key)
    return key


def read_latest(sport: str, now: datetime, max_age_min: float):
    """-> (events | None, meta). meta = {age_min, fetched_at, events, quotes, books, reason?}. A file older than
    max_age_min is refused (reason set); a missing/unreadable one is a reason, never an exception."""
    from mlb_core import storage
    key = OUT_KEY.format(sport=sport)
    try:
        obj = json.loads(storage.read_bytes(key))
    except Exception as exc:                                                            # noqa: BLE001
        return None, {"reason": f"no BettingPros live file yet ({type(exc).__name__})"}
    try:
        ts = datetime.fromisoformat(str(obj["fetched_at"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None, {"reason": "BettingPros live file has no fetched_at"}
    age = (now - ts).total_seconds() / 60.0
    c = obj.get("counts") or {}
    meta = {"age_min": round(age, 1), "fetched_at": ts.isoformat(), "events": len(obj.get("events") or []),
            "quotes": c.get("quotes"), "books": c.get("books")}
    if age > max_age_min:
        return None, {**meta, "reason": f"BettingPros live file is {age:.0f} min old (> {max_age_min:.0f})"}
    events = obj.get("events") or []
    if not events:
        return None, {**meta, "reason": "BettingPros live file has no priced games"}
    return events, meta
