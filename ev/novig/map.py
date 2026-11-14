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
