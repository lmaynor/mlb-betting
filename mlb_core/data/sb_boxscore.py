"""
mlb_core.data.sb_boxscore -- Batter-game stolen-base / caught-stealing labels.

Why this exists:
  Statcast's public per-pitch CSV export (mlb_core/data/statcast.py) does not
  surface stolen_base_*/caught_stealing_*/pickoff_* events at all -- verified
  live 2026-08-20 across 4 real dates plus a group_by A/B test (see
  handoffs/scope_stolen_base_model_2026-08-20.md s2). Real per-batter-game
  SB/CS counts have to come from the MLB Stats API boxscore instead, via the
  same fetch_game_result() every settler already uses. This module runs that
  in bulk across historical game_pks, mirroring mlb_core.data.scoring's
  resumable, checkpointed scoring_backfill_gcs() pattern exactly.

Schema (one row per batter-game):
  game_pk, game_date, batter_mlbam_id, batter_name, team, starter,
  batting_order, stolen_bases, caught_stealing

Endpoint: reuses mlb_core.data.game_result.fetch_game_result() (schedule
status check + linescore + boxscore per game_pk -- same cost as one
settlement lookup, ~3 MLB Stats API calls).
"""
import logging
import time
from datetime import date, timedelta

import pandas as pd

from mlb_core.data.game_result import fetch_game_result

logger = logging.getLogger(__name__)

# fetch_game_result() has its own internal retry/backoff per HTTP call; this
# is just courteous pacing BETWEEN GAMES so a multi-thousand-game backfill
# doesn't hammer the API. Matches mlb_core.data.scoring's MLB_API_SLEEP_SEC.
MLB_API_SLEEP_SEC = 0.25

SB_COLS = ["game_pk", "game_date", "batter_mlbam_id", "batter_name", "team",
           "starter", "batting_order", "stolen_bases", "caught_stealing"]


def _batter_rows_from_result(result: dict, game_date: str) -> list:
    """Extract one row per batter from a fetch_game_result() dict."""
    rows = []
    for name, bat in result.get("batters", {}).items():
        rows.append({
            "game_pk":         result["game_pk"],
            "game_date":       game_date,
            "batter_mlbam_id": bat.get("mlbam_id"),
            "batter_name":     name,
            "team":            bat.get("team", ""),
            "starter":         bat.get("starter", False),
            "batting_order":   bat.get("batting_order"),
            "stolen_bases":    bat.get("stolen_bases", 0),
            "caught_stealing": bat.get("caught_stealing", 0),
        })
    return rows


def fetch_sb_rows_for_game_pks(game_date_pairs: list, verbose: bool = True) -> pd.DataFrame:
    """
    Fetch batter-game SB/CS rows for a list of (game_pk, game_date) pairs.
    Paces requests with MLB_API_SLEEP_SEC between calls. Games that are not
    yet Final (or fail) are silently skipped -- caller retries later via the
    nightly refresh.

    For long backfills use sb_backfill_gcs() instead -- it checkpoints to
    GCS so partial progress survives an interrupt.
    """
    frames = []
    total = len(game_date_pairs)
    failed = 0
    for i, (gpk, gdate) in enumerate(game_date_pairs):
        if i > 0:
            time.sleep(MLB_API_SLEEP_SEC)
        result = fetch_game_result(int(gpk))
        if result is None:
            failed += 1
            continue
        rows = _batter_rows_from_result(result, gdate)
        if rows:
            frames.append(pd.DataFrame(rows))
        if verbose and (i + 1) % 250 == 0:
            kept = sum(len(f) for f in frames)
            logger.info(f"  sb_boxscore progress: {i + 1}/{total} games | "
                        f"{kept} batter-rows kept | {failed} failed")
    if not frames:
        return pd.DataFrame(columns=SB_COLS)
    out = pd.concat(frames, ignore_index=True)
    if verbose:
        logger.info(f"  sb_boxscore fetch done: {out['game_pk'].nunique()} games | "
                    f"{len(out):,} batter-rows | {failed} failed")
    return out


def _upload_sb_csv(df: pd.DataFrame, key: str):
    """Helper: dedupe + write an SB DataFrame to GCS."""
    from mlb_core import storage as _st  # twin-aware master IO
    df = df.drop_duplicates(subset=["game_pk", "batter_mlbam_id"], keep="last")
    _st.write_csv(df, key)
    return df


def sb_backfill_gcs(gcs_master_key: str, game_date_pairs: list,
                     checkpoint_every: int = 500) -> pd.DataFrame:
    """
    Resumable, checkpointed backfill of batter-game SB/CS labels.

    On start: loads existing master from GCS (if any) and skips game_pks
    already covered -- re-running after a partial failure picks up where it
    left off. During run: every `checkpoint_every` games, uploads a snapshot
    to GCS -- an interrupt loses at most that many games of work. On finish:
    final upload writes the complete master. Idempotent, dedupes on
    (game_pk, batter_mlbam_id) with keep="last". Mirrors
    mlb_core.data.scoring.scoring_backfill_gcs() exactly.
    """
    from mlb_core import storage as _st  # twin-aware master IO

    if _st.exists(gcs_master_key):
        existing = _st.read_csv(gcs_master_key, low_memory=False)
        already_have = set(existing["game_pk"].astype(int).unique())
        logger.info(f"  Existing SB master: {len(existing):,} rows | "
                    f"{len(already_have):,} games already covered")
    else:
        existing = pd.DataFrame(columns=SB_COLS)
        already_have = set()
        logger.info("  No existing SB master -- starting fresh")

    todo = [(gpk, gdate) for gpk, gdate in game_date_pairs if int(gpk) not in already_have]
    skipped = len(game_date_pairs) - len(todo)
    logger.info(f"SB boxscore backfill: {len(todo):,} games to fetch "
                f"({skipped:,} already covered) -> {gcs_master_key}")

    if not todo:
        logger.info("  Nothing to fetch -- master already complete")
        return existing

    accumulated = [existing] if not existing.empty else []
    chunk_frames = []
    failed = 0

    for i, (gpk, gdate) in enumerate(todo):
        if i > 0:
            time.sleep(MLB_API_SLEEP_SEC)
        result = fetch_game_result(int(gpk))
        if result is None:
            failed += 1
        else:
            rows = _batter_rows_from_result(result, gdate)
            if rows:
                chunk_frames.append(pd.DataFrame(rows))

        done = i + 1
        is_checkpoint = (done % checkpoint_every == 0) or (done == len(todo))
        if is_checkpoint and chunk_frames:
            chunk_df = pd.concat(chunk_frames, ignore_index=True)
            accumulated.append(chunk_df)
            combined = pd.concat(accumulated, ignore_index=True)
            combined = _upload_sb_csv(combined, gcs_master_key)
            accumulated = [combined]
            chunk_frames = []
            logger.info(f"  CHECKPOINT {done}/{len(todo)} | "
                        f"master: {len(combined):,} rows / "
                        f"{combined['game_pk'].nunique():,} games | "
                        f"failed so far: {failed}")
        elif is_checkpoint:
            logger.info(f"  CHECKPOINT {done}/{len(todo)} | no new rows this chunk | "
                        f"failed so far: {failed}")

    final = accumulated[-1] if accumulated else existing
    logger.info(f"  SB master updated: {len(final):,} rows | "
                f"{final['game_pk'].nunique():,} games")
    return final


def sb_nightly_gcs(gcs_master_key: str) -> None:
    """Fetch yesterday's batter-game SB/CS rows, append to the GCS master.
    Intended to be called from /refresh-data alongside scoring_nightly_gcs()."""
    from mlb_core.data.lineups import _get_games_for_date
    from mlb_core import storage as _st  # twin-aware master IO

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"sb_nightly_gcs: fetching {yesterday}")

    games = _get_games_for_date(yesterday)
    if not games:
        logger.warning(f"sb_nightly_gcs: no games found for {yesterday}")
        return

    pairs = [(g["game_pk"], yesterday) for g in games]
    new_df = fetch_sb_rows_for_game_pks(pairs, verbose=False)
    if new_df.empty:
        logger.warning("sb_nightly_gcs: no SB data returned (games may not be Final yet)")
        return

    if _st.exists(gcs_master_key):
        existing = _st.read_csv(gcs_master_key, low_memory=False)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.drop_duplicates(subset=["game_pk", "batter_mlbam_id"], keep="last")
    _st.write_csv(combined, gcs_master_key)
    logger.info(f"sb_nightly_gcs: master updated {len(combined):,} rows | through {yesterday}")
