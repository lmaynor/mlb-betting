"""
One-time historical backfill: batter-game stolen-base / caught-stealing
labels, from MLB Stats API boxscores, for training the new SB (stolen base)
model.

Why 2023+ only (not the full 2021+ statcast_master window): the 2023-03-30
pitch-clock rule change (bigger bases, pickoff-disengagement limits) shifted
stolen-base behavior materially -- this repo's own NRFI builder already
flags "SB success... changed materially" at that boundary. Training on
pre-2023 base-stealing behavior would dilute the signal with a different
game. See handoffs/scope_stolen_base_model_2026-08-20.md s4 (regime risk).

Usage (local, real GCS creds required -- see mlb_core/storage.py):
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data PYTHONPATH=. \\
        python3 scripts/backfill_sb_boxscore.py [--start 2023-03-01] [--end YYYY-MM-DD]

Resumable: sb_backfill_gcs() checkpoints to GCS every 500 games, and skips
any game_pk already present in the master on a re-run.
"""
import argparse
import logging
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backfill_sb_boxscore")

SB_MASTER_KEY = "Scoring/sb_boxscore_master.csv"


def _build_game_pk_date_pairs(start_date: str, end_date: str) -> list:
    """Fetch every regular-season + postseason game_pk in [start_date,
    end_date]. gameType=R,F,D,L,W explicitly excludes spring training (S),
    exhibition (E), and all-star (A) games -- those have different
    competitive incentives / roster rules and would add noise to
    stolen-base behavior.

    GOTCHA (discovered live this session, not previously documented
    anywhere in this repo): the MLB Stats API schedule endpoint SILENTLY
    CLIPS a startDate/endDate range spanning more than one calendar year
    down to just the first season -- no error, no pagination hint, HTTP 200
    with a plausible-looking totalItems count. Confirmed by direct testing:
    a 2023-03-01..2026-08-19 query returned exactly the same 2,517 games as
    a 2023-01-01..2023-12-31 query (first/last dates both landed inside
    2023 only). Fix: one ranged call PER CALENDAR YEAR, concatenated --
    still 4 calls total instead of ~1,300+ per-date calls, and verified
    correct per-year (2023=2517, 2024=2512, 2026 partial=1938 games)."""
    import requests
    from datetime import datetime

    session = requests.Session()
    session.headers.update({"User-Agent": "mlb-betting/1.0"})

    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    pairs = []
    for year in range(start_year, end_year + 1):
        year_start = max(start_date, f"{year}-01-01")
        year_end = min(end_date, f"{year}-12-31")
        url = (
            "https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&startDate={year_start}&endDate={year_end}"
            "&gameType=R,F,D,L,W"
        )
        logger.info(f"Fetching schedule for {year} ({year_start} .. {year_end})...")
        r = session.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()

        year_pairs = []
        for d in data.get("dates", []):
            date_str = d.get("date")
            for g in d.get("games", []):
                # Only Final games have a settleable boxscore; non-Final
                # (postponed/future) games are skipped -- the nightly
                # refresh (once wired) picks up newly-Final games going
                # forward.
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                year_pairs.append((g["gamePk"], date_str))
        logger.info(f"  {year}: {len(year_pairs)} Final games")
        pairs.extend(year_pairs)

    logger.info(f"Schedule scan done: {len(pairs)} total Final games "
                f"({start_date} .. {end_date}, regular + postseason, {end_year - start_year + 1} seasons)")
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-03-01")
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    ap.add_argument("--checkpoint-every", type=int, default=500)
    args = ap.parse_args()

    from mlb_core.data.sb_boxscore import sb_backfill_gcs

    pairs = _build_game_pk_date_pairs(args.start, args.end)
    if not pairs:
        logger.error("No games found in range -- aborting.")
        return

    logger.info(f"Starting SB boxscore backfill: {len(pairs)} games -> {SB_MASTER_KEY}")
    final = sb_backfill_gcs(SB_MASTER_KEY, pairs, checkpoint_every=args.checkpoint_every)
    logger.info(f"BACKFILL COMPLETE: {len(final):,} batter-game rows | "
                f"{final['game_pk'].nunique():,} games | "
                f"date range {final['game_date'].min()} .. {final['game_date'].max()}")
    logger.info(f"Total real stolen bases in dataset: {int(final['stolen_bases'].sum())}")
    logger.info(f"Total real caught stealing in dataset: {int(final['caught_stealing'].sum())}")


if __name__ == "__main__":
    main()
