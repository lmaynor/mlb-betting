"""
F5 Pro system config.
"""
import os
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, UMPIRES_MASTER, SEASON_START_MONTH, SEASON_END_MONTH,
    SEASON_RANGES,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(r"C:\Users\lmayn\Downloads\mlb-betting\F5_Pro_System")

cfg = {
    "version":            "v5",
    "base_dir":           str(BASE_DIR),

    # Season
    "season_start":       2021,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,
    "mlb_season_ranges": SEASON_RANGES,

    # Shared data
    "statcast_master":    str(STATCAST_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "umpire_master":      str(UMPIRES_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),

    # F5-specific files
    "odds_master":        str(BASE_DIR / "data" / "f5_odds_master.csv"),
    "game_features":      str(BASE_DIR / "data" / "f5_game_features.csv"),
    "model_features":     str(BASE_DIR / "data" / "model_features.csv"),
    "model_f5":           str(BASE_DIR / "models" / "xgb_f5_v5.json"),
    "model_meta":         str(BASE_DIR / "models" / "model_meta_f5_v5.json"),
    "bet_db":             str(BASE_DIR / "data" / "f5_bets.db"),

    # GCS keys (Cloud Run uses these; local mode uses paths above)
    "gcs_model_f5":          "F5_Pro_System/models/xgb_f5_v5.json",
    "gcs_model_meta":        "F5_Pro_System/models/model_meta_f5_v5.json",
    "gcs_model_features":    "F5_Pro_System/data/model_features.csv",
    "gcs_calibrator":        "F5_Pro_System/models/isotonic_calibrator_f5_v5.pkl",
    "gcs_pitcher_starts":    "F5_Pro_System/data/pitcher_starts.csv",
    "gcs_team_offense":      "F5_Pro_System/data/team_offense.csv",

    # Betting params
    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "cap_units":      3.0,  # F5: one bet per game
    "paper_mode":     True,
    "PAPER":          True,
    "BANKROLL":       1000,
}

SEASON_WEIGHTS = {2021: 0.25, 2022: 0.5, 2023: 0.75, 2024: 1.0, 2025: 1.0, 2026: 1.0}