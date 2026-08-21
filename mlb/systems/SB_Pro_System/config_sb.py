"""
SB_Pro_System/config_sb.py -- SB (stolen base) Pro v1 system config.

NegBin count regressor predicting lambda (expected stolen bases per game).
At score time: P(SB > line) = 1 - NegBin_CDF(floor(line), lambda, nb_alpha).
Mirrors BATTER_HITS_System/config_batter_hits.py shape exactly -- same
market shape (real O/U, confirmed live 2026-08-20), same NegBin architecture.

See handoffs/scope_stolen_base_model_2026-08-20.md for the full data-
availability writeup and why this is a NegBin count model, not an
HR-style binary classifier.
"""
from pathlib import Path
from mlb_core.config import (
    BASE_DATA, STATCAST_MASTER, WEATHER_MASTER,
    LINEUPS_MASTER, SEASON_START_MONTH, SEASON_END_MONTH, TIMEZONE,
    DEFAULT_STATCAST_CACHE, DEFAULT_WEATHER_CACHE, DEFAULT_LINEUP_CACHE,
)

BASE_DIR = Path(__file__).parent

# -- Feature contract -- keep in lockstep with retrain_sb_v1.py --------------
# The "on-base ability" features (times_on_base/single/bb/hbp rate) are
# deliberately about REACHING base via a single/walk/HBP specifically, not
# any hit -- a double/triple/HR advances PAST the stealing opportunity
# instead of creating one. See scope doc s4.
SB_FEATURES = [
    # Runner: opportunity to reach base (singles/BB/HBP specifically)
    "times_on_base_L20",       # (1B+BB+HBP)/PA last 20g -- opportunity rate
    "single_rate_L20",         # singles/PA last 20g -- most common SB-opportunity event
    "bb_rate_L20",              # walks/PA last 20g
    "hbp_rate_L20",             # HBP/PA last 20g
    "k_rate_L20",               # strikeouts/PA -- fewer Ks -> more opportunities
    # Runner: speed and SB-specific history
    "sprint_speed_ft_sec",     # Savant leaderboard, season-level
    "sb_per_game_L20",         # rolling mean stolen bases/game (direct lambda proxy)
    "sb_per_game_L50",
    "sb_attempt_rate_L20",     # (SB+CS)/game last 20g
    "cs_rate_L20",             # caught-stealing/game last 20g
    "sb_success_pct_L50",      # SB/(SB+CS) last 50g, ratio-of-sums
    "sb_season",                # season-to-date SB/game
    # Runner: lineup slot + handedness
    "ewma_batting_order",
    "stand_L",                  # 1 if left-handed batter (marginal 1B head start)
    # Opposing pitcher
    "p_throws_L",                # 1 if left-handed pitcher (real hold advantage vs 1B)
    "pitcher_sb_allowed",        # season-level B-Ref counting stat
    "pitcher_cs_allowed",
    "pitcher_pickoffs",          # season-level B-Ref counting stat -- pickoff-move
                                  # skill/usage, distinct from SB/CS-allowed outcomes
    # Opposing catcher -- first system in this codebase to need this at all
    "catcher_maxeff_arm_2b_3b_sba",
    "catcher_exchange_2b_3b_sba",
    "catcher_pop_2b_sba",
    # Situational / regime
    "is_home",
    "post_pitch_clock",        # 2023-03-30 rule change -- bigger effect here than elsewhere
]

cfg = {
    "version":            "v1",
    "base_dir":           str(BASE_DIR),

    # Season -- 2023+ ONLY (not 2021, unlike most other systems). The
    # 2023-03-30 pitch-clock/bigger-base/disengagement-limit rules shifted
    # stolen-base behavior materially -- training on pre-2023 data would
    # dilute the signal with a different game. See scope doc s4/s8 risk register.
    "season_start":       2023,
    "season_start_month": SEASON_START_MONTH,
    "season_end_month":   SEASON_END_MONTH,
    "mlb_season_ranges": {
        2023: ("2023-03-30", "2023-10-01"),
        2024: ("2024-03-20", "2024-09-29"),
        2025: ("2025-03-18", "2025-09-28"),
        2026: ("2026-03-26", "2026-10-04"),
    },

    # Shared data (lake)
    "statcast_master":    str(STATCAST_MASTER),
    "lineups_master":     str(LINEUPS_MASTER),
    "weather_master":     str(WEATHER_MASTER),
    # New master this system introduced -- see mlb_core/data/sb_boxscore.py.
    # Statcast's public per-pitch export cannot see SB/CS events at all
    # (verified live 2026-08-20); this comes from MLB Stats API boxscores.
    "gcs_sb_boxscore_master": "Scoring/sb_boxscore_master.csv",
    # Starting-catcher identity per game -- see mlb_core/data/lineups.py
    # catcher_backfill_gcs() / get_starting_catchers().
    "gcs_catcher_master":     "AuxData/catcher_identity_master.csv",

    # Shared cache dirs
    "statcast_cache_dir": str(DEFAULT_STATCAST_CACHE),
    "lineup_cache_dir":   str(DEFAULT_LINEUP_CACHE),
    "weather_cache_dir":  str(DEFAULT_WEATHER_CACHE),

    # SB-specific files (local mode)
    "sb_batter_features":   str(BASE_DIR / "data" / "sb_batter_features.csv"),
    "model_features":       str(BASE_DIR / "data" / "model_features.csv"),
    "model_xgb":            str(BASE_DIR / "models" / "xgb_sb_v1.json"),
    "model_meta":           str(BASE_DIR / "models" / "model_meta_sb_v1.json"),
    "calibrator":           str(BASE_DIR / "models" / "lambda_calibrator_sb_v1.pkl"),
    "bet_db":               str(BASE_DIR / "data" / "sb_bets.db"),

    # GCS keys -- match SB_Pro_System/ layout in bucket
    "gcs_sb_batter_features": "SB_Pro_System/data/sb_batter_features.csv",
    "gcs_model_features":     "SB_Pro_System/data/model_features.csv",
    "gcs_model_xgb":          "SB_Pro_System/models/xgb_sb_v1.json",
    "gcs_model_meta":         "SB_Pro_System/models/model_meta_sb_v1.json",
    "gcs_calibrator":         "SB_Pro_System/models/lambda_calibrator_sb_v1.pkl",
    # Reuse HR player order map (same batting order data every batter system reuses)
    "gcs_player_order_map":   "HR_Pro/data/player_order_map.csv",

    # Betting params -- min_edge/kelly conservative like BATTER_HITS pending
    # real settled data; LOG_ONLY gate lives in the runner, not here.
    "min_edge":       0.04,
    "kelly_fraction": 0.25,
    "min_kelly_pct":  0.005,
    "max_kelly_pct":  0.05,
    "cap_units":      10.0,
    "PAPER":          True,
    "BANKROLL":       1000,

    # NegBin CDF (Monte Carlo kept for compatibility but CDF used directly)
    "mc_sims": 10_000,
    "mc_cap":  10,
}
