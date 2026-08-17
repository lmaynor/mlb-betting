"""
Shared MLB umpire scorecard pull for all systems.

Data source:
  https://umpscorecards.com/api/games?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&seasonType=R
  Returns a JSON object with a "rows" list. Each row is one game with the
  game's home plate umpire, called-pitch accuracy, consistency, run impact,
  and many other per-game metrics.

NRFI feature engineering only needs three raw columns from this feed:
  overall_accuracy, consistency, total_run_impact
The rolling L30 aggregations live in runners/build_nrfi_features.py.

We keep all columns when writing the master so other systems can use them
later without re-fetching.
"""
import requests
import pandas as pd
from datetime import date, timedelta

_session = requests.Session()
_session.headers.update({
    "Accept":     "application/json, text/plain, */*",
    "User-Agent": "mlb-betting/1.0",
})

UMP_API_URL = "https://umpscorecards.com/api/games"


def umpires_fetch_range(start_date: str, end_date: str,
                        season_type: str = "R") -> pd.DataFrame:
    """Fetch umpire scorecards for [start_date, end_date] inclusive.

    Args:
        start_date: 'YYYY-MM-DD' string.
        end_date:   'YYYY-MM-DD' string.
        season_type: 'R' regular | 'S' spring | 'P' postseason. Default 'R'.

    Returns:
        DataFrame with all raw columns from the API. Empty if request fails
        or returns no rows.
    """
    params = {
        "startDate":  start_date,
        "endDate":    end_date,
        "seasonType": season_type,
    }
    try:
        r = _session.get(UMP_API_URL, params=params, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"  Umpires fetch failed {start_date}–{end_date}: {e}")
        return pd.DataFrame()

    rows = r.json().get("rows", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def umpires_backfill_gcs(gcs_bucket: str, gcs_master_key: str,
                          start_date: str, end_date: str,
                          season_type: str = "R") -> pd.DataFrame:
    """Fetch a date range, write/append to GCS master, return combined frame.

    Use this for the initial bootstrap (e.g. start_date='2021-04-01' to
    today). For daily updates, use umpires_nightly_gcs() instead.
    """
    from mlb_core import storage as _st  # twin-aware master IO

    print(f"Umpires backfill (GCS): {start_date} → {end_date}")
    new_df = umpires_fetch_range(start_date, end_date, season_type=season_type)
    if new_df.empty:
        print("  No rows returned")
        return pd.DataFrame()
    print(f"  Fetched {len(new_df):,} rows")

    if _st.exists(gcs_master_key):
        existing = _st.read_csv(gcs_master_key, low_memory=False)
        print(f"  Existing master: {len(existing):,} rows")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        print("  No existing master - creating new")
        combined = new_df

    combined = combined.drop_duplicates(subset=["game_pk"], keep="last")

    _st.write_csv(combined, gcs_master_key)
    print(f"  Master updated: {len(combined):,} rows")
    return combined


def umpires_nightly_gcs(gcs_bucket: str, gcs_master_key: str,
                         season_type: str = "R", **kwargs):
    """Fetch yesterday's umpire scorecards, append to GCS master."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    umpires_backfill_gcs(
        gcs_bucket=gcs_bucket,
        gcs_master_key=gcs_master_key,
        start_date=yesterday,
        end_date=yesterday,
        season_type=season_type,
    )
