"""
BATTER_TB_System/config_batter_tb.py - BATTER_TB Pro v1 system config.

Count regressor predicting lambda (expected total bases per game).
At score time: P(total_bases > line) = 1 - NegBin_CDF(floor(line), lambda, nb_alpha).
"""
from pathlib import Path
from mlb_core.config import (
    STATCAST_MASTER, WEATHER_MASTER, LINEUPS_MASTER,
    SEASON_START_MONTH, SEASON_END_MONTH, SEASON_RANGES,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(__file__).parent

BATTER_TB_FEATURES = [
    "tb_per_game_L20",
    "tb_per_game_L50",
    "tb_rate_L20",
    "tb_rate_season",
    "xbh_rate_L20",
    "xbh_rate_L50",
    "slg_contact_L20",
    "slg_contact_L50",
    "hard_hit_L20",
    "barrel_rate_L20",
    "ld_rate_L20",
    "fb_rate_L20",
    "batter_pa_per_game_L20",
    "ewma_batting_order",
    "tb_vs_hand_career",
    "tb_vs_hand_season",
    "pitcher_tb_per_9_L20",
    "pitcher_xbh_rate_L20",
    "pitcher_hard_hit_L20",
    "pitcher_barrel_rate_L20",
    "tb_park_factor",
    "is_home",
    "temperature_f",
    "is_dome",
    "post_pitch_clock",
]

cfg = {
    "version":            "v1",
    "base_dir":           str(BASE_DIR),

    "season_start":       2021,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,
    "mlb_season_ranges": SEASON_RANGES,

    "statcast_master":    str(STATCAST_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),

    "batter_tb_features":  str(BASE_DIR / "data" / "batter_tb_features.csv"),
    "pitcher_tb_features": str(BASE_DIR / "data" / "pitcher_tb_features.csv"),
    "model_features":      str(BASE_DIR / "data" / "model_features.csv"),
    "model_xgb":           str(BASE_DIR / "models" / "xgb_batter_tb_v1.json"),
    "model_meta":          str(BASE_DIR / "models" / "model_meta_batter_tb_v1.json"),
    "calibrator":          str(BASE_DIR / "models" / "lambda_calibrator_batter_tb_v1.pkl"),
    "bet_db":              str(BASE_DIR / "data" / "batter_tb_bets.db"),

    "gcs_batter_tb_features":  "BATTER_TB_System/data/batter_tb_features.csv",
    "gcs_pitcher_tb_features": "BATTER_TB_System/data/pitcher_tb_features.csv",
    "gcs_model_features":      "BATTER_TB_System/data/model_features.csv",
    "gcs_model_xgb":           "BATTER_TB_System/models/xgb_batter_tb_v1.json",
    "gcs_model_meta":          "BATTER_TB_System/models/model_meta_batter_tb_v1.json",
    "gcs_calibrator":          "BATTER_TB_System/models/lambda_calibrator_batter_tb_v1.pkl",
    "gcs_player_order_map":    "HR_Pro/data/player_order_map.csv",
    "gcs_weather_master":      "Weather/weather_master.csv",

    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.04,
    "cap_units":      10.0,
    "PAPER":          True,
    "BANKROLL":       1000,

    "mc_sims": 10_000,
    "mc_cap":  14,
}
