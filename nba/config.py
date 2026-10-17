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


# -- Kaggle (eoinamoore historical stats.nba.com dataset) ----------------------
KAGGLE_DATASET = "eoinamoore/historical-nba-data-and-player-box-scores"
STATS_NBA_PREFIX = f"{NBA_PREFIX}/stats_nba"


def stats_nba_raw_key(relpath: str) -> str:
    return f"{STATS_NBA_PREFIX}/raw/{relpath}"


STATS_NBA_INGEST_SENTINEL = f"{STATS_NBA_PREFIX}/last_ingest.json"

# -- The Odds API (live odds) --------------------------------------------------
# Ported from the nba-parlay-generator reference. NOTE: api.the-odds-api.com is
# blocked on the office LAN (gambling category) -- live calls only work from
# Cloud Run. Key from Secret Manager (odds-api-key) -> env THE_ODDS_API_KEY.
# Free tier is 500 requests/month: player props cost 1 credit PER EVENT, game
# lines (h2h/spreads/totals) cost 1 credit for the WHOLE slate. Be frugal.
ODDS_API_BASE = os.environ.get("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
ODDS_SPORT_KEY = "basketball_nba"
ODDS_REGION = "us"

# us-region books we track; order = preference for tie-breaks in best-book.
ODDS_DEFAULT_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars"]

# player-prop market api-key -> short name
ODDS_PROP_MARKETS = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes",
    "player_points_rebounds_assists": "pra",
}
ODDS_GAME_MARKETS = ["h2h", "spreads", "totals"]

ODDS_PREFIX = f"{NBA_PREFIX}/odds"
ODDS_LATEST = f"{ODDS_PREFIX}/latest.json"


def odds_raw_key(date: str, kind: str, hhmm: str) -> str:
    return f"{ODDS_PREFIX}/raw/{date}/{kind}_{hhmm}.json"


def odds_csv_key(date: str, kind: str, hhmm: str) -> str:
    return f"{ODDS_PREFIX}/{date}/{kind}_{hhmm}.csv"


# -- ParlayAPI (chosen live odds provider; sport-agnostic accumulator) ----------
# parlay-api.com -- 1000 credits/mo free (60 req/sec); $5 = 20k. Same response
# shape as The Odds API EXCEPT prop outcome name is "Over <player>" with
# description="<player>". Billing: props 1 credit per (event x market); whole-slate
# game lines 1 credit per market. Request oddsFormat=american. Pinnacle included.
# Key from Secret Manager (parlay-api-key) -> env PARLAY_API_KEY. Reachable from
# Cloud Run (office LAN blocks gambling category).
# NOTE: ParlayAPI has NO historical player props -- accumulator banks them forward.
PARLAY_API_BASE = os.environ.get("PARLAY_API_BASE", "https://parlay-api.com/v1")
PARLAY_REGION = "us"

# default prop markets per sport (override via CLI).
# NOTE: ParlayAPI uses its own `player_*` market scheme; flatten_parlay_props only keeps
# keys starting with `player_`. MLB keys verified against a live payload 2026-06-16.
# `outs` arrives under TWO keys (player_pitcher_outs / player_pitching_outs), both
# collapsed to the short name 'outs' in parlay_extract._market_short.
PARLAY_PROP_MARKETS = {
    # NBA/NHL "core" sets verified against the live ParlayAPI /v1/markets catalog 2026-10-14. Each market
    # costs 1 credit per (event x market) whether or not any book returns it, so this list is a budget
    # decision, not just a wishlist. Alternate ladders (`*_alternate`) are deliberately NOT here -- they are
    # one-sided prices and ~double the cost; add them via PARLAY_PROP_MARKETS_ALT once the credit plan allows.
    "basketball_nba": ["player_points", "player_rebounds", "player_assists", "player_threes",
                       "player_points_rebounds_assists"],
    # NHL: goalscorer ("player_goals" also carries TEAM-total junk rows -- filtered in parlay_extract),
    # shots on goal, points/assists, goalie saves (not posted until goalies confirm -- often empty early in the
    # day, but still charged), power-play points, blocked shots.
    "icehockey_nhl": ["player_goals", "player_shots_on_goal", "player_points", "player_assists",
                      "player_total_saves"],
    "baseball_mlb": [
        # batter props (home_runs is a yes/no market: outcomes "Yes"/"No" @ 0.5)
        "player_hits", "player_total_bases", "player_home_runs",
        # pitcher props -- "player_outs" is the live outs-recorded key (verified
        # payload 2026-06-29); player_pitcher_outs/_pitching_outs are NOT returned.
        "player_strikeouts", "player_earned_runs", "player_outs",
        # stolen bases -- verified live 2026-08-20 (real event probe, 11
        # catalog books, 5-6 live per game; some books e.g. caesars/novig
        # quote a one-sided "Yes" only rather than a real Over/Under -- see
        # sgo.py's extract_stolen_base_odds() docstring).
        "player_stolen_bases",
    ],
}
PARLAY_GAME_MARKETS = ["h2h", "spreads", "totals"]

# Optional second tier, enabled with `--tier full` on the accumulator (more credits per event).
PARLAY_PROP_MARKETS_ALT = {
    "basketball_nba": ["player_steals", "player_blocks", "player_turnovers", "player_points_rebounds",
                       "player_points_assists", "player_rebounds_assists", "player_points_alternate",
                       "player_rebounds_alternate", "player_assists_alternate", "player_threes_alternate"],
    "icehockey_nhl": ["player_power_play_points", "player_blocked_shots", "player_hits"],
}

# DFS / pick'em apps quote ~-112/-112 on every line: they carry the NUMBER, not a price. They are kept in the
# raw payload (training data) but excluded from best-book CSVs and from any fair-probability or alert logic.
DFS_BOOKS = frozenset({"sleeper", "underdog", "prizepicks", "dabble", "betr", "fliff"})

# -- Per-sport monthly ParlayAPI credit budgets (the shared pool is ONE plan; see nba/odds/credit_ledger.py) --
# Defaults assume the 20k/mo plan that is live as of 2026-10-14 (15.2k left that day, MLB postseason + the NFL
# sweep still spending). Override per sport with ODDS_BANK_BUDGET_<SPORTKEY> once the 100k plan is applied.
ODDS_BANK_DEFAULT_BUDGET = {
    "icehockey_nhl": 3000,
    "basketball_nba": 3000,
}
# Never spend the shared pool below this many remaining credits (read from x-requests-remaining).
PARLAY_RESERVE_FLOOR = int(os.environ.get("PARLAY_RESERVE_FLOOR", "2500"))

# accumulated-odds lake (sport-namespaced; multi-sport)
ODDSACCUM_PREFIX = "OddsAccum"


def oddsaccum_raw_key(sport: str, date: str, kind: str, hhmm: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/raw/{date}/{kind}_{hhmm}.json"


def oddsaccum_csv_key(sport: str, date: str, kind: str, hhmm: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/{date}/{kind}_{hhmm}.csv"


def oddsaccum_latest_key(sport: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/latest.json"


def oddsaccum_latest_kind_key(sport: str, kind: str) -> str:
    """Per-kind pointer. latest.json is shared by props AND game_lines runs, so whichever ran last wins -- a consumer
    that wants the newest PROPS snapshot (the alerts sweep) must not read it."""
    return f"{ODDSACCUM_PREFIX}/{sport}/latest_{kind}.json"


GAMES_MASTER = f"{NBA_PREFIX}/games_master.csv"
TEAM_BOX_MASTER = f"{NBA_PREFIX}/team_boxscores_master.csv"
PLAYER_BOX_MASTER = f"{NBA_PREFIX}/player_boxscores_master.csv"
LAST_REFRESH = f"{NBA_PREFIX}/last_refresh.json"
