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

# -- Cache dirs ----------------------------------------------------------------
DEFAULT_STATCAST_CACHE = BASE_DATA / "Statcast" / "cache_daily"
DEFAULT_WEATHER_CACHE  = BASE_DATA / "Weather"  / "cache_daily"
DEFAULT_LINEUP_CACHE   = BASE_DATA / "Lineups"  / "cache_daily"
