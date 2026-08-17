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

Architecture mirrors retrain_k_v1.py exactly:
  1. Load K feature CSV from GCS (reuses same build pipeline).
  2. Walk-forward CV across last 3 available years.
  3. OOS eval (70/10/20 split).
  4. Full retrain on 100% of data.
  5. Fit NB dispersion alpha from residuals.
  6. Write artifacts to GCS: OUTS_Pro_System/models/

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
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from mlb.systems.OUTS_Pro_System.config_outs import OUTS_FEATURES

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


def _cv_folds(df: pd.DataFrame, n: int = 3) -> list[int]:
    """Walk-forward test years: the most recent `n` years actually present in
    the data (finding C3.3), not a hardcoded literal. [2023, 2024, 2025] used
    to sit here as a fixed constant -- it goes silently stale every offseason
    (it's 2026-08-17 as this fix lands and that literal still stopped at
    2025), quietly excluding the newest season from CV, from the OOS
    train/test split, and from the leakage check, with no error or warning.
    """
    years = sorted(int(y) for y in df["year"].dropna().unique())
    return years[-n:] if len(years) >= n else years


# GCS keys
GCS_MODEL_FEATURES   = "K_Pro_System/data/model_features.csv"   # shared with K
GCS_BOOSTER_LATEST   = f"OUTS_Pro_System/models/xgb_outs_{VERSION}.json"
GCS_META_LATEST      = f"OUTS_Pro_System/models/model_meta_outs_{VERSION}.json"
GCS_BOOSTER_ARCHIVE  = f"OUTS_Pro_System/models/archive/xgb_outs_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE     = f"OUTS_Pro_System/models/archive/model_meta_outs_{VERSION}.{{ts}}.json"
GCS_CALIBRATOR       = f"OUTS_Pro_System/models/isotonic_calibrator_outs_{VERSION}.pkl"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# ── Metrics ──────────────────────────────────────────────────────────────────

def _mae(y, p):  return float(np.mean(np.abs(np.array(y) - np.array(p))))
def _rmse(y, p): return float(np.sqrt(np.mean((np.array(y) - np.array(p)) ** 2)))
def _r2(y, p):
    ss_res = float(np.sum((np.array(y) - np.array(p)) ** 2))
    ss_tot = float(np.sum((np.array(y) - np.mean(y)) ** 2))
    return 1.0 - ss_res / max(ss_tot, 1e-9)


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


# ── Walk-forward CV ───────────────────────────────────────────────────────────

def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    results = []
    logger.info("=== Walk-forward CV ===")

    for test_year in _cv_folds(df):
        train_years = [test_year - 2, test_year - 1]
        df_tr = df[df["year"].isin(train_years)]
        df_te = df[df["year"] == test_year]

        if len(df_tr) < 50 or len(df_te) < 10:
            logger.info(f"  fold {test_year}: insufficient data -- skip")
            continue

        X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
        y_tr = df_tr[TARGET].astype(float)
        X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
        y_te = df_te[TARGET].astype(float)

        # C03: carve val from train for early stopping
        nval      = int(len(X_tr) * (7 / 8))
        dtrain_s  = xgb.DMatrix(X_tr.iloc[:nval],  label=y_tr.iloc[:nval],  feature_names=features)
        dval_s    = xgb.DMatrix(X_tr.iloc[nval:],  label=y_tr.iloc[nval:],  feature_names=features)
        dtest     = xgb.DMatrix(X_te, label=y_te, feature_names=features)

        b = xgb.train(
            XGB_PARAMS, dtrain_s,
            num_boost_round=NUM_BOOST_ROUND,
            evals=[(dtrain_s, "train"), (dval_s, "val")],
            early_stopping_rounds=EARLY_STOPPING_ROUNDS,
            verbose_eval=False,
        )

        y_pred = b.predict(dtest)
        mae  = _mae(y_te, y_pred)
        rmse = _rmse(y_te, y_pred)
        r2   = _r2(y_te, y_pred)
        cal  = float(np.mean(y_pred) - np.mean(y_te))
        best = int(getattr(b, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

        results.append({
            "test_year": test_year, "n_train": len(df_tr), "n_test": len(df_te),
            "mae": mae, "rmse": rmse, "r2": r2, "cal_gap": cal, "best_iteration": best,
        })
        logger.info(
            f"  fold {test_year}: MAE={mae:.3f} RMSE={rmse:.3f} "
            f"R2={r2:.3f} cal={cal:+.3f} best_iter={best} "
            f"n_train={len(df_tr)} n_test={len(df_te)}"
        )

    return results


# ── OOS eval ─────────────────────────────────────────────────────────────────

def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    last   = _cv_folds(df)[-1]
    df_tr  = df[df["year"] < last]
    df_te  = df[df["year"] == last]

    if len(df_te) < 10:
        raise RuntimeError(f"OOS test fold ({last}) has too few rows ({len(df_te)})")

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[TARGET].astype(float)

    logger.info(
        f"OOS split | train={len(df_tr)} (years <{last}) | "
        f"test={len(df_te)} (year={last}) | features={len(features)}"
    )

    nval      = int(len(X_tr) * (7 / 8))
    dtrain    = xgb.DMatrix(X_tr.iloc[:nval], label=y_tr.iloc[:nval], feature_names=features)
    dval      = xgb.DMatrix(X_tr.iloc[nval:], label=y_tr.iloc[nval:], feature_names=features)
    dtest     = xgb.DMatrix(X_te, label=y_te, feature_names=features)

    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )

    y_pred = booster.predict(dtest)
    mae    = _mae(y_te, y_pred)
    rmse   = _rmse(y_te, y_pred)
    r2     = _r2(y_te, y_pred)
    cal    = float(np.mean(y_pred) - np.mean(y_te))
    best   = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(
        f"OOS results | MAE={mae:.3f} RMSE={rmse:.3f} "
        f"R2={r2:.3f} cal={cal:+.3f} best_iter={best}"
    )
    return {
        "train_rows":    int(len(df_tr)),
        "test_rows":     int(len(df_te)),
        "mae_oos":       round(mae, 4),
        "rmse_oos":      round(rmse, 4),
        "r2_oos":        round(r2, 4),
        "cal_oos":       round(cal, 4),
        "best_iteration": best,
        "test_year":     int(last),
    }


# ── Full retrain ──────────────────────────────────────────────────────────────

def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    logger.info(f"full retrain | rows={len(df)} features={len(features)} rounds={best_iter}")
    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(float)
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    return xgb.train(XGB_PARAMS, dtrain, num_boost_round=best_iter, verbose_eval=False)


# ── NB dispersion fit ─────────────────────────────────────────────────────────

def _fit_nb_alpha(booster: xgb.Booster, df: pd.DataFrame, features: list) -> float:
    """Fit NB dispersion parameter from full-data residuals.
    NB(mu, alpha): var = mu + alpha*mu^2 -> alpha = (var - mu) / mu^2
    Clamp to [0.01, 0.50].
    """
    try:
        X   = df[features].apply(pd.to_numeric, errors="coerce")
        dm  = xgb.DMatrix(X, feature_names=features)
        y   = df[TARGET].astype(float).values
        p   = booster.predict(dm)
        var = float(np.var(y - p))
        mu  = float(np.mean(p))
        alpha = float(np.clip((var - mu) / max(mu ** 2, 1e-6), 0.01, 0.50))
        logger.info(f"NB alpha | mu={mu:.3f} resid_var={var:.3f} alpha={alpha:.4f}")
        return alpha
    except Exception as e:
        logger.warning(f"nb_alpha fit failed ({e}) -- using default 0.10")
        return 0.10


# ── Isotonic calibrator ───────────────────────────────────────────────────────

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
    from mlb_core.storage import write_bytes, upload_model
    import pickle

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

    nb_alpha = _fit_nb_alpha(booster, df, available)

    # Calibrator
    calibrator = _fit_calibrator(booster, df, available)

    # Feature stats
    X_all = df[available].apply(pd.to_numeric, errors="coerce")
    fmeans = {f: float(X_all[f].mean(skipna=True)) for f in available if not pd.isna(X_all[f].mean(skipna=True))}
    fstds  = {f: round(float(X_all[f].std(skipna=True)), 6) for f in available if not pd.isna(X_all[f].std(skipna=True))}

    # C04: empirical percentiles for PSI
    fpdists: dict = {}
    for _f in available:
        try:
            col = pd.to_numeric(df[_f], errors="coerce").dropna()
            if len(col) < 10:
                continue
            fpdists[_f] = {
                "p5":    round(float(np.percentile(col, 5)), 6),
                "p10":   round(float(np.percentile(col, 10)), 6),
                "p25":   round(float(np.percentile(col, 25)), 6),
                "p50":   round(float(np.percentile(col, 50)), 6),
                "p75":   round(float(np.percentile(col, 75)), 6),
                "p90":   round(float(np.percentile(col, 90)), 6),
                "p95":   round(float(np.percentile(col, 95)), 6),
                "prop_1": round(float((col == 1).mean()), 6),
            }
        except Exception:
            continue

    # CV summary
    wf_summary: dict = {}
    cv_ci_lo = cv_ci_hi = None
    if wf and len(wf) >= 2:
        import scipy.stats as _st
        maes = [f["mae"] for f in wf]
        cv_ci_lo, cv_ci_hi = _st.t.interval(
            0.95, len(maes) - 1,
            loc=float(np.mean(maes)),
            scale=float(_st.sem(maes)),
        )
        cv_ci_lo = round(float(cv_ci_lo), 4)
        cv_ci_hi = round(float(cv_ci_hi), 4)
        wf_df = pd.DataFrame(wf)
        wf_summary = {
            "wf_mae":     round(float(wf_df["mae"].mean()), 4),
            "wf_rmse":    round(float(wf_df["rmse"].mean()), 4),
            "wf_r2":      round(float(wf_df["r2"].mean()), 4),
            "wf_cal":     round(float(wf_df["cal_gap"].mean()), 4),
            "wf_mae_std": round(float(wf_df["mae"].std()), 4),
            "wf_folds":   wf,
        }

    ts = _ts()
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

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = Path(f.name)

    try:
        booster.save_model(str(tmp))

        for key, label in [
            (booster_archive_key, "archive booster"),
            (GCS_BOOSTER_LATEST,  "latest booster"),
        ]:
            try:
                upload_model(tmp, key)
                logger.info(f"{label}: {key}")
            except Exception as e:
                return {"status": "error", "error": f"{label} write: {e}"}

        for key, data, label in [
            (meta_archive_key, meta_bytes, "archive meta"),
            (GCS_META_LATEST,  meta_bytes, "latest meta"),
        ]:
            try:
                write_bytes(data, key)
                logger.info(f"{label}: {key}")
            except Exception as e:
                return {"status": "error", "error": f"{label} write: {e}"}

        # Write calibrator
        try:
            cal_bytes = pickle.dumps(calibrator)
            write_bytes(cal_bytes, GCS_CALIBRATOR)
            logger.info(f"calibrator: {GCS_CALIBRATOR} ({len(cal_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"calibrator write failed (non-fatal): {e}")

    finally:
        tmp.unlink(missing_ok=True)

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
