"""
training/retrain_k_v1.py — K Pro v1 full retrain (Cloud Run Job).

Mirrors K_Pro_v1.ipynb Section 7 (OOS eval, walk-forward CV) + Section 7b
(full retrain on all data). Adapts the NRFI v17 retrain pattern:
  1. Load model_features.csv from GCS, drop today's-slate rows (target NaN).
  2. Walk-forward CV across cfg['cv_folds'] for diagnostic MAE/RMSE/R².
  3. Section 7-style OOS split (train: pre-last_fold, test: last_fold) to
     get best_iteration.
  4. Section 7b: retrain on 100% of data using best_iteration rounds.
  5. Compute feature_means from full training set (for runner NaN-fill).
  6. Write archive + latest pointer to GCS for both booster and meta.

Output GCS keys (matches K_Pro_System/config_k.py):
  - K_Pro_System/models/xgb_k_v1.json
  - K_Pro_System/models/model_meta_v1.json
  - K_Pro_System/models/archive/xgb_k_v1.{ts}.json
  - K_Pro_System/models/archive/model_meta_v1.{ts}.json

Notebook contract (K_FEATURES + XGB_PARAMS) is duplicated below so this
script is self-contained. If K_Pro_System/config_k.py:K_FEATURES changes,
mirror here AND flag in the next handoff.

The CV/eval/persist mechanics (walk-forward CV, OOS split, leakage check,
NB dispersion fit, feature stats, CI bootstrap, archive-then-latest GCS
write) are shared with retrain_sb_v1.py/retrain_outs_v1.py/
retrain_batter_hits_v1.py/retrain_game_v1.py via _retrain_common.py --
see that module's docstring for what's shared vs genuinely per-system.

Entrypoint: `python -m training.retrain_k_v1` (Cloud Run Job command).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

import pandas as pd
import xgboost as xgb

from mlb.systems.K_Pro_System.config_k import K_FEATURES
from mlb.training import _retrain_common as common
from mlb.training._retrain_common import cv_folds as _cv_folds
from mlb.training._retrain_common import mae as _mae
from mlb.training._retrain_common import rmse as _rmse
from mlb.training._retrain_common import r2 as _r2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Notebook contract — keep in sync with K_Pro_System/config_k.py:K_FEATURES
# and K_Pro_v1.ipynb Section 0. 34 features.

VERSION = "v1"
TARGET = "starter_ks"

XGB_PARAMS = {
    "objective":        "count:poisson",
    "eval_metric":      "poisson-nloglik",
    "max_depth":        4,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 15,
    "reg_alpha":        1.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
}

NUM_BOOST_ROUND = 2000
EARLY_STOPPING_ROUNDS = 50

# Per-system metric family for the shared CV/OOS/leakage mechanics (count:
# MAE/RMSE/R2). "mae" is the PRIMARY metric -- drives mae_train/overfit_gap
# and the leakage-check baseline.
_METRICS = [("mae", _mae), ("rmse", _rmse), ("r2", _r2)]


# GCS keys — must match K_Pro_System/config_k.py
GCS_MODEL_FEATURES  = "K_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"K_Pro_System/models/xgb_k_{VERSION}.json"
GCS_META_LATEST     = f"K_Pro_System/models/model_meta_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"K_Pro_System/models/archive/xgb_k_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"K_Pro_System/models/archive/model_meta_{VERSION}.{{ts}}.json"


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS — run /build-features for K first"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"
    if df.empty:
        return None, f"{GCS_MODEL_FEATURES} is empty"
    if TARGET not in df.columns:
        return None, f"target column {TARGET!r} missing from features CSV"

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)

    # Drop today's-slate rows (no actual K count yet)
    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    logger.info(f"loaded features: {len(df):,} training rows ({dropped} slate rows excluded) | "
                f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
                f"mean Ks={df[TARGET].mean():.2f}")
    return df, None


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    """Section 7 walk-forward CV: train on prior 2 years, test on held-out year."""
    return common.walk_forward_cv(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_train=50, min_test=10, cv_folds_fn=_cv_folds,
        filter_train_years=False, logger=logger,
    )


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    """Section 7 OOS model: train pre-last-fold, test on last-fold."""
    return common.oos_eval(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_test=10, cv_folds_fn=_cv_folds,
        include_train_metrics=True, logger=logger,
    )


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    """Section 7b: retrain on 100% of data with best_iteration rounds."""
    return common.full_retrain(df, features, TARGET, XGB_PARAMS, best_iter, logger=logger)


def _feature_means(df: pd.DataFrame, features: list) -> dict:
    return common.feature_means(df, features, warn_on_skip=True, logger=logger)


def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.10) -> list[str]:
    """Warn if removing any single feature improves OOS MAE by >threshold.

    A feature whose removal improves MAE by >10% may be carrying target
    information (leakage). Warning only -- does not abort the retrain.
    Skipped if env var K_SKIP_LEAKAGE_CHECK=1 is set (for fast reruns).

    Only checks features with >50% non-NaN coverage to avoid false
    positives from sparse columns.
    """
    return common.leakage_check(
        df, features, oos, TARGET, XGB_PARAMS,
        baseline_key="mae_oos", metric_fn=_mae, higher_is_better=False,
        skip_env_var="K_SKIP_LEAKAGE_CHECK", cv_folds_fn=_cv_folds,
        threshold=threshold, logger=logger,
    )


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in K_FEATURES if f in df.columns]
    missing = [f for f in K_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (will be skipped): {missing}")

    try:
        wf = _walk_forward_cv(df, available)
    except Exception as e:
        return {"status": "error", "error": f"walk-forward CV: {e}"}

    try:
        oos = _oos_eval(df, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    leakage_suspects = _leakage_check(df, available, oos)
    if leakage_suspects:
        logger.warning(f"proceeding with retrain despite {len(leakage_suspects)} "
                       f"leakage suspect(s) -- review before promoting to production")

    try:
        booster = _full_retrain(df, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    # C07: fit NB dispersion parameter from full-data residuals.
    nb_alpha = common.fit_nb_alpha(booster, df, available, TARGET, logger=logger)

    fmeans = _feature_means(df, available)

    # T10: feature_stds for PSI drift monitor (T14)
    fstds = common.feature_stds(df, available)

    # C04: empirical percentiles for PSI drift monitor.
    fpdists = common.feature_dists(df, available)

    # T10: Bootstrap 95% CI on CV mean MAE + wf_* summary
    cv_ci_lo, cv_ci_hi, wf_summary = common.wf_ci_and_summary(
        wf, primary_metric="mae", metric_names=["mae", "rmse", "r2"],
        require_ci_for_summary=False,
    )

    meta = {
        "version":       VERSION,
        "model_type":    "k_poisson",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      available,
        "nb_alpha":      nb_alpha,
        "feature_dists":  fpdists,
        "feature_means": fmeans,
        "feature_stds":  fstds,
        "cv_folds":      _cv_folds(df),
        "cv_mae_ci_lo":  cv_ci_lo,
        "cv_mae_ci_hi":  cv_ci_hi,
        **oos,
        **wf_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    ts = common.ts()
    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    # Archives first (preserve history if latest write fails), then latest pointers.
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
        "status":          "ok",
        "version":         VERSION,
        "features":        len(available),
        "feature_means":   len(fmeans),
        "train_rows":      oos["train_rows"],
        "test_rows":       oos["test_rows"],
        "mae_oos":         oos["mae_oos"],
        "rmse_oos":        oos["rmse_oos"],
        "r2_oos":          oos["r2_oos"],
        "best_iteration":  oos["best_iteration"],
        "wf_mae":          wf_summary.get("wf_mae"),
        "wf_mae_std":      wf_summary.get("wf_mae_std"),
        "leakage_suspects": leakage_suspects,
        "booster_archive": booster_archive_key,
        "meta_archive":    meta_archive_key,
        "booster_latest":  GCS_BOOSTER_LATEST,
        "meta_latest":     GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
