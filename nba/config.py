"""NBA package configuration: SportsBlaze endpoints, GCS keys, season window.

GCS keys are relative to the shared bucket (resolved by mlb_core.storage from
MLB_GCS_BUCKET / GCS_BUCKET). All NBA data sits under the NBA/ prefix so it
never collides with the MLB data lake.
"""
import os

# -- SportsBlaze API (no auth) -------------------------------------------------
SB_BASE = os.environ.get("NBA_SB_BASE", "https://cache.sportsblaze.com")
LEAGUE = "nba"

# -- GCS prefix ----------------------------------------------------------------
NBA_PREFIX = "NBA"

# -- Season window -------------------------------------------------------------
# NBA season runs preseason (early Oct) through the Finals (mid-late June).
# Iterate Oct 1 (start year) -> Jul 1 (next year) to capture every game type.
SEASON_START_MONTH = 10
SEASON_START_DAY = 1
SEASON_END_MONTH = 7
SEASON_END_DAY = 1
TIMEZONE = "America/New_York"

# season.type values seen in the feed (for reference / validation)
SEASON_TYPES = ("Preseason", "Regular Season", "Playoffs")

# -- Seasons (start year) ------------------------------------------------------
# 2018-19 .. 2025-26 confirmed available from /seasons/nba.
AVAILABLE_SEASONS = list(range(2018, 2026))
# Default backfill target: 7 seasons (2019-20 .. 2025-26).
BACKFILL_SEASONS = list(range(2019, 2026))

# -- Politeness ----------------------------------------------------------------
# No documented rate limit; throttle anyway. Override with NBA_REQUEST_DELAY.
REQUEST_DELAY_SEC = float(os.environ.get("NBA_REQUEST_DELAY", "0.4"))

# -- The 19 box-score stat fields (identical for team and player totals) -------
STAT_FIELDS = [
    "points", "minutes", "plus_minus",
    "field_goals_made", "field_goals_attempted",
    "two_points_made", "two_points_attempted",
    "three_points_made", "three_points_attempted",
    "free_throws_made", "free_throws_attempted",
    "rebounds", "rebounds_offensive", "rebounds_defensive",
    "assists", "steals", "blocks", "turnovers", "fouls",
]


# -- GCS key helpers -----------------------------------------------------------
def raw_seasons_key() -> str:
    return f"{NBA_PREFIX}/raw/seasons.json"


def raw_teams_key() -> str:
    return f"{NBA_PREFIX}/raw/teams.json"


def raw_players_key() -> str:
    return f"{NBA_PREFIX}/raw/players.json"


def raw_schedule_key(year: int) -> str:
    return f"{NBA_PREFIX}/raw/schedule_{year}.json"


def raw_boxscore_key(date: str) -> str:
    return f"{NBA_PREFIX}/raw/boxscores/{date}.json"


GAMES_MASTER = f"{NBA_PREFIX}/games_master.csv"
TEAM_BOX_MASTER = f"{NBA_PREFIX}/team_boxscores_master.csv"
PLAYER_BOX_MASTER = f"{NBA_PREFIX}/player_boxscores_master.csv"
LAST_REFRESH = f"{NBA_PREFIX}/last_refresh.json"
