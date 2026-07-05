"""
Per-game per-inning scoring truth from MLB Stats API.

Why this exists:
  The Statcast master in GCS is missing scoring columns (bat_score,
  post_bat_score) for historical data. Rather than rebuilding the entire
  Statcast master, we fetch authoritative inning-by-inning scoring directly
  from the MLB Stats API and write a small lookup table to GCS.

  NRFI uses 1st-inning runs.
  F5  uses 1st-5-inning runs.
  K   may use late-inning context.

  Since one API call returns all innings of a game, capturing the full
  inning-by-inning detail costs nothing extra — and makes the data useful
  to every system that needs it.

Schema (long format, one row per half-inning):
  game_pk     int      MLB game id
  inning      int      1..N (N can exceed 9 for extra innings)
  half        str      "top" (away batting) or "bot" (home batting)
  runs        int      runs scored by the batting team in this half
  hits        int      hits in this half
  errors      int      errors committed by the fielding team in this half
  lob         int      runners left on base when the half ended

Endpoint:
  https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore
  ~3.6KB per game. No auth, no aggressive rate limiting.
"""
import logging
import time
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": "mlb-betting/1.0"})

MLB_LINESCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore"

# Pace between calls. MLB Stats API is generous but we don't want to hammer
# it on a multi-thousand-game backfill.
MLB_API_SLEEP_SEC = 0.25


def fetch_inning_scores(game_pk: int) -> Optional[pd.DataFrame]:
    """
    Fetch all half-innings for one game.

    Returns DataFrame with one row per (inning, half) — or None on error
    or if the game had no innings (postponed/cancelled).
    """
    try:
        r = _session.get(MLB_LINESCORE_URL.format(game_pk=game_pk), timeout=30)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"  scoring fetch failed for game_pk={game_pk}: {e}")
        return None

    innings = r.json().get("innings", [])
    if not innings:
        return None

    rows = []
    for inning_data in innings:
        inning_num = inning_data.get("num")
        if inning_num is None:
            continue
        for half_key, half_label in [("away", "top"), ("home", "bot")]:
            half = inning_data.get(half_key, {})
            # If the bottom of the final inning wasn't played (home team won
            # without batting), runs/hits will be missing. Skip rather than
            # invent zeros — null is the truth.
            runs = half.get("runs")
            hits = half.get("hits")
            if runs is None and hits is None:
                continue
            rows.append({
                "game_pk": int(game_pk),
                "inning":  int(inning_num),
                "half":    half_label,
                "runs":    int(runs) if runs is not None else 0,
                "hits":    int(hits) if hits is not None else 0,
                "errors":  int(half.get("errors", 0) or 0),
                "lob":     int(half.get("leftOnBase", 0) or 0),
            })

    if not rows:
        return None
    return pd.DataFrame(rows)


def fetch_scores_for_game_pks(game_pks: list, verbose: bool = True) -> pd.DataFrame:
    """
    Fetch inning-by-inning scores for a list of game_pks. Paces requests
    with MLB_API_SLEEP_SEC between calls.

    Returns long-format DataFrame: one row per (game_pk, inning, half).
    Failed games are logged and dropped.

    For long backfills use scoring_backfill_gcs() instead — it checkpoints
    to GCS so partial progress survives VM recycling.
    """
    frames = []
    total  = len(game_pks)
    failed = 0

    for i, gpk in enumerate(game_pks):
        if i > 0:
            time.sleep(MLB_API_SLEEP_SEC)
        df = fetch_inning_scores(gpk)
        if df is None:
            failed += 1
            continue
        frames.append(df)
        if verbose and (i + 1) % 500 == 0:
            done = i + 1
            kept = len(frames)
            logger.info(f"  scoring progress: {done}/{total} | "
                        f"games kept {kept} | failed {failed}")

    if not frames:
        return pd.DataFrame(columns=["game_pk", "inning", "half",
                                      "runs", "hits", "errors", "lob"])

    out = pd.concat(frames, ignore_index=True)
    if verbose:
        logger.info(f"  scoring fetch done: {out['game_pk'].nunique()} games | "
                    f"{len(out):,} half-innings | {failed} failed")
    return out


def _upload_scoring_csv(df: pd.DataFrame, bucket, key: str):
    """Helper: dedupe + write a scoring DataFrame to GCS."""
    df = df.drop_duplicates(subset=["game_pk", "inning", "half"], keep="last")
    tmp = "/tmp/scoring_master_new.csv"
    df.to_csv(tmp, index=False)
    bucket.blob(key).upload_from_filename(
        tmp, content_type="text/csv", timeout=600
    )
    return df


def scoring_backfill_gcs(gcs_bucket: str, gcs_master_key: str,
                          game_pks: list,
                          checkpoint_every: int = 1000) -> pd.DataFrame:
    """
    Resumable, checkpointed backfill.

    On start: loads existing scoring_master.csv from GCS (if any) and skips
    game_pks already covered. So re-running after a partial failure picks
    up where it left off.

    During run: every `checkpoint_every` games, uploads a snapshot to GCS.
    A VM recycle mid-backfill loses at most the last `checkpoint_every`
    games of work.

    On finish: final upload writes the complete master.

    Idempotent. Dedupes on (game_pk, inning, half) with keep="last".
    """
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    blob = bucket.blob(gcs_master_key)

    # 1. Load existing master (if any) and identify what we already have
    if blob.exists():
        existing = pd.read_csv(blob.open("r"), low_memory=False)
        already_have = set(existing["game_pk"].astype(int).unique())
        logger.info(f"  Existing master: {len(existing):,} rows | "
                    f"{len(already_have):,} games already covered")
    else:
        existing = pd.DataFrame(columns=["game_pk", "inning", "half",
                                          "runs", "hits", "errors", "lob"])
        already_have = set()
        logger.info("  No existing master — starting fresh")

    todo = [g for g in game_pks if int(g) not in already_have]
    skipped = len(game_pks) - len(todo)
    logger.info(f"Scoring backfill: {len(todo):,} games to fetch "
                f"({skipped:,} already covered) → "
                f"gs://{gcs_bucket}/{gcs_master_key}")

    if not todo:
        logger.info("  Nothing to fetch — master already complete")
        return existing

    # 2. Fetch in chunks, checkpoint every `checkpoint_every` games
    accumulated = [existing] if not existing.empty else []
    chunk_frames = []
    failed = 0

    for i, gpk in enumerate(todo):
        if i > 0:
            time.sleep(MLB_API_SLEEP_SEC)
        df = fetch_inning_scores(gpk)
        if df is None:
            failed += 1
        else:
            chunk_frames.append(df)

        # Checkpoint every N games (or at the end)
        done = i + 1
        is_checkpoint = (done % checkpoint_every == 0) or (done == len(todo))
        if is_checkpoint and chunk_frames:
            chunk_df = pd.concat(chunk_frames, ignore_index=True)
            accumulated.append(chunk_df)
            combined = pd.concat(accumulated, ignore_index=True)
            combined = _upload_scoring_csv(combined, bucket, gcs_master_key)
            # Keep memory bounded: replace the list with the deduped result
            accumulated = [combined]
            chunk_frames = []
            logger.info(f"  CHECKPOINT {done}/{len(todo)} | "
                        f"master: {len(combined):,} rows / "
                        f"{combined['game_pk'].nunique():,} games | "
                        f"failed so far: {failed}")

    final = accumulated[-1] if accumulated else existing
    logger.info(f"  Master updated: {len(final):,} rows | "
                f"{final['game_pk'].nunique():,} games")
    return final


def scoring_nightly_gcs(gcs_bucket: str, gcs_master_key: str) -> None:
    """Fetch yesterday's inning scores, append to GCS master.

    Called by /refresh-data nightly. Gets game_pks from the MLB schedule
    API then fetches linescore for each via fetch_scores_for_game_pks().
    """
    from datetime import date, timedelta
    from google.cloud import storage
    from mlb_core.data.lineups import _get_games_for_date

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"scoring_nightly_gcs: fetching {yesterday}")

    games = _get_games_for_date(yesterday)
    if not games:
        logger.warning(f"scoring_nightly_gcs: no games found for {yesterday}")
        return

    game_pks = [g["game_pk"] for g in games]
    logger.info(f"scoring_nightly_gcs: {len(game_pks)} games to fetch")

    new_df = fetch_scores_for_game_pks(game_pks, verbose=False)
    if new_df.empty:
        logger.warning("scoring_nightly_gcs: no scoring data returned")
        return

    from mlb_core import storage as _st  # twin-aware master IO

    if _st.exists(gcs_master_key):
        existing = _st.read_csv(gcs_master_key, low_memory=False)
        logger.info(f"scoring_nightly_gcs: existing master {len(existing):,} rows")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        logger.info("scoring_nightly_gcs: no existing master — creating new")
        combined = new_df

    combined = combined.drop_duplicates(
        subset=["game_pk", "inning", "half"], keep="last"
    )

    _st.write_csv(combined, gcs_master_key)
    logger.info(f"scoring_nightly_gcs: master updated {len(combined):,} rows "
                f"| through {yesterday}")
