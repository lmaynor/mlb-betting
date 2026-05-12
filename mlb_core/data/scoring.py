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


def scoring_backfill_gcs(gcs_bucket: str, gcs_master_key: str,
                          game_pks: list) -> pd.DataFrame:
    """
    Fetch scoring for the given game_pks, merge with existing GCS master,
    write back. Idempotent — re-runs with overlapping game_pks dedupe on
    (game_pk, inning, half) with keep="last".

    Use for initial bootstrap (pass all historical game_pks) or for daily
    updates (pass yesterday's game_pks).
    """
    from google.cloud import storage

    logger.info(f"Scoring backfill: {len(game_pks):,} game_pks → "
                f"gs://{gcs_bucket}/{gcs_master_key}")

    new_df = fetch_scores_for_game_pks(game_pks)
    if new_df.empty:
        logger.warning("  No scoring rows fetched")
        return pd.DataFrame()

    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    blob = bucket.blob(gcs_master_key)

    if blob.exists():
        existing = pd.read_csv(blob.open("r"), low_memory=False)
        logger.info(f"  Existing master: {len(existing):,} rows")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        logger.info("  No existing master — creating new")
        combined = new_df

    combined = combined.drop_duplicates(
        subset=["game_pk", "inning", "half"], keep="last"
    )

    tmp = "/tmp/scoring_master_new.csv"
    combined.to_csv(tmp, index=False)
    blob.upload_from_filename(tmp, content_type="text/csv", timeout=600)
    logger.info(f"  Master updated: {len(combined):,} rows | "
                f"{combined['game_pk'].nunique():,} games")
    return combined
