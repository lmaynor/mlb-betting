"""
training/calibrate_nrfi_v18.py -- Fit and upload isotonic calibrator for NRFI v18.

Mirrors calibrate_nrfi_v17.py but scores rows with the v18 sub-model ensemble
(pitcher + lineup + context sub-models stacked via logistic regression) before
fitting the isotonic regression on the resulting game-level YRFI probabilities.

Run after retrain_nrfi_v18.py:
  gcloud run jobs execute mlb-retrain-nrfi-v18 --region=us-central1
  gcloud run jobs execute mlb-calibrate-nrfi-v18 --region=us-central1
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import IsotonicRegression
from sklearn.metrics import brier_score_loss

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION          = "v18"
TRAIN_TEST_SPLIT = 0.7
TARGET           = "yrfi"

GCS_MODEL_FEATURES = "NRFI_Pro_System/data/model_features.csv"
GCS_META           = "NRFI_Pro_System/models/model_meta_v18.json"
GCS_BOOSTER_KEYS   = {
    "pitcher": "NRFI_Pro_System/models/xgb_pitcher_v18.json",
    "lineup":  "NRFI_Pro_System/models/xgb_lineup_v18.json",
    "context": "NRFI_Pro_System/models/xgb_context_v18.json",
}
GCS_CALIBRATOR     = "NRFI_Pro_System/models/isotonic_calibrator_v18.pkl"


def _load_data():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found"
    df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    if df.empty:
        return None, "model_features.csv is empty"
    if TARGET not in df.columns:
        return None, f"target '{TARGET}' missing"
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"YRFI rate {df[TARGET].mean():.3f}"
    )
    return df, None


def _load_ensemble():
    """Load all 3 sub-boosters + meta from GCS."""
    from mlb_core.storage import read_bytes, download_model, exists
    if not exists(GCS_META):
        return None, None, f"{GCS_META} not found -- run retrain_nrfi_v18 first"
    try:
        meta = json.loads(read_bytes(GCS_META))
    except Exception as e:
        return None, None, f"meta load: {e}"

    sub_boosters: dict[str, xgb.Booster] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, key in GCS_BOOSTER_KEYS.items():
            b = xgb.Booster()
            local = download_model(key, Path(tmpdir) / f"xgb_{name}.json")
            b.load_model(str(local))
            b.best_ntree_limit = meta["sub_models"][name].get("best_iteration", 0)
            sub_boosters[name] = b
            logger.info(f"  [{name}] loaded | best_iter={b.best_ntree_limit}")

    return sub_boosters, meta, None


def _score_half_rows_ensemble(sub_boosters, meta, df: pd.DataFrame) -> np.ndarray:
    """Score each pitcher row with the v18 ensemble. Returns half-inning YRFI probs."""
    sub_order  = meta["stacker"]["sub_order"]
    coef       = np.array(meta["stacker"]["coef"])
    intercept  = float(meta["stacker"]["intercept"])

    sub_probs = []
    for name in sub_order:
        features      = meta["sub_models"][name]["features"]
        feature_means = meta["sub_models"][name].get("feature_means", {})
        best_iter     = meta["sub_models"][name].get("best_iteration", 0)
        booster       = sub_boosters[name]

        X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
        for col in features:
            m = feature_means.get(col)
            if m is not None:
                X[col] = X[col].fillna(float(m))
        dm = xgb.DMatrix(X.astype(float), feature_names=features)
        if best_iter:
            p = booster.predict(dm, iteration_range=(0, best_iter))
        else:
            p = booster.predict(dm)
        sub_probs.append(p)

    meta_X = np.column_stack(sub_probs)
    logit  = meta_X @ coef + intercept
    return 1.0 / (1.0 + np.exp(-logit))


def _build_game_level(df: pd.DataFrame, p_half: np.ndarray) -> pd.DataFrame:
    """Combine half-inning probs to game-level YRFI. Mirrors calibrate_nrfi_v17.py."""
    df = df.copy()
    df["p_half"] = p_half
    pivot = df.pivot_table(
        index=["game_pk", "game_date"],
        columns="pitcher_is_home",
        values=["p_half", TARGET],
        aggfunc="first",
    )
    pivot.columns = [f"{col}_{side}" for col, side in pivot.columns]
    pivot = pivot.reset_index()
    pivot = pivot.dropna(subset=["p_half_1", "p_half_0"])
    pivot["model_yrfi_prob"] = 1.0 - (1.0 - pivot["p_half_1"]) * (1.0 - pivot["p_half_0"])
    # Game-level YRFI actual: either half of the 1st inning scored.
    # TARGET is half-inning YRFI in model_features, so using only one row here
    # would calibrate a full-inning probability against a half-inning target and
    # systematically inflate NRFI/Under confidence.
    pivot["yrfi"] = (
        pivot[f"{TARGET}_1"].astype(int) | pivot[f"{TARGET}_0"].astype(int)
    ).astype(int)
    logger.info(f"game-level rows: {len(pivot):,} | YRFI rate {pivot['yrfi'].mean():.3f}")
    return pivot[["game_pk", "game_date", "model_yrfi_prob", "yrfi"]]


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_data()
    if err:
        return {"status": "error", "error": err}

    sub_boosters, meta, err = _load_ensemble()
    if err:
        return {"status": "error", "error": err}

    p_half = _score_half_rows_ensemble(sub_boosters, meta, df)

    try:
        game_df = _build_game_level(df, p_half)
    except Exception as e:
        return {"status": "error", "error": f"game-level build: {e}"}

    game_df  = game_df.sort_values("game_date").reset_index(drop=True)
    split_idx = int(len(game_df) * TRAIN_TEST_SPLIT)
    train_g  = game_df.iloc[:split_idx]
    oos_g    = game_df.iloc[split_idx:]

    train_through = game_df["game_date"].iloc[split_idx - 1].date()
    oos_from      = game_df["game_date"].iloc[split_idx].date()

    logger.info(
        f"calibration split | train={len(train_g)} (thru {train_through}) | "
        f"OOS={len(oos_g)} (from {oos_from})"
    )
    logger.info(
        f"OOS | actual_rate={oos_g['yrfi'].mean():.3f} | "
        f"model_mean={oos_g['model_yrfi_prob'].mean():.3f}"
    )

    if len(oos_g) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos_g)}) -- need >= 50"}

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_g["model_yrfi_prob"].values, train_g["yrfi"].values)

    raw_brier  = float(brier_score_loss(oos_g["yrfi"], oos_g["model_yrfi_prob"]))
    cal_preds  = iso.predict(oos_g["model_yrfi_prob"].values)
    cal_brier  = float(brier_score_loss(oos_g["yrfi"], cal_preds))

    logger.info(
        f"calibration | raw Brier={raw_brier:.4f} | calibrated Brier={cal_brier:.4f} | "
        f"improvement={raw_brier - cal_brier:+.4f}"
    )
    logger.info(
        f"calibrated OOS | mean_pred={cal_preds.mean():.3f} | "
        f"actual={oos_g['yrfi'].mean():.3f} | gap={cal_preds.mean() - oos_g['yrfi'].mean():+.3f}"
    )

    if cal_brier > raw_brier:
        logger.warning(
            f"CALIBRATOR DEGRADES OOS Brier ({raw_brier:.4f} -> {cal_brier:.4f}). "
            f"Raw ensemble is better calibrated -- review before promoting v18."
        )

    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"uploaded: {GCS_CALIBRATOR} ({len(cal_bytes):,} bytes)")
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":              "ok",
        "version":             VERSION,
        "train_games":         int(len(train_g)),
        "oos_games":           int(len(oos_g)),
        "oos_from":            str(oos_from),
        "raw_brier":           round(raw_brier, 4),
        "calibrated_brier":    round(cal_brier, 4),
        "raw_mean_pred":       round(float(oos_g["model_yrfi_prob"].mean()), 4),
        "calibrated_mean_pred": round(float(cal_preds.mean()), 4),
        "gcs_calibrator":      GCS_CALIBRATOR,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
