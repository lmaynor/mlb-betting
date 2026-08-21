"""
One-time historical backfill: starting-catcher identity per game, for the
SB (stolen base) model's catcher join (join_catcher_aux -> catcher pop time/
arm strength). Reuses the same 2023+ regular+postseason game_pk list logic
as scripts/backfill_sb_boxscore.py (see that file's docstring for why 2023+
only).

Usage:
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data PYTHONPATH=. \\
        python3 scripts/backfill_catcher_identity.py [--start 2023-03-01] [--end YYYY-MM-DD]
"""
import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backfill_catcher_identity")

CATCHER_MASTER_KEY = "AuxData/catcher_identity_master.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-03-01")
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    ap.add_argument("--checkpoint-every", type=int, default=1000)
    args = ap.parse_args()

    # Reuse the exact same schedule-scan logic as the SB boxscore backfill
    # (per-year ranged calls -- the MLB Stats API silently clips a multi-year
    # range to the first season, see that file's docstring).
    from scripts.backfill_sb_boxscore import _build_game_pk_date_pairs
    from mlb_core.data.lineups import catcher_backfill_gcs

    pairs = _build_game_pk_date_pairs(args.start, args.end)
    game_pks = [gpk for gpk, _ in pairs]
    if not game_pks:
        logger.error("No games found in range -- aborting.")
        return

    logger.info(f"Starting catcher identity backfill: {len(game_pks)} games -> {CATCHER_MASTER_KEY}")
    final = catcher_backfill_gcs(CATCHER_MASTER_KEY, game_pks, checkpoint_every=args.checkpoint_every)
    n_both = final[["away_catcher_id", "home_catcher_id"]].notna().all(axis=1).sum()
    logger.info(f"BACKFILL COMPLETE: {len(final):,} games | both catchers resolved: {n_both:,} "
                f"({n_both / len(final):.1%})")


if __name__ == "__main__":
    main()
