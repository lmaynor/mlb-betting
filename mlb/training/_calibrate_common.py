"""
mlb.training._calibrate_common -- shared load/score/fit mechanics for the
lambda-calibrator scripts (calibrate_k_v1.py, calibrate_sb_v1.py,
calibrate_batter_hits_v1.py, calibrate_batter_tb_v1.py). Their own
docstrings admitted these "mirror X exactly" -- verified via `diff` on
extracted function bodies: _load_data/_load_booster/_score_lambda were
byte-for-byte identical (modulo variable names, log precision, and one
harmless unused local in calibrate_k_v1.py's copy). Everything genuinely
per-system (GCS paths, TARGET, VERSION, the lambda column name, the gap
warning threshold and its unit label) stays in each calibrate_*.py file.

Real, pre-existing asymmetry preserved here rather than silently unified:
calibrate_batter_tb_v1.py's `run()` is a strict subset of the other three --
no split/OOS logging, no gap-threshold warning, no "calibrator degrades OOS
MAE" warning, and its returned dict is MISSING "oos_from" and
"oos_mean_actual_{x}" (2 fewer keys than K/SB/BATTER_HITS's calibrator meta
shape). This is the same kind of drift as OUTS's missing OOS keys in
_retrain_common.py -- gated here via `verbose=False`, not "fixed". See the
refactor report for why this needs a human decision, not a silent unify.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression


def load_lambda_data(gcs_model_features: str, target: str, *, metric_label: str,
                      logger=None):
    """Shared `_load_data`: read model_features.csv, sort by game_date, drop
    rows with no actual target value yet (today's slate). Returns (df, err)."""
    from mlb_core.storage import read_csv, exists

    if not exists(gcs_model_features):
        return None, f"{gcs_model_features} not found in GCS"
    df = read_csv(gcs_model_features, low_memory=False)
    if df.empty:
        return None, "model_features.csv is empty"
    if target not in df.columns:
        return None, f"target column {target!r} missing"

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    df = df.dropna(subset=[target]).copy()
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df.dropna(subset=[target])

    if logger:
        logger.info(
            "loaded %s rows | %s -> %s | mean %s=%.4f",
            f"{len(df):,}", df["game_date"].min().date(), df["game_date"].max().date(),
            metric_label, df[target].mean(),
        )
    return df, None


def load_booster(gcs_meta: str, gcs_booster: str, features_fallback: list, *, logger=None):
    """Shared `_load_booster`: load model_meta_*.json + the booster it
    points at. Returns (booster, features, feature_means, err)."""
    import json
    from mlb_core.storage import read_bytes, download_model

    try:
        meta = json.loads(read_bytes(gcs_meta))
    except Exception as e:
        return None, None, None, f"meta load: {e}"

    features = meta.get("features") or features_fallback
    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        local = download_model(gcs_booster, Path(tmpdir) / "booster.json")
        booster.load_model(str(local))

    best_iter = meta.get("best_iteration", 0)
    booster.best_ntree_limit = best_iter
    feature_means = meta.get("feature_means", {}) or {}
    if logger:
        logger.info("booster loaded | features=%d | best_iteration=%s", len(features), best_iter)
    return booster, features, feature_means, None


def score_lambda(booster, features: list, feature_means: dict, df: pd.DataFrame):
    """Shared `_score_lambda`: fill missing feature values from the
    training-time feature_means, then predict raw lambda (expected count)
    using the runner's own safe iteration_range pattern."""
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


def fit_and_evaluate_calibrator(
    df: pd.DataFrame,
    target: str,
    lambda_col: str,
    *,
    train_test_split: float = 0.8,
    min_oos: int = 50,
    gap_threshold: float | None = None,
    gap_unit_label: str = "",
    oos_actual_key: str | None = None,
    verbose: bool = True,
    logger=None,
):
    """Shared fit + evaluate block: 80/20 time-based OOS split, fit
    IsotonicRegression(lambda_raw -> actual) on the TRAIN slice only (never
    leak val/test into calibration), evaluate raw vs calibrated MAE/bias on
    the OOS slice.

    Returns (iso, result_dict, err). `result_dict` always has train_rows,
    oos_rows, raw_mae, calibrated_mae, raw_bias, calibrated_bias,
    raw_mean_lambda, calibrated_mean. When `verbose=True` it additionally
    logs split/OOS/calibration diagnostics, applies the gap-threshold and
    "calibrator degrades OOS MAE" warnings, and includes "oos_from" +
    `oos_actual_key` (calibrate_batter_tb_v1.py is the one caller that sets
    verbose=False and omits all of this -- see module docstring).
    """
    df = df.sort_values("game_date").reset_index(drop=True)
    split_idx = int(len(df) * train_test_split)
    oos = df.iloc[split_idx:].copy()

    if verbose and logger:
        train_through = df["game_date"].iloc[split_idx - 1].date()
        oos_from = df["game_date"].iloc[split_idx].date()
        logger.info("split | train=%d rows (thru %s) | OOS=%d rows (from %s)",
                    split_idx, train_through, len(oos), oos_from)
        logger.info("OOS | actual mean=%.4f | model mean lambda=%.4f | raw bias=%+.4f",
                    oos[target].mean(), oos[lambda_col].mean(),
                    oos[lambda_col].mean() - oos[target].mean())

    if len(oos) < min_oos:
        return None, None, f"OOS split too small ({len(oos)} rows) -- need >= {min_oos}"

    train_df = df.iloc[:split_idx].copy()
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_df[lambda_col].values, train_df[target].values)

    cal_lambdas = iso.predict(oos[lambda_col].values)

    raw_mae = float(np.mean(np.abs(oos[lambda_col].values - oos[target].values)))
    cal_mae = float(np.mean(np.abs(cal_lambdas - oos[target].values)))
    raw_bias = float(oos[lambda_col].mean() - oos[target].mean())
    cal_bias = float(cal_lambdas.mean() - oos[target].mean())
    gap = abs(cal_lambdas.mean() - oos[target].mean())

    if verbose and logger:
        logger.info("calibration | raw MAE=%.4f | calibrated MAE=%.4f | improvement=%+.4f",
                    raw_mae, cal_mae, raw_mae - cal_mae)
        if gap_threshold is not None and gap > gap_threshold:
            logger.warning("calibrated mean still %.3f %s from actual -- calibrator may need more OOS data",
                           gap, gap_unit_label)
        if cal_mae > raw_mae:
            logger.warning("CALIBRATOR DEGRADES OOS MAE (%.4f -> %.4f). Raw lambda is more accurate on this split.",
                           raw_mae, cal_mae)

    result = {
        "train_rows": split_idx,
        "oos_rows": len(oos),
        "raw_mae": round(raw_mae, 4),
        "calibrated_mae": round(cal_mae, 4),
        "raw_bias": round(raw_bias, 4),
        "calibrated_bias": round(cal_bias, 4),
        "raw_mean_lambda": round(float(oos[lambda_col].mean()), 4),
        "calibrated_mean": round(float(cal_lambdas.mean()), 4),
    }
    if verbose:
        result["oos_from"] = str(df["game_date"].iloc[split_idx].date())
        if oos_actual_key:
            result[oos_actual_key] = round(float(oos[target].mean()), 4)

    return iso, result, None


def write_calibrator(iso, gcs_calibrator: str, *, logger=None):
    """Pickle + upload the fitted calibrator. Returns an error dict on
    failure, or None on success."""
    import pickle
    from mlb_core.storage import write_bytes

    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, gcs_calibrator)
        if logger:
            logger.info("uploaded: %s (%s bytes)", gcs_calibrator, f"{len(cal_bytes):,}")
        return None
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}
