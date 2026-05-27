"""
OUTS_Pro_System/config_outs.py -- OUTS Pro v1 system config.

Shares K feature CSV but uses separate model artifacts.
Runner (run_k.py) loads these via cfg["gcs_outs_model"] etc.
"""
import os

# Inherit K config as base -- OUTS shares the same feature pipeline
from K_Pro_System.config_k import cfg as _k_cfg

cfg = dict(_k_cfg)

# Override OUTS-specific model artifact paths
cfg["gcs_outs_model"]       = "OUTS_Pro_System/models/xgb_outs_v1.json"
cfg["gcs_outs_meta"]        = "OUTS_Pro_System/models/model_meta_outs_v1.json"
cfg["gcs_outs_calibrator"]  = "OUTS_Pro_System/models/isotonic_calibrator_outs_v1.pkl"

# OUTS feature list -- subset of K features focused on durability.
# arsenal_* added 2026-05-27: pitch mix style affects outing length
# (e.g. heavy-FB pitchers have different manager hooks vs. off-speed artists).
OUTS_FEATURES = [
    "avg_ip_L5",
    "avg_bf_L5",
    "days_rest",
    "short_rest",
    "k_pct_L5",
    "k_pct_L10",
    "velo_mean_L5",
    "velo_trend_L5",
    "fb_pct_L10",
    "breaking_pct_L10",
    # Savant season-level pitch mix
    "arsenal_fb_usage",
    "arsenal_breaking_usage",
    "arsenal_pitch_diversity",
    "opp_k_rate_L14",
    "opp_k_rate_vs_hand_L14",
    "opp_lineup_pct_L",
    "opp_top3_k_rate_L50",
    "ump_overall_accuracy_L30",
    "ump_k_boost_L30",
    "ump_consistency_L30",
    "is_home",
    "temperature_f",
    "is_dome",
    "post_pitch_clock",
]

cfg["outs_features"] = OUTS_FEATURES
