"""nba.props configuration: stats, windows, thresholds, artifact names.

LOG-ONLY / paper pillar (CONTEXT s19, s21). Nothing here is registered in the MLB registry, writes the `bets` table,
or posts to Discord. No NBA prop odds history exists, so NO edge is claimed anywhere in this package.
"""
from __future__ import annotations

import os

MODEL_VERSION = "v1"
TIMEZONE = "America/New_York"

# canonical stat name (same vocabulary as ev.props.STAT_KEYS_BY_SPORT['nba']) -> box-score column
STATS = ["points", "rebounds", "assists", "threes", "pra"]
STAT_COL = {"points": "points", "rebounds": "rebounds", "assists": "assists",
            "threes": "three_points_made", "pra": "pra"}
# a hard cap on the support evaluated for CRPS / pmf sums (a count above this is not credible)
SUPPORT_MAX = {"points": 90, "rebounds": 40, "assists": 30, "threes": 16, "pra": 120}

# data window / row policy
EXCLUDE_SEASON_TYPES = ("Preseason",)        # preseason minutes are unrepresentative: out of history AND targets
MIN_HISTORY_GAMES = 5                        # a training/scoring row needs >= this many prior played games
SCORE_MIN_HISTORY_GAMES = 15                 # live scorer is stricter: thin history -> abstain
MIN_PLAYED_MINUTES = 0.0                     # played := minutes > 0 (46 rows carry played=True with 0:00)

# rolling feature windows (games the player PLAYED; DNP games are not in the series)
WINDOWS = (5, 10, 20)
EWM_HALFLIVES = (8, 30)
LEAGUE_WINDOW_DAYS = 90                      # league-average normaliser for team/opponent rates
TEAM_EWM_HALFLIFE = 10                       # games
SCHEDULE_WINDOW_DAYS = 7

POS_GROUP = {"PG": "G", "SG": "G", "G": "G", "SF": "W", "F": "W", "G-F": "W", "F-G": "W", "PF": "B", "C": "B",
             "F-C": "B", "C-F": "B"}

# ---- availability features (OPT-IN feature set "v2"; the default feature list is UNCHANGED) ----
# A model trained before these existed keeps its own `features` list in its meta and reads only those columns
# (StatModel._dm reindexes), so emitting extra columns can never break an old artifact. To TRAIN with them, ask for them:
# build_training_frame(..., availability="history") and pass FEATURES_V2-style column lists explicitly.
FEATURE_SET_DEFAULT = "v1"
AVAILABILITY_FEATURES = ("team_missing_min_l10", "team_missing_usg_proxy_l10", "team_missing_n_rotation")
ROTATION_GAMES = 5                # rotation = played for the team in at least one of its previous N games
ROTATION_MIN_L10 = 10.0           # ... AND averaged >= this many minutes over his trailing 10 played games
ROTATION_MAX_GAP_DAYS = 30        # previous team game further back than this (offseason) -> features NaN, not guessed
USG_PROXY_AST_WEIGHT = 1.5        # usage proxy per game = PTS + 1.5*AST + REB (trailing L10 means)

# ---- live scorer guards (mirror ev/sweep_props_alerts.py; UNVALIDATED starting points) ----
MAX_HISTORY_GAP_DAYS = 4          # newest box score must be within this many days of the event date
ROLLOVER_MAX_GAP_DAYS = 150       # explicit opt-in only: last season's history carried into a new season
MIN_BOOKS = 3                     # two-sided real books quoting the (player, stat, point)
MIN_EV = 0.03
EV_CAP = 0.15                     # bigger apparent edges are far more often model/reference error (repo rule)
MAX_DISP = 0.06                   # MAD of the books' own fair P(over)
CLOSE_BUFFER_MIN = 20.0
MAX_SNAPSHOT_AGE_MIN = 45.0
# minutes-uncertainty abstain thresholds (heuristic, documented in the handoff)
ABSTAIN_MIN_STD10 = 7.5           # minutes std over the last 10 played games
ABSTAIN_MISSED_L10 = 3            # team games missed in the last 10
ABSTAIN_MIN_RATIO = (0.70, 1.30)  # minutes L5 / minutes L20 outside this band
ABSTAIN_GAMES_SINCE_RETURN = 2    # first N games back after >= 5 missed team games


def artifact_names(stat: str, version: str = MODEL_VERSION) -> dict:
    return {"booster": f"xgb_nba_props_{stat}_{version}.json",
            "meta": f"model_meta_nba_props_{stat}_{version}.json",
            "var": f"xgb_nba_props_{stat}_var_{version}.json"}


DEFAULT_ARTIFACT_DIR = os.environ.get(
    "NBA_PROPS_ARTIFACT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts"))
# GCS only behind an explicit --upload flag (never exercised in this PR)
GCS_MODEL_PREFIX = "Models/NBA/props"
GCS_EVIDENCE_PREFIX = "EVProps/nba_model"
