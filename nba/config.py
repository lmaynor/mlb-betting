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
# parlay-api.com -- 1000 credits/mo free (60 req/sec); the production plan is 100k/mo (see the budgets below). Same response
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

# -- ParlayAPI credit budgets: ONE shared pool, ONE place -------------------------------------------------------------------
# The plan is 100,000 credits / month (confirmed applied 2026-11). EVERY spender draws from that single pool, so every
# budget lives here and tests/test_parlay_budget.py pins the total:
#   per-sport accumulator ledgers (nba.odds.credit_ledger) ........ ODDS_BANK_DEFAULT_BUDGET
#   MLB snapshot job (mlb.runners.snapshot_odds) ................... MLB_SNAPSHOT_CEILING
#   NFL props sweep (ev.sweep_nfl_props) ........................... NFL_SWEEP_MONTHLY_BUDGET, metered by the same ledger under
#                                                                     NFL_SWEEP_LEDGER_SPORT (cumulative cap, no linear pace: NFL spend is
#                                                                     Thu/Sun/Mon-shaped); --max-credits still caps ONE firing
#   historical pulls (nba.odds.parlay_historical) .................. PARLAY_HISTORICAL_MONTHLY_BUDGET (planning figure)
# Owner decision 2026-11-01 (rebalanced same day after the NFL sweep went to 10 markets/event: NBA/NHL were pacing to ~20k each): NBA 35k, NHL 35k, NFL sweep 29k = 99k committed to the three live winter sports, plus 500 kept
# for testing (historical pulls; owner decision 2026-11-01).
# Rebalance 2026-11-03 (soccer vertical): EPL + UCL GAME-LINES banking added at 600/mo per league -- game_lines costs
# 3 credits per checkpoint (h2h/spreads/totals) x 6 checkpoints/day, a measured max of ~558/mo, so 600 is the honest
# size, not a round guess. Funded by trimming the NBA and NHL accumulator caps 35k -> 34.4k each (both pace ~20k
# against the cap), so the committed total below is unchanged. Soccer props are Phase 2 and have NO budget here.
# That is 99.5k, and the PARLAY_RESERVE_FLOOR (500) is the last 0.5k. The MLB snapshot ceiling is NOT in the winter total: MLB is
# paused for the offseason (deploy/pause_mlb_offseason.sh, resumes Mar 20). When it resumes, NFL is over (its 29k is free) and
# NBA + NHL + MLB = 89.5k fits, but check the ledgers first: re-balance with ODDS_BANK_BUDGET_<SPORTKEY> BEFORE running resume_mlb_season.sh.
# Change a number HERE and the shell script's copies fail the test until they match.
# Override a sport's ledger budget with ODDS_BANK_BUDGET_<SPORTKEY>.
PARLAY_MONTHLY_QUOTA = 100_000
MLB_SNAPSHOT_CEILING = 19_500
NFL_SWEEP_MONTHLY_BUDGET = 29_000
NFL_SWEEP_LEDGER_SPORT = "americanfootball_nfl_sweep"     # OddsAccum/{this}/_credits/{month}.json
PARLAY_HISTORICAL_MONTHLY_BUDGET = 500
ODDS_BANK_DEFAULT_BUDGET = {
    "icehockey_nhl": 34400,
    "basketball_nba": 34400,
    "americanfootball_nfl": 0,          # accumulator ledger only (no NFL banker job exists); manual probes use the 500 test budget
    "soccer_epl": 600,                  # game_lines banking only (soccer vertical, 2026-11-03; see the rebalance note above)
    "soccer_uefa_champs_league": 600,   # game_lines banking only (soccer vertical, 2026-11-03)
}
# Never spend the shared pool below this many remaining credits (read from x-requests-remaining). ONE floor for the ledger,
# the MLB snapshot job and the historical puller (was 500 / none / 2500).
PARLAY_RESERVE_FLOOR = int(os.environ.get("PARLAY_RESERVE_FLOOR", "500"))


def parlay_budget_total(mlb_active: bool = False) -> int:
    """Everything that may be spent in one month across all sports and tools (excludes the reserve floor).

    mlb_active=False is the winter profile (MLB snapshot paused); True adds the MLB ceiling."""
    total = (sum(ODDS_BANK_DEFAULT_BUDGET.values()) + NFL_SWEEP_MONTHLY_BUDGET + PARLAY_HISTORICAL_MONTHLY_BUDGET)
    return total + (MLB_SNAPSHOT_CEILING if mlb_active else 0)


# accumulated-odds lake (sport-namespaced; multi-sport)
ODDSACCUM_PREFIX = "OddsAccum"


def oddsaccum_raw_key(sport: str, date: str, kind: str, hhmm: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/raw/{date}/{kind}_{hhmm}.json"


def oddsaccum_csv_key(sport: str, date: str, kind: str, hhmm: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/{date}/{kind}_{hhmm}.csv"


# -- First-scorer micro markets (first basket / first TD; spike 2026-11-11) -----
# Forward-only banking: ParlayAPI is the only carrier and keeps no historical props, so these markets
# are banked by nba.odds.first_scorer from the day the job is provisioned. NOT added to
# PARLAY_PROP_MARKETS / _ALT: the props accumulator's best-book flattening would destroy the per-book
# outcome sets this market is made of, and its spend is budgeted separately at provisioning time
# (carved from the existing basketball_nba cap; the pinned total in tests/test_parlay_budget.py is
# unchanged). NFL first-TD uses the accumulator ledger for americanfootball_nfl, whose default budget
# is 0, so its job MUST set ODDS_BANK_BUDGET_AMERICANFOOTBALL_NFL (see deploy/setup_first_scorer_banking.sh).
FIRST_SCORER_MARKETS = {
    "basketball_nba": ["player_first_basket", "player_first_team_basket"],
    "americanfootball_nfl": ["player_1st_td"],
}
FIRST_SCORER_SNAPSHOTS = (120, 30, 5)          # minutes before tip/kick: T-120, T-30, T-5
FIRST_SCORER_MIN_OUTCOMES = 8                  # completeness gate: a book must list >=8 outcomes
FIRST_SCORER_PREFIX = "FirstScorer"


def first_scorer_raw_key(sport: str, date: str, event_id: str, snapshot: str) -> str:
    return f"{FIRST_SCORER_PREFIX}/{sport}/raw/{date}/{event_id}_{snapshot}.json"


def first_scorer_summary_key(sport: str, date: str, snapshot: str) -> str:
    return f"{FIRST_SCORER_PREFIX}/{sport}/{date}/summary_{snapshot}.json"


def first_scorer_latest_key(sport: str) -> str:
    return f"{FIRST_SCORER_PREFIX}/{sport}/latest.json"


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
