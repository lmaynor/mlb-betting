"""Nightly NBA incremental ingest. Runs year-round; no-ops on empty days.

Fetches one date's boxscores (default: yesterday in America/New_York), appends
new rows to the three masters, and writes the NBA/last_refresh.json sentinel.
Self-healing across the offseason: empty days write a status="skipped" sentinel
and touch nothing else, so the same daily schedule auto-starts ingesting when
the new season's games appear (~late October).

Run:
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nba.data.refresh
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mlb_core import storage
from nba.config import (GAMES_MASTER, LAST_REFRESH, PLAYER_BOX_MASTER,
                        TEAM_BOX_MASTER, TIMEZONE, raw_boxscore_key)
from nba.data import masters
from nba.data.flatten import flatten_boxscores
from nba.data.sportsblaze import SbClient

logger = logging.getLogger(__name__)


def _yesterday() -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    return (now - timedelta(days=1)).date().isoformat()


def _write_sentinel(payload: dict) -> None:
    payload = dict(payload)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        storage.write_bytes(json.dumps(payload).encode(), LAST_REFRESH)
    except Exception as exc:
        logger.warning("could not write NBA refresh sentinel: %s", exc)


def refresh_nightly_gcs(date: str = None) -> dict:
    dstr = date or _yesterday()
    client = SbClient()
    raw = client.get_boxscores(dstr) or {"events": []}
    # Cache raw regardless -- idempotent source of truth.
    storage.write_bytes(json.dumps(raw).encode(), raw_boxscore_key(dstr))

    games, team_box, player_box = flatten_boxscores(raw)
    if not games:
        logger.info("no NBA games on %s (offseason/off-day); no-op", dstr)
        result = {"status": "skipped", "date": dstr, "games": 0}
        _write_sentinel(result)
        return result

    n_g = masters.upsert(GAMES_MASTER, games)
    n_t = masters.upsert(TEAM_BOX_MASTER, team_box)
    n_p = masters.upsert(PLAYER_BOX_MASTER, player_box)
    logger.info("NBA refresh %s: %d games (+%d game / +%d team / +%d player rows)",
                dstr, len(games), n_g, n_t, n_p)
    result = {"status": "ok", "date": dstr, "games": len(games),
              "games_added": n_g, "team_rows_added": n_t, "player_rows_added": n_p}
    _write_sentinel(result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refresh_nightly_gcs()


if __name__ == "__main__":
    main()
