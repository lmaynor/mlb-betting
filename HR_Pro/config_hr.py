"""
HR Pro system config.
"""
import os
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, SEASON_START_MONTH, SEASON_END_MONTH,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(r"C:\Users\lmayn\Downloads\mlb-betting\HR_Pro")

cfg = {
    "version":            "v6",
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

    # Shared data
    "statcast_master":    str(STATCAST_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),

    # HR-specific files
    "odds_cache_dir":         str(BASE_DIR / "data" / "odds_cache_daily"),
    "park_factors":           str(BASE_DIR / "data" / "savant_hr_pf.csv"),
    "game_features_master":   str(BASE_DIR / "data" / "game_features_master.csv"),
    "player_game_master":     str(BASE_DIR / "data" / "player_game_master.csv"),
    "batter_features":        str(BASE_DIR / "data" / "batter_rolling_features.csv"),
    "platoon_features":       str(BASE_DIR / "data" / "batter_platoon_features.csv"),
    "pitcher_features":       str(BASE_DIR / "data" / "pitcher_hr_features.csv"),
    "model_features":         str(BASE_DIR / "data" / "model_features.csv"),
    "odds_master":            str(BASE_DIR / "data" / "odds_master.csv"),
    "odds_multibook_master":  str(BASE_DIR / "data" / "odds_multibook_master.csv"),
    "player_id_map":          str(BASE_DIR / "data" / "player_id_map.csv"),
    "player_order_map":       str(BASE_DIR / "data" / "player_order_map.csv"),
    "model_xgb":              str(BASE_DIR / "models" / "xgb_hr_v6.json"),
    "model_meta":             str(BASE_DIR / "models" / "model_meta_hr_v6.json"),
    "calibrator":             str(BASE_DIR / "models" / "isotonic_calibrator_hr_v6.pkl"),
    "bet_db":                 str(BASE_DIR / "data" / "hr_bets.db"),

    # Betting params
    "min_edge":       0.03,
    "kelly_fraction": 0.50,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "PAPER":          True,
    "BANKROLL":       1000,
}