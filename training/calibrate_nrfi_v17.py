"""
training/calibrate_nrfi_v17.py -- Fit and upload an isotonic calibrator for NRFI v17.

Problem: xgb_halfinn_v17 outputs raw sigmoid probabilities. At game level
(P_NRFI = (1-p_home)*(1-p_away)) these are systematically overconfident --
model says ~75% where the actual hit rate is ~33%.

Fix: fit sklearn IsotonicRegression on the OOS split (last 20% of
model_features.csv by date, same split used in retrain_nrfi_v17.py).
Upload the fitted calibrator to GCS as isotonic_calibrator_v17.pkl.

run_nrfi.py already calls _load_calibrator() and applies it when the .pkl
is present in GCS -- no runner changes needed.

Run as a Cloud Run Job:
  gcloud run jobs execute mlb-calibrate-nrfi --region=us-central1

Or one-shot from Cloud Shell:
  gcloud run jobs create mlb-calibrate-nrfi \\
    --image=gcr.io/concrete-crow-445205-m4/mlb-betting:latest \\
    --region=us-central1 \\
    --command=python \\
    --args="-m,training.calibrate_nrfi_v17" \\
    --set-secrets=MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DB_URL=mlb-db-url:latest \\
    --service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com \\
    --add-cloudsql-instances=concrete-crow-445205-m4:us-central1:mlb-betting-db \\
    --max-retries=1
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

# ── Mirror retrain_nrfi_v17.py constants exactly ──────────────────────────────

VERSION = "v17"
TRAIN_TEST_SPLIT = 0.7   # same 70/10/20 time-based split as the retrain job
TARGET = "yrfi"

HALFINN_FEATURES = [
    "zone_pct_L3", "chase_pct_L3", "whiff_pct_L3", "k_pct_L3", "bb_pct_L3",
    "hard_hit_pct_L3", "barrel_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
    "primary_whiff_rate_L3", "called_strike_pct_L3",
    "zone_pct_L10", "whiff_pct_L10", "k_pct_L10", "xwoba_allowed_L10", "velo_mean_L10",
    "velo_trend_L5", "days_rest", "short_rest", "arm_angle", "pitcher_is_home",
    # top3_batter_* removed 2026-05-19 (T06): not joined by builder. Re-add with T12.
    "temperature_f", "wind_speed_mph", "is_outdoor", "wind_out", "wind_in",
    "is_cold", "is_hot", "high_wind",
    "park_factor",
    "ump_overall_accuracy_L30", "ump_total_run_impact_L30", "ump_consistency_L30",
    "woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
    "platoon_edge",
]

GCS_MODEL_FEATURES  = "NRFI_Pro_System/data/model_features.csv"
GCS_BOOSTER         = f"NRFI_Pro_System/models/xgb_halfinn_{VERSION}.json"
GCS_META            = f"NRFI_Pro_System/models/model_meta_{VERSION}.json"
GCS_CALIBRATOR      = f"NRFI_Pro_System/models/isotonic_calibrator_{VERSION}.pkl"


def _load_data():
    """Load features CSV from GCS. Returns (df, error_string)."""
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
    logger.info(
        f"loaded {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"YRFI rate {df[TARGET].mean():.3f}"
    )
    return df, None


def _load_booster():
    """Load booster + meta from GCS. Returns (booster, features, meta, error_string)."""
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
    logger.info(f"booster loaded | features={len(features)} | best_iteration={best_iter}")
    return booster, features, meta, None


def _score_half_rows(booster, features, feature_means, df):
    """Score each per-pitcher row. Returns array of half-inning YRFI probs."""
    available = [f for f in features if f in df.columns]
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


def _build_game_level(df, p_half):
    """
    Combine per-pitcher half-inning probs into game-level YRFI probs.
    Mirrors run_nrfi.py exactly:
      P(YRFI) = 1 - (1 - p_home) * (1 - p_away)
    Returns DataFrame with columns: game_pk, game_date, model_yrfi_prob, yrfi (actual).
    Only games with both starters scored are included.
    """
    df = df.copy()
    df["p_half"] = p_half

    # Each row is a pitcher-half. We need to identify home vs away per game.
    # pitcher_is_home == 1 means this pitcher faces the away batters (home half).
    pivot = df.pivot_table(
        index=["game_pk", "game_date"],
        columns="pitcher_is_home",
        values=["p_half", TARGET],
        aggfunc="first",
    )
    pivot.columns = [f"{col}_{side}" for col, side in pivot.columns]
    pivot = pivot.reset_index()

    # Rename for clarity: pitcher_is_home=1 -> home pitcher (away half scored by him)
    # Convention matches the retrain: column 1 = home pitcher, 0 = away pitcher
    needed = ["p_half_1", "p_half_0", f"{TARGET}_1"]
    missing = [c for c in needed if c not in pivot.columns]
    if missing:
        raise ValueError(f"pivot missing columns after reshape: {missing}. "
                         f"Available: {list(pivot.columns)}")

    pivot = pivot.dropna(subset=["p_half_1", "p_half_0"])
    pivot["model_yrfi_prob"] = 1.0 - (1.0 - pivot["p_half_1"]) * (1.0 - pivot["p_half_0"])
    # Use home pitcher's row for the game-level actual (both rows have same game target)
    pivot["yrfi"] = pivot[f"{TARGET}_1"].astype(int)

    logger.info(f"game-level rows: {len(pivot):,} | YRFI rate {pivot['yrfi'].mean():.3f}")
    return pivot[["game_pk", "game_date", "model_yrfi_prob", "yrfi"]]


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    # 1. Load data
    df, err = _load_data()
    if err:
        return {"status": "error", "error": err}

    # 2. Load booster
    booster, features, meta, err = _load_booster()
    if err:
        return {"status": "error", "error": err}

    feature_means = meta.get("feature_means", {}) or {}

    # 3. Score all rows
    p_half = _score_half_rows(booster, features, feature_means, df)

    # 4. Combine to game-level
    try:
        game_df = _build_game_level(df, p_half)
    except Exception as e:
        return {"status": "error", "error": f"game-level build: {e}"}

    # 5. OOS split -- used for evaluation only, NOT for fitting.
    # Fit calibrator on ALL data for full range coverage.
    # OOS split is kept only to measure calibration improvement.
    game_df = game_df.sort_values("game_date").reset_index(drop=True)
    split_idx = int(len(game_df) * TRAIN_TEST_SPLIT)
    train_g = game_df.iloc[:split_idx]
    oos_g   = game_df.iloc[split_idx:]

    train_through = game_df["game_date"].iloc[split_idx - 1].date()
    oos_from      = game_df["game_date"].iloc[split_idx].date()

    logger.info(
        f"calibration split | train={len(train_g)} games (thru {train_through}) | "
        f"OOS={len(oos_g)} games (from {oos_from})"
    )
    logger.info(
        f"OOS YRFI rate: actual={oos_g['yrfi'].mean():.3f} | "
        f"model mean={oos_g['model_yrfi_prob'].mean():.3f}"
    )

    if len(oos_g) < 50:
        return {"status": "error",
                "error": f"OOS split too small ({len(oos_g)} games) -- need >= 50"}

    # 6. Fit isotonic calibrator on TRAIN data only (T04, 2026-05-19).
    # Prior version fit on all data "for full range coverage" — that caused
    # the OOS Brier to be in-sample and overoptimistic. We now fit on
    # train_g and evaluate on oos_g. If the calibrator degrades OOS Brier,
    # the raw model is better calibrated than the isotonic fit and should be
    # used directly (flag will appear in logs).
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(train_g["model_yrfi_prob"].values, train_g["yrfi"].values)

    # 7. Evaluate calibration improvement
    raw_brier  = float(brier_score_loss(oos_g["yrfi"], oos_g["model_yrfi_prob"]))
    cal_preds  = iso.predict(oos_g["model_yrfi_prob"].values)
    cal_brier  = float(brier_score_loss(oos_g["yrfi"], cal_preds))
    logger.info(
        f"calibration | raw Brier={raw_brier:.4f} | calibrated Brier={cal_brier:.4f} | "
        f"improvement={raw_brier - cal_brier:+.4f}"
    )
    logger.info(
        f"calibrated OOS | mean_pred={cal_preds.mean():.3f} | "
        f"mean_actual={oos_g['yrfi'].mean():.3f} | "
        f"gap={cal_preds.mean() - oos_g['yrfi'].mean():+.3f}"
    )

    # 8. Sanity check: calibrated mean should be within 5 pct pts of actual
    gap = abs(cal_preds.mean() - oos_g["yrfi"].mean())
    if gap > 0.05:
        logger.warning(
            f"calibrated mean still {gap:.3f} from actual -- "
            f"calibrator may not have enough OOS data to converge"
        )

    # Warn if calibrator is hurting rather than helping on OOS (T04).
    if cal_brier > raw_brier:
        logger.warning(
            f"CALIBRATOR DEGRADES OOS Brier ({raw_brier:.4f} → {cal_brier:.4f}). "
            f"Raw model is better calibrated. Consider skipping calibrator upload "
            f"or re-evaluating the train/OOS split boundary."
        )

    # 9. Upload calibrator to GCS
    cal_bytes = pickle.dumps(iso, protocol=4)
    try:
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"uploaded calibrator: {GCS_CALIBRATOR} ({len(cal_bytes):,} bytes)")
    except Exception as e:
        return {"status": "error", "error": f"calibrator upload: {e}"}

    return {
        "status":              "ok",
        "version":             VERSION,
        "train_games":         int(len(train_g)),
        "oos_games":           int(len(oos_g)),
        "oos_from":            str(oos_from),
        "oos_yrfi_rate":       round(float(oos_g["yrfi"].mean()), 4),
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
