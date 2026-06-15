"""One-time multi-season NBA backfill from SportsBlaze into the GCS data lake.

Stores raw daily boxscore JSON under NBA/raw/boxscores/{date}.json (resume-safe:
cached dates are read from GCS instead of re-fetched unless --force), then
rebuilds the three masters (games / team_box / player_box) via masters.upsert.

Run (Cloud Shell, with the shared bucket env set):
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data \
        python3 -m nba.data.backfill --seasons 2019-2025

    # subset / single season / force refetch:
    python3 -m nba.data.backfill --seasons 2024
    python3 -m nba.data.backfill --seasons 2019,2020 --force
"""
import argparse
import json
import logging
from datetime import date, timedelta

from mlb_core import storage
from nba.config import (BACKFILL_SEASONS, GAMES_MASTER, PLAYER_BOX_MASTER,
                        SEASON_END_DAY, SEASON_END_MONTH, SEASON_START_DAY,
                        SEASON_START_MONTH, TEAM_BOX_MASTER, raw_boxscore_key,
                        raw_players_key, raw_schedule_key, raw_seasons_key,
                        raw_teams_key)
from nba.data import masters
from nba.data.flatten import flatten_boxscores
from nba.data.sportsblaze import SbClient

logger = logging.getLogger(__name__)


def _season_dates(year: int):
    start = date(year, SEASON_START_MONTH, SEASON_START_DAY)
    end = date(year + 1, SEASON_END_MONTH, SEASON_END_DAY)
    d = start
    while d < end:
        yield d.isoformat()
        d += timedelta(days=1)


def _fetch_or_cache(client: SbClient, dstr: str, force: bool) -> dict:
    key = raw_boxscore_key(dstr)
    if not force and storage.exists(key):
        try:
            return json.loads(storage.read_bytes(key))
        except Exception as exc:
            logger.warning("cached raw unreadable for %s (%s); refetching", dstr, exc)
    raw = client.get_boxscores(dstr) or {"events": []}
    storage.write_bytes(json.dumps(raw).encode(), key)
    return raw


def _parse_seasons(spec: str):
    if not spec:
        return list(BACKFILL_SEASONS)
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def run(seasons=None, force=False) -> dict:
    seasons = seasons or list(BACKFILL_SEASONS)
    client = SbClient()

    # Snapshot reference data (seasons / teams / current rosters).
    for getter, key in ((client.get_seasons, raw_seasons_key()),
                        (client.get_teams, raw_teams_key()),
                        (client.get_players, raw_players_key())):
        try:
            data = getter()
            if data is not None:
                storage.write_bytes(json.dumps(data).encode(), key)
        except Exception as exc:
            logger.warning("reference snapshot failed for %s: %s", key, exc)

    all_games, all_team, all_player = [], [], []
    for year in seasons:
        try:
            sched = client.get_schedule(year)
            if sched is not None:
                storage.write_bytes(json.dumps(sched).encode(), raw_schedule_key(year))
        except Exception as exc:
            logger.warning("schedule snapshot failed for %s: %s", year, exc)

        ndays = game_days = ngames = 0
        for dstr in _season_dates(year):
            ndays += 1
            raw = _fetch_or_cache(client, dstr, force)
            g, t, p = flatten_boxscores(raw)
            if g:
                game_days += 1
                ngames += len(g)
                all_games.extend(g)
                all_team.extend(t)
                all_player.extend(p)
        logger.info("season %s: %d days scanned, %d game-days, %d games",
                    year, ndays, game_days, ngames)

    n_g = masters.upsert(GAMES_MASTER, all_games)
    n_t = masters.upsert(TEAM_BOX_MASTER, all_team)
    n_p = masters.upsert(PLAYER_BOX_MASTER, all_player)
    logger.info("masters updated: games +%d, team_box +%d, player_box +%d", n_g, n_t, n_p)
    return {"games_added": n_g, "team_rows_added": n_t, "player_rows_added": n_p,
            "games_seen": len(all_games)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="", help="e.g. 2019-2025 or 2024 or 2019,2020")
    ap.add_argument("--force", action="store_true", help="refetch even if raw is cached")
    args = ap.parse_args()
    seasons = _parse_seasons(args.seasons)
    logger.info("NBA backfill seasons=%s force=%s", seasons, args.force)
    result = run(seasons=seasons, force=args.force)
    logger.info("done: %s", result)


if __name__ == "__main__":
    main()
