"""
training/retrain_nrfi_v17.py — NRFI Pro v17 full retrain (Cloud Run Job).

Mirrors NRFI_Pro_Complete_v17.ipynb Sections 8 + 8b:
  1. Load model_features.csv from GCS.
  2. Section 8: time-based 80/20 split, train with early stopping,
     capture OOS metrics (auc_oos, brier_oos, logloss_oos, best_iteration).
  3. Section 8b: retrain on 100% of data using best_iteration rounds.
  4. Compute feature_means from full training set (for runner NaN-fill).
  5. Write archive + latest pointer to GCS for both booster and meta.

Output GCS keys (matches NRFI_Pro_System/config_nrfi.py):
  - NRFI_Pro_System/models/xgb_halfinn_v17.json
  - NRFI_Pro_System/models/model_meta_v17.json
  - NRFI_Pro_System/models/archive/xgb_halfinn_v17.{ts}.json
  - NRFI_Pro_System/models/archive/model_meta_v17.{ts}.json

Notebook contract (HALFINN_FEATURES + XGB_PARAMS) is duplicated below so this
script is self-contained. If notebook cell `v17_02296` changes, mirror the
change here AND flag it in the next handoff. No automated drift check.

Entrypoint: `python -m training.retrain_nrfi_v17` (the Cloud Run Job command).
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Notebook contract (cell v17_02296). Keep in sync with NRFI v17 notebook ──

VERSION = "v17"

# NOTE: lineup_pct_L was removed from HALFINN_FEATURES on 2026-05-12 after
# diagnosing AUC inflation in the deployed retrain Job. With lineup_pct_L
# in the feature set, test AUC was 0.7763 (train 0.8069). With it removed,
# AUC drops to 0.5765 (train 0.7011), matching the notebook's documented
# v16/v17 OOS performance (~0.59). lineup_pct_L has only 50 unique values
# (fractions of LHB count in top 3 batters) and corr(lineup_pct_L, yrfi)
# = -0.0294 — pairwise signal is noise-level. The 0.20 AUC contribution
# came from non-linear interactions that, combined with weather/umpire/
# park features, fingerprint individual games. platoon_edge (a derived
# feature: woba_split_STD * (lineup_pct_L - 0.40)) was diagnosed
# separately — it adds ~0.04 AUC of independent signal (15k unique values,
# corr -0.10 with lineup_pct_L) so it stays. The other handedness
# features (woba_vs_L_STD, woba_vs_R_STD, woba_split_STD) contribute
# essentially zero on their own (~0.002 AUC) but stay for notebook parity.
# If reintroducing lineup_pct_L, re-run the leave-one-out diagnostic on
# fresh data first. See session transcript 2026-05-12 for full analysis.
HALFINN_FEATURES = [
    # Pitcher rolling (last 3 starts)
    "zone_pct_L3", "chase_pct_L3", "whiff_pct_L3", "k_pct_L3", "bb_pct_L3",
    "hard_hit_pct_L3", "barrel_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
    "primary_whiff_rate_L3", "called_strike_pct_L3",
    # Pitcher rolling (last 10 starts)
    "zone_pct_L10", "whiff_pct_L10", "k_pct_L10", "xwoba_allowed_L10", "velo_mean_L10",
    # Pitcher trend + context
    "velo_trend_L5", "days_rest", "short_rest", "arm_angle", "pitcher_is_home",
    # Batter quality (top-3 rolling)
    "top3_batter_woba_value_L50", "top3_batter_is_hard_hit_L50",
    "top3_batter_is_bb_L50", "top3_batter_is_k_L50",
    # Weather
    "temperature_f", "wind_speed_mph", "is_outdoor", "wind_out", "wind_in",
    "is_cold", "is_hot", "high_wind",
    # Park
    "park_factor",
    # Umpire
    "ump_overall_accuracy_L30", "ump_total_run_impact_L30", "ump_consistency_L30",
    # Platoon splits
    "woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
    "platoon_edge",  # lineup_pct_L removed 2026-05-12 — see comment above HALFINN_FEATURES
]

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      ["logloss", "auc"],
    "max_depth":        3,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha":        1.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
}

NUM_BOOST_ROUND = 800
EARLY_STOPPING_ROUNDS = 50
TARGET = "yrfi"
TRAIN_TEST_SPLIT = 0.8

# GCS keys — match NRFI_Pro_System/config_nrfi.py exactly
GCS_MODEL_FEATURES   = "NRFI_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST   = f"NRFI_Pro_System/models/xgb_halfinn_{VERSION}.json"
GCS_META_LATEST      = f"NRFI_Pro_System/models/model_meta_{VERSION}.json"
GCS_BOOSTER_ARCHIVE  = f"NRFI_Pro_System/models/archive/xgb_halfinn_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE     = f"NRFI_Pro_System/models/archive/model_meta_{VERSION}.{{ts}}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS — run /build-features for NRFI first"
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
    logger.info(f"loaded features: {len(df):,} rows | "
                f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
                f"YRFI rate {df[TARGET].mean():.3f}")
    return df, None


def _oos_eval(df: pd.DataFrame, X: pd.DataFrame, y: pd.Series,
              features: list) -> dict:
    """Section 8: 80/20 time-split + early stopping. Returns OOS metrics dict."""
    split_idx = int(len(X) * TRAIN_TEST_SPLIT)
    X_tr, X_te = X.iloc[:split_idx], X.iloc[split_idx:]
    y_tr, y_te = y.iloc[:split_idx], y.iloc[split_idx:]
    dates = df["game_date"]
    train_through = dates.iloc[split_idx - 1].strftime("%Y-%m-%d")
    test_from = dates.iloc[split_idx].strftime("%Y-%m-%d")

    logger.info(f"OOS split | train={len(X_tr)} (thru {train_through}) | "
                f"test={len(X_te)} (from {test_from}) | features={len(features)}")
    logger.info(f"YRFI rate | train={y_tr.mean():.3f} | test={y_te.mean():.3f}")

    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
    dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)

    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dtest, "test")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )

    preds = booster.predict(dtest)
    auc   = float(roc_auc_score(y_te, preds))
    brier = float(brier_score_loss(y_te, preds))
    ll    = float(log_loss(y_te, preds))

    # XGBoost's best_iteration is 0-indexed; the runner uses it as a count via
    # iteration_range=(0, ntree). Convert to count by adding 1, matching the
    # notebook's `best_iter = int(...best_iteration) + 1` convention.
    best_iter_count = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(f"OOS results | AUC={auc:.4f} Brier={brier:.4f} "
                f"LogLoss={ll:.4f} best_iter={best_iter_count}")
    logger.info(f"OOS calibration | mean_pred={preds.mean():.4f} "
                f"mean_actual={y_te.mean():.4f} gap={preds.mean() - y_te.mean():+.4f}")

    return {
        "train_through":  train_through,
        "test_from":      test_from,
        "train_rows":     int(len(X_tr)),
        "test_rows":      int(len(X_te)),
        "auc_oos":        round(auc, 4),
        "brier_oos":      round(brier, 4),
        "logloss_oos":    round(ll, 4),
        "mean_pred":      round(float(preds.mean()), 4),
        "mean_actual":    round(float(y_te.mean()), 4),
        "best_iteration": best_iter_count,
    }


def _full_retrain(X: pd.DataFrame, y: pd.Series, features: list,
                  best_iter: int) -> xgb.Booster:
    """Section 8b: train on 100% of data using best_iteration rounds."""
    logger.info(f"full retrain | rows={len(X)} features={len(features)} rounds={best_iter}")
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    # Notebook uses single eval_metric in full retrain (avoids extra logging)
    params = {**XGB_PARAMS, "eval_metric": "logloss"}
    booster = xgb.train(
        params, dtrain,
        num_boost_round=best_iter,
        verbose_eval=False,
    )
    return booster


def _feature_means(X: pd.DataFrame, features: list) -> dict:
    """Per-column training-set means. NaN-only columns are skipped (logged)."""
    means, skipped = {}, []
    for f in features:
        v = X[f].mean(skipna=True)
        if pd.isna(v):
            skipped.append(f)
        else:
            means[f] = float(v)
    if skipped:
        logger.warning(f"feature_means could not be computed for "
                       f"{len(skipped)} features: {sorted(skipped)[:5]}")
    logger.info(f"feature_means computed for {len(means)}/{len(features)} features")
    return means


def run() -> dict:
    """Orchestrate the retrain. Returns structured result for the Cloud Run Job."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "MLB_GCS_BUCKET not set — this job requires GCS mode"}

    # 1. Load features
    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in HALFINN_FEATURES if f in df.columns]
    missing = [f for f in HALFINN_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (will be skipped): {missing}")

    X = df[available].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(int)

    # 2. OOS eval (Section 8)
    try:
        oos = _oos_eval(df, X, y, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    # 3. Full retrain (Section 8b)
    try:
        booster = _full_retrain(X, y, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    # 4. feature_means
    fmeans = _feature_means(X, available)

    # 5. Build meta
    meta = {
        "version":      VERSION,
        "model_type":   "halfinn",
        "trained_at":   datetime.now(timezone.utc).isoformat(),
        "full_retrain": True,
        "features":     available,
        "feature_means": fmeans,
        # OOS metrics from Section 8 — never overwritten by full retrain
        **oos,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    # 6. Serialize booster to tmp file (XGBoost save_model needs a path)
    ts = _ts()
    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        booster_tmp = Path(f.name)
    try:
        booster.save_model(str(booster_tmp))

        # 7. Write archives first (preserve history if latest write fails)
        try:
            upload_model(booster_tmp, booster_archive_key)
            logger.info(f"archive booster: {booster_archive_key}")
        except Exception as e:
            return {"status": "error", "error": f"archive booster write: {e}"}

        try:
            write_bytes(meta_bytes, meta_archive_key)
            logger.info(f"archive meta:    {meta_archive_key}")
        except Exception as e:
            return {"status": "error", "error": f"archive meta write: {e}"}

        # 8. Then write latest pointers
        try:
            upload_model(booster_tmp, GCS_BOOSTER_LATEST)
            logger.info(f"latest booster:  {GCS_BOOSTER_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest booster write: {e}"}

        try:
            write_bytes(meta_bytes, GCS_META_LATEST)
            logger.info(f"latest meta:     {GCS_META_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest meta write: {e}"}
    finally:
        booster_tmp.unlink(missing_ok=True)

    return {
        "status":            "ok",
        "version":           VERSION,
        "features":          len(available),
        "feature_means":     len(fmeans),
        "train_rows":        oos["train_rows"],
        "test_rows":         oos["test_rows"],
        "auc_oos":           oos["auc_oos"],
        "brier_oos":         oos["brier_oos"],
        "best_iteration":    oos["best_iteration"],
        "booster_archive":   booster_archive_key,
        "meta_archive":      meta_archive_key,
        "booster_latest":    GCS_BOOSTER_LATEST,
        "meta_latest":       GCS_META_LATEST,
    }


def main():
    """Cloud Run Job entrypoint. Logs result and exits non-zero on failure."""
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
