"""
training/retrain_k_v1.py — K Pro v1 full retrain (Cloud Run Job).

Mirrors K_Pro_v1.ipynb Section 7 (OOS eval, walk-forward CV) + Section 7b
(full retrain on all data). Adapts the NRFI v17 retrain pattern:
  1. Load model_features.csv from GCS, drop today's-slate rows (target NaN).
  2. Walk-forward CV across cfg['cv_folds'] for diagnostic MAE/RMSE/R².
  3. Section 7-style OOS split (train: pre-last_fold, test: last_fold) to
     get best_iteration.
  4. Section 7b: retrain on 100% of data using best_iteration rounds.
  5. Compute feature_means from full training set (for runner NaN-fill).
  6. Write archive + latest pointer to GCS for both booster and meta.

Output GCS keys (matches K_Pro_System/config_k.py):
  - K_Pro_System/models/xgb_k_v1.json
  - K_Pro_System/models/model_meta_v1.json
  - K_Pro_System/models/archive/xgb_k_v1.{ts}.json
  - K_Pro_System/models/archive/model_meta_v1.{ts}.json

Notebook contract (K_FEATURES + XGB_PARAMS) is duplicated below so this
script is self-contained. If K_Pro_System/config_k.py:K_FEATURES changes,
mirror here AND flag in the next handoff.

Entrypoint: `python -m training.retrain_k_v1` (Cloud Run Job command).
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Notebook contract — keep in sync with K_Pro_System/config_k.py:K_FEATURES
# and K_Pro_v1.ipynb Section 0. 34 features.

VERSION = "v1"
TARGET = "starter_ks"

K_FEATURES = [
    "k_pct_L5", "k_pct_L10", "k_pct_STD", "k_per_9_L5", "k_per_9_L10",
    "first_pitch_strike_pct_L10", "hitter_count_rate_L10", "two_strike_k_rate_L10",
    "whiff_pct_L10", "zone_contact_pct_L10", "chase_pct_L10",
    "velo_mean_L5", "velo_trend_L5",
    "fb_pct_L10", "breaking_pct_L10", "primary_whiff_rate_L10",
    "avg_ip_L5", "avg_bf_L5", "days_rest", "short_rest",
    "opp_k_rate_L14", "opp_k_rate_vs_hand_L14", "opp_chase_rate_L14",
    "opp_whiff_rate_L14", "opp_lineup_pct_L", "opp_platoon_k_edge",
    "opp_top3_k_rate_L50",
    "ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30",
    # implied_win_pct removed 2026-05-19 (T02): market-derived feature trains
    # the model to mimic the line, eliminating closing-line edge by construction.
    # is_home and context features kept; moneyline proxy removed.
    "is_home", "temperature_f", "is_dome",
    # T13: Regime indicator — pitch clock 2023-03-30.
    "post_pitch_clock",
]

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

NUM_BOOST_ROUND = 2000
EARLY_STOPPING_ROUNDS = 50
CV_FOLDS = [2023, 2024, 2025]   # walk-forward test years (notebook default)

# GCS keys — must match K_Pro_System/config_k.py
GCS_MODEL_FEATURES  = "K_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"K_Pro_System/models/xgb_k_{VERSION}.json"
GCS_META_LATEST     = f"K_Pro_System/models/model_meta_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"K_Pro_System/models/archive/xgb_k_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"K_Pro_System/models/archive/model_meta_{VERSION}.{{ts}}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS — run /build-features for K first"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"
    if df.empty:
        return None, f"{GCS_MODEL_FEATURES} is empty"
    if TARGET not in df.columns:
        return None, f"target column {TARGET!r} missing from features CSV"

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)

    # Drop today's-slate rows (no actual K count yet)
    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    logger.info(f"loaded features: {len(df):,} training rows ({dropped} slate rows excluded) | "
                f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
                f"mean Ks={df[TARGET].mean():.2f}")
    return df, None


def _mae(y_true, y_pred): return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))
def _rmse(y_true, y_pred): return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))
def _r2(y_true, y_pred):
    ss_res = float(np.sum((np.array(y_true) - np.array(y_pred)) ** 2))
    ss_tot = float(np.sum((np.array(y_true) - np.mean(y_true)) ** 2))
    return 1 - ss_res / max(ss_tot, 1e-9)


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    """Section 7 walk-forward CV: train on prior 2 years, test on held-out year."""
    results = []
    logger.info("=== Walk-forward CV ===")
    for test_year in CV_FOLDS:
        train_years = [test_year - 2, test_year - 1]
        df_tr = df[df["year"].isin(train_years)]
        df_te = df[df["year"] == test_year]
        if len(df_tr) < 50 or len(df_te) < 10:
            logger.info(f"  fold {test_year}: insufficient data "
                        f"(train={len(df_tr)}, test={len(df_te)}) — skip")
            continue

        X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
        y_tr = df_tr[TARGET].astype(float)
        X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
        y_te = df_te[TARGET].astype(float)

        dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
        dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)
        booster = xgb.train(
            XGB_PARAMS, dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            evals=[(dtest, "test")],
            early_stopping_rounds=EARLY_STOPPING_ROUNDS,
            verbose_eval=False,
        )
        y_pred = booster.predict(dtest)
        mae   = _mae(y_te, y_pred)
        rmse  = _rmse(y_te, y_pred)
        r2    = _r2(y_te, y_pred)
        cal   = float(np.mean(y_pred) - np.mean(y_te))
        best  = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
        results.append({
            "test_year": test_year, "n_train": len(df_tr), "n_test": len(df_te),
            "mae": mae, "rmse": rmse, "r2": r2,
            "cal_gap": cal, "best_iteration": best,
        })
        logger.info(f"  fold {test_year}: MAE={mae:.3f} RMSE={rmse:.3f} R²={r2:.3f} "
                    f"cal={cal:+.3f} best_iter={best} "
                    f"n_train={len(df_tr)} n_test={len(df_te)}")
    return results


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    """Section 7 OOS model: train pre-last-fold, test on last-fold."""
    last = CV_FOLDS[-1]
    df_tr = df[df["year"] < last]
    df_te = df[df["year"] == last]
    if len(df_te) < 10:
        raise RuntimeError(f"OOS test fold ({last}) has too few rows ({len(df_te)})")

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[TARGET].astype(float)

    logger.info(f"OOS split | train={len(df_tr)} (years <{last}) | "
                f"test={len(df_te)} (year={last}) | features={len(features)}")

    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
    dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)
    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dtest, "test")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )
    y_pred = booster.predict(dtest)
    y_tr_pred = booster.predict(dtrain)

    mae_oos    = _mae(y_te, y_pred)
    mae_train  = _mae(y_tr, y_tr_pred)
    rmse_oos   = _rmse(y_te, y_pred)
    r2_oos     = _r2(y_te, y_pred)
    cal_oos    = float(np.mean(y_pred) - np.mean(y_te))
    best_iter  = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(f"OOS results | MAE={mae_oos:.3f} RMSE={rmse_oos:.3f} "
                f"R²={r2_oos:.3f} cal={cal_oos:+.3f} train_mae={mae_train:.3f} "
                f"overfit_gap={abs(mae_train - mae_oos):.3f} best_iter={best_iter}")

    return {
        "train_rows":     int(len(df_tr)),
        "test_rows":      int(len(df_te)),
        "mae_oos":        round(mae_oos, 4),
        "rmse_oos":       round(rmse_oos, 4),
        "r2_oos":         round(r2_oos, 4),
        "cal_oos":        round(cal_oos, 4),
        "mae_train":      round(mae_train, 4),
        "overfit_gap":    round(abs(mae_train - mae_oos), 4),
        "best_iteration": best_iter,
        "test_year":      int(last),
    }


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    """Section 7b: retrain on 100% of data with best_iteration rounds."""
    logger.info(f"full retrain | rows={len(df)} features={len(features)} rounds={best_iter}")
    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(float)
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    return xgb.train(XGB_PARAMS, dtrain, num_boost_round=best_iter, verbose_eval=False)


def _feature_means(df: pd.DataFrame, features: list) -> dict:
    means, skipped = {}, []
    X = df[features].apply(pd.to_numeric, errors="coerce")
    for f in features:
        v = X[f].mean(skipna=True)
        if pd.isna(v):
            skipped.append(f)
        else:
            means[f] = float(v)
    if skipped:
        logger.warning(f"feature_means could not be computed for "
                       f"{len(skipped)} features: {sorted(skipped)[:5]}")
    logger.info(f"feature_means computed for {len(means)}/{len(features)} features")
    return means



def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.10) -> list[str]:
    """Warn if removing any single feature improves OOS MAE by >threshold.

    A feature whose removal improves MAE by >10% may be carrying target
    information (leakage). Warning only -- does not abort the retrain.
    Skipped if env var K_SKIP_LEAKAGE_CHECK=1 is set (for fast reruns).

    Only checks features with >50% non-NaN coverage to avoid false
    positives from sparse columns.
    """
    import os
    if os.getenv("K_SKIP_LEAKAGE_CHECK") == "1":
        logger.info("leakage check skipped (K_SKIP_LEAKAGE_CHECK=1)")
        return []

    last = CV_FOLDS[-1]
    df_tr = df[df["year"] < last].copy()
    df_te = df[df["year"] == last].copy()
    if len(df_te) < 10:
        return []

    best_iter    = oos["best_iteration"]
    baseline_mae = oos["mae_oos"]
    y_tr = df_tr[TARGET].astype(float)
    y_te = df_te[TARGET].astype(float)
    suspicious = []

    logger.info(f"leakage check | baseline MAE={baseline_mae:.3f} | "
                f"threshold={threshold:.0%} | checking {len(features)} features")

    for feat in features:
        coverage = df_tr[feat].notna().mean() if feat in df_tr.columns else 0.0
        if coverage < 0.5:
            continue

        df_tr_z = df_tr.copy(); df_tr_z[feat] = 0.0
        df_te_z = df_te.copy(); df_te_z[feat] = 0.0

        dtrain_z = xgb.DMatrix(
            df_tr_z[features].apply(pd.to_numeric, errors="coerce"),
            label=y_tr, feature_names=features)
        dtest_z  = xgb.DMatrix(
            df_te_z[features].apply(pd.to_numeric, errors="coerce"),
            label=y_te, feature_names=features)

        b = xgb.train(XGB_PARAMS, dtrain_z,
                      num_boost_round=best_iter, verbose_eval=False)
        mae_z = _mae(y_te, b.predict(dtest_z))
        improvement = (baseline_mae - mae_z) / max(baseline_mae, 1e-9)

        if improvement > threshold:
            suspicious.append(feat)
            logger.warning(
                f"  LEAKAGE SUSPECT: {feat!r} | "
                f"MAE {baseline_mae:.3f} -> {mae_z:.3f} "
                f"(improvement={improvement:+.1%})"
            )

    if not suspicious:
        logger.info("  leakage check passed -- no suspicious features")
    else:
        logger.warning(
            f"  leakage check: {len(suspicious)} suspicious features: {suspicious}"
        )
    return suspicious


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model
    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in K_FEATURES if f in df.columns]
    missing = [f for f in K_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (will be skipped): {missing}")

    try:
        wf = _walk_forward_cv(df, available)
    except Exception as e:
        return {"status": "error", "error": f"walk-forward CV: {e}"}

    try:
        oos = _oos_eval(df, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    leakage_suspects = _leakage_check(df, available, oos)
    if leakage_suspects:
        logger.warning(f"proceeding with retrain despite {len(leakage_suspects)} "
                       f"leakage suspect(s) -- review before promoting to production")

    try:
        booster = _full_retrain(df, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    fmeans = _feature_means(df, available)

    # T10: feature_stds for PSI drift monitor (T14)
    fstds: dict = {}
    X_all = df[available].apply(pd.to_numeric, errors="coerce")
    for f in available:
        v = X_all[f].std(skipna=True)
        if not pd.isna(v):
            fstds[f] = round(float(v), 6)

    # T10: Bootstrap 95% CI on CV mean MAE
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

    wf_summary = {}
    if wf:
        wf_df = pd.DataFrame(wf)
        wf_summary = {
            "wf_mae":      round(float(wf_df["mae"].mean()), 4),
            "wf_rmse":     round(float(wf_df["rmse"].mean()), 4),
            "wf_r2":       round(float(wf_df["r2"].mean()), 4),
            "wf_cal":      round(float(wf_df["cal_gap"].mean()), 4),
            "wf_mae_std":  round(float(wf_df["mae"].std()), 4),
            "wf_folds":    wf,
        }

    meta = {
        "version":       VERSION,
        "model_type":    "k_poisson",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      available,
        "feature_means": fmeans,
        "feature_stds":  fstds,
        "cv_folds":      CV_FOLDS,
        "cv_mae_ci_lo":  cv_ci_lo,
        "cv_mae_ci_hi":  cv_ci_hi,
        **oos,
        **wf_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    ts = _ts()
    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        booster_tmp = Path(f.name)
    try:
        booster.save_model(str(booster_tmp))

        # Archives first (preserve history if latest write fails)
        try:
            upload_model(booster_tmp, booster_archive_key)
            logger.info(f"archive booster: {booster_archive_key}")
        except Exception as e:
            return {"status": "error", "error": f"archive booster write: {e}"}
        try:
            write_bytes(meta_bytes, meta_archive_key)
            logger.info(f"archive meta:    {meta_archive_key}")
        except Exception as e:
            return {"status": "error", "error": f"archive meta write: {e}"}

        # Latest pointers
        try:
            upload_model(booster_tmp, GCS_BOOSTER_LATEST)
            logger.info(f"latest booster:  {GCS_BOOSTER_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest booster write: {e}"}
        try:
            write_bytes(meta_bytes, GCS_META_LATEST)
            logger.info(f"latest meta:     {GCS_META_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest meta write: {e}"}
    finally:
        booster_tmp.unlink(missing_ok=True)

    return {
        "status":          "ok",
        "version":         VERSION,
        "features":        len(available),
        "feature_means":   len(fmeans),
        "train_rows":      oos["train_rows"],
        "test_rows":       oos["test_rows"],
        "mae_oos":         oos["mae_oos"],
        "rmse_oos":        oos["rmse_oos"],
        "r2_oos":          oos["r2_oos"],
        "best_iteration":  oos["best_iteration"],
        "wf_mae":          wf_summary.get("wf_mae"),
        "wf_mae_std":      wf_summary.get("wf_mae_std"),
        "leakage_suspects": leakage_suspects,
        "booster_archive": booster_archive_key,
        "meta_archive":    meta_archive_key,
        "booster_latest":  GCS_BOOSTER_LATEST,
        "meta_latest":     GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
