"""
training/calibrate_batter_tb_v1.py - Fit lambda calibrator for BATTER_TB v1.
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

from mlb.systems.BATTER_TB_System.config_batter_tb import BATTER_TB_FEATURES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger(__name__)

VERSION = "v1"
TRAIN_TEST_SPLIT = 0.8
TARGET = "batter_total_bases"
GCS_MODEL_FEATURES = "BATTER_TB_System/data/model_features.csv"
GCS_BOOSTER = "BATTER_TB_System/models/xgb_batter_tb_v1.json"
GCS_META = "BATTER_TB_System/models/model_meta_batter_tb_v1.json"
GCS_CALIBRATOR = "BATTER_TB_System/models/lambda_calibrator_batter_tb_v1.pkl"


def _load_data():
    from mlb_core.storage import exists, read_csv

    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS"
    df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    if df.empty:
        return None, "model_features.csv is empty"
    if TARGET not in df.columns:
        return None, f"target column {TARGET!r} missing"
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    logger.info(
        "loaded %s rows | %s -> %s | mean TB=%.3f",
        f"{len(df):,}",
        df["game_date"].min().date(),
        df["game_date"].max().date(),
        df[TARGET].mean(),
    )
    return df, None


def _load_booster():
    from mlb_core.storage import download_model, read_bytes

    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"
    features = meta.get("features") or BATTER_TB_FEATURES
    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(GCS_BOOSTER, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    return booster, features, meta.get("feature_means", {}) or {}, None


def _score_lambda(booster, features, feature_means, df):
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    for col in features:
        mean = feature_means.get(col)
        if mean is not None:
            X[col] = X[col].fillna(float(mean))
    dm = xgb.DMatrix(X.astype(float), feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    return booster.predict(dm, iteration_range=(0, ntree)) if ntree else booster.predict(dm)


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

    df["lambda_tb"] = _score_lambda(booster, features, feature_means, df)
    split_idx = int(len(df) * TRAIN_TEST_SPLIT)
    if len(df) - split_idx < 50:
        return {"status": "error", "error": f"OOS split too small ({len(df) - split_idx}) - need >= 50"}

    train_df = df.iloc[:split_idx].copy()
    oos = df.iloc[split_idx:].copy()
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_df["lambda_tb"].values, train_df[TARGET].values)
    cal = iso.predict(oos["lambda_tb"].values)

    raw_mae = float(np.mean(np.abs(oos["lambda_tb"].values - oos[TARGET].values)))
    cal_mae = float(np.mean(np.abs(cal - oos[TARGET].values)))
    raw_bias = float(oos["lambda_tb"].mean() - oos[TARGET].mean())
    cal_bias = float(cal.mean() - oos[TARGET].mean())

    try:
        write_bytes(pickle.dumps(iso, protocol=4), GCS_CALIBRATOR)
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status": "ok",
        "version": VERSION,
        "train_rows": split_idx,
        "oos_rows": len(oos),
        "raw_mae": round(raw_mae, 4),
        "calibrated_mae": round(cal_mae, 4),
        "raw_bias": round(raw_bias, 4),
        "calibrated_bias": round(cal_bias, 4),
        "raw_mean_lambda": round(float(oos["lambda_tb"].mean()), 4),
        "calibrated_mean": round(float(cal.mean()), 4),
        "gcs_calibrator": GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
