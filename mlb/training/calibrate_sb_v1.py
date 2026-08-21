"""
training/calibrate_sb_v1.py -- Fit lambda calibrator for SB (stolen base) v1.

SB model: count:poisson predicting lambda (expected stolen bases per game).
The runner converts lambda -> P(SB > line) via NegBin CDF. If lambda is
systematically inflated, every P(over) is inflated, producing fake edges.
This matters more here than for BATTER_HITS -- SB is a much rarer, more
right-skewed count (most games are 0), so raw lambda bias is easier to miss
by eye and easier for Kelly sizing to amplify into a real loss.

Calibration: fit IsotonicRegression(lambda_raw -> actual_stolen_bases) on
the OOS train split. Mirrors calibrate_batter_hits_v1.py exactly.

Entrypoint: python -m mlb.training.calibrate_sb_v1
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

from mlb.systems.SB_Pro_System.config_sb import SB_FEATURES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION          = "v1"
TRAIN_TEST_SPLIT = 0.8
TARGET           = "stolen_bases"

GCS_MODEL_FEATURES = "SB_Pro_System/data/model_features.csv"
GCS_BOOSTER        = "SB_Pro_System/models/xgb_sb_v1.json"
GCS_META           = "SB_Pro_System/models/model_meta_sb_v1.json"
GCS_CALIBRATOR     = "SB_Pro_System/models/lambda_calibrator_sb_v1.pkl"


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
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean SB={df[TARGET].mean():.4f}"
    )
    return df, None


def _load_booster():
    from mlb_core.storage import read_bytes, download_model
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features") or SB_FEATURES
    booster  = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(GCS_BOOSTER, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))

    best_iter = meta.get("best_iteration", 0)
    booster.best_ntree_limit = best_iter
    feature_means = meta.get("feature_means", {}) or {}
    logger.info(f"booster loaded | features={len(features)} | best_iteration={best_iter}")
    return booster, features, feature_means, None


def _score_lambda(booster, features, feature_means, df):
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

    lambdas = _score_lambda(booster, features, feature_means, df)
    df["lambda_sb"] = lambdas

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
        f"OOS | actual mean SB={oos[TARGET].mean():.4f} | "
        f"model mean lambda={oos['lambda_sb'].mean():.4f} | "
        f"raw bias={oos['lambda_sb'].mean() - oos[TARGET].mean():+.4f}"
    )

    if len(oos) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos)} rows) -- need >= 50"}

    train_df = df.iloc[:split_idx].copy()
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_df["lambda_sb"].values, train_df[TARGET].values)

    cal_lambdas = iso.predict(oos["lambda_sb"].values)

    raw_mae = float(np.mean(np.abs(oos["lambda_sb"].values - oos[TARGET].values)))
    cal_mae = float(np.mean(np.abs(cal_lambdas - oos[TARGET].values)))
    raw_bias = float(oos["lambda_sb"].mean() - oos[TARGET].mean())
    cal_bias = float(cal_lambdas.mean() - oos[TARGET].mean())
    gap      = abs(cal_lambdas.mean() - oos[TARGET].mean())

    logger.info(
        f"calibration | raw MAE={raw_mae:.4f} | calibrated MAE={cal_mae:.4f} | "
        f"improvement={raw_mae - cal_mae:+.4f}"
    )
    if gap > 0.1:
        logger.warning(
            f"calibrated mean still {gap:.3f} SB from actual -- "
            f"calibrator may need more OOS data"
        )
    if cal_mae > raw_mae:
        logger.warning(
            f"CALIBRATOR DEGRADES OOS MAE ({raw_mae:.4f} -> {cal_mae:.4f}). "
            f"Raw lambda is more accurate on this split."
        )

    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"uploaded: {GCS_CALIBRATOR} ({len(cal_bytes):,} bytes)")
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":            "ok",
        "version":           VERSION,
        "train_rows":        split_idx,
        "oos_rows":          len(oos),
        "oos_from":          str(oos_from),
        "oos_mean_actual_sb":round(float(oos[TARGET].mean()), 4),
        "raw_mae":           round(raw_mae, 4),
        "calibrated_mae":    round(cal_mae, 4),
        "raw_bias":          round(raw_bias, 4),
        "calibrated_bias":   round(cal_bias, 4),
        "raw_mean_lambda":   round(float(oos["lambda_sb"].mean()), 4),
        "calibrated_mean":   round(float(cal_lambdas.mean()), 4),
        "gcs_calibrator":    GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
