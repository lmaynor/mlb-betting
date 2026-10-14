"""NBA roster crosswalk from ESPN's public site API: player id <-> full name <-> team <-> headshot.

Why ESPN (probed 2026-10-14): the official NBA JSON endpoints (cdn.nba.com/static/json/*, stats.nba.com) answer 403 /
unreachable from here and stats.nba.com is notorious for blocking cloud IPs, whereas
site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}/roster returned 200 with full names, ids and a
headshot URL for every player. SportsBlaze ids are opaque UUIDs with no headshot. So ESPN is the crosswalk/headshot
source; its player ids are ESPN's (NOT NBA personIds) -- do not mix them with SportsBlaze or Kaggle ids.

Headshots: ESPN's combiner resizes server-side and keeps the alpha channel --
  https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{id}.png&w=200&h=146
  -> 31KB transparent PNG with a ~17h cache (vs 174KB / 4-minute cache for the original). Access-Control-Allow-Origin: *.

Output master: NBA/players_xwalk.csv keyed on espn_id. Reads are FAIL-LOUD: an unreadable existing master raises
rather than being treated as empty (which would overwrite the accumulated crosswalk with a single pull).

Run: GCS_BUCKET=... python3 -m nba.data.espn_players          (or MLB_BASE_DATA=/tmp/x for a local dry run)
"""
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from mlb_core import storage
from mlb_core.data.player_names import PlayerResolver, norm_name
from nba.config import NBA_PREFIX
from nba.teams import ESPN_ID, TEAMS, team_code

logger = logging.getLogger(__name__)

ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{tid}/roster"
HEADSHOT = "https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{pid}.png&w=200&h=146"
PLAYERS_XWALK = f"{NBA_PREFIX}/players_xwalk.csv"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-data/1.0)"}


def headshot_url(espn_id) -> str:
    return HEADSHOT.format(pid=int(espn_id))


def flatten_roster(code: str, roster: dict) -> list:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for a in (roster or {}).get("athletes") or []:
        full = a.get("fullName") or a.get("displayName")
        if a.get("id") is None or not full:
            continue
        pos = a.get("position") or {}
        rows.append({
            "espn_id": int(a["id"]), "full_name": full, "name_norm": norm_name(full), "team": code,
            "position": pos.get("abbreviation"), "jersey": a.get("jersey"),
            "headshot_url": headshot_url(a["id"]), "updated_at": now,
        })
    return rows


def fetch_all(session=None, delay: float = 0.25) -> dict:
    """-> {'rows': [...], 'failed': [codes]}. A failed team never aborts the run."""
    s = session or requests.Session()
    rows, failed = [], []
    for code, _name, _nba_id, espn_id, _abbr in TEAMS:
        try:
            r = s.get(ESPN_ROSTER.format(tid=espn_id), headers=_UA, timeout=30)
            r.raise_for_status()
            rows.extend(flatten_roster(code, r.json()))
        except Exception as exc:                                                         # noqa: BLE001
            logger.warning("ESPN roster %s failed: %r", code, exc)
            failed.append(code)
        time.sleep(delay)
    return {"rows": rows, "failed": failed}


def _load_strict() -> pd.DataFrame:
    if not storage.exists(PLAYERS_XWALK):
        return pd.DataFrame()
    return storage.read_csv(PLAYERS_XWALK)          # unreadable -> raises (do NOT treat as empty)


def refresh(session=None) -> dict:
    res = fetch_all(session)
    if not res["rows"]:
        raise RuntimeError(f"ESPN roster pull returned no players (failed teams: {res['failed']})")
    existing = _load_strict()
    new = pd.DataFrame(res["rows"])
    combined = pd.concat([existing, new], ignore_index=True).drop_duplicates(subset=["espn_id"], keep="last")
    storage.write_csv(combined.reset_index(drop=True), PLAYERS_XWALK)
    out = {"rows": len(new), "total": len(combined), "added": len(combined) - len(existing),
           "failed_teams": res["failed"]}
    logger.info("espn players: %s", out)
    return out


def build_resolver(df: pd.DataFrame) -> PlayerResolver:
    """PlayerResolver over the crosswalk (ids are ESPN ids; team is the NBA tricode)."""
    return PlayerResolver({"player_id": r.espn_id, "full_name": r.full_name, "team": r.team}
                          for r in df.itertuples(index=False))


def resolve(resolver: PlayerResolver, raw_name: str, team_hint: str = None, within=None):
    """Book name (+ team hint in any vocabulary) -> (espn_id | None, how)."""
    hint = team_code(team_hint) if team_hint else None
    return resolver.resolve(raw_name, hint, within=[team_code(t) for t in within] if within else None)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(refresh())


if __name__ == "__main__":
    main()
