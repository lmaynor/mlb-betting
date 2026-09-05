"""
training/retrain_outs_v1.py -- OUTS Pro v1 full retrain (Cloud Run Job). (E04)

Replaces the Normal(avg_ip, 1.5) proxy in run_k.py._simulate_outs with a
trained XGBoost count model (count:poisson + NB dispersion fit).

Target: starter_outs -- actual outs recorded by the starting pitcher.
Source: K_Pro_System/data/model_features.csv already has this column
        (built from boxscore by build_k_features.py).

Feature set: subset of K_FEATURES -- removes K-specific columns (k_pct,
k_per_9, whiff, zone_contact, chase, two_strike_k_rate, primary_whiff_rate,
first_pitch_strike_pct) and keeps durability/context features that drive
how deep a starter goes. Adds opp_bb_rate as walks-allowed proxy for
pitcher command (shorter outings when command is off).

Architecture mirrors retrain_k_v1.py's shared CV/eval/persist mechanics
(see _retrain_common.py), but with two real, deliberate differences that
this refactor preserves rather than silently unifying:
  - No _leakage_check at all (K/SB/BATTER_HITS/GAME all run one; OUTS never
    has).
  - _oos_eval's meta has 2 fewer keys (no mae_train/overfit_gap) --
    `include_train_metrics=False` below. FLAG FOR HUMAN DECISION: this
    means model_meta_outs_v1.json has always lacked overfitting diagnostics
    every other system's meta carries. Not fixed here (would change the
    on-disk meta shape); flagged in the refactor report for someone to
    decide whether to enable it.
  - OUTS also self-calibrates inline (fits + writes its own isotonic
    calibrator here) -- no separate `mlb-calibrate-outs` job exists (see
    docs/solutions/conventions/retrain-calibrate-sequence.md).

Output GCS keys:
  - OUTS_Pro_System/models/xgb_outs_v1.json
  - OUTS_Pro_System/models/model_meta_outs_v1.json
  - OUTS_Pro_System/models/archive/xgb_outs_v1.{ts}.json
  - OUTS_Pro_System/models/archive/model_meta_outs_v1.{ts}.json

Cloud Run Job: mlb-retrain-outs-v1 (to be created -- see deploy notes below)
Entrypoint: python -m training.retrain_outs_v1

Deploy notes:
  gcloud run jobs create mlb-retrain-outs-v1 \\
    --image gcr.io/concrete-crow-445205-m4/mlb-betting:latest \\
    --region us-central1 \\
    --command python \\
    --args "-m,training.retrain_outs_v1" \\
    --memory 4Gi --cpu 2 \\
    --set-secrets MLB_GCS_BUCKET=mlb-gcs-bucket:latest \\
    --set-cloudsql-instances concrete-crow-445205-m4:us-central1:mlb-betting-db \\
    --project concrete-crow-445205-m4
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from datetime import datetime, timezone

import pandas as pd
import xgboost as xgb

from mlb.systems.OUTS_Pro_System.config_outs import OUTS_FEATURES
from mlb.training import _retrain_common as common
from mlb.training._retrain_common import cv_folds as _cv_folds
from mlb.training._retrain_common import mae as _mae
from mlb.training._retrain_common import rmse as _rmse
from mlb.training._retrain_common import r2 as _r2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)

VERSION = "v1"
TARGET  = "starter_outs"   # actual outs recorded by SP -- must be in K feature CSV

# ── Feature set: durability + context, not K-specific pitch quality ─────────
# Removed from K_FEATURES: k_pct_*, k_per_9_*, whiff_pct_*, zone_contact_pct_*,
#   chase_pct_*, two_strike_k_rate_*, primary_whiff_rate_*, first_pitch_strike_pct_*
# Kept: IP proxies, rest, opponent, park, regime, umpire
# Added: opp_bb_rate_L14 (walks proxy for command -- short outings)

XGB_PARAMS = {
    "objective":        "count:poisson",
    "eval_metric":      "poisson-nloglik",
    "max_depth":        4,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 15,
    "reg_alpha":        1.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
}

NUM_BOOST_ROUND      = 2000
EARLY_STOPPING_ROUNDS = 50

# Per-system metric family for the shared CV/OOS mechanics (count: MAE/RMSE/R2).
_METRICS = [("mae", _mae), ("rmse", _rmse), ("r2", _r2)]


# GCS keys
GCS_MODEL_FEATURES   = "K_Pro_System/data/model_features.csv"   # shared with K
GCS_BOOSTER_LATEST   = f"OUTS_Pro_System/models/xgb_outs_{VERSION}.json"
GCS_META_LATEST      = f"OUTS_Pro_System/models/model_meta_outs_{VERSION}.json"
GCS_BOOSTER_ARCHIVE  = f"OUTS_Pro_System/models/archive/xgb_outs_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE     = f"OUTS_Pro_System/models/archive/model_meta_outs_{VERSION}.{{ts}}.json"
GCS_CALIBRATOR       = f"OUTS_Pro_System/models/isotonic_calibrator_outs_{VERSION}.pkl"


# ── Data load ─────────────────────────────────────────────────────────────────

def _load_features():
    from mlb_core.storage import read_csv, exists

    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found -- run /build-features for K first"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"

    if df.empty:
        return None, f"{GCS_MODEL_FEATURES} is empty"

    if TARGET not in df.columns:
        # starter_outs must be in the K feature CSV.
        # If missing, it needs to be added to build_k_features.py first.
        return None, (
            f"target column {TARGET!r} missing from K feature CSV. "
            f"Add starter_outs to build_k_features.py output before retraining."
        )

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    df = df.dropna(subset=[TARGET]).copy()
    df["year"] = df["game_date"].dt.year

    # Sanity check: outs should be in [0, 27]
    before = len(df)
    df = df[(df[TARGET] >= 0) & (df[TARGET] <= 27)].copy()
    if len(df) < before:
        logger.warning(f"dropped {before - len(df)} rows with out-of-range starter_outs")

    logger.info(
        f"loaded: {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean_outs={df[TARGET].mean():.2f} | std={df[TARGET].std():.2f}"
    )
    return df, None


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    return common.walk_forward_cv(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_train=50, min_test=10, cv_folds_fn=_cv_folds,
        filter_train_years=False, logger=logger,
    )


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    # include_train_metrics=False: OUTS's meta has always lacked
    # mae_train/overfit_gap -- see the module docstring's FLAG note.
    return common.oos_eval(
        df, features, TARGET, XGB_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS,
        metrics=_METRICS, min_test=10, cv_folds_fn=_cv_folds,
        include_train_metrics=False, logger=logger,
    )


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    return common.full_retrain(df, features, TARGET, XGB_PARAMS, best_iter, logger=logger)


# ── Isotonic calibrator (OUTS self-calibrates inline; no separate job) ───────

def _fit_calibrator(booster: xgb.Booster, df: pd.DataFrame, features: list) -> object:
    """Fit isotonic regression calibrator on train slice (70% boundary).
    Calibrates predicted lambda vs actual outs -- corrects systematic bias.
    """
    from sklearn.isotonic import IsotonicRegression

    split = int(len(df) * 0.70)
    df_tr = df.iloc[:split]

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float).values
    dm   = xgb.DMatrix(X_tr, feature_names=features)
    preds = booster.predict(dm)

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(preds, y_tr)

    # Evaluate on OOS slice
    df_oos = df.iloc[split:]
    X_oos  = df_oos[features].apply(pd.to_numeric, errors="coerce")
    y_oos  = df_oos[TARGET].astype(float).values
    dm_oos = xgb.DMatrix(X_oos, feature_names=features)
    raw_preds = booster.predict(dm_oos)
    cal_preds = iso.predict(raw_preds)

    raw_mae = _mae(y_oos, raw_preds)
    cal_mae = _mae(y_oos, cal_preds)
    logger.info(
        f"calibrator | OOS raw_mae={raw_mae:.3f} cal_mae={cal_mae:.3f} "
        f"improvement={raw_mae - cal_mae:+.4f} "
        f"X_min={iso.X_min_:.3f} X_max={iso.X_max_:.3f}"
    )
    return iso


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    from mlb_core.config import GCS_BUCKET

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in OUTS_FEATURES if f in df.columns]
    missing   = [f for f in OUTS_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (skipped): {missing}")

    # Walk-forward CV
    try:
        wf = _walk_forward_cv(df, available)
    except Exception as e:
        return {"status": "error", "error": f"walk-forward CV: {e}"}

    # OOS eval
    try:
        oos = _oos_eval(df, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    # Full retrain
    try:
        booster = _full_retrain(df, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    nb_alpha = common.fit_nb_alpha(booster, df, available, TARGET, logger=logger)

    # Calibrator
    calibrator = _fit_calibrator(booster, df, available)

    # Feature stats
    fmeans = common.feature_means(df, available, warn_on_skip=False, logger=None)
    fstds = common.feature_stds(df, available)

    # C04: empirical percentiles for PSI
    fpdists = common.feature_dists(df, available)

    # CV summary -- OUTS-only quirk: wf_summary is only populated when a CI
    # could also be computed (>=2 folds), unlike K/SB/BATTER_HITS/GAME.
    cv_ci_lo, cv_ci_hi, wf_summary = common.wf_ci_and_summary(
        wf, primary_metric="mae", metric_names=["mae", "rmse", "r2"],
        require_ci_for_summary=True,
    )

    ts = common.ts()
    booster_archive_key   = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key      = GCS_META_ARCHIVE.format(ts=ts)

    meta = {
        "version":        VERSION,
        "model_type":     "outs_poisson",
        "trained_at":     datetime.now(timezone.utc).isoformat(),
        "full_retrain":   True,
        "features":       available,
        "nb_alpha":       nb_alpha,
        "feature_dists":  fpdists,
        "feature_means":  fmeans,
        "feature_stds":   fstds,
        "cv_folds":       _cv_folds(df),
        "cv_mae_ci_lo":   cv_ci_lo,
        "cv_mae_ci_hi":   cv_ci_hi,
        "gcs_calibrator": GCS_CALIBRATOR,
        **oos,
        **wf_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    # OUTS writes both booster keys before either meta key (unlike the
    # archive-then-latest-per-artifact-type order K/SB/BATTER_HITS/GAME use)
    # -- preserved verbatim, not normalized, since write order changes
    # partial-failure behavior.
    err = common.persist_model_artifacts(
        booster, meta_bytes,
        steps=[
            {"kind": "booster", "key": booster_archive_key, "label": "archive booster"},
            {"kind": "booster", "key": GCS_BOOSTER_LATEST,  "label": "latest booster"},
            {"kind": "meta",    "key": meta_archive_key,    "label": "archive meta"},
            {"kind": "meta",    "key": GCS_META_LATEST,     "label": "latest meta"},
        ],
        logger=logger,
    )
    if err:
        return err

    # Write calibrator (non-fatal -- OUTS-only artifact, no separate calibrate job).
    try:
        cal_bytes = pickle.dumps(calibrator)
        from mlb_core.storage import write_bytes
        write_bytes(cal_bytes, GCS_CALIBRATOR)
        logger.info(f"calibrator: {GCS_CALIBRATOR} ({len(cal_bytes)} bytes)")
    except Exception as e:
        logger.warning(f"calibrator write failed (non-fatal): {e}")

    return {
        "status":         "ok",
        "version":        VERSION,
        "features":       len(available),
        "feature_means":  len(fmeans),
        "train_rows":     oos["train_rows"],
        "test_rows":      oos["test_rows"],
        "mae_oos":        oos["mae_oos"],
        "rmse_oos":       oos["rmse_oos"],
        "r2_oos":         oos["r2_oos"],
        "best_iteration": oos["best_iteration"],
        "nb_alpha":       nb_alpha,
        "wf_mae":         wf_summary.get("wf_mae"),
        "wf_mae_std":     wf_summary.get("wf_mae_std"),
        "booster_archive": booster_archive_key,
        "meta_archive":   meta_archive_key,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
