"""
training/retrain_game_v1.py -- GAME Pro v1 full retrain.

Binary classifier: XGBoost binary:logistic predicting P(home team wins
full game). Key differentiator from F5: dedicated bullpen features.

NOTE: The feature builder (runners/build_game_features.py) must include a
      `home_win` column in model_features.csv. This is derived from
      Scoring/scoring_master.csv: sum runs for home team across all innings,
      sum for away team, home > away -> 1, else -> 0.
      If `home_win` is absent, this retrain will abort with a clear error.

Output GCS keys (matches GAME_Pro_System/config_game.py):
  - GAME_Pro_System/models/xgb_game_v1.json
  - GAME_Pro_System/models/model_meta_game_v1.json
  - GAME_Pro_System/models/archive/...{ts}...

The CV/eval/persist mechanics are shared with retrain_k_v1.py/
retrain_sb_v1.py/retrain_outs_v1.py/retrain_batter_hits_v1.py via
_retrain_common.py, parametrized by GAME's own binary AUC/Brier/LogLoss
metric family (count systems use MAE/RMSE/R2) -- see that module's
docstring for what's shared vs genuinely per-system.

Entrypoint: python -m training.retrain_game_v1
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb

from mlb.systems.GAME_Pro_System.config_game import GAME_FEATURES
from mlb.training import _retrain_common as common
from mlb.training._retrain_common import cv_folds as _cv_folds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)


VERSION = "v1"
TARGET  = "home_win"
SYSTEM  = "GAME"

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      "auc",
    "max_depth":        3,
    "learning_rate":    0.05,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "gamma":            0.1,
    "seed":             42,
}

NUM_BOOST_ROUND       = 2000
EARLY_STOPPING_ROUNDS = 50


def _auc(y_true, y_pred):
    """Simple AUC via sklearn."""
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(np.array(y_true), np.array(y_pred)))
    except Exception:
        return float("nan")


def _brier(y_true, y_pred):
    return float(np.mean((np.array(y_pred) - np.array(y_true)) ** 2))


def _logloss(y_true, y_pred):
    eps = 1e-7
    p = np.clip(np.array(y_pred), eps, 1 - eps)
    y = np.array(y_true)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


# Per-system metric family for the shared CV/OOS/leakage mechanics (binary:
# AUC/Brier/LogLoss -- the real per-system difference vs the count systems'
# MAE/RMSE/R2). "auc" is the PRIMARY metric -- higher is better, which also
# flips the leakage-check comparison direction (see _leakage_check below).
_METRICS = [("auc", _auc), ("brier", _brier), ("logloss", _logloss)]


GCS_MODEL_FEATURES  = "GAME_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"GAME_Pro_System/models/xgb_game_{VERSION}.json"
GCS_META_LATEST     = f"GAME_Pro_System/models/model_meta_game_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"GAME_Pro_System/models/archive/xgb_game_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"GAME_Pro_System/models/archive/model_meta_game_{VERSION}.{{ts}}.json"


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found -- run /build-features for GAME first"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"
    if df.empty:
        return None, f"{GCS_MODEL_FEATURES} is empty"
    if TARGET not in df.columns:
        return None, (
            f"target column {TARGET!r} missing from features CSV. "
            "The feature builder must derive home_win from scoring_master.csv "
            "(sum home runs > sum away runs -> 1, else -> 0) and include it."
        )

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)

    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    df[TARGET] = df[TARGET].astype(int)
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    win_rate = df[TARGET].mean()
    logger.info(
        "loaded features: %d training rows (%d slate rows excluded) | "
        "%s -> %s | home_win_rate=%.3f",
        len(df), dropped,
        df["game_date"].min().date(), df["game_date"].max().date(),
        win_rate,
    )
    return df, None


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    return common.walk_forward_cv(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_train=100, min_test=20, cv_folds_fn=_cv_folds,
        filter_train_years=False, logger=logger,
    )


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    return common.oos_eval(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_test=20, cv_folds_fn=_cv_folds,
        include_train_metrics=True, logger=logger,
    )


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    return common.full_retrain(df, features, TARGET, XGB_PARAMS, best_iter, logger=logger)


def _feature_means(df: pd.DataFrame, features: list) -> dict:
    return common.feature_means(df, features, warn_on_skip=False, logger=logger)


def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.10) -> list[str]:
    # AUC: higher is better, so "zeroing a feature IMPROVES AUC" (raises it)
    # is the suspicious direction -- opposite of the count systems' MAE
    # (where zeroing a leaky feature LOWERS/improves MAE).
    return common.leakage_check(
        df, features, oos, TARGET, XGB_PARAMS,
        baseline_key="auc_oos", metric_fn=_auc, higher_is_better=True,
        skip_env_var="GAME_SKIP_LEAKAGE_CHECK", cv_folds_fn=_cv_folds,
        threshold=threshold, logger=logger,
    )


def run() -> dict:
    from mlb_core.config import GCS_BUCKET

    # Load Optuna-tuned params if available
    try:
        from mlb.training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params(SYSTEM)
        if tuned:
            global XGB_PARAMS
            XGB_PARAMS = tuned["xgb_params"]
            logger.info("Using Optuna-tuned params (score=%s)", tuned["best_score"])
    except Exception as e:
        logger.warning("Could not load tuned params: %s", e)

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in GAME_FEATURES if f in df.columns]
    missing   = [f for f in GAME_FEATURES if f not in df.columns]
    if missing:
        logger.warning("missing features (skipped): %s", missing)

    try:
        wf = _walk_forward_cv(df, available)
    except Exception as e:
        return {"status": "error", "error": f"walk-forward CV: {e}"}

    try:
        oos = _oos_eval(df, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    leakage_suspects = _leakage_check(df, available, oos)

    try:
        booster = _full_retrain(df, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    fmeans = _feature_means(df, available)
    fstds = common.feature_stds(df, available)
    fpdists = common.feature_dists(df, available)

    cv_ci_lo, cv_ci_hi, wf_summary = common.wf_ci_and_summary(
        wf, primary_metric="auc", metric_names=["auc", "brier", "logloss"],
        require_ci_for_summary=False,
    )
    # NOTE: retrain_game_v1.py originally named these keys cv_auc_ci_lo/hi
    # (not cv_mae_ci_lo/hi like the count systems); rename below.

    meta = {
        "version":        VERSION,
        "model_type":     "game_binary_logistic",
        "trained_at":     datetime.now(timezone.utc).isoformat(),
        "full_retrain":   True,
        "features":       available,
        "feature_dists":  fpdists,
        "feature_means":  fmeans,
        "feature_stds":   fstds,
        "cv_folds":       _cv_folds(df),
        "cv_auc_ci_lo":   cv_ci_lo,
        "cv_auc_ci_hi":   cv_ci_hi,
        **oos,
        **wf_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    ts = common.ts()
    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    err = common.persist_model_artifacts(
        booster, meta_bytes,
        steps=[
            {"kind": "booster", "key": booster_archive_key, "label": "archive booster"},
            {"kind": "meta",    "key": meta_archive_key,    "label": "archive meta"},
            {"kind": "booster", "key": GCS_BOOSTER_LATEST,  "label": "latest booster"},
            {"kind": "meta",    "key": GCS_META_LATEST,     "label": "latest meta"},
        ],
        logger=logger,
    )
    if err:
        return err

    return {
        "status":           "ok",
        "version":          VERSION,
        "features":         len(available),
        "train_rows":       oos["train_rows"],
        "test_rows":        oos["test_rows"],
        "auc_oos":          oos["auc_oos"],
        "brier_oos":        oos["brier_oos"],
        "logloss_oos":      oos["logloss_oos"],
        "best_iteration":   oos["best_iteration"],
        "wf_auc":           wf_summary.get("wf_auc"),
        "leakage_suspects": leakage_suspects,
        "booster_archive":  booster_archive_key,
        "meta_archive":     meta_archive_key,
        "booster_latest":   GCS_BOOSTER_LATEST,
        "meta_latest":      GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
