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
    builder_module: str               # "mlb.runners.build_hr_features"
    runner_module: str                # "mlb.runners.run_hr"
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
    force_gate: str | None = None     # None=auto | "on"=force-suppress | "off"=force-enable


# Canonical system order — used for ordered iteration, display, and digests.
CANONICAL_ORDER = ["HR", "1IOU", "K", "OUTS", "PITCHER_ER", "F5", "F1H", "BATTER_HITS", "BATTER_TB", "SB", "GAME", "1I"]

SYSTEMS: dict[str, SystemConfig] = {

    "HR": SystemConfig(
        name="HR",
        icon="🔴",
        builder_module="mlb.runners.build_hr_features",
        runner_module="mlb.runners.run_hr",
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
        builder_module="mlb.runners.build_nrfi_features",
        runner_module="mlb.runners.run_nrfi",
        feature_csv="NRFI_Pro_System/data/model_features.csv",
        model_artifact="NRFI_Pro_System/models/xgb_halfinn_v17.json",
        build_sentinel="NRFI_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-nrfi-v18"],
        calibrate_jobs=["mlb-calibrate-nrfi"],
        expected_hit_rate=0.55,
        # Retired 2026-06-24: no live edge (bet-sample AUC ~0.50, negative every
        # week across 05-05..06-22). Calibration cannot fix broken rank ordering.
        # Force-suppress until a retrain restores discrimination, then set to None.
        # See handoffs/handoff_2026-06-24_calibration_coverage.md.
        force_gate="on",
        tune_target="yrfi",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="NRFI_Pro_System/models/nrfi_tuned_params.json",
    ),

    "K": SystemConfig(
        name="K",
        icon="🟡",
        builder_module="mlb.runners.build_k_features",
        runner_module="mlb.runners.run_k",
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
        builder_module="mlb.runners.build_k_features",   # shares K builder
        runner_module="mlb.runners.run_k",   # OUTS is scored inside run_k; no run_outs module exists
        feature_csv="K_Pro_System/data/model_features.csv",  # shared with K
        model_artifact="OUTS_Pro_System/models/xgb_outs_v1.json",  # dedicated model (mlb-retrain-outs-v1); fixed 2026-08-17, see docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md finding A8 -- previously pointed at K's model, so no health check ever verified this file exists
        build_sentinel="K_Pro_System/data/last_build.json",   # shares K sentinel
        retrain_jobs=["mlb-retrain-outs-v1"],
        calibrate_jobs=[],                           # no separate calibrate job
        expected_hit_rate=0.52,
        # OUTS not in tune_hyperparams SYSTEM_CONFIG — no tune fields
    ),

    "PITCHER_ER": SystemConfig(
        name="PITCHER_ER",
        icon="🟤",
        builder_module="mlb.runners.build_k_features",   # shares K builder (Gamma proxy off K's lambda/avg_ip)
        runner_module="mlb.runners.run_k",   # scored inside run_k._score_pitcher_er; no run_pitcher_er module exists
        feature_csv="K_Pro_System/data/model_features.csv",  # shared with K -- no dedicated feature build
        model_artifact="K_Pro_System/models/xgb_k_v1.json",  # proxy depends on K's model, not a dedicated PITCHER_ER model
        build_sentinel="K_Pro_System/data/last_build.json",   # shares K sentinel
        expected_hit_rate=0.52,
        # Added 2026-08-17 (finding A9): PITCHER_ER previously had NO registry
        # entry at all, so is_suppressed("PITCHER_ER") -- which run_k.py DOES
        # call -- could never actually suppress anything: it fell through to
        # the dynamic gate file, which monitor_performance.py only ever
        # populates for registry-known systems. Combined with run_k.py having
        # no hardcoded log-only fallback (unlike F1H below), PITCHER_ER had
        # zero working protection despite CONTEXT.md documenting it as
        # log-only pending ~100-settled-bet validation -- see the paired
        # PITCHER_ER_LOG_ONLY flag added to run_k.py in the same fix.
    ),

    "F5": SystemConfig(
        name="F5",
        icon="🟢",
        builder_module="mlb.runners.build_f5_features",
        runner_module="mlb.runners.run_f5",
        feature_csv="F5_Pro_System/data/model_features.csv",
        model_artifact="F5_Pro_System/models/xgb_f5_v5.json",
        build_sentinel="F5_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-f5-v5"],
        calibrate_jobs=["mlb-calibrate-f5"],
        expected_hit_rate=0.52,
        # Retired 2026-06-24: no live edge (bet-sample AUC ~0.50, negative 6 of 7
        # weeks across 05-05..06-22). Already auto-suppressed; force makes it
        # deterministic (no hysteresis flip-back on noisy ROI) until retrain.
        # Set to None after mlb-retrain-f5-v5 restores discrimination.
        force_gate="on",
        # Fixed 2026-08-17 (finding C3.5): F5's real target column is
        # home_wins_f5 (retrain_f5_v5.py TARGET), not home_win -- that's
        # GAME's target. This and tune_hyperparams.py's identical F5 entry
        # both had the same wrong value, so a diff between the two files
        # alone would never have caught it.
        tune_target="home_wins_f5",
        tune_objective="binary:logistic",
        tune_metric="auc",
        tune_metric_dir="max",
        tune_output="F5_Pro_System/models/f5_tuned_params.json",
    ),

    "F1H": SystemConfig(
        name="F1H",
        icon="🔵",
        builder_module="mlb.runners.build_f5_features",   # shares F5 builder
        runner_module="mlb.runners.run_f5",   # scored inside run_f5._score_innings_submarkets; no run_f1h module exists
        feature_csv="F5_Pro_System/data/model_features.csv",  # shared with F5 -- scalar proxy off F5's p_home
        model_artifact="F5_Pro_System/models/xgb_f5_v5.json",  # proxy depends on F5's model, not a dedicated F1H model
        build_sentinel="F5_Pro_System/data/last_build.json",   # shares F5 sentinel
        expected_hit_rate=0.52,
        # Added 2026-08-17 (finding A9/E3): F1H previously had no registry
        # entry, so is_suppressed("F1H") could never reflect real state. Its
        # own retirement note ("no live edge, bet-sample AUC ~0.50,
        # net-negative trend") plus F5's own force_gate="on" above both apply
        # here too (F1H is a scalar proxy OFF that same model) -- force_gate
        # mirrors F5's rather than left to the dynamic gate. run_f5.py's
        # hardcoded LOG_ONLY_SYSTEMS={"F1H"} already keeps this at stake=0
        # regardless, but that hardcoded guard and this registry gate should
        # agree, not leave the registry one silently absent. Clear only
        # alongside F5's force_gate, once a dedicated F1H model exists.
        force_gate="on",
    ),

    "BATTER_HITS": SystemConfig(
        name="BATTER_HITS",
        icon="🩵",
        builder_module="mlb.runners.build_batter_hits_features",
        runner_module="mlb.runners.run_batter_hits",
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
        builder_module="mlb.runners.build_batter_tb_features",
        runner_module="mlb.runners.run_batter_tb",
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

    "SB": SystemConfig(
        name="SB",
        icon="🏃",
        builder_module="mlb.runners.build_sb_features",
        runner_module="mlb.runners.run_sb",
        feature_csv="SB_Pro_System/data/model_features.csv",
        model_artifact="SB_Pro_System/models/xgb_sb_v1.json",
        build_sentinel="SB_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-sb-v1"],
        calibrate_jobs=["mlb-calibrate-sb"],
        expected_hit_rate=0.52,  # placeholder; update after 200 settled bets
        log_only=True,  # new system, no settled bets yet -- run_sb.py's own
                        # LOG_ONLY=True module flag is what actually enforces
                        # stake=0 (see mlb_core/risk/gates.py's docstring: this
                        # field is documentation, not itself gate logic).
        tune_target="stolen_bases",
        tune_objective="count:poisson",
        tune_metric="poisson-nloglik",
        tune_metric_dir="min",
        tune_output="SB_Pro_System/models/sb_tuned_params.json",
    ),

    "GAME": SystemConfig(
        name="GAME",
        icon="🖤",
        builder_module="mlb.runners.build_game_features",
        runner_module="mlb.runners.run_game",
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
        builder_module="mlb.runners.build_nrfi_features",  # derived from NRFI half-inning model
        runner_module="mlb.runners.run_1i",
        feature_csv="NRFI_Pro_System/data/model_features.csv",
        model_artifact="NRFI_Pro_System/models/xgb_halfinn_v17.json",
        build_sentinel="NRFI_Pro_System/data/last_build.json",
        retrain_jobs=["mlb-retrain-nrfi-v18"],
        calibrate_jobs=["mlb-calibrate-nrfi"],
        expected_hit_rate=0.52,
        # Force-suppressed 2026-08-17 alongside 1IOU: 1I derives its
        # probabilities from the IDENTICAL NRFI half-inning model that is
        # force-suppressed under the "1IOU" entry above for "no live edge
        # (bet-sample AUC ~0.50, negative every week) -- calibration cannot
        # fix broken rank ordering." That's a defect in the underlying model,
        # not the market it's quoted against, so it applies here too. Clear
        # this (set to None) only alongside 1IOU, once a retrain restores
        # discrimination. See
        # docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md
        # finding A3.
        force_gate="on",
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
