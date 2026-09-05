import os
from pathlib import Path

# -- GCS (Cloud Run) vs local (Windows) ---------------------------------------
GCS_BUCKET = os.environ.get("MLB_GCS_BUCKET", "") or os.environ.get("GCS_BUCKET", "")
DB_URL = os.environ.get("MLB_DB_URL", "")
BASE_DATA  = Path(os.environ.get("MLB_BASE_DATA", r"C:\Users\lmayn\Downloads\Baseball_Data"))

# -- Shared data paths ---------------------------------------------------------
STATCAST_MASTER  = BASE_DATA / "Statcast" / "statcast_master.csv"
WEATHER_MASTER   = BASE_DATA / "Weather"  / "weather_master.csv"
LINEUPS_MASTER   = BASE_DATA / "Lineups"  / "lineups_master.csv"
UMPIRES_MASTER   = BASE_DATA / "Umpires"  / "umpscorecards_master.csv"
ODDS_DIR         = BASE_DATA / "Odds" / "DraftKings"

# -- Season params -------------------------------------------------------------
SEASON_START_MONTH = 3
SEASON_END_MONTH   = 11
TIMEZONE           = "America/New_York"

# Per-year MLB regular-season boundaries (actual Opening Day -> end of
# regular season). Consolidated 2026-09-04 -- previously hardcoded verbatim
# across 5 of 7 system configs (HR/F5/GAME/BATTER_HITS/BATTER_TB config_*.py).
# NRFI extends this with 2019-2020 (its own historical-odds backtest range)
# and SB subsets it to 2023+ (no usable data before the catcher-identity
# backfill start date) -- both still derive from this dict rather than
# hand-copying it, so a boundary correction here reaches every system.
SEASON_RANGES: dict[int, tuple[str, str]] = {
    2021: ("2021-04-01", "2021-10-03"),
    2022: ("2022-04-07", "2022-10-05"),
    2023: ("2023-03-30", "2023-10-01"),
    2024: ("2024-03-20", "2024-09-29"),
    2025: ("2025-03-18", "2025-09-28"),
    2026: ("2026-03-26", "2026-10-04"),
}

# -- Cache dirs ----------------------------------------------------------------
DEFAULT_STATCAST_CACHE = BASE_DATA / "Statcast" / "cache_daily"
DEFAULT_WEATHER_CACHE  = BASE_DATA / "Weather"  / "cache_daily"
DEFAULT_LINEUP_CACHE   = BASE_DATA / "Lineups"  / "cache_daily"
