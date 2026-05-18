"""
mlb_core.data.savant_leaderboards -- Baseball Savant season-level leaderboard pulls.

Six datasets, all fetched directly from Baseball Savant CSV endpoints (no pybaseball).
Each endpoint returns one row per player per season for the given year.

Datasets:
  exit_velocity_barrels  -- batter EV, barrel%, hard-hit%, xwOBA, xBA (2015+)
  expected_statistics    -- batter/pitcher xBA, xSLG, xwOBA, xERA (2015+)
  pitch_arsenals         -- pitcher pitch-type mix, avg velo, avg spin (2015+)
  sprint_speed           -- player sprint speed ft/sec (2015+)
  bat_tracking           -- batter bat speed, swing length, attack angle (2023+)
  batter_arsenal_stats   -- batter outcomes split by pitch type faced (2019+)

Storage pattern (matches all other mlb_core data modules):
  GCS:   gs://{bucket}/Statcast/savant_{dataset}_{year}.csv   (per-year cache)
         gs://{bucket}/Statcast/savant_{dataset}_master.csv   (all years combined)
  Local: BASE_DATA/Statcast/savant_{dataset}_{year}.csv
         BASE_DATA/Statcast/savant_{dataset}_master.csv

Backfill (one-time):
  savant_leaderboard_backfill_gcs(dataset, start_year, end_year)
  -- fetches each year sequentially with sleep between calls
  -- skips years already cached in GCS

Nightly refresh (in-season only):
  savant_leaderboard_nightly_gcs(dataset)
  -- re-fetches current season (year-to-date rolling data)
  -- replaces the current-season cache file and rebuilds master

Called by /refresh-data route in main.py (Loop A, 08:00 UTC).
"""
import io
import logging
import os
import random
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# All leaderboards available from 2015 except where noted
DATASET_START_YEAR = {
    "exit_velocity_barrels": 2015,
    "expected_statistics":   2015,
    "pitch_arsenals":        2015,
    "sprint_speed":          2015,
    "bat_tracking":          2023,   # only available from 2023 (launched mid-2024)
    "batter_arsenal_stats":  2019,   # pitch-type splits for batters
}

# Minimum sample thresholds -- low enough to capture platoon players
# but avoids noise from 1-PA appearances
DATASET_MIN = {
    "exit_velocity_barrels": 25,
    "expected_statistics":   25,
    "pitch_arsenals":        50,    # min pitches of that type
    "sprint_speed":          10,    # min competitive runs
    "bat_tracking":          25,
    "batter_arsenal_stats":  10,    # min PA vs that pitch type
}

# Rate limiting: Baseball Savant allows ~10 req/min comfortably.
# Backfill uses longer sleep to avoid triggering cloudflare blocks.
BACKFILL_SLEEP_MIN = 8.0    # seconds between backfill calls (min)
BACKFILL_SLEEP_MAX = 14.0   # seconds between backfill calls (max)
NIGHTLY_SLEEP      = 3.0    # seconds between nightly calls (only 1-2 calls)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
})


# ── URL builders ──────────────────────────────────────────────────────────

def _url_exit_velocity_barrels(year: int, player_type: str = "batter") -> str:
    # Note: path is statcast_leaderboard (no /leaderboard/ prefix)
    min_bbe = DATASET_MIN["exit_velocity_barrels"]
    return (
        f"https://baseballsavant.mlb.com/statcast_leaderboard"
        f"?year={year}&abs={min_bbe}&player_type={player_type}&csv=true"
    )


def _url_expected_statistics(year: int, player_type: str = "batter") -> str:
    min_pa = DATASET_MIN["expected_statistics"]
    return (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type={player_type}&year={year}&position=&team="
        f"&filterType=pa&min={min_pa}&csv=true"
    )


def _url_pitch_arsenals(year: int) -> str:
    # type=n_ returns pitch usage % -- most useful for feature engineering
    # type=avg_speed and type=avg_spin also available if needed later
    min_p = DATASET_MIN["pitch_arsenals"]
    return (
        f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
        f"?year={year}&min={min_p}&type=n_&hand=&csv=true"
    )


def _url_sprint_speed(year: int) -> str:
    # sprint_speed_leaderboard path (different from /leaderboard/sprint_speed)
    min_run = DATASET_MIN["sprint_speed"]
    return (
        f"https://baseballsavant.mlb.com/sprint_speed_leaderboard"
        f"?year={year}&position=&team=&min={min_run}&csv=true"
    )


def _url_bat_tracking(year: int) -> str:
    # Available 2023+. csv=true works on the /leaderboard/ path for this one.
    min_swings = DATASET_MIN["bat_tracking"]
    return (
        f"https://baseballsavant.mlb.com/leaderboard/bat-tracking"
        f"?year={year}&team=&min={min_swings}&csv=true"
    )


def _url_batter_arsenal_stats(year: int) -> str:
    # Batter outcomes vs each pitch type
    min_pa = DATASET_MIN["batter_arsenal_stats"]
    return (
        f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        f"?type=batter&pitchType=&year={year}&team=&min={min_pa}&csv=1"
    )


_URL_BUILDERS = {
    "exit_velocity_barrels": _url_exit_velocity_barrels,
    "expected_statistics":   _url_expected_statistics,
    "pitch_arsenals":        _url_pitch_arsenals,
    "sprint_speed":          _url_sprint_speed,
    "bat_tracking":          _url_bat_tracking,
    "batter_arsenal_stats":  _url_batter_arsenal_stats,
}


# ── Core fetch ────────────────────────────────────────────────────────────

def _fetch_leaderboard(dataset: str, year: int,
                       max_retries: int = 4) -> pd.DataFrame | None:
    """Fetch one year of one leaderboard from Baseball Savant.

    Returns a DataFrame with a `year` column added, or None on failure.
    Retries with exponential backoff on transient errors.
    """
    builder = _URL_BUILDERS.get(dataset)
    if builder is None:
        raise ValueError(f"Unknown leaderboard dataset: {dataset!r}. "
                         f"Valid: {list(_URL_BUILDERS)}")

    url = builder(year)
    logger.info(f"savant_leaderboards: fetching {dataset} {year} -- {url}")

    for attempt in range(max_retries):
        try:
            resp = _SESSION.get(url, timeout=30)
            resp.raise_for_status()

            # Savant returns HTML on rate-limit or auth failure
            content = resp.text.strip()
            if content.startswith("<!") or content.startswith("<html"):
                logger.warning(
                    f"savant_leaderboards: HTML response for {dataset} {year} "
                    f"(attempt {attempt + 1}) -- likely rate-limited, sleeping"
                )
                time.sleep(30 + attempt * 15)
                continue

            if len(content) < 20:
                logger.info(f"savant_leaderboards: empty response for {dataset} {year}")
                return None

            df = pd.read_csv(io.StringIO(content), low_memory=False)
            if df.empty:
                logger.info(f"savant_leaderboards: 0-row CSV for {dataset} {year}")
                return None

            # Add year column if not present (some endpoints omit it)
            if "year" not in df.columns:
                df.insert(0, "year", year)
            else:
                df["year"] = df["year"].fillna(year)

            logger.info(
                f"savant_leaderboards: {dataset} {year} -> "
                f"{len(df):,} rows, {len(df.columns)} cols"
            )
            return df

        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 403:
                logger.warning(
                    f"savant_leaderboards: 403 for {dataset} {year} "
                    f"(attempt {attempt + 1}) -- sleeping longer"
                )
                time.sleep(45 + attempt * 20)
            else:
                logger.warning(f"savant_leaderboards: HTTP {status} for {dataset} {year}")
                time.sleep((2 ** attempt) * 5)

        except Exception as e:
            logger.warning(
                f"savant_leaderboards: fetch error {dataset} {year} "
                f"(attempt {attempt + 1}): {e}"
            )
            time.sleep((2 ** attempt) * 3 + random.uniform(1, 3))

    logger.error(f"savant_leaderboards: all {max_retries} attempts failed "
                 f"for {dataset} {year}")
    return None


# ── GCS key helpers ───────────────────────────────────────────────────────

def _gcs_year_key(dataset: str, year: int) -> str:
    return f"Statcast/savant_{dataset}_{year}.csv"


def _gcs_master_key(dataset: str) -> str:
    return f"Statcast/savant_{dataset}_master.csv"


def _local_year_path(dataset: str, year: int) -> Path:
    from mlb_core.config import BASE_DATA
    d = BASE_DATA / "Statcast" / "savant_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"savant_{dataset}_{year}.csv"


def _local_master_path(dataset: str) -> Path:
    from mlb_core.config import BASE_DATA
    return BASE_DATA / "Statcast" / f"savant_{dataset}_master.csv"


# ── Master rebuild ────────────────────────────────────────────────────────

def _rebuild_master_gcs(dataset: str, years: list[int]) -> pd.DataFrame:
    """Load all per-year GCS files and write the combined master.

    Skips missing years gracefully. Returns the combined DataFrame.
    """
    from mlb_core.storage import read_csv, write_csv, exists

    frames = []
    for year in sorted(years):
        key = _gcs_year_key(dataset, year)
        if not exists(key):
            continue
        try:
            df = read_csv(key, low_memory=False)
            if not df.empty:
                frames.append(df)
        except Exception as e:
            logger.warning(f"savant_leaderboards: could not load {key}: {e}")

    if not frames:
        logger.warning(f"savant_leaderboards: no data to build master for {dataset}")
        return pd.DataFrame()

    master = pd.concat(frames, ignore_index=True)

    # Dedup on (player_id or last_name+first_name, year) -- column names vary by dataset
    id_cols = _dedup_cols(dataset, master.columns.tolist())
    if id_cols:
        master = master.drop_duplicates(subset=id_cols, keep="last")

    master = master.sort_values(
        ["year"] + [c for c in ["last_name", "player_id"] if c in master.columns]
    ).reset_index(drop=True)

    write_csv(master, _gcs_master_key(dataset))
    logger.info(
        f"savant_leaderboards: master {dataset} rebuilt -- "
        f"{len(master):,} rows across {master['year'].nunique()} seasons"
    )
    return master


def _dedup_cols(dataset: str, columns: list[str]) -> list[str]:
    """Return the best deduplication key columns available for this dataset."""
    candidates = [
        ["year", "player_id"],
        ["year", "mlbam_id"],
        ["year", "last_name", "first_name"],
        ["year", "last_name"],
    ]
    for candidate in candidates:
        if all(c in columns for c in candidate):
            return candidate
    return ["year"] if "year" in columns else []


# ── Public API: backfill ──────────────────────────────────────────────────

def savant_leaderboard_backfill_gcs(
    dataset: str,
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill one leaderboard dataset from start_year to end_year.

    Each year is fetched sequentially with a random sleep between calls
    to avoid rate limiting. Already-cached years are skipped unless
    force=True.

    Args:
        dataset:    One of the keys in DATASET_START_YEAR.
        start_year: First year to fetch. Defaults to DATASET_START_YEAR[dataset].
        end_year:   Last year to fetch. Defaults to current season.
        force:      If True, re-fetch even if a cache file exists in GCS.

    Returns:
        Dict of {year: rows_fetched} for each year attempted.
    """
    from mlb_core.storage import write_csv, exists

    if dataset not in DATASET_START_YEAR:
        raise ValueError(f"Unknown dataset {dataset!r}. Valid: {list(DATASET_START_YEAR)}")

    default_start = DATASET_START_YEAR[dataset]
    start_year = max(start_year or default_start, default_start)
    end_year   = end_year or date.today().year

    years = list(range(start_year, end_year + 1))
    logger.info(
        f"savant_leaderboards: backfill {dataset} "
        f"{start_year}-{end_year} ({len(years)} seasons)"
    )

    results: dict[int, int] = {}
    fetched_years: list[int] = []

    for i, year in enumerate(years):
        gcs_key = _gcs_year_key(dataset, year)

        if not force and exists(gcs_key):
            logger.info(
                f"savant_leaderboards: {dataset} {year} already cached -- skipping "
                f"(use force=True to re-fetch)"
            )
            results[year] = -1   # sentinel: skipped
            fetched_years.append(year)
            continue

        df = _fetch_leaderboard(dataset, year)

        if df is None or df.empty:
            logger.warning(f"savant_leaderboards: no data for {dataset} {year}")
            results[year] = 0
        else:
            write_csv(df, gcs_key)
            results[year] = len(df)
            fetched_years.append(year)
            logger.info(
                f"savant_leaderboards: cached {dataset} {year} "
                f"({len(df):,} rows) -> {gcs_key}"
            )

        # Sleep between calls -- skip sleep after last year
        if i < len(years) - 1:
            sleep_secs = random.uniform(BACKFILL_SLEEP_MIN, BACKFILL_SLEEP_MAX)
            logger.debug(f"savant_leaderboards: sleeping {sleep_secs:.1f}s")
            time.sleep(sleep_secs)

    # Rebuild master from all cached years
    all_cached_years = list(range(default_start, end_year + 1))
    _rebuild_master_gcs(dataset, all_cached_years)

    total_new = sum(v for v in results.values() if v > 0)
    skipped   = sum(1 for v in results.values() if v == -1)
    logger.info(
        f"savant_leaderboards: backfill {dataset} complete -- "
        f"{total_new:,} rows fetched, {skipped} years skipped (cached)"
    )
    return results


def savant_leaderboards_backfill_all_gcs(
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill ALL six datasets.

    Runs each dataset's backfill in sequence with an extra pause between
    datasets to be polite to Baseball Savant's servers.

    Returns dict of {dataset: {year: rows}} for all datasets.
    """
    all_results = {}
    datasets = list(DATASET_START_YEAR.keys())

    for i, dataset in enumerate(datasets):
        logger.info(
            f"savant_leaderboards: starting backfill dataset "
            f"{i + 1}/{len(datasets)}: {dataset}"
        )
        try:
            results = savant_leaderboard_backfill_gcs(
                dataset, start_year=start_year, end_year=end_year, force=force
            )
            all_results[dataset] = results
        except Exception as e:
            logger.error(f"savant_leaderboards: backfill failed for {dataset}: {e}")
            all_results[dataset] = {"error": str(e)}

        # Extra inter-dataset pause (except after last)
        if i < len(datasets) - 1:
            pause = random.uniform(20.0, 35.0)
            logger.info(
                f"savant_leaderboards: inter-dataset pause {pause:.1f}s "
                f"before {datasets[i + 1]}"
            )
            time.sleep(pause)

    return all_results


# ── Public API: nightly refresh ───────────────────────────────────────────

def savant_leaderboard_nightly_gcs(dataset: str) -> dict:
    """Re-fetch the current season's leaderboard and rebuild master.

    Leaderboards are cumulative season-to-date, so we replace the
    current-year cache file on every nightly run during the season.

    Called by /refresh-data route (Loop A, 08:00 UTC) via
    savant_leaderboards_nightly_all_gcs().

    Returns dict with status and row count.
    """
    from mlb_core.storage import write_csv

    year = date.today().year

    # Only run during MLB season (March-November)
    current_month = date.today().month
    if not (3 <= current_month <= 11):
        logger.info(
            f"savant_leaderboards: skipping nightly {dataset} -- "
            f"off-season (month={current_month})"
        )
        return {"status": "skipped", "reason": "off_season"}

    # bat_tracking only available from 2023
    if year < DATASET_START_YEAR.get(dataset, 2015):
        logger.info(
            f"savant_leaderboards: skipping nightly {dataset} -- "
            f"year {year} before dataset start {DATASET_START_YEAR[dataset]}"
        )
        return {"status": "skipped", "reason": "before_dataset_start"}

    logger.info(f"savant_leaderboards: nightly refresh {dataset} {year}")

    df = _fetch_leaderboard(dataset, year)
    if df is None or df.empty:
        logger.warning(
            f"savant_leaderboards: nightly {dataset} {year} returned no data"
        )
        return {"status": "error", "rows": 0}

    gcs_key = _gcs_year_key(dataset, year)
    write_csv(df, gcs_key)
    logger.info(
        f"savant_leaderboards: nightly {dataset} {year} -> "
        f"{len(df):,} rows -> {gcs_key}"
    )

    # Rebuild master to include updated current season
    default_start = DATASET_START_YEAR[dataset]
    all_years     = list(range(default_start, year + 1))
    _rebuild_master_gcs(dataset, all_years)

    time.sleep(NIGHTLY_SLEEP)

    return {"status": "ok", "year": year, "rows": len(df)}


def savant_leaderboards_nightly_all_gcs() -> dict:
    """Run nightly refresh for all six datasets.

    Called once daily from /refresh-data. Each call fetches the current
    season's rolling data and rebuilds that dataset's master.

    Returns dict of {dataset: result} for monitoring.
    """
    results = {}
    datasets = list(DATASET_START_YEAR.keys())

    for dataset in datasets:
        try:
            result = savant_leaderboard_nightly_gcs(dataset)
            results[dataset] = result
        except Exception as e:
            logger.error(
                f"savant_leaderboards: nightly failed for {dataset}: {e}"
            )
            results[dataset] = {"status": "error", "error": str(e)}

        # Brief pause between datasets even during nightly
        time.sleep(random.uniform(4.0, 8.0))

    total_rows = sum(
        r.get("rows", 0) for r in results.values()
        if isinstance(r, dict) and r.get("status") == "ok"
    )
    logger.info(
        f"savant_leaderboards: nightly all complete -- "
        f"{total_rows:,} total rows refreshed"
    )
    return results


# ── Public API: load helpers (for feature builders) ───────────────────────

def load_savant_leaderboard(dataset: str,
                             years: list[int] | None = None) -> pd.DataFrame:
    """Load a leaderboard master (or subset of years) from GCS or local.

    Args:
        dataset: One of the keys in DATASET_START_YEAR.
        years:   If provided, filters the master to only these seasons.
                 If None, loads the full master.

    Returns:
        DataFrame. Empty DataFrame if master not found.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, exists

    if GCS_BUCKET:
        key = _gcs_master_key(dataset)
        if not exists(key):
            logger.warning(f"savant_leaderboards: master not found: {key}")
            return pd.DataFrame()
        try:
            df = read_csv(key, low_memory=False)
        except Exception as e:
            logger.error(f"savant_leaderboards: load failed {key}: {e}")
            return pd.DataFrame()
    else:
        local = _local_master_path(dataset)
        if not local.exists():
            logger.warning(f"savant_leaderboards: master not found: {local}")
            return pd.DataFrame()
        df = pd.read_csv(local, low_memory=False)

    if years is not None and "year" in df.columns:
        df = df[df["year"].isin(years)].copy()

    return df


# ── Local-mode helpers (for notebook/Windows development) ─────────────────

def savant_leaderboard_backfill_local(
    dataset: str,
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Local equivalent of backfill_gcs -- writes to BASE_DATA/Statcast/."""
    if dataset not in DATASET_START_YEAR:
        raise ValueError(f"Unknown dataset: {dataset!r}")

    default_start = DATASET_START_YEAR[dataset]
    start_year = max(start_year or default_start, default_start)
    end_year   = end_year or date.today().year

    years   = list(range(start_year, end_year + 1))
    results: dict[int, int] = {}

    for i, year in enumerate(years):
        local_path = _local_year_path(dataset, year)

        if not force and local_path.exists():
            logger.info(f"savant_leaderboards: {dataset} {year} cached locally -- skipping")
            results[year] = -1
            continue

        df = _fetch_leaderboard(dataset, year)
        if df is None or df.empty:
            results[year] = 0
        else:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(local_path, index=False)
            results[year] = len(df)
            logger.info(f"savant_leaderboards: {dataset} {year} -> {local_path}")

        if i < len(years) - 1:
            time.sleep(random.uniform(BACKFILL_SLEEP_MIN, BACKFILL_SLEEP_MAX))

    # Rebuild local master
    frames = []
    for year in range(default_start, end_year + 1):
        p = _local_year_path(dataset, year)
        if p.exists():
            try:
                frames.append(pd.read_csv(p, low_memory=False))
            except Exception:
                pass

    if frames:
        master = pd.concat(frames, ignore_index=True)
        id_cols = _dedup_cols(dataset, master.columns.tolist())
        if id_cols:
            master = master.drop_duplicates(subset=id_cols, keep="last")
        master_path = _local_master_path(dataset)
        master.to_csv(master_path, index=False)
        logger.info(f"savant_leaderboards: local master {dataset} -> {master_path} "
                    f"({len(master):,} rows)")

    return results
