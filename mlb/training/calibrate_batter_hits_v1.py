"""
training/calibrate_batter_hits_v1.py — Fit lambda calibrator for BATTER_HITS v1.

BATTER_HITS model: count:poisson predicting lambda (expected hits per game).
The runner converts lambda -> P(hits > line) via NegBin CDF. If lambda is
systematically inflated, every P(over) is inflated, producing fake edges.

Calibration: fit IsotonicRegression(lambda_raw -> actual_hits) on the OOS
train split. Mirrors calibrate_k_v1.py exactly.

The load/score/fit mechanics are shared with calibrate_k_v1.py/
calibrate_sb_v1.py/calibrate_batter_tb_v1.py via _calibrate_common.py --
see that module's docstring for what's shared vs genuinely per-system.

Entrypoint: python -m training.calibrate_batter_hits_v1
"""
from __future__ import annotations

import json
import logging
import sys

from mlb.systems.BATTER_HITS_System.config_batter_hits import BATTER_HITS_FEATURES
from mlb.training import _calibrate_common as common

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


def _load_data():
    return common.load_lambda_data(GCS_MODEL_FEATURES, TARGET, metric_label="hits", logger=logger)


def _load_booster():
    return common.load_booster(GCS_META, GCS_BOOSTER, BATTER_HITS_FEATURES, logger=logger)


def _score_lambda(booster, features, feature_means, df):
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

    lambdas = _score_lambda(booster, features, feature_means, df)
    df["lambda_hits"] = lambdas

    iso, result, err = common.fit_and_evaluate_calibrator(
        df, TARGET, "lambda_hits",
        train_test_split=TRAIN_TEST_SPLIT, min_oos=50,
        gap_threshold=0.3, gap_unit_label="hits",
        oos_actual_key="oos_mean_actual_hits",
        verbose=True, logger=logger,
    )
    if err:
        return {"status": "error", "error": err}

    err = common.write_calibrator(iso, GCS_CALIBRATOR, logger=logger)
    if err:
        return err

    return {
        "status":               "ok",
        "version":              VERSION,
        "train_rows":           result["train_rows"],
        "oos_rows":             result["oos_rows"],
        "oos_from":             result["oos_from"],
        "oos_mean_actual_hits": result["oos_mean_actual_hits"],
        "raw_mae":              result["raw_mae"],
        "calibrated_mae":       result["calibrated_mae"],
        "raw_bias":             result["raw_bias"],
        "calibrated_bias":      result["calibrated_bias"],
        "raw_mean_lambda":      result["raw_mean_lambda"],
        "calibrated_mean":      result["calibrated_mean"],
        "gcs_calibrator":       GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
