"""
GAME_Pro_System/config_game.py -- GAME Pro v1 system config.

Binary classifier predicting P(home team wins full game).
Key differentiator: dedicated bullpen features (xwOBA allowed, K%, BB%,
fatigue IP) which the F5 system completely ignores.

Mirrors BATTER_HITS_System/config_batter_hits.py shape exactly.
"""
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, SEASON_START_MONTH, SEASON_END_MONTH, TIMEZONE,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(__file__).parent

# -- Feature contract -- keep in lockstep with training/retrain_game_v1.py ---
# 42 features. If you add/remove one here, mirror in training/retrain_game_v1.py.
GAME_FEATURES = [
    # Home starter
    "home_k_pct_L3",
    "home_xwoba_allowed_L3",
    "home_velo_mean_L3",
    "home_bb_pct_L3",
    "home_starter_ip_avg_L5",
    "home_starter_days_rest",
    "home_whiff_pct_L3",
    "home_hard_hit_allowed_L3",
    # Away starter
    "away_k_pct_L3",
    "away_xwoba_allowed_L3",
    "away_velo_mean_L3",
    "away_bb_pct_L3",
    "away_starter_ip_avg_L5",
    "away_starter_days_rest",
    "away_whiff_pct_L3",
    "away_hard_hit_allowed_L3",
    # Home bullpen (key differentiator from F5)
    "home_bullpen_xwoba_L14",
    "home_bullpen_k_pct_L14",
    "home_bullpen_bb_pct_L14",
    "home_bullpen_ip_L7",
    "home_bullpen_whiff_pct_L14",
    "home_bullpen_hard_hit_L14",
    # Away bullpen
    "away_bullpen_xwoba_L14",
    "away_bullpen_k_pct_L14",
    "away_bullpen_bb_pct_L14",
    "away_bullpen_ip_L7",
    "away_bullpen_whiff_pct_L14",
    "away_bullpen_hard_hit_L14",
    # Team offense
    "home_team_woba_L20",
    "away_team_woba_L20",
    "home_team_k_pct_L20",
    "away_team_k_pct_L20",
    "home_run_diff_L20",
    "home_team_hard_hit_L20",
    "away_team_hard_hit_L20",
    # Park / weather / context
    "park_factor",
    "temperature_f",
    "wind_speed_mph",
    "wind_out",
    "wind_in",
    "is_dome",
    "post_pitch_clock",
]

cfg = {
    "version":            "v1",
    "base_dir":           str(BASE_DIR),

    # Season
    "season_start":       2021,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,
    "mlb_season_ranges": {
        2021: ("2021-04-01", "2021-10-03"),
        2022: ("2022-04-07", "2022-10-05"),
        2023: ("2023-03-30", "2023-10-01"),
        2024: ("2024-03-20", "2024-09-29"),
        2025: ("2025-03-18", "2025-09-28"),
        2026: ("2026-03-26", "2026-10-04"),
    },

    # Shared data (lake)
    "statcast_master":    str(STATCAST_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "weather_master":     str(WEATHER_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),

    # GAME-specific files (local mode)
    "model_features":    str(BASE_DIR / "data" / "model_features.csv"),
    "starter_features":  str(BASE_DIR / "data" / "starter_game_features.csv"),
    "bullpen_features":  str(BASE_DIR / "data" / "bullpen_game_features.csv"),
    "team_offense":      str(BASE_DIR / "data" / "team_offense_features.csv"),
    "model_xgb":         str(BASE_DIR / "models" / "xgb_game_v1.json"),
    "model_meta":        str(BASE_DIR / "models" / "model_meta_game_v1.json"),
    "calibrator":        str(BASE_DIR / "models" / "isotonic_calibrator_game_v1.pkl"),
    "bet_db":            str(BASE_DIR / "data" / "game_bets.db"),

    # GCS keys -- match GAME_Pro_System/ layout in bucket
    "gcs_model_features":   "GAME_Pro_System/data/model_features.csv",
    "gcs_starter_features": "GAME_Pro_System/data/starter_game_features.csv",
    "gcs_bullpen_features": "GAME_Pro_System/data/bullpen_game_features.csv",
    "gcs_team_offense":     "GAME_Pro_System/data/team_offense_features.csv",
    "gcs_model_xgb":        "GAME_Pro_System/models/xgb_game_v1.json",
    "gcs_model_meta":       "GAME_Pro_System/models/model_meta_game_v1.json",
    "gcs_calibrator":       "GAME_Pro_System/models/isotonic_calibrator_game_v1.pkl",

    # Betting params
    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "cap_units":      10.0,
    "PAPER":          True,
    "BANKROLL":       1000,
}
