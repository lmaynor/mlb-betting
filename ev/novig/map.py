"""
ev.novig.map -- Novig player-prop markets -> proposition (STRICT, fail closed). NFL first (2026-10-12);
NHL shots on goal + saves added 2026-11-12 and NBA rebounds + threes 2026-11-13 (the Novig taker wiring
Argon's market-making ruling made a precondition). One mapper, parameterized by league: the market shape
is identical across leagues.

Shape (public catalog, verified live 2026-10-12 NFL / 2026-11-12 NHL / 2026-11-13 NBA):
market.description "<Player> <strike> <TYPE>" (e.g. "Marvin Harrison Jr. 22.5 RECEIVING_YARDS",
"Patrick Kane 3.5 SHOTS_ON_GOAL", "Brook Lopez 1.5 REBOUNDS"), marketType, strike "22.5",
outcomes "Over 22.5" / "Under 22.5" (identified by NAME, never by array position -- the docs warn the
order is not fixed), fee {coefficient, charged}, voids "FMV". Event description "Away Team @ Home Team"
(full names) + startsTs (ms).
YES-equivalent: Novig "Over N-0.5" == Kalshi "N+" == a sportsbook over at N-0.5.
NHL game keys use the NHL-API abbrevs (LAK/NJD/SJS/TBL/UTA) -- the vocabulary the rest of the NHL pipeline
(nhl.config.team_abbrev, ev.sweep_props_alerts) builds its game keys in. NBA game keys use the tricodes
(nba.teams.TEAMS / ev.sports.nba CODES: GSW/SAS/LAC...) the NBA pipeline builds its keys in.
NBA also lists a parallel *_PROTECTED market for most props (same player/line/event, own marketId and
book): a different listed product whose settlement is not the plain market's, so every *_PROTECTED type
stays unmapped and fails closed, like every other non-enabled type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ev.kalshi_map import game_key
from ev.props import norm_name
from ev.sports.nfl import TEAM_NAMES
from mlb.execution.kalshi.mapping import MapFail
from nba.teams import TEAMS as _NBA_TEAMS
from nhl.config import TEAMS as _NHL_TEAMS

_ET = ZoneInfo("America/New_York")
STAT_BY_TYPE = {"RECEIVING_YARDS": "rec_yds", "RUSHING_YARDS": "rush_yds", "PASSING_YARDS": "pass_yds",
                "RECEPTIONS": "receptions", "PASSING_TOUCHDOWNS": "pass_tds"}
# NHL: only the two stats with a served NHL fair behind them (SOG first, saves second). Every other NHL
# marketType (PLAYER_GOALS / ASSISTS / POINTS / ...) stays unmapped on purpose, same fail-closed rule as NFL.
STAT_BY_TYPE_NHL = {"SHOTS_ON_GOAL": "sog", "SAVES": "saves"}
# NBA: only the two officials-allowlisted trained-signal stats (ev.model_picks.PROPS_SIGNAL_ALLOWLIST:
# threes + rebounds). POINTS is display-only on the served board and ASSISTS was removed from the signal
# allowlist 2026-11-10 (Argon props D3) and is banned as a fair input in the Novig ruling -- both stay
# unmapped, exactly like NHL goals / assists / points. Combos (POINTS_REBOUNDS, PRA, ...) and every
# *_PROTECTED twin stay unmapped too.
STAT_BY_TYPE_NBA = {"REBOUNDS": "rebounds", "THREE_POINTERS_MADE": "threes"}
STAT_TABLES = {"NFL": STAT_BY_TYPE, "NHL": STAT_BY_TYPE_NHL, "NBA": STAT_BY_TYPE_NBA}
NHL_TEAM_NAMES = {name: api for api, name, _kalshi in _NHL_TEAMS}   # full name -> NHL-API abbrev
NBA_TEAM_NAMES = {name: code for code, name, *_rest in _NBA_TEAMS}  # full name -> tricode
TEAM_TABLES = {"NFL": TEAM_NAMES, "NHL": NHL_TEAM_NAMES, "NBA": NBA_TEAM_NAMES}

STAT_BY_TYPE_BY_SPORT: dict[str, dict[str, str]] = {
    "nfl": {
        "RECEIVING_YARDS": "rec_yds",
        "RUSHING_YARDS": "rush_yds",
        "PASSING_YARDS": "pass_yds",
        "RECEPTIONS": "receptions",
        "PASSING_TOUCHDOWNS": "pass_tds",
        # UNVERIFIED marketType string — confirm against live Novig catalog
        "PASSING_COMPLETIONS": "pass_comp",
        "PASS_COMPLETIONS": "pass_comp",
        "RUSHING_ATTEMPTS": "rush_att",
        "RUSH_ATTEMPTS": "rush_att",
        "ANYTIME_TOUCHDOWN": "anytime_td",
    },
    "nba": {
        # UNVERIFIED marketType string — confirm against live Novig catalog
        "POINTS": "points",
        "REBOUNDS": "rebounds",
        "THREES": "threes",
        "THREE_POINTERS_MADE": "threes",
        "ASSISTS": "assists",
        "POINTS_REBOUNDS_ASSISTS": "pra",
        "PRA": "pra",
        "POINTS_REBOUNDS": "pr",
        "PR": "pr",
        "POINTS_ASSISTS": "pa",
        "PA": "pa",
        "REBOUNDS_ASSISTS": "ra",
        "RA": "ra",
        "BLOCKS": "blocks",
        "STEALS": "steals",
    },
    "nhl": {
        # UNVERIFIED marketType string — confirm against live Novig catalog
        "SHOTS_ON_GOAL": "sog",
        "SOG": "sog",
        "SAVES": "saves",
        "GOALIE_SAVES": "saves",
        "GOALS": "goals",
        "ASSISTS": "assists",
        "POINTS": "points",
        "BLOCKED_SHOTS": "blocked_shots",
    },
    "mlb": {
        # UNVERIFIED marketType string — confirm against live Novig catalog
        "STRIKEOUTS": "k",
        "PITCHER_STRIKEOUTS": "k",
        "OUTS": "outs",
        "PITCHER_OUTS": "outs",
        "HOME_RUNS": "hr",
        "TOTAL_BASES": "tb",
        "HITS": "hits",
        "STOLEN_BASES": "sb",
        # Game-level markets: mapped as stat keys, not forced through player-prop parser
        "NRFI": "nrfi",  # UNVERIFIED marketType string — game-level market
        "F5": "f5",  # UNVERIFIED marketType string — game-level market
        "GAME": "game",  # UNVERIFIED marketType string — game-level market
        "FULL_GAME": "game",  # UNVERIFIED marketType string — game-level market
    },
}


@dataclass(frozen=True)
class NovigLeg:
    market_id: str
    event_id: str
    stat: str
    player: str
    player_norm: str
    strike: float
    game_key: str
    start_utc: datetime
    over_id: str
    under_id: str
    fee_coefficient: float
    fee_charged: str                # 'WHEN_LIVE' (free pre-game) | 'ALWAYS'
    description: str
    ask_size: float | None = None
    bid_size: float | None = None
    quote_ts: datetime | None = None


def event_index(events: list, league: str = "NFL") -> dict:
    """{eventId: (game_key, start_utc)} for events whose teams we know."""
    names = TEAM_TABLES[league]
    out = {}
    for e in events:
        parts = (e.get("description") or "").split(" @ ")
        if len(parts) != 2:
            continue
        away, home = names.get(parts[0].strip()), names.get(parts[1].strip())
        if not away or not home or not e.get("startsTs"):
            continue
        start = datetime.fromtimestamp(e["startsTs"] / 1000, timezone.utc)
        out[e["eventId"]] = (game_key(league.lower(), start.astimezone(_ET).date().isoformat(), away, home), start)
    return out


def map_prop_market(m: dict, events: dict, league: str = "NFL"):
    stat = STAT_TABLES[league].get(m.get("marketType"))
    if stat is None:
        return MapFail("REJ_UNMAPPED", f"marketType {m.get('marketType')!r} not enabled")
    if m.get("status") != "OPEN":
        return MapFail("REJ_UNMAPPED", f"market status {m.get('status')!r}")
    ev = events.get(m.get("eventId"))
    if ev is None:
        return MapFail("REJ_UNMAPPED", "event not found / unknown teams")
    gk, start = ev
    try:
        strike = float(m.get("strike"))
    except (TypeError, ValueError):
        return MapFail("REJ_UNMAPPED", "no strike")
    if abs(strike * 2 - round(strike * 2)) > 1e-9 or abs(strike - round(strike)) < 1e-9:
        return MapFail("REJ_RULES_DIFFER", f"strike {strike} is not a half point (push possible)")
    mt = re.fullmatch(r"(.+) (\d+(?:\.\d+)?) ([A-Z_]+)", (m.get("description") or "").strip())
    if not mt or float(mt.group(2)) != strike or mt.group(3) != m.get("marketType"):
        return MapFail("REJ_RULES_DIFFER", f"description {m.get('description')!r} disagrees with strike/type")
    player = mt.group(1).strip()
    outs = {o.get("name"): o.get("outcomeId") for o in m.get("outcomes", [])}
    over, under = outs.get(f"Over {m['strike']}"), outs.get(f"Under {m['strike']}")
    if len(outs) != 2 or not over or not under:
        return MapFail("REJ_RULES_DIFFER", f"outcomes {sorted(outs)} are not exactly Over/Under {m['strike']}")
    if m.get("voids") not in ("FMV", "PUSH", "Pushes"):
        return MapFail("REJ_RULES_DIFFER", f"voids {m.get('voids')!r}")
    fee = m.get("fee") or {}
    return NovigLeg(market_id=m["marketId"], event_id=m["eventId"], stat=stat, player=player,
                    player_norm=norm_name(player), strike=strike, game_key=gk, start_utc=start, over_id=over,
                    under_id=under, fee_coefficient=float(fee.get("coefficient", 0.03)),
                    fee_charged=fee.get("charged", "WHEN_LIVE"), description=m["description"])


def map_prop_market_for_sport(m: dict, events: dict, sport: str):
    """Sport-parameterized mapping helper for Novig markets.
    
    Reads sport-specific stat table from STAT_BY_TYPE_BY_SPORT.
    Caller provides events index: {eventId: (game_key, start_utc)}.
    Supports optional ask_size, bid_size, and quote_ts persistence.
    """
    sport_key = (sport or "").strip().lower()
    stat_table = STAT_BY_TYPE_BY_SPORT.get(sport_key)
    if stat_table is None:
        return MapFail("REJ_UNMAPPED", f"sport {sport!r} not configured")

    market_type = m.get("marketType")
    stat = stat_table.get(market_type)
    if stat is None:
        return MapFail("REJ_UNMAPPED", f"marketType {market_type!r} not enabled for {sport}")

    if m.get("status") != "OPEN":
        return MapFail("REJ_UNMAPPED", f"market status {m.get('status')!r}")

    ev = events.get(m.get("eventId"))
    if ev is None:
        return MapFail("REJ_UNMAPPED", "event not found / unknown teams")
    gk, start = ev

    try:
        strike = float(m.get("strike"))
    except (TypeError, ValueError):
        return MapFail("REJ_UNMAPPED", "no strike")

    is_game_level = stat in ("nrfi", "f5", "game")

    if not is_game_level:
        if abs(strike * 2 - round(strike * 2)) > 1e-9 or abs(strike - round(strike)) < 1e-9:
            return MapFail("REJ_RULES_DIFFER", f"strike {strike} is not a half point (push possible)")

        mt = re.fullmatch(r"(.+) (\d+(?:\.\d+)?) ([A-Z_]+)", (m.get("description") or "").strip())
        if not mt or float(mt.group(2)) != strike or mt.group(3) != market_type:
            return MapFail("REJ_RULES_DIFFER", f"description {m.get('description')!r} disagrees with strike/type")
        player = mt.group(1).strip()
        player_norm = norm_name(player)
    else:
        player = (m.get("description") or "").strip()
        player_norm = player.lower()

    outs = {o.get("name"): o for o in m.get("outcomes", [])}
    over_o = outs.get(f"Over {m['strike']}")
    under_o = outs.get(f"Under {m['strike']}")
    if len(outs) != 2 or not over_o or not under_o:
        return MapFail("REJ_RULES_DIFFER", f"outcomes {sorted(outs)} are not exactly Over/Under {m['strike']}")

    over_id = over_o.get("outcomeId")
    under_id = under_o.get("outcomeId")
    if not over_id or not under_id:
        return MapFail("REJ_RULES_DIFFER", "missing outcomeId on Over/Under outcomes")

    if m.get("voids") not in ("FMV", "PUSH", "Pushes"):
        return MapFail("REJ_RULES_DIFFER", f"voids {m.get('voids')!r}")

    fee = m.get("fee") or {}

    ask_size = None
    for k in ("ask_size", "askSize", "l1_ask_dollars", "l1_ask_size"):
        if k in m and m[k] is not None:
            try:
                ask_size = float(m[k])
                break
            except (ValueError, TypeError):
                pass
    if ask_size is None and over_o:
        for k in ("ask_size", "askSize", "size", "ask_depth", "depth"):
            if k in over_o and over_o[k] is not None:
                try:
                    ask_size = float(over_o[k])
                    break
                except (ValueError, TypeError):
                    pass

    bid_size = None
    for k in ("bid_size", "bidSize", "l1_bid_dollars", "l1_bid_size"):
        if k in m and m[k] is not None:
            try:
                bid_size = float(m[k])
                break
            except (ValueError, TypeError):
                pass
    if bid_size is None and over_o:
        for k in ("bid_size", "bidSize", "bid_depth"):
            if k in over_o and over_o[k] is not None:
                try:
                    bid_size = float(over_o[k])
                    break
                except (ValueError, TypeError):
                    pass
    if bid_size is None and under_o:
        for k in ("bid_size", "bidSize", "size"):
            if k in under_o and under_o[k] is not None:
                try:
                    bid_size = float(under_o[k])
                    break
                except (ValueError, TypeError):
                    pass

    quote_ts = None
    for k in ("quote_ts", "quoteTs", "updatedTs", "updated_ts", "timestamp", "ts"):
        val = m.get(k)
        if val is not None:
            if isinstance(val, datetime):
                quote_ts = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            elif isinstance(val, (int, float)):
                sec = val / 1000.0 if val > 1e11 else float(val)
                quote_ts = datetime.fromtimestamp(sec, timezone.utc)
            elif isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    quote_ts = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            if quote_ts is not None:
                break

    return NovigLeg(
        market_id=m["marketId"],
        event_id=m["eventId"],
        stat=stat,
        player=player,
        player_norm=player_norm,
        strike=strike,
        game_key=gk,
        start_utc=start,
        over_id=over_id,
        under_id=under_id,
        fee_coefficient=float(fee.get("coefficient", 0.03)),
        fee_charged=fee.get("charged", "WHEN_LIVE"),
        description=m["description"],
        ask_size=ask_size,
        bid_size=bid_size,
        quote_ts=quote_ts,
    )
