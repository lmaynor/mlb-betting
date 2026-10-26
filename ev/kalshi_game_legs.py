"""
ev.kalshi_game_legs -- game-winner (moneyline) fair values for the Kalshi RFQ fair book.

The RFQ flow is dominated by combos of GAME WINNERS (KXNFLGAME / KXNBAGAME / KXNHLGAME ...) and NBA props, while the
props feed only covers NFL player props, so nearly every leg was unpriceable. This module turns one ParlayAPI slate pull
(h2h, ONE credit per sport for the whole slate) into feed rows for `ev.kalshi_rfq_pricer.leg_from_row`:

    Kalshi ticker  KX<LG>GAME-<YY><MON><DD><AWAY><HOME>-<CODE>      yes == <CODE> wins the game
    p_yes          median over books of each book's own 2-way de-vigged P(team wins); Kalshi and DFS books excluded
                   (leave-Kalshi-out), Kambi clones folded into their original.

Tickers are BUILT from the book event and round-tripped through ev.kalshi_map.parse_event, so the grammar is the strict
mapper's, and a team name that does not resolve to a code in the league's complete Kalshi code table drops the game (fail
closed -- a wrong code is a wrong price). Not covered (stay unpriced): NCAAF/NCAAWB/MLS game winners, spreads, totals.
NFL ties settle $0.50 per team on Kalshi; a two-way de-vig ignores them (a ~0.5pp effect, inside the safety pad).
"""

from __future__ import annotations

import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

from ev.kalshi_map import game_key, parse_event
from ev.props import DFS_BOOKS
from ev.sports import ADAPTERS
from mlb.execution.kalshi.mapping import MapFail
from mlb_core.alerts.discord_routes import BOOK_ALIASES, canonical_book
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

_ET = ZoneInfo("America/New_York")
PARLAY_SPORT_KEYS = {"nfl": "americanfootball_nfl", "nba": "basketball_nba", "nhl": "icehockey_nhl"}
_MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
EXCLUDED_BOOKS = DFS_BOOKS | {"kalshi"}


def team_code_fn(sport: str):
    """full team name -> that league's Kalshi code, or None. Resolution uses each pillar's own table; the caller still
    checks the result against the adapter's complete code set."""
    if sport == "nfl":
        from ev.sports.nfl import TEAM_NAMES
        return lambda name: TEAM_NAMES.get(name)
    if sport == "nhl":
        from nhl.config import team_abbrev
        return team_abbrev
    from nba.teams import team_code
    return team_code


def _mad(x) -> float:
    m = statistics.median(x)
    return float(statistics.median(abs(v - m) for v in x))


def _p_home(book: dict, home: str, away: str):
    """One book's own de-vigged P(home wins) from its h2h market, or None (no h2h / 3-way / unmatched names)."""
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "h2h":
            continue
        px = {}
        for o in mk.get("outcomes", []) or []:
            if o.get("name") in (home, away) and o.get("price") is not None:
                px.setdefault(o["name"], int(o["price"]))
        if len(px) != 2 or len(mk.get("outcomes", []) or []) != 2:        # a Draw outcome => 3-way: not our proposition
            return None
        fair_home, _ = devig_two_way(american_to_implied_prob(px[home]), american_to_implied_prob(px[away]), "proportional")
        return float(fair_home)
    return None


def slate_rows(slate: list, sport: str, now: float, team_code=None) -> tuple[list[dict], dict]:
    """ParlayAPI slate events (h2h) -> ([feed row per Kalshi team market], stats). `now` stamps updated_ts."""
    adapter = ADAPTERS[sport]
    team_code = team_code or team_code_fn(sport)
    rows: list[dict] = []
    stats = {"events": 0, "games_priced": 0, "dropped_unknown_team": 0, "dropped_thin": 0}
    for ev in slate or []:
        stats["events"] += 1
        home_n, away_n = ev.get("home_team"), ev.get("away_team")
        home, away = team_code(home_n), team_code(away_n)
        if home not in adapter.codes or away not in adapter.codes or home == away or not ev.get("commence_time"):
            stats["dropped_unknown_team"] += 1
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(_ET)
        event_ticker = f"{adapter.series('ml')}-{start:%y}{_MON[start.month - 1]}{start:%d}{away}{home}"
        if parse_event(adapter, event_ticker) != (start.date().isoformat(), away, home):
            stats["dropped_unknown_team"] += 1                                    # round-trip failed: do not guess
            continue
        books = ev.get("bookmakers", []) or []
        present = {str(b.get("key", "")).strip().lower() for b in books}
        per_book: dict[str, float] = {}
        for b in books:
            raw = str(b.get("key", "")).strip().lower()
            if raw in BOOK_ALIASES and BOOK_ALIASES[raw] in present:
                continue                                                          # Kambi clone: count the original once
            name = canonical_book(b.get("key", "")) if raw in BOOK_ALIASES else raw
            if name in EXCLUDED_BOOKS or raw in EXCLUDED_BOOKS:
                continue
            p = _p_home(b, home_n, away_n)
            if p is not None and 0.0 < p < 1.0:
                per_book[name] = p
        if not per_book:
            stats["dropped_thin"] += 1
            continue
        vals = list(per_book.values())
        p_home = float(statistics.median(vals))
        gk = game_key(sport, start.date().isoformat(), away, home)
        for code, p in ((home, p_home), (away, 1.0 - p_home)):
            rows.append({"ticker": f"{event_ticker}-{code}", "sport": sport, "game_key": gk, "market_class": "game_ml",
                         "player_norm": None, "stat": "ml", "strike": 0.0, "p_yes": p, "n_books": len(vals),
                         "disp": _mad(vals), "updated_ts": now})
        stats["games_priced"] += 1
    return rows, stats


def fetch_game_rows(client, sports, now: float) -> tuple[list[dict], dict]:
    """One slate pull per sport (1 credit each). A sport that fails is skipped and reported, never fatal."""
    rows: list[dict] = []
    stats: dict = {}
    for sport in sports:
        try:
            slate = client.get_slate(PARLAY_SPORT_KEYS[sport], markets=["h2h"])
            r, s = slate_rows(slate, sport, now)
            rows += r
            stats[sport] = s
        except Exception as e:                                                  # noqa: BLE001
            stats[sport] = {"error": repr(e)[:200]}
    return rows, stats
