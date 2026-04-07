"""
F5 Pro system config.
"""
import os
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, UMPIRES_MASTER, SEASON_START_MONTH, SEASON_END_MONTH,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(r"C:\Users\lmayn\Downloads\mlb-betting\F5_Pro_System")

cfg = {
    "version":            "v4",
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
    "umpire_master":      str(UMPIRES_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),

    # F5-specific files
    "odds_master":        str(BASE_DIR / "data" / "f5_odds_master.csv"),
    "game_features":      str(BASE_DIR / "data" / "f5_game_features.csv"),
    "model_f5":           str(BASE_DIR / "models" / "xgb_f5_v4.json"),
    "model_meta":         str(BASE_DIR / "models" / "model_meta_f5_v4.json"),
    "bet_db":             str(BASE_DIR / "data" / "f5_bets.db"),

    # Betting params
    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "paper_mode":     True,
    "PAPER":          True,
    "BANKROLL":       1000,
}

SEASON_WEIGHTS = {2021: 0.25, 2022: 0.5, 2023: 0.75, 2024: 1.0, 2025: 1.0, 2026: 1.0}