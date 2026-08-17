"""
training/calibrate_game_v1.py -- Fit isotonic calibrator for GAME Pro v1.

GAME model: binary:logistic predicting P(home team wins full game).
The raw model output is a sigmoid probability in [0, 1]. Isotonic regression
maps raw_prob -> calibrated_prob on an 80/20 time-based OOS split.

Calibration: IsotonicRegression(raw_prob -> home_win) on the OOS train split.
Evaluation: Brier score (lower is better) instead of MAE.
Mirrors calibrate_batter_hits_v1.py exactly, adapted for binary classification.

Entrypoint: python -m training.calibrate_game_v1
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

from mlb.systems.GAME_Pro_System.config_game import GAME_FEATURES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION          = "v1"
TRAIN_TEST_SPLIT = 0.8
TARGET           = "home_win"

GCS_MODEL_FEATURES = "GAME_Pro_System/data/model_features.csv"
GCS_BOOSTER        = "GAME_Pro_System/models/xgb_game_v1.json"
GCS_META           = "GAME_Pro_System/models/model_meta_game_v1.json"
GCS_CALIBRATOR     = "GAME_Pro_System/models/isotonic_calibrator_game_v1.pkl"


def _brier(y_true, y_pred):
    return float(np.mean((np.array(y_pred) - np.array(y_true)) ** 2))


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
    df[TARGET] = df[TARGET].astype(int)
    logger.info(
        "loaded %d rows | %s -> %s | home_win_rate=%.3f",
        len(df),
        df["game_date"].min().date(), df["game_date"].max().date(),
        df[TARGET].mean(),
    )
    return df, None


def _load_booster():
    from mlb_core.storage import read_bytes, download_model
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features") or GAME_FEATURES
    booster  = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(GCS_BOOSTER, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))

    best_iter = meta.get("best_iteration", 0)
    booster.best_ntree_limit = best_iter
    feature_means = meta.get("feature_means", {}) or {}
    logger.info("booster loaded | features=%d | best_iteration=%d | AUC_OOS=%s",
                len(features), best_iter, meta.get("auc_oos", "?"))
    return booster, features, feature_means, None


def _score_prob(booster, features, feature_means, df):
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is not None:
                X[col] = X[col].fillna(float(mean))
    X  = X.astype(float)
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

    raw_probs = _score_prob(booster, features, feature_means, df)
    df["raw_prob"] = raw_probs

    df = df.sort_values("game_date").reset_index(drop=True)
    split_idx     = int(len(df) * TRAIN_TEST_SPLIT)
    oos           = df.iloc[split_idx:].copy()
    train_through = df["game_date"].iloc[split_idx - 1].date()
    oos_from      = df["game_date"].iloc[split_idx].date()

    logger.info(
        "split | train=%d rows (thru %s) | OOS=%d rows (from %s)",
        split_idx, train_through, len(oos), oos_from,
    )
    logger.info(
        "OOS | actual win_rate=%.3f | model mean_prob=%.3f | raw_bias=%+.3f",
        oos[TARGET].mean(), oos["raw_prob"].mean(),
        oos["raw_prob"].mean() - oos[TARGET].mean(),
    )

    if len(oos) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos)} rows) -- need >= 50"}

    train_df = df.iloc[:split_idx].copy()
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_df["raw_prob"].values, train_df[TARGET].values.astype(float))

    cal_probs = iso.predict(oos["raw_prob"].values)
    cal_probs = np.clip(cal_probs, 0.0, 1.0)

    raw_brier = _brier(oos[TARGET].values, oos["raw_prob"].values)
    cal_brier = _brier(oos[TARGET].values, cal_probs)
    raw_bias  = float(oos["raw_prob"].mean() - oos[TARGET].mean())
    cal_bias  = float(cal_probs.mean() - oos[TARGET].mean())
    gap       = abs(cal_probs.mean() - oos[TARGET].mean())

    logger.info(
        "calibration | raw Brier=%.4f | calibrated Brier=%.4f | improvement=%+.4f",
        raw_brier, cal_brier, raw_brier - cal_brier,
    )
    if gap > 0.05:
        logger.warning(
            "calibrated mean still %.3f from actual win rate -- "
            "calibrator may need more OOS data",
            gap,
        )
    if cal_brier > raw_brier:
        logger.warning(
            "CALIBRATOR DEGRADES OOS Brier (%.4f -> %.4f). "
            "Raw probability is more accurate on this split.",
            raw_brier, cal_brier,
        )

    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info("uploaded: %s (%d bytes)", GCS_CALIBRATOR, len(cal_bytes))
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":                  "ok",
        "version":                 VERSION,
        "train_rows":              split_idx,
        "oos_rows":                len(oos),
        "oos_from":                str(oos_from),
        "oos_home_win_rate":       round(float(oos[TARGET].mean()), 4),
        "raw_brier":               round(raw_brier, 4),
        "calibrated_brier":        round(cal_brier, 4),
        "brier_improvement":       round(raw_brier - cal_brier, 4),
        "raw_bias":                round(raw_bias, 4),
        "calibrated_bias":         round(cal_bias, 4),
        "raw_mean_prob":           round(float(oos["raw_prob"].mean()), 4),
        "calibrated_mean_prob":    round(float(cal_probs.mean()), 4),
        "gcs_calibrator":          GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
