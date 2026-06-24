"""
NRFI Pro system config.
All system-specific paths and parameters live here.
Shared paths (Statcast, Weather, Lineups) come from mlb_core.config.
"""
import os
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, UMPIRES_MASTER,
    SEASON_START_MONTH, SEASON_END_MONTH, TIMEZONE,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(r"C:\Users\lmayn\Downloads\mlb-betting\NRFI_Pro_System")

cfg = {
    "version":            "v18",
    "base_dir":           str(BASE_DIR),

    # Season
    "season_start":       2019,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,
    "mlb_season_ranges": {
        2019: ("2019-03-28", "2019-09-29"),
        2020: ("2020-07-23", "2020-09-27"),
        2021: ("2021-04-01", "2021-10-03"),
        2022: ("2022-04-07", "2022-10-05"),
        2023: ("2023-03-30", "2023-10-01"),
        2024: ("2024-03-20", "2024-09-29"),
        2025: ("2025-03-18", "2025-09-28"),
        2026: ("2026-03-26", "2026-10-04"),
    },

    # Shared data (read from Baseball_Data lake)
    "statcast_master":    str(STATCAST_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    "umpire_master":      str(UMPIRES_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),

    # NRFI-specific files
    "odds_master":        str(BASE_DIR / "data" / "nrfi_odds_master.csv"),
    "pitcher_features":   str(BASE_DIR / "data" / "pitcher_start_features.csv"),
    "model_features":     str(BASE_DIR / "data" / "model_features.csv"),
    "model_halfinn":      str(BASE_DIR / "models" / "xgb_halfinn_v17.json"),
    "model_gamelevel":    str(BASE_DIR / "models" / "xgb_gamelevel_v17.json"),
    "model_meta":         str(BASE_DIR / "models" / "model_meta_v17.json"),
    "calibrator":         str(BASE_DIR / "models" / "isotonic_calibrator_v17.pkl"),
    "bet_db":             str(BASE_DIR / "data" / "nrfi_bets.db"),

    # GCS keys (Cloud Run uses these; local mode uses paths above)
    # v17 keys kept for reference and fallback
    "gcs_model_halfinn":      "NRFI_Pro_System/models/xgb_halfinn_v17.json",
    "gcs_model_gamelevel":    "NRFI_Pro_System/models/xgb_gamelevel_v17.json",
    "gcs_model_meta":         "NRFI_Pro_System/models/model_meta_v17.json",
    "gcs_calibrator":         "NRFI_Pro_System/models/isotonic_calibrator_v17.pkl",
    "gcs_model_features":     "NRFI_Pro_System/data/model_features.csv",
    "gcs_pitcher_features":   "NRFI_Pro_System/data/pitcher_start_features.csv",
    # v18 ensemble keys (runner auto-detects and prefers v18 when present)
    "gcs_model_pitcher_v18":  "NRFI_Pro_System/models/xgb_pitcher_v18.json",
    "gcs_model_lineup_v18":   "NRFI_Pro_System/models/xgb_lineup_v18.json",
    "gcs_model_context_v18":  "NRFI_Pro_System/models/xgb_context_v18.json",
    "gcs_model_meta_v18":     "NRFI_Pro_System/models/model_meta_v18.json",
    "gcs_calibrator_v18":     "NRFI_Pro_System/models/isotonic_calibrator_v18.pkl",

    # Betting params
    "min_edge":           0.06,
    "kelly_fraction":     0.25,
    "min_kelly_pct":      0.005,
    "max_kelly_pct":      0.05,
    "cap_units":          3.0,  # NRFI: one bet per game
    "scraper_delay":      3.0,
    "PAPER":              True,
    "BANKROLL":           1000,
}