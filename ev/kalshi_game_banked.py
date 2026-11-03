"""
ev.kalshi_game_banked -- game-line fair values for the Kalshi RFQ fair book from ALREADY-BANKED feeds (zero ParlayAPI credits).

ev.kalshi_game_legs.fetch_game_rows pulls a live ParlayAPI slate (1 credit per market per sport per pull, capped at
--max-daily-game-credits, so the pull cadence stretches to ~1.8 h and every row is older than the pricer's staleness gate most of
the time). It needs PARLAY_API_KEY, and with no key every game / spread / total leg is unmapped. Two free feeds already hold the same
lines:

  1. BestBook live ladders   Odds/bestbook_live/{sport}/game_lines_latest.json (ev.bestbook_game_lines; hourly crawl; NFL, NBA, NHL).
                             ~16 venues and ~90 spread / ~90 total rungs a game, so Kalshi's "wins by over N.5" ladder is quoted
                             directly instead of being interpolated from one or two lines.
  2. Banked ParlayAPI        OddsAccum/{sport}/raw/{day}/game_lines_{HHMM}.json (nba.game.odds_feed; NBA / NHL only, ~6 runs a day).
                             Used only when BestBook has nothing fresh for the sport.

Both are converted to the ParlayAPI slate shape and priced by ev.kalshi_game_legs.slate_rows, so the leave-Kalshi-out consensus, DFS
exclusion, Kambi folding, half-point filter, NHL-totals-disabled rule and rung interpolation are the SAME code as the live path.

updated_ts is the feed's own timestamp (BestBook `fetched_at`, the snapshot's `timestamp`), never the read time: the leg gate's
staleness check must see the real age. Games that have started are dropped (a live line is not priced by a pre-game consensus).
Nothing here is validated against settled outcomes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ev.kalshi_game_legs import slate_rows
from ev.props import DFS_BOOKS
from ev.sports import ADAPTERS, kalshi_code
from mlb_core.alerts.discord_routes import canonical_book

log = logging.getLogger("ev.kalshi_game_banked")

BANKED_SPORTS = ("nfl", "nba", "nhl")
BESTBOOK_MAX_AGE_MIN = 180.0               # same horizon as ev.game_odds_board: the crawl is hourly, three misses = not live
PARLAY_BANKED_KEY = {"nba": "basketball_nba", "nhl": "icehockey_nhl"}     # NFL game lines are not banked from ParlayAPI


def _price(v):
    """American price as int, or None (missing / non-numeric / |p| < 100)."""
    try:
        p = int(float(v))
    except (TypeError, ValueError):
        return None
    return p if abs(p) >= 100 else None


def _dt(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def events_from_bestbook(sport: str, games: list, now_dt: datetime) -> tuple[list[dict], dict]:
    """BestBook game-line games -> (ParlayAPI-slate-shaped events, stats). Team names in the events ARE Kalshi codes (pass an identity
    team_code to slate_rows). A quote needs both sides priced to become an h2h / spreads / totals market (a one-sided quote cannot be
    de-vigged, and slate_rows' helpers require the pair anyway). Spread `line` is the HOME handicap; a = AWAY / OVER, b = HOME / UNDER."""
    stats = {"games": 0, "started": 0, "unmapped": 0, "quotes": 0}
    events: list[dict] = []
    for g in games or []:
        if not isinstance(g, dict):
            continue
        away = kalshi_code(sport, str(g.get("away_team") or ""))
        home = kalshi_code(sport, str(g.get("home_team") or ""))
        start = _dt(g.get("commence_time"))
        if not away or not home or away == home or start is None:
            stats["unmapped"] += 1
            continue
        if start <= now_dt:
            stats["started"] += 1
            continue
        books: dict[str, dict] = {}
        for q in g.get("quotes") or []:
            if not isinstance(q, dict):
                continue
            book = canonical_book(str(q.get("book") or "")).strip().lower()
            a, b = _price(q.get("a")), _price(q.get("b"))
            market = q.get("market")
            if not book or book in DFS_BOOKS or a is None or b is None:
                continue
            bm = books.setdefault(book, {"key": book, "markets": []})
            mk = {m["key"]: m for m in bm["markets"]}
            if market == "ml":
                bm["markets"].append({"key": "h2h", "outcomes": [{"name": home, "price": b}, {"name": away, "price": a}]})
            elif market in ("spread", "total"):
                try:
                    line = float(q.get("line"))
                except (TypeError, ValueError):
                    continue
                key = "spreads" if market == "spread" else "totals"
                m = mk.get(key)
                if m is None:
                    m = {"key": key, "outcomes": []}
                    bm["markets"].append(m)
                if market == "spread":
                    m["outcomes"] += [{"name": home, "point": line, "price": b}, {"name": away, "point": -line, "price": a}]
                else:
                    m["outcomes"] += [{"name": "Over", "point": line, "price": a}, {"name": "Under", "point": line, "price": b}]
            else:
                continue
            stats["quotes"] += 1
        if books:
            stats["games"] += 1
            events.append({"home_team": home, "away_team": away, "commence_time": start.isoformat(), "bookmakers": list(books.values())})
    return events, stats


def banked_game_rows(sports, now: float) -> tuple[list[dict], dict]:
    """Feed rows (ev.kalshi_rfq_pricer.leg_from_row shape) for each sport from the free banked feeds -> (rows, stats).

    Per sport: BestBook ladders if fresh, else (NBA / NHL) the banked ParlayAPI snapshot, else nothing (stats[sport]['unavailable']
    says why, and the caller may fall back to a live pull). A sport that raises is skipped and reported, never fatal."""
    from ev import bestbook_events
    now_dt = datetime.fromtimestamp(now, timezone.utc)
    rows: list[dict] = []
    stats: dict = {}
    for sport in sports:
        if sport not in ADAPTERS:
            stats[sport] = {"unavailable": f"unknown sport {sport!r}"}
            continue
        try:
            games, meta = bestbook_events.read_latest_game_lines(sport, now_dt, BESTBOOK_MAX_AGE_MIN)
            reasons = [meta.get("reason") or "no BestBook game lines"]
            if games:
                events, ev_stats = events_from_bestbook(sport, games, now_dt)
                stamp = _dt(meta.get("fetched_at"))
                r, s = slate_rows(events, sport, stamp.timestamp() if stamp else now,
                                  team_code=lambda c, _s=sport: c if c in ADAPTERS[_s].codes else None)
                if r:
                    rows += r
                    stats[sport] = dict(s, source="bestbook", age_min=meta.get("age_min"), **ev_stats)
                    continue
                reasons = ["BestBook file held no priceable upcoming game"]
            key = PARLAY_BANKED_KEY.get(sport)
            if key:
                from nba.game import odds_feed                   # lazy: nba.game pulls scipy, which the BestBook path never needs
                pa_events, pa_meta = odds_feed.read_banked_game_lines(key, now_dt)
                if pa_events:
                    stamp = _dt(pa_meta.get("snapshot_ts"))
                    r, s = slate_rows(pa_events, sport, stamp.timestamp() if stamp else now)
                    if r:
                        rows += r
                        stats[sport] = dict(s, source="oddsaccum", age_min=pa_meta.get("age_min"))
                        continue
                reasons.append(pa_meta.get("reason") or "banked ParlayAPI game lines held no priceable game")
            stats[sport] = {"unavailable": " | ".join(str(x) for x in reasons)[:300]}
        except Exception as e:                                                  # noqa: BLE001
            log.exception("banked game lines for %s failed", sport)
            stats[sport] = {"error": repr(e)[:200]}
    return rows, stats
