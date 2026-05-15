"""
Shared Statcast pull for all systems.
One master CSV, inning filter applied at load time.
"""
import os
import time
import random
import pandas as pd
from io import StringIO
from pathlib import Path
from datetime import date, datetime, timedelta

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

STATCAST_URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv"
    "?all=true"
    "&hfPT=&hfAB=&hfBBT=&hfPR=&hfZ=&stadium=&hfBBL=&hfNewZones="
    "&hfGT=R%7C"
    "&hfSea=&hfSit="
    "&player_type=pitcher"
    "&hfOuts=&opponent=&pitcher_throws=&batter_stands=&hfSA="
    "&game_date_gt={date}"
    "&game_date_lt={date}"
    "&team=&position=&hfRO=&home_road=&hfFlag=&metric_1="
    "&min_pitches=0&min_results=0"
    "&group_by=name&sort_col=pitches&sort_order=desc&min_abs=0"
    "&type=details"
)

STATCAST_FIELDS = [
    "pitcher", "player_name", "batter", "game_pk", "game_date",
    "inning", "inning_topbot",
    "pitch_type", "pitch_name", "release_speed", "release_spin_rate", "spin_axis",
    "release_extension", "effective_speed",
    "zone", "type", "description", "events",
    "launch_speed", "launch_angle", "launch_speed_angle",
    "estimated_woba_using_speedangle", "woba_value",
    "home_team", "away_team", "p_throws", "stand",
    "balls", "strikes", "outs_when_up", "at_bat_number",
    "post_bat_score", "bat_score", "delta_run_exp",
    "pitcher_days_since_prev_game", "n_thruorder_pitcher",
    "arm_angle", "bat_speed", "swing_length",
    "hc_x", "hc_y",
    "release_pos_x", "release_pos_z",
    "on_1b", "on_2b", "on_3b",
]

import requests
_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0"})


def _fetch_day(date_str: str) -> str | None:
    """Fetch one day from Baseball Savant. Returns raw CSV text or None."""
    url = STATCAST_URL.format(date=date_str)
    for attempt in range(5):
        try:
            r = _session.get(url, timeout=60)
            r.raise_for_status()
            if r.text.strip().startswith("<!"):
                raise ValueError("HTML response - rate limited")
            if len(r.text.strip()) < 50:
                return None
            return r.text
        except ValueError:
            time.sleep((2 ** attempt) * 3 + random.uniform(1, 3))
        except Exception:
            time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
    return None


def _build_date_list(start_date, end_date=None,
                     season_start_month: int = 3,
                     season_end_month: int = 11):
    if end_date is None:
        end_date = date.today()
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    dates, cursor = [], start_date
    while cursor <= end_date:
        if season_start_month <= cursor.month <= season_end_month:
            dates.append(cursor)
        cursor += timedelta(days=1)
    return dates


def statcast_historical(
    cache_dir: Path,
    master_path: Path,
    season_start: int = 2019,
    season_start_month: int = 3,
    season_end_month: int = 11,
):
    """Bulk pull season_start-present. Skips cached days. Safe to interrupt."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    cached = {f.replace(".csv", "") for f in os.listdir(cache_dir) if f.endswith(".csv")}
    to_pull = [d for d in all_dates if d.strftime("%Y-%m-%d") not in cached]

    print(f"Statcast historical pull")
    print(f"  Season days : {len(all_dates)}")
    print(f"  Cached      : {len(cached)}")
    print(f"  To fetch    : {len(to_pull)}")

    consecutive_failures = 0
    fetched = 0
    it = tqdm(to_pull, desc="Statcast", unit="day") if HAS_TQDM else to_pull

    for d in it:
        date_str = d.strftime("%Y-%m-%d")
        cache_file = cache_dir / f"{date_str}.csv"
        csv_text = _fetch_day(date_str)

        if csv_text is None:
            pd.DataFrame(columns=STATCAST_FIELDS).to_csv(cache_file, index=False)
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print("10 consecutive failures - stopping.")
                break
            continue

        consecutive_failures = 0
        try:
            df = pd.read_csv(StringIO(csv_text))
        except Exception as e:
            print(f"  Parse failed {date_str}: {e}")
            continue

        if df.empty:
            pd.DataFrame(columns=STATCAST_FIELDS).to_csv(cache_file, index=False)
            continue

        df.to_csv(cache_file, index=False)
        fetched += 1
        time.sleep(random.uniform(0.8, 1.4))

    print(f"Done - {fetched} days fetched")
    statcast_rebuild_master(cache_dir, master_path,
                            season_start=season_start,
                            season_start_month=season_start_month,
                            season_end_month=season_end_month)


def statcast_nightly(cache_dir: Path, master_path: Path, **kwargs):
    """Re-pull yesterday and rebuild master."""
    cache_dir = Path(cache_dir)
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_file = cache_dir / f"{yesterday}.csv"
    if cache_file.exists():
        cache_file.unlink()
    print(f"Statcast nightly: {yesterday}")
    csv_text = _fetch_day(yesterday)
    if csv_text is None:
        print("  No data for yesterday")
        return
    df = pd.read_csv(StringIO(csv_text))
    df.to_csv(cache_file, index=False)
    print(f"  {len(df):,} pitches cached")
    statcast_rebuild_master(cache_dir, master_path, **kwargs)


def statcast_rebuild_master(
    cache_dir: Path,
    master_path: Path,
    season_start: int = 2019,
    season_start_month: int = 3,
    season_end_month: int = 11,
):
    """Rebuild master CSV from all daily cache files."""
    cache_dir = Path(cache_dir)
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    print("Rebuilding Statcast master...")
    frames, skipped_empty, skipped_missing = [], 0, 0

    for d in all_dates:
        f = cache_dir / f"{d.strftime('%Y-%m-%d')}.csv"
        if not f.exists():
            skipped_missing += 1
            continue
        if f.stat().st_size < 10:
            skipped_empty += 1
            continue
        try:
            df = pd.read_csv(f, low_memory=False)
        except Exception:
            skipped_empty += 1
            continue
        if df.empty or len(df.columns) == 0:
            skipped_empty += 1
            continue
        frames.append(df)

    if not frames:
        print("  No usable cache files found")
        return

    print(f"  Files: {len(frames)} with data | {skipped_empty} empty | {skipped_missing} not cached")
    master = pd.concat(frames, ignore_index=True)
    available = [c for c in STATCAST_FIELDS if c in master.columns]
    master = master[available]
    master = master.drop_duplicates(
        subset=[c for c in ["game_pk", "batter", "at_bat_number", "pitch_number"] if c in master.columns]
    )
    master.to_csv(master_path, index=False)
    print(f"  {len(master):,} rows | {master['game_date'].min()} to {master['game_date'].max()}")


def load_statcast(master_path: Path, inning: int | None = None) -> pd.DataFrame:
    """
    Load Statcast master. Optionally filter to a single inning.

    Args:
        master_path: Path to statcast_master.csv
        inning:      If set, filters to rows where inning == this value.
                     Pass inning=1 for NRFI. None = all innings (HR, F5, K).
    """
    df = pd.read_csv(master_path, low_memory=False)
    if inning is not None:
        df = df[pd.to_numeric(df["inning"], errors="coerce") == inning].copy()
    return df

def statcast_backfill_gcs(gcs_bucket: str, gcs_master_key: str, dates: list) -> dict:
    """Backfill Statcast master for a list of date strings (YYYY-MM-DD).
    Loads master once, appends all dates, deduplicates, uploads once.
    Returns dict of {date: pitches_added}.
    """
    from google.cloud import storage
    client  = storage.Client()
    bucket  = client.bucket(gcs_bucket)
    blob    = bucket.blob(gcs_master_key)
    existing = pd.read_csv(blob.open("r"), low_memory=False)
    print(f"  Existing master: {len(existing):,} rows")
    results = {}
    for d in dates:
        print(f"  Fetching {d}...")
        csv_text = _fetch_day(d)
        if csv_text is None:
            print(f"    No data for {d}")
            results[d] = 0
            continue
        new_df = pd.read_csv(StringIO(csv_text))
        if new_df.empty:
            print(f"    Empty response for {d}")
            results[d] = 0
            continue
        available = [c for c in STATCAST_FIELDS if c in new_df.columns]
        new_df = new_df[available]
        print(f"    {d}: {len(new_df):,} pitches fetched")
        results[d] = len(new_df)
        existing = pd.concat([existing, new_df], ignore_index=True)
    dedup_cols = [c for c in ["game_pk", "batter", "at_bat_number", "pitch_number"] if c in existing.columns]
    existing = existing.drop_duplicates(subset=dedup_cols)
    tmp = "/tmp/statcast_master_backfill.csv"
    existing.to_csv(tmp, index=False)
    blob.upload_from_filename(tmp, content_type="text/csv", timeout=600)
    print(f"  Master updated: {len(existing):,} rows")
    return results


def statcast_nightly_gcs(gcs_bucket: str, gcs_master_key: str, **kwargs):
    """Fetch yesterday, append to GCS master. No local cache needed."""
    from google.cloud import storage
    from io import BytesIO

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Statcast nightly (GCS): {yesterday}")

    csv_text = _fetch_day(yesterday)
    if csv_text is None:
        print("  No data for yesterday")
        return

    new_df = pd.read_csv(StringIO(csv_text))
    if new_df.empty:
        print("  Empty response")
        return

    available = [c for c in STATCAST_FIELDS if c in new_df.columns]
    new_df = new_df[available]
    print(f"  Fetched {len(new_df):,} pitches")

    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    blob = bucket.blob(gcs_master_key)

    existing = pd.read_csv(blob.open("r"), low_memory=False)
    print(f"  Existing master: {len(existing):,} rows")

    combined = pd.concat([existing, new_df], ignore_index=True)
    dedup_cols = [c for c in ["game_pk", "batter", "at_bat_number", "pitch_number"] if c in combined.columns]
    combined = combined.drop_duplicates(subset=dedup_cols)

    tmp = "/tmp/statcast_master_new.csv"
    combined.to_csv(tmp, index=False)
    blob.upload_from_filename(tmp, content_type="text/csv", timeout=600)
    print(f"  Master updated: {len(combined):,} rows | through {yesterday}")
