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

The load/score/fit mechanics are shared with calibrate_k_v1.py/
calibrate_batter_hits_v1.py/calibrate_batter_tb_v1.py via
_calibrate_common.py -- see that module's docstring for what's shared vs
genuinely per-system.

Entrypoint: python -m mlb.training.calibrate_sb_v1
"""
from __future__ import annotations

import json
import logging
import sys

from mlb.systems.SB_Pro_System.config_sb import SB_FEATURES
from mlb.training import _calibrate_common as common

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
    return common.load_lambda_data(GCS_MODEL_FEATURES, TARGET, metric_label="SB", logger=logger)


def _load_booster():
    return common.load_booster(GCS_META, GCS_BOOSTER, SB_FEATURES, logger=logger)


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
    df["lambda_sb"] = lambdas

    iso, result, err = common.fit_and_evaluate_calibrator(
        df, TARGET, "lambda_sb",
        train_test_split=TRAIN_TEST_SPLIT, min_oos=50,
        gap_threshold=0.1, gap_unit_label="SB",
        oos_actual_key="oos_mean_actual_sb",
        verbose=True, logger=logger,
    )
    if err:
        return {"status": "error", "error": err}

    err = common.write_calibrator(iso, GCS_CALIBRATOR, logger=logger)
    if err:
        return err

    return {
        "status":             "ok",
        "version":            VERSION,
        "train_rows":         result["train_rows"],
        "oos_rows":           result["oos_rows"],
        "oos_from":           result["oos_from"],
        "oos_mean_actual_sb": result["oos_mean_actual_sb"],
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
