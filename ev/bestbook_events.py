"""
ev.bestbook_events -- LIVE BestBook (bestbook.app) props as ParlayAPI-shaped event dicts.

Converts BestBook's live multi-book aggregated prop boards into ParlayAPI EVENT payloads
for NFL, NHL, and NBA, serving as a high-frequency backup filler feed alongside BettingPros
and banked ParlayAPI lines in ev.daily_projections.load_events.

Event shape emitted:
    {id, bb_game_key, sport_key, commence_time, away_team, home_team, source: 'bestbook',
     bookmakers: [{key, title, markets: [{key: 'player_points', outcomes: [{name: 'Over', ...}]}]}]}

Storage: Odds/bestbook_live/{sport}/latest.json = {sport, fetched_at, window, events, counts}.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev.bettingpros_events import PARLAY_KEY
from ev import bestbook_game_lines as bgl
from mlb_core.odds import bestbook as bb

log = logging.getLogger("ev.bestbook_events")

_ET = ZoneInfo("America/New_York")
SPORTS = ("nfl", "nhl", "nba")
OUT_KEY = "Odds/bestbook_live/{sport}/latest.json"
GAME_LINES_KEY = "Odds/bestbook_live/{sport}/game_lines_latest.json"   # full-game ML / spread / total ladders (ev.bestbook_game_lines)

DEFAULT_DAYS = {"nfl": 7, "nhl": 3, "nba": 7}    # ET calendar days INCLUDING today (today .. today + days - 1)
MAX_CONSECUTIVE_FETCH_FAILURES = 3

BESTBOOK_STAT_MAP: dict[str, dict[str, str]] = {
    "nba": {
        "nbapts": "points",
        "nbareb": "rebounds",
        "nbaast": "assists",
        "nba3pm": "threes",
        "nbapra": "pra",
        "nbapr": "pr",
        "nbapa": "pa",
        "nbara": "ra",
        "nbablk": "blocks",
        "nbastl": "steals",
        "nbato": "turnovers",
        "nbadd": "double_double",
        "nbatd": "triple_double",
    },
    "nhl": {
        "goals": "goals",
        "points": "points",
        "assists": "assists",
        "sog": "sog",
        "shots": "sog",
        "saves": "saves",
        "blocks": "blocked_shots",
        "blocked_shots": "blocked_shots",
    },
    "nfl": {
        "passing": "pass_yds",
        "rushyds": "rush_yds",
        "recyds": "rec_yds",
        "rec": "receptions",
        "rushatt": "rush_attempts",
        "td": "anytime_td",
        "anytime_td": "anytime_td",
        "passtd": "pass_tds",
        "pass_tds": "pass_tds",
        "passatt": "pass_attempts",
        "pass_attempts": "pass_attempts",
        "passcomp": "pass_completions",
        "pass_completions": "pass_completions",
        "int": "interceptions",
        "interceptions": "interceptions",
    },
}

_FAMILY_TOKEN_MAP: dict[str, str] = {
    "pts": "points",
    "reb": "rebounds",
    "ast": "assists",
    "3pm": "threes",
    "threes": "threes",
    "pra": "pra",
    "pr": "pr",
    "pa": "pa",
    "ra": "ra",
    "blk": "blocks",
    "stl": "steals",
    "to": "turnovers",
    "dd": "double_double",
    "td": "anytime_td",
    "goals": "goals",
    "sog": "sog",
    "shots": "sog",
    "saves": "saves",
    "passyds": "pass_yds",
    "rushyds": "rush_yds",
    "recyds": "rec_yds",
    "rushatt": "rush_attempts",
    "passtd": "pass_tds",
    "passatt": "pass_attempts",
    "passcomp": "pass_completions",
    "interceptions": "interceptions",
}

_PERIOD_RE = re.compile(r"[:@](?:1q|2q|3q|4q|1h|2h|1p|2p|3p|eh|eq|m3|race)", re.IGNORECASE)


def stat_from_row(sport: str, row: dict) -> str | None:
    """Determine the canonical stat from BestBook row's section and family.
    Returns None if it is a period/combo/non-prop market."""
    if not row.get("player"):
        return None
    sec = str(row.get("section") or "").lower()
    fam = str(row.get("family") or "").lower()

    # Reject non-full-game periods and special combos
    if _PERIOD_RE.search(sec) or _PERIOD_RE.search(fam):
        return None
    if any(k in sec or k in fam for k in ("pair", "h2h", "comb", "oddeven", "cscore", "bands", "lead")):
        return None

    # Check direct section mapping
    sec_map = BESTBOOK_STAT_MAP.get(sport, {})
    if sec in sec_map:
        stat = sec_map[sec]
        # In NFL, section 'td' with 'anytime'
        return stat

    # Inspect family segments: e.g. "nba:2026-10-30-CHA-WAS|pts|kyshawn george"
    parts = fam.split("|")
    if len(parts) >= 2:
        token = parts[1].strip()
        if token in _FAMILY_TOKEN_MAP:
            stat = _FAMILY_TOKEN_MAP[token]
            if stat in PARLAY_KEY.get(sport, {}):
                return stat

    return None


def point_from_row(line, rung, stat: str) -> float | None:
    """Convert BestBook (line, rung) into a half-point or exact ParlayAPI point."""
    if stat in ("anytime_td", "double_double", "triple_double"):
        return 0.5

    try:
        ln = float(line)
    except (TypeError, ValueError):
        return None

    rung_str = str(rung or "").strip()
    if rung_str.endswith("+") and ln.is_integer() and ln >= 1:
        return ln - 0.5
    return ln


def parse_american(val) -> int | None:
    """Parse integer American odds from native string or numeric value."""
    if val is None:
        return None
    s = str(val).replace("\u2212", "-").strip()
    if s.endswith("\u00a2") or s.endswith("c"):  # e.g. Novig '49c'
        try:
            prob = float(s[:-1].strip()) / 100.0
            return bb.prob_to_american(prob)
        except ValueError:
            return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _two_sided_stats(row: dict) -> tuple[int, float]:
    """(number of two-sided non-DFS book quotes on a rung row, mean |P(yes) - 0.5| over them). A row with no two-sided quote has
    distance 1.0 (never preferred on the tie-break). Quote yes/no are BestBook implied-probability PERCENTS."""
    n, dist = 0, 0.0
    for q in row.get("quotes") or []:
        if q.get("yes") is None or q.get("no") is None or bb.venue_info(q.get("venue"))[1] == "dfs":
            continue
        n += 1
        py = bb.pct_to_prob(q.get("yes"))
        dist += abs(py - 0.5) if py is not None else 0.5
    return n, (dist / n if n else 1.0)


def build_events_from_snapshots(
    sport: str,
    slate_games: list[dict],
    snapshots,
    now: datetime,
    canon=None,
    game_lines: list | None = None,
) -> tuple[list[dict], dict]:
    """Pure transform: converts slate games and snapshots into ParlayAPI-shaped event dicts.
    Returns (events, counts). When `game_lines` is a list, each game's full-game ML / spread / total ladders (ev.bestbook_game_lines)
    are appended to it as {bb_key, away_team, home_team, commence_time, quotes}, read from the SAME snapshot (no extra request).

    `snapshots` is a {game_key: snapshot} dict OR a callable game_key -> snapshot. The crawler passes a callable so each game's
    multi-MB snapshot is fetched only for games that pass the started / unknown-team filters, turned into its event, and
    dropped before the next one is fetched (holding every game at once was ~100 MB for a full NFL slate)."""
    if canon is None:
        from ev.daily_projections import canon_team as canon

    keys = PARLAY_KEY.get(sport, {})
    events: list[dict] = []
    counts = {
        "events_seen": len(slate_games),
        "events_started": 0,
        "events_unknown_team": 0,
        "events_no_quotes": 0,
        "events_priced": 0,
        "game_lines_games": 0,
        "game_lines_quotes": 0,
        "quotes": 0,
        "books": set(),
    }

    rows = player_stat_rows = None
    for g in slate_games:
        rows = player_stat_rows = None            # release the previous game's snapshot BEFORE fetching the next
        game_key = g.get("key")
        if not game_key:
            continue

        # Parse start time
        start_str = g.get("start")
        start = None
        if start_str:
            try:
                start = datetime.fromisoformat(str(start_str).replace("Z", "+00:00"))
            except ValueError:
                start = None

        if start and start <= now:
            counts["events_started"] += 1
            continue

        away = canon(sport, g.get("away"))
        home = canon(sport, g.get("home"))
        if not away or not home:
            counts["events_unknown_team"] += 1
            log.warning("bestbook %s event %s: unknown team(s) %r @ %r", sport, game_key, g.get("away"), g.get("home"))
            continue

        snap = (snapshots(game_key) if callable(snapshots) else snapshots.get(game_key)) or {}
        rows = snap.get("rows") or []
        snap = None                       # the raw snapshot is as big as `rows`: keep only the row list
        if not rows:
            counts["events_no_quotes"] += 1
            continue

        if game_lines is not None and start is not None:
            gl_quotes = bgl.prune_single_book(bgl.game_line_quotes(rows))
            if gl_quotes:
                game_lines.append({"bb_key": game_key, "away_team": away, "home_team": home,
                                   "commence_time": start.astimezone(timezone.utc).isoformat(), "quotes": gl_quotes})
                counts["game_lines_games"] += 1
                counts["game_lines_quotes"] += len(gl_quotes)

        # Group rows by (player_norm, stat) to detect main line vs alternates
        player_stat_rows: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            stat = stat_from_row(sport, r)
            if not stat or stat not in keys:
                continue
            player = str(r.get("player") or "").strip()
            if not player:
                continue
            player_stat_rows.setdefault((player.lower(), stat), []).append(r)

        # Main line per (player, stat): the line with the MOST two-sided books; ties go to the line whose consensus is closest to
        # even odds (what a book posts as its main line), then to the lower number, so the pick never depends on row order.
        main_lines: dict[tuple[str, str], float] = {}
        for (pl_norm, st), r_list in player_stat_rows.items():
            best_key = None
            best_line = None
            for r in r_list:
                pt = point_from_row(r.get("line"), r.get("rung"), st)
                if pt is None:
                    continue
                two_sided, dist = _two_sided_stats(r)
                key = (-two_sided, dist, pt)
                if best_key is None or key < best_key:
                    best_key, best_line = key, pt
            if best_line is not None:
                main_lines[(pl_norm, st)] = best_line

        # Build bookmaker outcomes
        by_book: dict[str, dict[str, list[dict]]] = {}
        total_quotes = 0

        for r in rows:
            stat = stat_from_row(sport, r)
            if not stat or stat not in keys:
                continue
            player = str(r.get("player") or "").strip()
            if not player:
                continue
            pt = point_from_row(r.get("line"), r.get("rung"), stat)
            if pt is None:
                continue

            pl_norm = player.lower()
            is_alt = (pl_norm, stat) in main_lines and pt != main_lines[(pl_norm, stat)]
            mkt_key = keys[stat]
            if is_alt:
                mkt_key += "_alternate"

            team = r.get("team")
            team_code = canon(sport, team) if team else None
            desc = f"{player} ({team_code})" if team_code else player

            quotes = r.get("quotes")
            if quotes is None and r.get("venue") not in (None, "match"):
                quotes = [r]

            for q in quotes or []:
                code = q.get("venue") or ""
                canonical_book, kind = bb.venue_info(code)
                if kind == "dfs" or canonical_book == "kalshi":
                    continue

                over_p = parse_american(q.get("yesNative"))
                if over_p is None:
                    over_p = bb.pct_to_american(q.get("yes"))

                under_p = parse_american(q.get("noNative"))
                if under_p is None:
                    under_p = bb.pct_to_american(q.get("no"))

                if over_p is None and under_p is None:
                    continue

                outs = by_book.setdefault(canonical_book, {}).setdefault(mkt_key, [])
                if over_p is not None:
                    outs.append({"name": "Over", "description": desc, "price": int(over_p), "point": float(pt)})
                    total_quotes += 1
                if under_p is not None:
                    outs.append({"name": "Under", "description": desc, "price": int(under_p), "point": float(pt)})
                    total_quotes += 1

        if not by_book:
            counts["events_no_quotes"] += 1
            continue

        bookmakers = [
            {"key": b, "title": b, "markets": [{"key": mk, "outcomes": outs} for mk, outs in sorted(mkts.items())]}
            for b, mkts in sorted(by_book.items())
        ]

        counts["events_priced"] += 1
        counts["quotes"] += total_quotes
        counts["books"].update(by_book.keys())

        commence_time = start.strftime("%Y-%m-%dT%H:%M:%SZ") if start else (start_str or "")
        events.append({
            "id": f"bb_{game_key}",
            "bb_game_key": game_key,
            "sport_key": sport,
            "source": "bestbook",
            "commence_time": commence_time,
            "away_team": away,
            "home_team": home,
            "bookmakers": bookmakers,
        })

    counts["books"] = sorted(counts["books"])
    events.sort(key=lambda x: (x["commence_time"], x["away_team"]))
    return events, counts


def crawl_sport(
    sport: str,
    now: datetime | None = None,
    days: int | None = None,
) -> dict:
    """Crawl live BestBook props for one sport and format as ParlayAPI event payloads."""
    sp = sport.lower()
    if sp not in SPORTS:
        raise ValueError(f"unsupported sport {sport!r}; expected one of {SPORTS}")

    now = now or datetime.now(timezone.utc)
    t0 = time.monotonic()

    slate = bb.fetch_slate()
    sport_games = [g for g in slate if g.get("sport") == sp]

    # Target window = exactly `days` ET calendar days starting today (today .. today + days - 1).
    max_days = DEFAULT_DAYS.get(sp, 7) if days is None else days
    d0 = now.astimezone(_ET).date()
    end_date = d0 + timedelta(days=max_days)          # exclusive

    target_games = []
    for g in sport_games:
        st_str = g.get("start")
        if st_str:
            try:
                st = datetime.fromisoformat(str(st_str).replace("Z", "+00:00"))
                if st.astimezone(_ET).date() < end_date:
                    target_games.append(g)
            except ValueError:
                target_games.append(g)
        else:
            target_games.append(g)

    state = {"n": 0, "consecutive_fail": 0, "failed": 0}

    def fetch_one(game_key: str) -> dict:
        if state["n"]:
            # Randomized pause between per-game requests: spreads load instead of hitting the endpoint at a fixed rhythm
            time.sleep(random.uniform(0.6, 1.8))
        state["n"] += 1
        try:
            snap = bb.fetch_game_snapshot(game_key)
        except bb.BestBookError as e:
            # the client already retried with backoff; one dead game must not sink the slate, a dead endpoint must stop the crawl
            state["failed"] += 1
            state["consecutive_fail"] += 1
            log.warning("bestbook %s: snapshot for %s failed (%s)", sp, game_key, e)
            if state["consecutive_fail"] >= MAX_CONSECUTIVE_FETCH_FAILURES:
                raise
            return {}
        state["consecutive_fail"] = 0
        return snap

    game_lines: list[dict] = []
    events, counts = build_events_from_snapshots(sp, target_games, fetch_one, now, game_lines=game_lines)
    counts["events_upcoming"] = len(sport_games)
    counts["events_fetch_failed"] = state["failed"]

    return {
        "sport": sp,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window": {"days": max_days},
        "events": events,
        "game_lines": game_lines,
        "counts": counts,
        "seconds": round(time.monotonic() - t0, 1),
    }


def publishable(result: dict) -> tuple[bool, str]:
    """Check if the crawl produced valid data worthy of replacing latest.json."""
    c = result.get("counts", {})
    if c.get("events_priced", 0) > 0 or c.get("game_lines_games", 0) > 0:
        return True, "priced"
    if c.get("events_upcoming", 0) == 0:
        return True, "no upcoming games"
    return False, "games listed but no prices found"


def write_latest(result: dict, key: str | None = None) -> str:
    from mlb_core import storage

    default_path = key is None
    key = key or OUT_KEY.format(sport=result["sport"])
    props = {k: v for k, v in result.items() if k != "game_lines"}      # the props reader never parses the ladders
    storage.write_bytes(json.dumps(props, default=str).encode(), key)
    games = result.get("game_lines") or []
    if default_path and games:                                          # an empty crawl keeps the previous good file
        c = result.get("counts") or {}
        storage.write_bytes(json.dumps({
            "sport": result["sport"], "fetched_at": result["fetched_at"], "window": result.get("window"), "games": games,
            "counts": {"games": c.get("game_lines_games", len(games)), "quotes": c.get("game_lines_quotes")},
        }, default=str).encode(), GAME_LINES_KEY.format(sport=result["sport"]))
    return key


def read_latest(sport: str, now: datetime, max_age_min: float) -> tuple[list | None, dict]:
    """Read latest.json for sport, enforcing max_age_min."""
    from mlb_core import storage

    key = OUT_KEY.format(sport=sport)
    try:
        obj = json.loads(storage.read_bytes(key))
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": f"no BestBook live file yet ({type(exc).__name__})"}

    try:
        ts = datetime.fromisoformat(str(obj["fetched_at"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None, {"reason": "BestBook live file has no fetched_at"}

    age = (now - ts).total_seconds() / 60.0
    c = obj.get("counts") or {}
    meta = {
        "age_min": round(age, 1),
        "fetched_at": ts.isoformat(),
        "events": len(obj.get("events") or []),
        "quotes": c.get("quotes"),
        "books": c.get("books"),
    }
    if age > max_age_min:
        return None, {**meta, "reason": f"BestBook live file is {age:.0f} min old (> {max_age_min:.0f})"}
    events = obj.get("events") or []
    if not events:
        return None, {**meta, "reason": "BestBook live file has no priced games"}
    return events, meta


def read_latest_game_lines(sport: str, now: datetime, max_age_min: float) -> tuple[list | None, dict]:
    """Read the BestBook game-line ladders of one sport -> (games, meta); games is None (with meta['reason']) when the file is
    missing, has no fetched_at, is older than max_age_min, or holds no games."""
    from mlb_core import storage

    try:
        obj = json.loads(storage.read_bytes(GAME_LINES_KEY.format(sport=sport)))
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": f"no BestBook game-lines file yet ({type(exc).__name__})"}
    try:
        ts = datetime.fromisoformat(str(obj["fetched_at"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None, {"reason": "BestBook game-lines file has no fetched_at"}
    age = (now - ts).total_seconds() / 60.0
    games = obj.get("games") or []
    meta = {"age_min": round(age, 1), "fetched_at": ts.isoformat(), "games": len(games),
            "quotes": (obj.get("counts") or {}).get("quotes")}
    if age > max_age_min:
        return None, {**meta, "reason": f"BestBook game-lines file is {age:.0f} min old (> {max_age_min:.0f})"}
    if not games:
        return None, {**meta, "reason": "BestBook game-lines file has no games"}
    return games, meta
