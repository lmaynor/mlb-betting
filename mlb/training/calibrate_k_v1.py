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

The load/score/fit mechanics are shared with calibrate_sb_v1.py/
calibrate_batter_hits_v1.py/calibrate_batter_tb_v1.py via
_calibrate_common.py -- see that module's docstring for what's shared vs
genuinely per-system.

Entrypoint: python -m training.calibrate_k_v1
"""
from __future__ import annotations

import json
import logging
import sys

from mlb.systems.K_Pro_System.config_k import K_FEATURES
from mlb.training import _calibrate_common as common

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


def _load_data():
    return common.load_lambda_data(GCS_MODEL_FEATURES, TARGET, metric_label="Ks", logger=logger)


def _load_booster():
    return common.load_booster(GCS_META, GCS_BOOSTER, K_FEATURES, logger=logger)


def _score_lambda(booster, features, feature_means, df):
    """Score rows. Returns array of lambda (expected K count)."""
    return common.score_lambda(booster, features, feature_means, df)


def run() -> dict:
    from mlb_core.config import GCS_BUCKET

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

    # Fit isotonic calibrator on TRAIN slice only (T05, 2026-05-19).
    # lambda -> actual_ks mapping learned from historical training data only.
    iso, result, err = common.fit_and_evaluate_calibrator(
        df, TARGET, "lambda_k",
        train_test_split=TRAIN_TEST_SPLIT, min_oos=50,
        gap_threshold=0.5, gap_unit_label="Ks",
        oos_actual_key="oos_mean_actual_ks",
        verbose=True, logger=logger,
    )
    if err:
        return {"status": "error", "error": err}

    # Upload
    err = common.write_calibrator(iso, GCS_CALIBRATOR, logger=logger)
    if err:
        return err

    return {
        "status":             "ok",
        "version":            VERSION,
        "train_rows":         result["train_rows"],
        "oos_rows":           result["oos_rows"],
        "oos_from":           result["oos_from"],
        "oos_mean_actual_ks": result["oos_mean_actual_ks"],
        "raw_mae":            result["raw_mae"],
        "calibrated_mae":     result["calibrated_mae"],
        "raw_bias":           result["raw_bias"],
        "calibrated_bias":    result["calibrated_bias"],
        "raw_mean_lambda":    result["raw_mean_lambda"],
        "calibrated_mean":    result["calibrated_mean"],
        "gcs_calibrator":     GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
