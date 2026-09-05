"""
training/calibrate_batter_tb_v1.py - Fit lambda calibrator for BATTER_TB v1.

The load/score/fit mechanics are shared with calibrate_k_v1.py/
calibrate_sb_v1.py/calibrate_batter_hits_v1.py via _calibrate_common.py.

NOTE: unlike the other three, this script has never logged split/OOS
diagnostics, never applied a gap-threshold or "calibrator degrades" warning,
and its result dict has always been missing "oos_from"/"oos_mean_actual_tb"
(2 fewer keys than K/SB/BATTER_HITS's calibrator result shape). That is
real, pre-existing drift -- preserved here via `verbose=False`, not fixed.
See _calibrate_common.py's docstring.
"""
from __future__ import annotations

import json
import logging
import sys

from mlb.systems.BATTER_TB_System.config_batter_tb import BATTER_TB_FEATURES
from mlb.training import _calibrate_common as common

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
    return common.load_lambda_data(GCS_MODEL_FEATURES, TARGET, metric_label="TB", logger=logger)


def _load_booster():
    return common.load_booster(GCS_META, GCS_BOOSTER, BATTER_TB_FEATURES, logger=None)


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

    df["lambda_tb"] = _score_lambda(booster, features, feature_means, df)

    iso, result, err = common.fit_and_evaluate_calibrator(
        df, TARGET, "lambda_tb",
        train_test_split=TRAIN_TEST_SPLIT, min_oos=50,
        verbose=False, logger=logger,
    )
    if err:
        # NOTE: the "too small" message text/wording is normalized here to
        # match the other three calibrate scripts (shared _calibrate_common
        # code) -- this script's original wording differed cosmetically
        # ("(N) - need >= 50" vs "(N rows) -- need >= 50"). Never surfaced
        # anywhere but this Cloud Run Job's own error log; not part of any
        # GCS key, meta.json field, or model artifact.
        return {"status": "error", "error": err}

    err = common.write_calibrator(iso, GCS_CALIBRATOR, logger=None)
    if err:
        return err

    return {
        "status": "ok",
        "version": VERSION,
        "train_rows": result["train_rows"],
        "oos_rows": result["oos_rows"],
        "raw_mae": result["raw_mae"],
        "calibrated_mae": result["calibrated_mae"],
        "raw_bias": result["raw_bias"],
        "calibrated_bias": result["calibrated_bias"],
        "raw_mean_lambda": result["raw_mean_lambda"],
        "calibrated_mean": result["calibrated_mean"],
        "gcs_calibrator": GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
