"""
training/retrain_sb_v1.py -- SB (stolen base) Pro v1 full retrain.

NegBin count regressor: XGBoost count:poisson predicting lambda (expected
stolen bases per game). At score time, P(SB > line) = 1 - NegBin_CDF(floor(line),
lambda, nb_alpha). Mirrors retrain_batter_hits_v1.py exactly -- same NegBin
architecture, same market shape (real O/U, confirmed live 2026-08-20).

Trains on 2023+ data ONLY (see SB_Pro_System/config_sb.py's season_start) --
the 2023-03-30 pitch-clock/bigger-base rules shifted stolen-base behavior
materially, so pre-2023 data is a different game, not just more history.

Output GCS keys (matches SB_Pro_System/config_sb.py):
  - SB_Pro_System/models/xgb_sb_v1.json
  - SB_Pro_System/models/model_meta_sb_v1.json
  - SB_Pro_System/models/archive/...{ts}...

The CV/eval/persist mechanics are shared with retrain_k_v1.py/
retrain_outs_v1.py/retrain_batter_hits_v1.py/retrain_game_v1.py via
_retrain_common.py -- see that module's docstring for what's shared vs
genuinely per-system.

Entrypoint: python -m mlb.training.retrain_sb_v1
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

import pandas as pd
import xgboost as xgb

from mlb.systems.SB_Pro_System.config_sb import SB_FEATURES
from mlb.training import _retrain_common as common
from mlb.training._retrain_common import cv_folds as _cv_folds
from mlb.training._retrain_common import mae as _mae
from mlb.training._retrain_common import rmse as _rmse
from mlb.training._retrain_common import r2 as _r2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)


VERSION = "v1"
TARGET  = "stolen_bases"

# Optuna-tuned 2026-08-20 (E09, 50 trials, nested time-series CV) --
# GCS_BUCKET/SB_Pro_System/models/sb_tuned_params.json, best_score=0.0008.
# Promoted to the static default here so walkforward.py's rolling backtest
# (which reads this module-level dict directly, not run()'s GCS pickup)
# exercises the tuned params too. Pre-tuning values, for reference:
# max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
# min_child_weight=20, reg_alpha=1.0, reg_lambda=3.0, gamma=0.5.
XGB_PARAMS = {
    "objective":        "count:poisson",
    "eval_metric":      "poisson-nloglik",
    "max_depth":        3,
    "learning_rate":    0.08962530515077032,
    "subsample":        0.6543593406804693,
    "colsample_bytree": 0.9687941853341576,
    "min_child_weight": 5,
    "reg_alpha":        0.06571433972048624,
    "reg_lambda":       1.8306495567702976,
    "gamma":            0.005556621064822541,
    "seed":             42,
}

NUM_BOOST_ROUND       = 2000
EARLY_STOPPING_ROUNDS = 50

# Per-system metric family for the shared CV/OOS/leakage mechanics (count:
# MAE/RMSE/R2). "mae" is the PRIMARY metric.
_METRICS = [("mae", _mae), ("rmse", _rmse), ("r2", _r2)]


GCS_MODEL_FEATURES  = "SB_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"SB_Pro_System/models/xgb_sb_{VERSION}.json"
GCS_META_LATEST     = f"SB_Pro_System/models/model_meta_sb_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"SB_Pro_System/models/archive/xgb_sb_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"SB_Pro_System/models/archive/model_meta_sb_{VERSION}.{{ts}}.json"


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found -- run /build-features for SB first"
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

    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    logger.info(
        f"loaded features: {len(df):,} training rows ({dropped} slate rows excluded) | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean SB={df[TARGET].mean():.4f} | pct games with SB>=1={100*(df[TARGET]>=1).mean():.2f}%"
    )
    return df, None


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    return common.walk_forward_cv(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_train=100, min_test=20, cv_folds_fn=_cv_folds,
        filter_train_years=True, logger=logger,
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
    return common.leakage_check(
        df, features, oos, TARGET, XGB_PARAMS,
        baseline_key="mae_oos", metric_fn=_mae, higher_is_better=False,
        skip_env_var="SB_SKIP_LEAKAGE_CHECK", cv_folds_fn=_cv_folds,
        threshold=threshold, logger=logger,
    )


def run() -> dict:
    from mlb_core.config import GCS_BUCKET

    try:
        from mlb.training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params("SB")
        if tuned:
            global XGB_PARAMS
            XGB_PARAMS = tuned["xgb_params"]
            logger.info(f"Using Optuna-tuned params (score={tuned['best_score']})")
    except Exception as e:
        logger.warning(f"Could not load tuned params: {e}")

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in SB_FEATURES if f in df.columns]
    missing   = [f for f in SB_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (skipped): {missing}")

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

    # NB dispersion: var = mu + alpha*mu^2  =>  alpha = (var - mu) / mu^2.
    # Stolen bases are a rare, highly right-skewed count (most games are 0) --
    # expect nb_alpha to land near the high end of the clip range.
    nb_alpha = common.fit_nb_alpha(booster, df, available, TARGET, logger=logger)

    fmeans = _feature_means(df, available)
    fstds = common.feature_stds(df, available)
    fpdists = common.feature_dists(df, available)

    cv_ci_lo, cv_ci_hi, wf_summary = common.wf_ci_and_summary(
        wf, primary_metric="mae", metric_names=["mae", "rmse", "r2"],
        require_ci_for_summary=False,
    )

    meta = {
        "version":       VERSION,
        "model_type":    "sb_poisson",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      available,
        "nb_alpha":      nb_alpha,
        "feature_dists": fpdists,
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
        "mae_oos":          oos["mae_oos"],
        "rmse_oos":         oos["rmse_oos"],
        "r2_oos":           oos["r2_oos"],
        "best_iteration":   oos["best_iteration"],
        "nb_alpha":         nb_alpha,
        "wf_mae":           wf_summary.get("wf_mae"),
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
