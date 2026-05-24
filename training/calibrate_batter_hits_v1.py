"""
training/calibrate_batter_hits_v1.py — Fit lambda calibrator for BATTER_HITS v1.

BATTER_HITS model: count:poisson predicting lambda (expected hits per game).
The runner converts lambda -> P(hits > line) via NegBin CDF. If lambda is
systematically inflated, every P(over) is inflated, producing fake edges.

Calibration: fit IsotonicRegression(lambda_raw -> actual_hits) on the OOS
train split. Mirrors calibrate_k_v1.py exactly.

Entrypoint: python -m training.calibrate_batter_hits_v1
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

VERSION          = "v1"
TRAIN_TEST_SPLIT = 0.8
TARGET           = "batter_hits"

GCS_MODEL_FEATURES = "BATTER_HITS_System/data/model_features.csv"
GCS_BOOSTER        = "BATTER_HITS_System/models/xgb_batter_hits_v1.json"
GCS_META           = "BATTER_HITS_System/models/model_meta_batter_hits_v1.json"
GCS_CALIBRATOR     = "BATTER_HITS_System/models/lambda_calibrator_batter_hits_v1.pkl"

BATTER_HITS_FEATURES = [
    "hits_per_game_L20", "hits_per_game_L50",
    "hits_rate_L20", "hits_rate_season",
    "babip_L20", "babip_L50",
    "contact_pct_L20", "chase_pct_L20",
    "ld_rate_L20", "gb_rate_L20",
    "hard_hit_L20",
    "batter_pa_per_game_L20", "ewma_batting_order",
    "hits_vs_hand_career", "hits_vs_hand_season",
    "pitcher_babip_allowed_L20", "pitcher_hits_per_9_L20",
    "pitcher_gb_rate_L20", "pitcher_k_pct_L20",
    "hits_park_factor",
    "is_home", "temperature_f", "is_dome", "post_pitch_clock",
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
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean hits={df[TARGET].mean():.3f}"
    )
    return df, None


def _load_booster():
    from mlb_core.storage import read_bytes, download_model
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features") or BATTER_HITS_FEATURES
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
    df["lambda_hits"] = lambdas

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
        f"OOS | actual mean hits={oos[TARGET].mean():.3f} | "
        f"model mean lambda={oos['lambda_hits'].mean():.3f} | "
        f"raw bias={oos['lambda_hits'].mean() - oos[TARGET].mean():+.3f}"
    )

    if len(oos) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos)} rows) — need >= 50"}

    train_df = df.iloc[:split_idx].copy()
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_df["lambda_hits"].values, train_df[TARGET].values)

    cal_lambdas = iso.predict(oos["lambda_hits"].values)

    raw_mae = float(np.mean(np.abs(oos["lambda_hits"].values - oos[TARGET].values)))
    cal_mae = float(np.mean(np.abs(cal_lambdas - oos[TARGET].values)))
    raw_bias = float(oos["lambda_hits"].mean() - oos[TARGET].mean())
    cal_bias = float(cal_lambdas.mean() - oos[TARGET].mean())
    gap      = abs(cal_lambdas.mean() - oos[TARGET].mean())

    logger.info(
        f"calibration | raw MAE={raw_mae:.4f} | calibrated MAE={cal_mae:.4f} | "
        f"improvement={raw_mae - cal_mae:+.4f}"
    )
    if gap > 0.3:
        logger.warning(
            f"calibrated mean still {gap:.3f} hits from actual — "
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
        "status":               "ok",
        "version":              VERSION,
        "train_rows":           split_idx,
        "oos_rows":             len(oos),
        "oos_from":             str(oos_from),
        "oos_mean_actual_hits": round(float(oos[TARGET].mean()), 4),
        "raw_mae":              round(raw_mae, 4),
        "calibrated_mae":       round(cal_mae, 4),
        "raw_bias":             round(raw_bias, 4),
        "calibrated_bias":      round(cal_bias, 4),
        "raw_mean_lambda":      round(float(oos["lambda_hits"].mean()), 4),
        "calibrated_mean":      round(float(cal_lambdas.mean()), 4),
        "gcs_calibrator":       GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
