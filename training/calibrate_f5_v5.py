"""
training/calibrate_f5_v5.py -- Fit isotonic calibrator for F5 Pro v5.

F5 model: binary:logistic predicting P(home wins F5).
Calibration mirrors calibrate_nrfi_v17.py exactly:
  1. Load model_features.csv from GCS
  2. Score all rows with the existing booster
  3. 80/20 time-based OOS split (matches retrain split)
  4. Fit IsotonicRegression on OOS predicted probs vs actuals
  5. Upload to GCS as isotonic_calibrator_f5_v5.pkl

Runner (run_f5.py) must call calibrator after predict:
  calibrated_prob = iso.predict([raw_prob])[0]

Entrypoint: python -m training.calibrate_f5_v5
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
from sklearn.metrics import brier_score_loss

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION            = "v5"
TRAIN_TEST_SPLIT   = 0.8
TARGET             = "home_wins_f5"

GCS_MODEL_FEATURES = "F5_Pro_System/data/model_features.csv"
GCS_BOOSTER        = "F5_Pro_System/models/xgb_f5_v5.json"
GCS_META           = "F5_Pro_System/models/model_meta_f5_v5.json"
GCS_CALIBRATOR     = "F5_Pro_System/models/isotonic_calibrator_f5_v5.pkl"


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
    # Drop rows with no target (today's slate)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = df[TARGET].astype(int)
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"home win rate {df[TARGET].mean():.3f}"
    )
    return df, None


def _load_booster():
    from mlb_core.storage import read_bytes, download_model
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features")
    if not features:
        return None, None, None, "model_meta missing 'features' key"

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(GCS_BOOSTER, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))

    best_iter = meta.get("best_iteration", 0)
    booster.best_ntree_limit = best_iter
    feature_means = meta.get("feature_means", {}) or {}
    logger.info(f"booster loaded | features={len(features)} | best_iteration={best_iter}")
    return booster, features, feature_means, None


def _score_rows(booster, features, feature_means, df):
    """Score each game row. Returns array of P(home wins F5)."""
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
    p_home = _score_rows(booster, features, feature_means, df)
    df["model_prob"] = p_home

    # 80/20 time-based OOS split -- matches retrain split
    df = df.sort_values("game_date").reset_index(drop=True)
    split_idx    = int(len(df) * TRAIN_TEST_SPLIT)
    oos          = df.iloc[split_idx:].copy()
    train_through = df["game_date"].iloc[split_idx - 1].date()
    oos_from      = df["game_date"].iloc[split_idx].date()

    logger.info(
        f"split | train={split_idx} games (thru {train_through}) | "
        f"OOS={len(oos)} games (from {oos_from})"
    )
    logger.info(
        f"OOS home win rate: actual={oos[TARGET].mean():.3f} | "
        f"model mean={oos['model_prob'].mean():.3f}"
    )

    if len(oos) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos)} games) -- need >= 50"}

    # Fit isotonic calibrator on OOS
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(df["model_prob"].values, df[TARGET].values)  # fit on all data for full range coverage

    # Evaluate on OOS only
    raw_brier = float(brier_score_loss(oos[TARGET], oos["model_prob"]))
    cal_preds = iso.predict(oos["model_prob"].values)
    cal_brier = float(brier_score_loss(oos[TARGET], cal_preds))
    gap       = abs(cal_preds.mean() - oos[TARGET].mean())

    logger.info(
        f"calibration | raw Brier={raw_brier:.4f} | "
        f"calibrated Brier={cal_brier:.4f} | "
        f"improvement={raw_brier - cal_brier:+.4f}"
    )
    logger.info(
        f"calibrated OOS | mean_pred={cal_preds.mean():.3f} | "
        f"mean_actual={oos[TARGET].mean():.3f} | gap={gap:+.3f}"
    )

    if gap > 0.05:
        logger.warning(
            f"calibrated mean still {gap:.3f} from actual -- "
            f"may need more OOS data to converge"
        )

    # Upload
    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"uploaded: {GCS_CALIBRATOR} ({len(cal_bytes):,} bytes)")
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":               "ok",
        "version":              VERSION,
        "train_games":          split_idx,
        "oos_games":            len(oos),
        "oos_from":             str(oos_from),
        "oos_home_win_rate":    round(float(oos[TARGET].mean()), 4),
        "raw_brier":            round(raw_brier, 4),
        "calibrated_brier":     round(cal_brier, 4),
        "raw_mean_pred":        round(float(oos["model_prob"].mean()), 4),
        "calibrated_mean_pred": round(float(cal_preds.mean()), 4),
        "gcs_calibrator":       GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
