"""
training/tune_hyperparams.py -- E09: Optuna nested CV hyperparameter search.

Standalone tuner -- run BEFORE a retrain job to find optimal XGB_PARAMS.
Results are written to GCS as {system}_tuned_params.json and also printed
to stdout for manual inspection.

Usage (Cloud Shell):
  # Tune one system
  python -m training.tune_hyperparams --system NRFI --n-trials 50

  # Tune all systems
  python -m training.tune_hyperparams --system ALL --n-trials 50

  # Fast mode (fewer trials for smoke test)
  python -m training.tune_hyperparams --system K --n-trials 10

The tuned params are picked up automatically by each retrain script when
{system}_tuned_params.json exists in GCS. If not found, retrain falls back
to the hardcoded XGB_PARAMS defaults (existing behavior unchanged).

Architecture:
  - Outer loop: time-series CV (last 3 available years as test folds)
  - Inner loop: Optuna TPE search, 3-fold time-series CV on train slice
  - Metric: minimize mean OOS metric (AUC for binary, MAE for regression)
  - Search space:
      max_depth:        [2, 4]
      learning_rate:    [0.01, 0.10]  log-uniform
      min_child_weight: [5, 50]
      subsample:        [0.6, 1.0]
      colsample_bytree: [0.5, 1.0]
      reg_alpha:        [0.01, 5.0]   log-uniform
      reg_lambda:       [0.5, 5.0]
      gamma:            [0.0, 2.0]

Note on E09 gating:
  Per §22 E09: "Depends on E02-E05 being done first -- tuning on broken
  features is noise." This script is ready to run but should only be
  executed after the weather backfill (E07), feature rebuilds, and
  NRFI/F5/K retrains are complete.

Dependencies: optuna (not in requirements.txt -- add before running)
  pip install optuna --break-system-packages
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

# ── Default search space (same for all systems) ──────────────────────────────
SEARCH_SPACE = {
    "max_depth":        ("int",   2,    4),
    "learning_rate":    ("float", 0.01, 0.10, True),   # log-uniform
    "min_child_weight": ("int",   5,    50),
    "subsample":        ("float", 0.6,  1.0),
    "colsample_bytree": ("float", 0.5,  1.0),
    "reg_alpha":        ("float", 0.01, 5.0, True),    # log-uniform
    "reg_lambda":       ("float", 0.5,  5.0),
    "gamma":            ("float", 0.0,  2.0),
}

# Fixed params that don't get tuned
FIXED_PARAMS = {
    "seed":          42,
    "verbosity":     0,
    "n_jobs":        1,
}

# Per-system config
SYSTEM_CONFIG = {
    "NRFI": {
        "gcs_features":  "NRFI_Pro_System/data/model_features.csv",
        "target":        "yrfi",
        "objective":     "binary:logistic",
        "eval_metric":   "auc",
        "metric_dir":    "max",    # higher is better
        "gcs_output":    "NRFI_Pro_System/models/nrfi_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 500,
    },
    "K": {
        "gcs_features":  "K_Pro_System/data/model_features.csv",
        "target":        "starter_ks",
        "objective":     "count:poisson",
        "eval_metric":   "poisson-nloglik",
        "metric_dir":    "min",    # lower is better
        "gcs_output":    "K_Pro_System/models/k_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 1000,
        "filter_col":    "starter_ks",   # drop NaN target rows
    },
    "F5": {
        "gcs_features":  "F5_Pro_System/data/model_features.csv",
        "target":        "home_win",
        "objective":     "binary:logistic",
        "eval_metric":   "auc",
        "metric_dir":    "max",
        "gcs_output":    "F5_Pro_System/models/f5_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 500,
    },
    "HR": {
        "gcs_features":  "HR_Pro/data/model_features.csv",
        "target":        "hr_flag",
        "objective":     "binary:logistic",
        "eval_metric":   "auc",
        "metric_dir":    "max",
        "gcs_output":    "HR_Pro/models/hr_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 500,
    },
    "BATTER_HITS": {
        "gcs_features":  "BATTER_HITS_System/data/model_features.csv",
        "target":        "batter_hits",
        "objective":     "count:poisson",
        "eval_metric":   "poisson-nloglik",
        "metric_dir":    "min",
        "gcs_output":    "BATTER_HITS_System/models/batter_hits_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 2000,
        "filter_col":    "batter_hits",
    },
    "GAME": {
        "gcs_features":  "GAME_Pro_System/data/model_features.csv",
        "target":        "home_win",
        "objective":     "binary:logistic",
        "eval_metric":   "auc",
        "metric_dir":    "max",
        "gcs_output":    "GAME_Pro_System/models/game_tuned_params.json",
        "early_stopping": 30,
        "num_boost_round": 500,
    },
}


def _suggest_params(trial) -> dict:
    """Build a param dict from an Optuna trial."""
    import optuna
    params = dict(FIXED_PARAMS)
    for name, spec in SEARCH_SPACE.items():
        kind = spec[0]
        lo, hi = spec[1], spec[2]
        log = len(spec) > 3 and spec[3]
        if kind == "int":
            params[name] = trial.suggest_int(name, lo, hi)
        elif kind == "float":
            params[name] = trial.suggest_float(name, lo, hi, log=log)
    return params


def _load_features(cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Load features CSV from GCS, return (df, numeric_feature_cols)."""
    from mlb_core.storage import read_csv

    df = read_csv(cfg["gcs_features"], low_memory=False)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)

    target = cfg["target"]
    filter_col = cfg.get("filter_col", target)
    df = df.dropna(subset=[filter_col]).copy()
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df.dropna(subset=[target]).copy()

    # Feature columns: numeric, not the target, not metadata
    exclude = {
        target, "game_date", "game_pk", "pitcher", "batter",
        "home_team", "away_team", "player",
    }
    feature_cols = [
        c for c in df.columns
        if c not in exclude
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].notna().mean() >= 0.10
    ]

    logger.info(
        f"Loaded {len(df):,} rows | "
        f"features={len(feature_cols)} | "
        f"target={target} mean={df[target].mean():.3f}"
    )
    return df, feature_cols


def _cv_score(params: dict, df: pd.DataFrame, features: list,
              cfg: dict, n_folds: int = 3) -> float:
    """Inner CV: time-series n-fold on train slice. Returns mean metric."""
    objective   = cfg["objective"]
    eval_metric = cfg["eval_metric"]
    metric_dir  = cfg["metric_dir"]
    target      = cfg["target"]
    es          = cfg["early_stopping"]
    nbr         = cfg["num_boost_round"]

    full_params = {**params, "objective": objective, "eval_metric": eval_metric}

    years = sorted(df["game_date"].dt.year.unique())
    if len(years) < n_folds + 1:
        n_folds = max(1, len(years) - 1)

    fold_metrics = []
    for fold_idx in range(n_folds):
        test_year  = years[-(fold_idx + 1)]
        train_df   = df[df["game_date"].dt.year < test_year]
        test_df    = df[df["game_date"].dt.year == test_year]

        if len(train_df) < 100 or len(test_df) < 20:
            continue

        # Carve val from train (C03 pattern)
        nval      = max(20, len(train_df) // 10)
        tr_inner  = train_df.iloc[:-nval]
        val_inner = train_df.iloc[-nval:]

        X_tr  = tr_inner[features].apply(pd.to_numeric, errors="coerce")
        y_tr  = tr_inner[target].astype(float)
        X_val = val_inner[features].apply(pd.to_numeric, errors="coerce")
        y_val = val_inner[target].astype(float)
        X_te  = test_df[features].apply(pd.to_numeric, errors="coerce")
        y_te  = test_df[target].astype(float)

        dtrain = xgb.DMatrix(X_tr,  label=y_tr,  feature_names=features)
        dval   = xgb.DMatrix(X_val, label=y_val, feature_names=features)
        dtest  = xgb.DMatrix(X_te,  label=y_te,  feature_names=features)

        b = xgb.train(
            full_params, dtrain,
            num_boost_round=nbr,
            evals=[(dtrain, "train"), (dval, "val")],
            early_stopping_rounds=es,
            verbose_eval=False,
        )

        preds = b.predict(dtest)

        if objective == "binary:logistic":
            from sklearn.metrics import roc_auc_score
            try:
                score = float(roc_auc_score(y_te, preds))
            except Exception:
                continue
        else:
            score = float(np.mean(np.abs(y_te.values - preds)))

        fold_metrics.append(score)

    if not fold_metrics:
        return 0.0 if metric_dir == "max" else 999.0
    return float(np.mean(fold_metrics))


def tune_system(system: str, n_trials: int = 50) -> dict:
    """Run Optuna tuning for one system. Returns best params dict."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    cfg = SYSTEM_CONFIG[system]
    df, features = _load_features(cfg)

    direction = "maximize" if cfg["metric_dir"] == "max" else "minimize"

    def objective_fn(trial):
        params = _suggest_params(trial)
        score  = _cv_score(params, df, features, cfg)
        return score

    logger.info(f"E09 tuning | system={system} n_trials={n_trials} direction={direction}")
    study = optuna.create_study(direction=direction)
    study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    best_score = study.best_value

    logger.info(f"E09 best | system={system} score={best_score:.4f} params={best}")

    result = {
        "system":       system,
        "tuned_at":     datetime.now(timezone.utc).isoformat(),
        "n_trials":     n_trials,
        "direction":    direction,
        "best_score":   round(best_score, 4),
        "best_params":  best,
        "fixed_params": FIXED_PARAMS,
        # Full params ready to drop into retrain script
        "xgb_params": {
            **FIXED_PARAMS,
            **best,
            "objective":   cfg["objective"],
            "eval_metric": cfg["eval_metric"],
        },
    }

    # Write to GCS
    try:
        from mlb_core.storage import write_bytes
        write_bytes(
            json.dumps(result, indent=2).encode("utf-8"),
            cfg["gcs_output"],
        )
        logger.info(f"E09 saved | {cfg['gcs_output']}")
    except Exception as e:
        logger.warning(f"E09 GCS write failed (non-fatal): {e}")

    return result


def load_tuned_params(system: str) -> dict | None:
    """Load tuned params from GCS if available. Returns None if not found.

    Called by each retrain script at the top of run() to optionally
    override hardcoded XGB_PARAMS.

    Usage in retrain scripts:
        from training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params("NRFI")
        if tuned:
            XGB_PARAMS = tuned["xgb_params"]
            logger.info(f"Using Optuna-tuned params (score={tuned['best_score']})")
    """
    cfg = SYSTEM_CONFIG.get(system)
    if not cfg:
        return None
    try:
        from mlb_core.storage import read_bytes, exists
        if not exists(cfg["gcs_output"]):
            return None
        return json.loads(read_bytes(cfg["gcs_output"]))
    except Exception as e:
        logger.warning(f"load_tuned_params({system}): {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="E09: Optuna hyperparameter tuning")
    parser.add_argument("--system", required=True,
                        choices=list(SYSTEM_CONFIG.keys()) + ["ALL"],
                        help="System to tune, or ALL")
    parser.add_argument("--n-trials", type=int, default=50,
                        help="Number of Optuna trials per system (default 50)")
    args = parser.parse_args()

    systems = list(SYSTEM_CONFIG.keys()) if args.system == "ALL" else [args.system]

    results = {}
    for system in systems:
        try:
            result = tune_system(system, n_trials=args.n_trials)
            results[system] = result
            print(f"\n{'='*60}")
            print(f"E09 {system} TUNING COMPLETE")
            print(f"  Best score: {result['best_score']}")
            print(f"  Best params:")
            for k, v in result["best_params"].items():
                print(f"    {k}: {v}")
            print(f"\n  Drop-in XGB_PARAMS for retrain_{system.lower()}_*.py:")
            print(f"  {json.dumps(result['xgb_params'], indent=4)}")
        except Exception as e:
            logger.error(f"E09 {system} failed: {e}")
            results[system] = {"error": str(e)}

    print(json.dumps(results, indent=2, default=str))
    sys.exit(0 if all("error" not in v for v in results.values()) else 1)


if __name__ == "__main__":
    main()
