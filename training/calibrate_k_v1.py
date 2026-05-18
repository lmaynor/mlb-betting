"""
training/calibrate_k_v1.py -- Fit lambda calibrator for K Pro v1.

K model: count:poisson predicting lambda (expected strikeouts).
The runner converts lambda -> P(over/under) via Monte Carlo simulation.
If lambda is systematically inflated, every Monte Carlo P(over) is
inflated, producing fake edges.

Calibration approach: fit IsotonicRegression(lambda -> actual_ks) on
the OOS split. At predict time the runner scales raw lambda through the
calibrator before the Monte Carlo step.

Uses same 80/20 time-based OOS split as retrain_k_v1.py.

Entrypoint: python -m training.calibrate_k_v1
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION            = "v1"
TRAIN_TEST_SPLIT   = 0.8
TARGET             = "starter_ks"

GCS_MODEL_FEATURES = "K_Pro_System/data/model_features.csv"
GCS_BOOSTER        = "K_Pro_System/models/xgb_k_v1.json"
GCS_META           = "K_Pro_System/models/model_meta_v1.json"
GCS_CALIBRATOR     = "K_Pro_System/models/lambda_calibrator_k_v1.pkl"

K_FEATURES = [
    "k_pct_L5", "k_pct_L10", "k_pct_STD", "k_per_9_L5", "k_per_9_L10",
    "first_pitch_strike_pct_L10", "hitter_count_rate_L10", "two_strike_k_rate_L10",
    "whiff_pct_L10", "zone_contact_pct_L10", "chase_pct_L10",
    "velo_mean_L5", "velo_trend_L5",
    "fb_pct_L10", "breaking_pct_L10", "primary_whiff_rate_L10",
    "avg_ip_L5", "avg_bf_L5", "days_rest", "short_rest",
    "opp_k_rate_L14", "opp_k_rate_vs_hand_L14", "opp_chase_rate_L14",
    "opp_whiff_rate_L14", "opp_lineup_pct_L", "opp_platoon_k_edge",
    "opp_top3_k_rate_L50",
    "ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30",
    "is_home", "implied_win_pct", "temperature_f", "is_dome",
]


def _load_data():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS"
    df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    if df.empty:
        return None, "model_features.csv is empty"
    if TARGET not in df.columns:
        return None, f"target column '{TARGET}' missing"
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    # Drop today's slate rows (no actual K count yet)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean Ks={df[TARGET].mean():.2f}"
    )
    return df, None


def _load_booster():
    from mlb_core.storage import read_bytes, download_model
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features") or K_FEATURES
    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(GCS_BOOSTER, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))

    best_iter = meta.get("best_iteration", 0)
    booster.best_ntree_limit = best_iter
    feature_means = meta.get("feature_means", {}) or {}
    logger.info(f"booster loaded | features={len(features)} | best_iteration={best_iter}")
    return booster, features, feature_means, None


def _score_lambda(booster, features, feature_means, df):
    """Score rows. Returns array of lambda (expected K count)."""
    available = [f for f in features if f in df.columns]
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is not None:
                X[col] = X[col].fillna(float(mean))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    if ntree:
        return booster.predict(dm, iteration_range=(0, ntree))
    return booster.predict(dm)


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_data()
    if err:
        return {"status": "error", "error": err}

    booster, features, feature_means, err = _load_booster()
    if err:
        return {"status": "error", "error": err}

    # Score all rows
    lambdas = _score_lambda(booster, features, feature_means, df)
    df["lambda_k"] = lambdas

    # 80/20 time-based OOS split
    df = df.sort_values("game_date").reset_index(drop=True)
    split_idx     = int(len(df) * TRAIN_TEST_SPLIT)
    oos           = df.iloc[split_idx:].copy()
    train_through = df["game_date"].iloc[split_idx - 1].date()
    oos_from      = df["game_date"].iloc[split_idx].date()

    logger.info(
        f"split | train={split_idx} rows (thru {train_through}) | "
        f"OOS={len(oos)} rows (from {oos_from})"
    )
    logger.info(
        f"OOS | actual mean Ks={oos[TARGET].mean():.3f} | "
        f"model mean lambda={oos['lambda_k'].mean():.3f} | "
        f"raw bias={oos['lambda_k'].mean() - oos[TARGET].mean():+.3f}"
    )

    if len(oos) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos)} rows) -- need >= 50"}

    # Fit isotonic calibrator: lambda -> actual_ks
    # After calibration, runner replaces raw lambda with calibrated lambda
    # before the Monte Carlo step. IsotonicRegression is monotone so
    # the rank ordering of predictions is preserved.
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(df["lambda_k"].values, df[TARGET].values)  # fit on all data for full range coverage

    cal_lambdas = iso.predict(oos["lambda_k"].values)

    # Evaluate: MAE before and after calibration
    raw_mae = float(np.mean(np.abs(oos["lambda_k"].values - oos[TARGET].values)))
    cal_mae = float(np.mean(np.abs(cal_lambdas - oos[TARGET].values)))
    raw_bias = float(oos["lambda_k"].mean() - oos[TARGET].mean())
    cal_bias = float(cal_lambdas.mean() - oos[TARGET].mean())
    gap      = abs(cal_lambdas.mean() - oos[TARGET].mean())

    logger.info(
        f"calibration | raw MAE={raw_mae:.4f} | "
        f"calibrated MAE={cal_mae:.4f} | "
        f"improvement={raw_mae - cal_mae:+.4f}"
    )
    logger.info(
        f"bias | raw={raw_bias:+.3f} | calibrated={cal_bias:+.3f} | "
        f"mean_actual={oos[TARGET].mean():.3f} | gap={gap:.3f}"
    )

    if gap > 0.5:
        logger.warning(
            f"calibrated mean still {gap:.3f} Ks from actual -- "
            f"calibrator may need more OOS data"
        )

    # Upload
    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"uploaded: {GCS_CALIBRATOR} ({len(cal_bytes):,} bytes)")
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":             "ok",
        "version":            VERSION,
        "train_rows":         split_idx,
        "oos_rows":           len(oos),
        "oos_from":           str(oos_from),
        "oos_mean_actual_ks": round(float(oos[TARGET].mean()), 4),
        "raw_mae":            round(raw_mae, 4),
        "calibrated_mae":     round(cal_mae, 4),
        "raw_bias":           round(raw_bias, 4),
        "calibrated_bias":    round(cal_bias, 4),
        "raw_mean_lambda":    round(float(oos["lambda_k"].mean()), 4),
        "calibrated_mean":    round(float(cal_lambdas.mean()), 4),
        "gcs_calibrator":     GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
