from pathlib import Path

# -- Root paths ----------------------------------------------------------------
BASE_DATA = Path(r"C:\Users\lmayn\Downloads\Baseball_Data")

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

# -- Cache dirs (local per-system, not tracked in git) -------------------------
DEFAULT_STATCAST_CACHE = BASE_DATA / "Statcast" / "cache_daily"
DEFAULT_WEATHER_CACHE  = BASE_DATA / "Weather"  / "cache_daily"
DEFAULT_LINEUP_CACHE   = BASE_DATA / "Lineups"  / "cache_daily"
