"""One-time multi-season NHL backfill from the official NHL API into the GCS data lake.

Per season: walk the weekly schedule, then for every FINAL game fetch boxscore + play-by-play
(raw JSON cached under NHL/raw/ -- resume-safe, a re-run reads the cache instead of the API unless
--force) and upsert the masters. Masters are flushed once per season so a long run never holds
more than one season of rows in memory.

Cost: ~2 API calls/game, ~1,400 games/season -> ~3,000 calls/season at 0.25s throttle (~15-30 min
per season). Run it as a Cloud Run Job or locally:

    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.backfill --seasons 2019-2026

    python3 -m nhl.data.backfill --seasons 2026 --limit 20      # local smoke test (writes under MLB_BASE_DATA)
"""
import argparse
import logging
from datetime import date

from nhl.data import ingest
from nhl.data.nhl_api import NhlClient
from nhl.data.settings import (BACKFILL_SEASONS, WALK_END_DAY, WALK_END_MONTH, WALK_START_DAY,
                               WALK_START_MONTH)

logger = logging.getLogger(__name__)


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


def run(seasons=None, force=False, limit=None, client=None) -> dict:
    seasons = seasons or list(BACKFILL_SEASONS)
    client = client or NhlClient()
    today = date.today()
    totals = {"games_seen": 0, "failed_game_ids": []}

    for year in seasons:
        start = date(year, WALK_START_MONTH, WALK_START_DAY)
        end = min(date(year + 1, WALK_END_MONTH, WALK_END_DAY), today)
        if start > today:
            logger.info("season %s has not started; skipping", year)
            continue
        games = [g for g in ingest.games_in_weeks(client, start, end, season=year * 10000 + year + 1)
                 if ingest.is_final(g)]
        games.sort(key=lambda g: g["id"])
        if limit:
            games = games[:limit]
        logger.info("season %s: %d final games to ingest", year, len(games))

        acc = ingest.ingest_games(client, games, force=force)
        added = ingest.write_masters(acc) if acc["games"] else {}
        logger.info("season %s done: %d ingested, %d failed, %s",
                    year, len(acc["games"]), len(acc["failed"]), added)
        totals["games_seen"] += len(acc["games"])
        totals["failed_game_ids"].extend(acc["failed"])
        for k, v in added.items():
            totals[k] = totals.get(k, 0) + v
    return totals


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="", help="e.g. 2019-2026 or 2025 or 2019,2020 (start year)")
    ap.add_argument("--force", action="store_true", help="refetch even if raw is cached")
    ap.add_argument("--limit", type=int, default=0, help="max games per season (smoke tests)")
    args = ap.parse_args()
    seasons = _parse_seasons(args.seasons)
    logger.info("NHL backfill seasons=%s force=%s limit=%s", seasons, args.force, args.limit or None)
    result = run(seasons=seasons, force=args.force, limit=args.limit or None)
    logger.info("done: %d games, failed=%s", result["games_seen"], result["failed_game_ids"])


if __name__ == "__main__":
    main()
