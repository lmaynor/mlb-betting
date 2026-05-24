"""
BATTER_HITS_System/config_batter_hits.py — BATTER_HITS Pro v1 system config.

NegBin count regressor predicting lambda (expected hits per game).
At score time: P(hits > line) = 1 - NegBin_CDF(floor(line), lambda, nb_alpha).

Mirrors K_Pro_System/config_k.py shape. Batter-side analog of the K/OUTS
pitcher models.
"""
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, SEASON_START_MONTH, SEASON_END_MONTH, TIMEZONE,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(__file__).parent

# ── Feature contract — keep in lockstep with retrain_batter_hits_v1.py ───────
# 24 features. If you add/remove one here, mirror in training/retrain_batter_hits_v1.py.
BATTER_HITS_FEATURES = [
    # Direct hits count / rate
    "hits_per_game_L20",       # avg hits/game last 20g (direct lambda proxy)
    "hits_per_game_L50",       # avg hits/game last 50g
    "hits_rate_L20",           # hits/PA last 20g (quality, adj for PA variation)
    "hits_rate_season",        # season-to-date hits/PA
    # BABIP (contact quality on balls in play)
    "babip_L20",
    "babip_L50",
    # Contact metrics
    "contact_pct_L20",         # 1 - whiff/swing
    "chase_pct_L20",           # fewer chases -> more contact -> more hits
    # Batted ball profile
    "ld_rate_L20",             # line drive rate (highest BABIP)
    "gb_rate_L20",             # ground ball rate (BABIP > fly balls)
    "hard_hit_L20",            # hard hit rate
    # PA volume
    "batter_pa_per_game_L20",  # avg PA per game (lineup slot proxy)
    "ewma_batting_order",      # EWMA batting order position
    # Platoon splits
    "hits_vs_hand_career",     # career hits/PA vs today's pitcher arm
    "hits_vs_hand_season",     # season hits/PA vs today's pitcher arm
    # Pitcher features (hits-allowed side)
    "pitcher_babip_allowed_L20",  # BABIP against over last 20 starts
    "pitcher_hits_per_9_L20",     # H/9 over last 20 starts
    "pitcher_gb_rate_L20",        # GB% allowed (GB pitchers allow more hits)
    "pitcher_k_pct_L20",          # K rate allowed (fewer K -> more balls in play)
    # Park + context
    "hits_park_factor",
    "is_home",
    "temperature_f",
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

    # BATTER_HITS-specific files (local mode)
    "batter_hits_features":   str(BASE_DIR / "data" / "batter_hits_features.csv"),
    "pitcher_hits_features":  str(BASE_DIR / "data" / "pitcher_hits_features.csv"),
    "model_features":         str(BASE_DIR / "data" / "model_features.csv"),
    "model_xgb":              str(BASE_DIR / "models" / "xgb_batter_hits_v1.json"),
    "model_meta":             str(BASE_DIR / "models" / "model_meta_batter_hits_v1.json"),
    "calibrator":             str(BASE_DIR / "models" / "lambda_calibrator_batter_hits_v1.pkl"),
    "bet_db":                 str(BASE_DIR / "data" / "batter_hits_bets.db"),

    # GCS keys — match BATTER_HITS_System/ layout in bucket
    "gcs_batter_hits_features":  "BATTER_HITS_System/data/batter_hits_features.csv",
    "gcs_pitcher_hits_features": "BATTER_HITS_System/data/pitcher_hits_features.csv",
    "gcs_model_features":        "BATTER_HITS_System/data/model_features.csv",
    "gcs_model_xgb":             "BATTER_HITS_System/models/xgb_batter_hits_v1.json",
    "gcs_model_meta":            "BATTER_HITS_System/models/model_meta_batter_hits_v1.json",
    "gcs_calibrator":            "BATTER_HITS_System/models/lambda_calibrator_batter_hits_v1.pkl",
    # Reuse HR player order map (same batting order data)
    "gcs_player_order_map":      "HR_Pro/data/player_order_map.csv",

    # Betting params
    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "cap_units":      10.0,   # multiple batters per game
    "PAPER":          True,
    "BANKROLL":       1000,

    # Monte Carlo (NegBin CDF; MC kept for compatibility but CDF used directly)
    "mc_sims": 10_000,
    "mc_cap":  10,
}
