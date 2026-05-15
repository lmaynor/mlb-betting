"""
K Pro v1 system config.
Mirrors NRFI_Pro_System/config_nrfi.py shape so the runner/training/build
scripts use the same idioms.

Shared infra (Statcast, Weather, Lineups, Umpires) comes from
mlb_core.config; system-specific files live under K_Pro_System/.

Notebook reference: K_Pro_v1.ipynb Section 0.
"""
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, UMPIRES_MASTER,
    SEASON_START_MONTH, SEASON_END_MONTH, TIMEZONE,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(r"C:\Users\lmayn\Downloads\mlb-betting\K_Pro_System")

# ── Feature contract — keep in lockstep with retrain_k_v1.py and the notebook
# K_Pro_v1.ipynb Section 0. 34 features total. If you add/remove one here, mirror
# in training/retrain_k_v1.py and flag in the next handoff.
K_FEATURES = [
    # K rate
    "k_pct_L5", "k_pct_L10", "k_pct_STD", "k_per_9_L5", "k_per_9_L10",
    # Count leverage
    "first_pitch_strike_pct_L10", "hitter_count_rate_L10", "two_strike_k_rate_L10",
    # Stuff
    "whiff_pct_L10", "zone_contact_pct_L10", "chase_pct_L10",
    "velo_mean_L5", "velo_trend_L5",
    # Pitch mix
    "fb_pct_L10", "breaking_pct_L10", "primary_whiff_rate_L10",
    # Durability
    "avg_ip_L5", "avg_bf_L5", "days_rest", "short_rest",
    # Opponent (today's lineup)
    "opp_k_rate_L14", "opp_k_rate_vs_hand_L14", "opp_chase_rate_L14",
    "opp_whiff_rate_L14", "opp_lineup_pct_L", "opp_platoon_k_edge",
    "opp_top3_k_rate_L50",
    # Umpire
    "ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30",
    # Context
    "is_home", "implied_win_pct", "temperature_f", "is_dome",
]

cfg = {
    "version":            "v1",
    "base_dir":           str(BASE_DIR),

    # Season — matches NRFI
    "season_start":       2021,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,

    # Shared data (lake)
    "statcast_master":    str(STATCAST_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    "umpire_master":      str(UMPIRES_MASTER),

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),

    # K-specific files (local mode)
    "pitcher_features":   str(BASE_DIR / "data" / "pitcher_k_features.csv"),
    "lineup_features":    str(BASE_DIR / "data" / "lineup_k_features.csv"),
    "model_features":     str(BASE_DIR / "data" / "model_features.csv"),
    "model_k":            str(BASE_DIR / "models" / "xgb_k_v1.json"),
    "model_meta":         str(BASE_DIR / "models" / "model_meta_v1.json"),
    "bet_db":             str(BASE_DIR / "data" / "k_bets.db"),

    # GCS keys — match the K_Pro_System/ layout in the bucket
    "gcs_pitcher_features": "K_Pro_System/data/pitcher_k_features.csv",
    "gcs_lineup_features":  "K_Pro_System/data/lineup_k_features.csv",
    "gcs_model_features":   "K_Pro_System/data/model_features.csv",
    "gcs_model_k":          "K_Pro_System/models/xgb_k_v1.json",
    "gcs_model_meta":       "K_Pro_System/models/model_meta_v1.json",

    # Betting params
    "min_edge":           0.04,
    "kelly_fraction":     0.25,
    "min_kelly_pct":      0.005,
    "max_kelly_pct":      0.05,
    "cap_units":          5.0,  # K: K + OUTS props per game
    "PAPER":              True,
    "BANKROLL":           1000,

    # Monte Carlo
    "mc_sims":            10_000,
    "mc_cap":             14,
}
