"""NHL package configuration: official NHL web API endpoints, GCS keys, team vocabularies.

Source: the official, unauthenticated NHL web API (https://api-web.nhle.com/v1). It is undocumented-but-stable,
widely used, and returned 200 from a developer laptop on 2026-10-14 for schedule / score / boxscore / landing /
play-by-play / roster / standings. SportsBlaze (used for NBA) has /teams/nhl and /players/nhl but 404s on
/seasons, /schedule and /boxscores for NHL (probed 2026-10-14), so it cannot be the NHL box-score backbone.

All NHL data sits under the NHL/ prefix of the shared bucket so it never collides with MLB/NBA.
"""
import os
import unicodedata

API_BASE = os.environ.get("NHL_API_BASE", "https://api-web.nhle.com/v1")
REQUEST_DELAY_SEC = float(os.environ.get("NHL_REQUEST_DELAY", "0.25"))
USER_AGENT = "Mozilla/5.0 (compatible; beezy-fyi-data/1.0)"
TIMEZONE = "America/New_York"

NHL_PREFIX = "NHL"

# game types in the feed: 1 = preseason, 2 = regular season, 3 = playoffs (4 = all-star / other, ignored)
GAME_TYPES = (1, 2, 3)
FINAL_STATES = frozenset({"OFF", "FINAL"})

# Season window: preseason starts mid/late Sept, Cup final ends by late June. Iterating Sept 1 -> Jul 15 covers it.
SEASON_START_MONTH, SEASON_START_DAY = 9, 1
SEASON_END_MONTH, SEASON_END_DAY = 7, 15
# Seasons are identified by their START year (2025 == 2025-26 == API season id 20252026).
FIRST_BACKFILL_SEASON = 2019
BACKFILL_SEASONS = list(range(2019, 2026))


def season_id(start_year: int) -> int:
    return int(f"{start_year}{start_year + 1}")


# -- GCS keys ---------------------------------------------------------------------------------------------------------
def raw_boxscore_key(game_id) -> str:
    return f"{NHL_PREFIX}/raw/boxscores/{int(game_id)}.json"


def raw_schedule_key(day: str) -> str:
    return f"{NHL_PREFIX}/raw/schedule/{day}.json"


GAMES_MASTER = f"{NHL_PREFIX}/games_master.csv"
TEAM_BOX_MASTER = f"{NHL_PREFIX}/team_boxscores_master.csv"
SKATER_BOX_MASTER = f"{NHL_PREFIX}/skater_boxscores_master.csv"
GOALIE_BOX_MASTER = f"{NHL_PREFIX}/goalie_boxscores_master.csv"
PLAYERS_MASTER = f"{NHL_PREFIX}/players_master.csv"          # roster crosswalk (id <-> full name <-> headshot)
TEAMS_MASTER = f"{NHL_PREFIX}/teams_master.csv"              # abbrev/name/logo vocabulary
LAST_REFRESH = f"{NHL_PREFIX}/last_refresh.json"

MASTER_KEYS = {
    GAMES_MASTER: ["game_id"],
    TEAM_BOX_MASTER: ["game_id", "team_id"],
    SKATER_BOX_MASTER: ["game_id", "player_id"],
    GOALIE_BOX_MASTER: ["game_id", "player_id"],
    PLAYERS_MASTER: ["player_id"],
    TEAMS_MASTER: ["abbrev"],
}

# -- Teams: ONE table bridging every vocabulary we meet --------------------------------------------------------------
# (nhl_api_abbrev, full name as ParlayAPI/books write it, Kalshi code, ESPN abbrev).
# nhl_api_abbrev  : what api-web.nhle.com returns (LAK/NJD/SJS/TBL are the API's tricodes).
# kalshi          : ev.sports.nhl.CODES (LA/NJ/SJ/TB...), the mapper's vocabulary.
# Verified against /standings/now (32 teams) in tests/test_nhl_data.py::test_team_table_matches_standings.
TEAMS = [
    ("ANA", "Anaheim Ducks", "ANA"), ("BOS", "Boston Bruins", "BOS"), ("BUF", "Buffalo Sabres", "BUF"),
    ("CGY", "Calgary Flames", "CGY"), ("CAR", "Carolina Hurricanes", "CAR"), ("CHI", "Chicago Blackhawks", "CHI"),
    ("COL", "Colorado Avalanche", "COL"), ("CBJ", "Columbus Blue Jackets", "CBJ"), ("DAL", "Dallas Stars", "DAL"),
    ("DET", "Detroit Red Wings", "DET"), ("EDM", "Edmonton Oilers", "EDM"), ("FLA", "Florida Panthers", "FLA"),
    ("LAK", "Los Angeles Kings", "LA"), ("MIN", "Minnesota Wild", "MIN"), ("MTL", "Montreal Canadiens", "MTL"),
    ("NSH", "Nashville Predators", "NSH"), ("NJD", "New Jersey Devils", "NJ"), ("NYI", "New York Islanders", "NYI"),
    ("NYR", "New York Rangers", "NYR"), ("OTT", "Ottawa Senators", "OTT"), ("PHI", "Philadelphia Flyers", "PHI"),
    ("PIT", "Pittsburgh Penguins", "PIT"), ("SEA", "Seattle Kraken", "SEA"), ("SJS", "San Jose Sharks", "SJ"),
    ("STL", "St. Louis Blues", "STL"), ("TBL", "Tampa Bay Lightning", "TB"), ("TOR", "Toronto Maple Leafs", "TOR"),
    ("UTA", "Utah Mammoth", "UTA"), ("VAN", "Vancouver Canucks", "VAN"), ("VGK", "Vegas Golden Knights", "VGK"),
    ("WSH", "Washington Capitals", "WSH"), ("WPG", "Winnipeg Jets", "WPG"),
]


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


NAME_TO_ABBREV = {_fold(n): a for a, n, _ in TEAMS}
KALSHI_OF = {a: k for a, _, k in TEAMS}
ABBREV_OF_KALSHI = {k: a for a, _, k in TEAMS}


def team_abbrev(name_or_abbrev: str):
    """'Montreal Canadiens' (accented forms too) / 'MTL' / 'LA' (Kalshi) -> NHL-API abbrev, or None."""
    s = (name_or_abbrev or "").strip()
    if not s:
        return None
    up = s.upper()
    if up in KALSHI_OF:
        return up
    if up in ABBREV_OF_KALSHI:
        return ABBREV_OF_KALSHI[up]
    return NAME_TO_ABBREV.get(_fold(s))
