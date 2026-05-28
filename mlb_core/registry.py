"""
mlb_core/registry.py — Single source of truth for all MLB betting system config.

Adding a new system requires ONE entry here instead of editing 8+ files.

Usage:
    from mlb_core.registry import SYSTEMS, get_system, active_systems

    cfg = get_system("1IOU")
    print(cfg.feature_csv)   # "NRFI_Pro_System/data/model_features.csv"

    for name in active_systems():
        cfg = SYSTEMS[name]
        ...

This module is importable anywhere — it has NO imports from runners/ or
training/ and does NOT require GCS/DB connections at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemConfig:
    name: str                         # "HR", "NRFI", etc.
    icon: str                         # Discord/display emoji
    builder_module: str               # "runners.build_hr_features"
    runner_module: str                # "runners.run_hr"
    feature_csv: str                  # GCS key for model_features.csv
    model_artifact: str               # GCS key for xgb_*.json
    build_sentinel: str               # GCS key for last_build.json
    retrain_jobs: list[str] = field(default_factory=list)    # Cloud Run Job names
    calibrate_jobs: list[str] = field(default_factory=list)  # Cloud Run Job names
    expected_hit_rate: float = 0.52   # for monitor_performance thresholds
    log_only: bool = False            # True = LOG_ONLY gate active (new systems)
    active: bool = True               # include in daily build/run cycles
    tune_target: str | None = None    # target col for tune_hyperparams
    tune_objective: str | None = None # "binary:logistic" / "count:poisson"
    tune_metric: str | None = None    # "auc" / "poisson-nloglik"
    tune_metric_dir: str | None = None # "max" / "min"
    tune_output: str | None = None    # GCS key for tuned_params.json


# Canonical system order — used for ordered iteration, display, and digests.
CANONICAL_ORDER = ["HR", "1IOU", "K", "OUTS", "F5", "BATTER_HITS", "BATTER_TB", "GAME", "1I"]

SYSTEMS: dict[str, SystemConfig] = {

    "HR": SystemConfig(
        name="HR",
        icon="🔴",
        builder_module="runners.build_hr_features",
        runner_module="runners.run_hr",
        feature_csv="HR_Pro/data/model_features.csv",
        model_artifact="HR_Pro/models/xgb_hr_v6.json",
        build_sentinel="HR_Pro/data/last_build.json",
        retrain_jobs=["mlb-retrain-hr-v6"],
        calibrate_jobs=["mlb-calibrate-hr"],
        expected_hit_rate=0.07,
        tune_target="hr_flag",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="HR_Pro/models/hr_tuned_params.json",
    ),

    "1IOU": SystemConfig(
        name="1IOU",
        icon="🔵",
        builder_module="runners.build_nrfi_features",
        runner_module="runners.run_nrfi",
        feature_csv="NRFI_Pro_System/data/model_features.csv",
        model_artifact="NRFI_Pro_System/models/xgb_halfinn_v17.json",
        build_sentinel="NRFI_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-nrfi-v18"],
        calibrate_jobs=["mlb-calibrate-nrfi"],
        expected_hit_rate=0.55,
        tune_target="yrfi",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="NRFI_Pro_System/models/nrfi_tuned_params.json",
    ),

    "K": SystemConfig(
        name="K",
        icon="🟡",
        builder_module="runners.build_k_features",
        runner_module="runners.run_k",
        feature_csv="K_Pro_System/data/model_features.csv",
        model_artifact="K_Pro_System/models/xgb_k_v1.json",
        build_sentinel="K_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-k-v1"],
        calibrate_jobs=["mlb-calibrate-k"],
        expected_hit_rate=0.52,
        tune_target="starter_ks",
        tune_objective="count:poisson",
        tune_metric="poisson-nloglik",
        tune_metric_dir="min",
        tune_output="K_Pro_System/models/k_tuned_params.json",
    ),

    # OUTS shares K's feature CSV (same feature build) and has its own model
    # artifact from the dedicated mlb-retrain-outs-v1 Cloud Run Job.
    "OUTS": SystemConfig(
        name="OUTS",
        icon="🟠",
        builder_module="runners.build_k_features",   # shares K builder
        runner_module="runners.run_outs",
        feature_csv="K_Pro_System/data/model_features.csv",  # shared with K
        model_artifact="K_Pro_System/models/xgb_k_v1.json",  # OUTS uses K model artifact (no separate OUTS artifact in MODEL_KEYS)
        build_sentinel="K_Pro_System/data/last_build.json",   # shares K sentinel
        retrain_jobs=["mlb-retrain-outs-v1"],
        calibrate_jobs=[],                           # no separate calibrate job
        expected_hit_rate=0.52,
        # OUTS not in tune_hyperparams SYSTEM_CONFIG — no tune fields
    ),

    "F5": SystemConfig(
        name="F5",
        icon="🟢",
        builder_module="runners.build_f5_features",
        runner_module="runners.run_f5",
        feature_csv="F5_Pro_System/data/model_features.csv",
        model_artifact="F5_Pro_System/models/xgb_f5_v5.json",
        build_sentinel="F5_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-f5-v5"],
        calibrate_jobs=["mlb-calibrate-f5"],
        expected_hit_rate=0.52,
        tune_target="home_win",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="F5_Pro_System/models/f5_tuned_params.json",
    ),

    "BATTER_HITS": SystemConfig(
        name="BATTER_HITS",
        icon="🩵",
        builder_module="runners.build_batter_hits_features",
        runner_module="runners.run_batter_hits",
        feature_csv="BATTER_HITS_System/data/model_features.csv",
        model_artifact="BATTER_HITS_System/models/xgb_batter_hits_v1.json",
        build_sentinel="BATTER_HITS_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-batter-hits"],
        calibrate_jobs=["mlb-calibrate-batter-hits"],
        expected_hit_rate=0.52,  # update after 200 settled bets
        tune_target="batter_hits",
        tune_objective="count:poisson",
        tune_metric="poisson-nloglik",
        tune_metric_dir="min",
        tune_output="BATTER_HITS_System/models/batter_hits_tuned_params.json",
    ),

    "BATTER_TB": SystemConfig(
        name="BATTER_TB",
        icon="🟩",
        builder_module="runners.build_batter_tb_features",
        runner_module="runners.run_batter_tb",
        feature_csv="BATTER_TB_System/data/model_features.csv",
        model_artifact="BATTER_TB_System/models/xgb_batter_tb_v1.json",
        build_sentinel="BATTER_TB_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-batter-tb"],
        calibrate_jobs=["mlb-calibrate-batter-tb"],
        expected_hit_rate=0.52,
        tune_target="batter_total_bases",
        tune_objective="count:poisson",
        tune_metric="poisson-nloglik",
        tune_metric_dir="min",
        tune_output="BATTER_TB_System/models/batter_tb_tuned_params.json",
    ),

    "GAME": SystemConfig(
        name="GAME",
        icon="🖤",
        builder_module="runners.build_game_features",
        runner_module="runners.run_game",
        feature_csv="GAME_Pro_System/data/model_features.csv",
        model_artifact="GAME_Pro_System/models/xgb_game_v1.json",
        build_sentinel="GAME_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-game-v1"],
        calibrate_jobs=["mlb-calibrate-game"],
        expected_hit_rate=0.52,  # binary moneyline; update after 200 settled bets
        tune_target="home_win",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="GAME_Pro_System/models/game_tuned_params.json",
    ),

    "1I": SystemConfig(
        name="1I",
        icon="1️⃣",
        builder_module="runners.build_nrfi_features",  # derived from NRFI half-inning model
        runner_module="runners.run_1i",
        feature_csv="NRFI_Pro_System/data/model_features.csv",
        model_artifact="NRFI_Pro_System/models/xgb_halfinn_v17.json",
        build_sentinel="NRFI_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-nrfi-v18"],
        calibrate_jobs=["mlb-calibrate-nrfi"],
        expected_hit_rate=0.52,
    ),
}


def get_system(name: str) -> SystemConfig:
    """Return SystemConfig for `name`. Raises ValueError with clear message if not found."""
    cfg = SYSTEMS.get(name)
    if cfg is None:
        valid = ", ".join(CANONICAL_ORDER)
        raise ValueError(
            f"Unknown system {name!r}. Valid systems: {valid}"
        )
    return cfg


def active_systems() -> list[str]:
    """Return names of all active systems in canonical order."""
    return [s for s in CANONICAL_ORDER if s in SYSTEMS and SYSTEMS[s].active]
