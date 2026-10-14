"""NHL team vocabulary master (NHL/teams_master.csv): abbrev <-> name <-> Kalshi code <-> logo URLs <-> team id.

Names/codes come from nhl.config.TEAMS (static, verified against /standings/now). Logo URLs follow the API's own
scheme (https://assets.nhle.com/logos/nhl/svg/{ABBREV}_{light|dark}.svg, verified 200). team_id is learned from the
schedule (standings omits it); a team not seen yet keeps a blank id and is filled on a later run.
"""
import logging
from datetime import date, timedelta

from nhl.config import KALSHI_OF, TEAMS, TEAMS_MASTER
from nhl.data import masters

logger = logging.getLogger(__name__)

LOGO = "https://assets.nhle.com/logos/nhl/svg/{a}_{v}.svg"


def team_ids_from_schedule(client, start: date = None, weeks: int = 6) -> dict:
    """{abbrev: team_id} learned from consecutive schedule weeks."""
    ids, d = {}, start or date.today()
    for _ in range(weeks):
        sched = client.schedule(d.isoformat()) or {}
        for day in sched.get("gameWeek") or []:
            for g in day.get("games") or []:
                for side in ("awayTeam", "homeTeam"):
                    t = g.get(side) or {}
                    if t.get("abbrev") and t.get("id") is not None:
                        ids[t["abbrev"]] = int(t["id"])
        if len(ids) >= len(TEAMS):
            break
        d += timedelta(days=7)
    return ids


def build_rows(ids: dict = None) -> list:
    ids = ids or {}
    return [{"abbrev": a, "team_id": ids.get(a), "name": n, "kalshi_code": KALSHI_OF[a],
             "logo_light": LOGO.format(a=a, v="light"), "logo_dark": LOGO.format(a=a, v="dark")}
            for a, n, _ in TEAMS]


def refresh_teams(client) -> dict:
    try:
        ids = team_ids_from_schedule(client)
    except Exception as exc:                                                              # noqa: BLE001
        logger.warning("team id discovery failed (%r); writing teams without ids", exc)
        ids = {}
    added = masters.upsert(TEAMS_MASTER, build_rows(ids))
    return {"teams": len(TEAMS), "ids_known": len(ids), "added": added}
