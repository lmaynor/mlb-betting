"""
ev.novig.map -- Novig NFL player-prop markets -> proposition (STRICT, fail closed).

Shape (public catalog, verified live 2026-10-12): market.description "<Player> <strike> <TYPE>" (e.g.
"Marvin Harrison Jr. 22.5 RECEIVING_YARDS"), marketType, strike "22.5", outcomes "Over 22.5" / "Under 22.5"
(identified by NAME, never by array position -- the docs warn the order is not fixed), fee {coefficient, charged},
voids "FMV". Event description "Away Team @ Home Team" (full names) + startsTs (ms).
YES-equivalent: Novig "Over N-0.5" == Kalshi "N+" == a sportsbook over at N-0.5.
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

_ET = ZoneInfo("America/New_York")
STAT_BY_TYPE = {"RECEIVING_YARDS": "rec_yds", "RUSHING_YARDS": "rush_yds", "PASSING_YARDS": "pass_yds",
                "RECEPTIONS": "receptions", "PASSING_TOUCHDOWNS": "pass_tds"}


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


def event_index(events: list) -> dict:
    """{eventId: (game_key, start_utc)} for events whose teams we know."""
    out = {}
    for e in events:
        parts = (e.get("description") or "").split(" @ ")
        if len(parts) != 2:
            continue
        away, home = TEAM_NAMES.get(parts[0].strip()), TEAM_NAMES.get(parts[1].strip())
        if not away or not home or not e.get("startsTs"):
            continue
        start = datetime.fromtimestamp(e["startsTs"] / 1000, timezone.utc)
        out[e["eventId"]] = (game_key("nfl", start.astimezone(_ET).date().isoformat(), away, home), start)
    return out


def map_prop_market(m: dict, events: dict):
    stat = STAT_BY_TYPE.get(m.get("marketType"))
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
