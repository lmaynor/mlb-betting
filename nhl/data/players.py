"""NHL roster crosswalk: player_id <-> full name <-> headshot.

Boxscores carry ABBREVIATED names ("B. Tkachuk"); sportsbooks carry full names with their own spelling quirks. The
roster endpoint (/roster/{team}/current) carries full first/last names, the position, and a ready-made headshot URL,
so it is the crosswalk source. Output master: NHL/players_master.csv, keyed on player_id.

Headshots: `headshot_url` is the API's own season+team-pathed mug; `headshot_latest_url` needs only the player id
(https://assets.nhle.com/mugs/nhl/latest/{id}.png -- verified 200, transparent 336x336 PNG, ~100KB) and is what the
frontend uses, because it survives trades and season rollovers.
"""
import logging
from datetime import datetime, timezone

from mlb_core.data.player_names import PlayerResolver, norm_name
from nhl.config import PLAYERS_MASTER, TEAMS, team_abbrev
from nhl.data import masters

logger = logging.getLogger(__name__)

HEADSHOT_LATEST = "https://assets.nhle.com/mugs/nhl/latest/{pid}.png"
_GROUP = {"C": "F", "L": "F", "R": "F", "LW": "F", "RW": "F", "W": "F", "D": "D", "G": "G"}


def headshot_latest_url(player_id) -> str:
    return HEADSHOT_LATEST.format(pid=int(player_id))


def _nm(d) -> str:
    return (d.get("default") if isinstance(d, dict) else d) or ""


def flatten_roster(abbrev: str, roster: dict) -> list:
    rows, now = [], datetime.now(timezone.utc).isoformat()
    for group in ("forwards", "defensemen", "goalies"):
        for p in (roster or {}).get(group) or []:
            first, last = _nm(p.get("firstName")), _nm(p.get("lastName"))
            full = f"{first} {last}".strip()
            if p.get("id") is None or not full:
                continue
            pos = p.get("positionCode")
            rows.append({
                "player_id": int(p["id"]), "full_name": full, "first_name": first, "last_name": last,
                "name_norm": norm_name(full), "team": abbrev, "position_code": pos,
                "position_group": _GROUP.get(pos, "F"), "sweater": p.get("sweaterNumber"),
                "shoots_catches": p.get("shootsCatches"), "height_in": p.get("heightInInches"),
                "weight_lb": p.get("weightInPounds"), "birth_date": p.get("birthDate"),
                "headshot_url": p.get("headshot"), "headshot_latest_url": headshot_latest_url(p["id"]),
                "updated_at": now,
            })
    return rows


def _pull(client, abbrevs):
    rows, failed = [], []
    for a in abbrevs:
        try:
            r = client.roster(a)
        except Exception as exc:                                                         # noqa: BLE001
            logger.warning("roster %s failed: %r", a, exc)
            failed.append(a)
            continue
        if not r:
            failed.append(a)
            continue
        rows.extend(flatten_roster(a, r))
    return rows, failed


def refresh_players(client, abbrevs=None, retry_pause: float = 20.0) -> dict:
    """Pull every team's current roster and upsert the crosswalk. Failed teams get ONE second pass after a pause
    (a 429 window usually clears); any still failing are reported in `failed_teams` -- the caller must treat a
    non-empty list as a PARTIAL crosswalk (see covered_teams()). Never fatal: older rows stay in the master."""
    abbrevs = abbrevs or [a for a, _, _ in TEAMS]
    rows, failed = _pull(client, abbrevs)
    if failed and retry_pause is not None:
        import time
        time.sleep(retry_pause)
        more, failed = _pull(client, failed)
        rows.extend(more)
    added = masters.upsert(PLAYERS_MASTER, rows) if rows else 0
    logger.info("players: %d roster rows, +%d new, %d team(s) failed %s", len(rows), added, len(failed), failed)
    return {"rows": len(rows), "added": added, "failed_teams": failed}


def covered_teams(rows) -> set:
    """Team abbrevs that have at least one player in the crosswalk. A resolution is only trustworthy for an event
    whose BOTH teams are covered: with a team's roster missing, a same-named player on it simply is not a candidate,
    so the name resolves 'exactly' to the player on the team that WAS loaded. (Hazard, not an observed mis-merge: on
    2026-10-14 four rosters -- TBL, TOR, UTA, VAN -- failed with 429 on one run and lookups still returned results; the
    current NHL/NBA crosswalks contain no same-name pairs, but MLB does, e.g. two Max Muncys.)"""
    return {(r.get("team") if isinstance(r, dict) else getattr(r, "team", None)) for r in rows} - {None}


def build_resolver(rows) -> PlayerResolver:
    """PlayerResolver over crosswalk rows ({player_id, full_name, team})."""
    return PlayerResolver(rows)


def resolve(resolver: PlayerResolver, raw_name: str, team_hint: str = None):
    """Book name (+ team hint in ANY vocabulary: 'WSH', 'LA', 'Los Angeles Kings') -> (player_id | None, how)."""
    hint = team_abbrev(team_hint) if team_hint else None
    return resolver.resolve(raw_name, hint)
