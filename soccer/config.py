"""soccer.config -- league registry for the soccer vertical.

One entry per league; ACTIVE_SOCCER_LEAGUES is the Phase 1 launch set (EPL + UCL). Expanding to the rest of the
Big 5 is a one-line change to ACTIVE_SOCCER_LEAGUES: every consumer (ingest, ratings, board, Kalshi legs) reads
its league parameters from here.

Field meanings:
  parlay_key        ParlayAPI sport key (the odds feed's own naming; coverage verified by scripts/probe_soccer_odds.py)
  fd_code           football-data.co.uk CSV stem (mmz4281/{season}/{fd_code}.csv); UCL has no per-season mmz4281
                    file, so its fd_code is a label and the UCL closing-odds pull is best-effort (see ingest)
  understat_league  Understat league slug for the matchesData page, or None when the league has NO Understat page
                    (UCL: Understat only publishes domestic leagues). A None league gets its ratings from the
                    pooled Big-5 domestic fit plus the ClubElo fallback (soccer.game.run_daily), never from a
                    per-league Understat pull -- understat.com/league/UCL/{year} is a 404.
  kalshi_prefix     Kalshi series prefix for the league (futures series verified live 2026-11-03; the GAME/TOTAL
                    suffix grammar is the repo's standard adapter grammar and is re-verified by the probe)
  name              display name
"""

from __future__ import annotations

# The ONE xG definition this vertical prices from (football-docs / Nutmeg metric-card contract): Understat's
# shot-based match xG. Every projection row carries this string so a second xG source can never be mixed in silently.
XG_VARIANT = "understat_shot_v1"

ENGINE_NAME = "dixon_coles_xg_v1"
ENGINE_LABEL = "UNVALIDATED model estimate, not a tested edge"

PREDICTIONS_KEY = "EVSweep/soccer_games/predictions_latest.json"
ODDS_BOARD_KEY = "EVSweep/soccer_games/odds_board_latest.json"

SOCCER_LEAGUES: dict[str, dict] = {
    "epl": {
        "parlay_key": "soccer_epl",
        "fd_code": "E0",
        "understat_league": "EPL",
        "kalshi_prefix": "KXEPL",
        "name": "Premier League",
    },
    "ucl": {
        "parlay_key": "soccer_uefa_champs_league",
        "fd_code": "UCL",
        "understat_league": None,
        "kalshi_prefix": "KXUCL",
        "name": "Champions League",
    },
    # Big 5 expansion set: registered but NOT active in Phase 1.
    "laliga": {
        "parlay_key": "soccer_spain_la_liga",
        "fd_code": "SP1",
        "understat_league": "La_liga",
        "kalshi_prefix": "KXLALIGA",
        "name": "La Liga",
    },
    "seriea": {
        "parlay_key": "soccer_italy_serie_a",
        "fd_code": "I1",
        "understat_league": "Serie_A",
        "kalshi_prefix": "KXSERIEA",
        "name": "Serie A",
    },
    "bundesliga": {
        "parlay_key": "soccer_germany_bundesliga",
        "fd_code": "D1",
        "understat_league": "Bundesliga",
        "kalshi_prefix": "KXBUNDESLIGA",
        "name": "Bundesliga",
    },
    "ligue1": {
        "parlay_key": "soccer_france_ligue_one",
        "fd_code": "F1",
        "understat_league": "Ligue_1",
        "kalshi_prefix": "KXLIGUE1",
        "name": "Ligue 1",
    },
}

ACTIVE_SOCCER_LEAGUES: tuple[str, ...] = ("epl", "ucl")

# The domestic leagues Understat actually publishes (its only coverage: no UCL, no cups). These are the rating
# pool for every league whose understat_league is None: UCL clubs earn their attack/defense ratings in their
# domestic league, and ClubElo (one cross-European scale) anchors the pool so a Spanish and an English club's
# ratings are comparable in a UCL fixture.
DOMESTIC_UNDERSTAT_LEAGUES: tuple[str, ...] = ("epl", "laliga", "bundesliga", "seriea", "ligue1")

# Home advantage in a cross-league European fixture is real but smaller than in a domestic league (travel,
# unfamiliar ground, no weekly home crowd edge): UCL expected goals scale the fitted domestic gamma by this.
UCL_HFA_SCALE = 0.85

# Phase 1 market set, in board order. Player props are Phase 2 (soccer.props, gated OFF).
SOCCER_MARKETS: tuple[str, ...] = ("ml", "total", "btts", "dc")
