"""
training/retrain_hr_v6.py — HR Pro v6 full retrain (Cloud Run Job). (T11)

Replaces retrain_hr_meta.py (meta-patch shim). Mirrors the NRFI/F5 retrain
pattern but adapted for the batter-level prediction problem:
  - One row per (batter, game_pk)
  - Target: hr (binary — did batter hit a HR this game?)
  - Class imbalance: HR rate ~7%; XGBoost handles via scale_pos_weight

Steps:
  1. Load model_features.csv from GCS.
  2. OOS eval: 80/20 time-split, early stopping, AUC/Brier.
  3. Walk-forward CV across last 3 available years (T10).
  4. Full retrain on 100% of data using best_iteration.
  5. Compute feature_means + feature_stds.
  6. Write archive + latest pointer to GCS.

retrain_hr_meta.py is now a deprecated stub. Do not run both.

Output GCS keys (match HR_Pro/config_hr.py):
  - HR_Pro/models/xgb_hr_v6.json
  - HR_Pro/models/model_meta_hr_v6.json
  - HR_Pro/models/archive/xgb_hr_v6.{ts}.json
  - HR_Pro/models/archive/model_meta_hr_v6.{ts}.json

Entrypoint: python -m training.retrain_hr_v6
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from scipy.stats import chi2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


VERSION          = "v6"
TARGET           = "hr"
TRAIN_TEST_SPLIT = 0.8
NUM_BOOST_ROUND  = 2000
EARLY_STOPPING   = 50

# Columns that are never model features
_NON_FEATURE_COLS = {
    "batter", "game_pk", "game_date", "home_team", "away_team", "home_abbr",
    "player_name", "opp_pitcher_id", "pitcher", "stand",
    "season", "year", "date", "hr",
    # Removed market-derived (T03)
    "team_moneyline", "implied_win_pct",
    # Raw in-game aggregates (target proxies)
    "hr_game", "barrel_game", "hard_hit_game", "fb_game",
    "hr_per_fb_num", "fb_count_game", "hr_per_fb_game",
    "xwoba_game", "ev_game", "ev_max_game", "la_mean_game", "la_std_game",
    "sweet_spot_game", "hr_zone_game",
}

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      ["logloss", "auc"],
    "max_depth":        4,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 20,
    "reg_alpha":        2.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
    # HR is rare (~7%); scale_pos_weight corrects for class imbalance.
    # Value = (n_neg / n_pos) set dynamically in run() from training data.
}

GCS_MODEL_FEATURES  = "HR_Pro/data/model_features.csv"
GCS_BOOSTER_LATEST  = "HR_Pro/models/xgb_hr_v6.json"
GCS_META_LATEST     = "HR_Pro/models/model_meta_hr_v6.json"
GCS_BOOSTER_ARCHIVE = "HR_Pro/models/archive/xgb_hr_v6.{ts}.json"
GCS_META_ARCHIVE    = "HR_Pro/models/archive/model_meta_hr_v6.{ts}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _hosmer_lemeshow(y_true, y_pred, n_bins=10):
    try:
        df = pd.DataFrame({"y": y_true, "p": y_pred})
        df["bin"] = pd.qcut(df["p"], q=n_bins, duplicates="drop")
        g = df.groupby("bin").agg(obs=("y","sum"), exp=("p","sum"), n=("y","count"))
        hl = ((g["obs"] - g["exp"])**2 / (g["exp"] * (1 - g["exp"]/g["n"]))).sum()
        return float(chi2.sf(hl, df=n_bins - 2))
    except Exception:
        return float("nan")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, None, f"{GCS_MODEL_FEATURES} not found — run /build-features for HR"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, None, f"features load: {e}"
    if df.empty:
        return None, None, "model_features.csv is empty"
    if TARGET not in df.columns:
        return None, None, f"target column '{TARGET}' missing"

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = df[TARGET].astype(int)

    # Feature list: all numeric, not in exclusion set, ≥ 10% coverage
    features = [
        c for c in df.columns
        if c not in _NON_FEATURE_COLS
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].notna().mean() >= 0.10
    ]
    hr_rate = df[TARGET].mean()
    logger.info(
        f"HR features: {len(df):,} batter-game rows | "
        f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
        f"HR rate {hr_rate:.4f} | {len(features)} features"
    )
    return df, features, None


def _oos_eval(df, X, y, features, scale_pos_weight):
    split_idx = int(len(X) * TRAIN_TEST_SPLIT)
    X_tr, X_te = X.iloc[:split_idx], X.iloc[split_idx:]
    y_tr, y_te = y.iloc[:split_idx], y.iloc[split_idx:]
    train_through = df["game_date"].iloc[split_idx - 1].strftime("%Y-%m-%d")
    test_from     = df["game_date"].iloc[split_idx].strftime("%Y-%m-%d")

    logger.info(
        f"OOS split | train={len(X_tr):,} (thru {train_through}) | "
        f"test={len(X_te):,} (from {test_from}) | "
        f"HR rate train={y_tr.mean():.4f} test={y_te.mean():.4f}"
    )
    params = {**XGB_PARAMS, "scale_pos_weight": scale_pos_weight}
    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
    dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)
    booster = xgb.train(
        params, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dtest, "test")],
        early_stopping_rounds=EARLY_STOPPING,
        verbose_eval=200,
    )
    preds    = booster.predict(dtest)
    auc      = float(roc_auc_score(y_te, preds))
    brier    = float(brier_score_loss(y_te, preds))
    ll       = float(log_loss(y_te, preds))
    hl_p     = _hosmer_lemeshow(y_te.values, preds)
    cal_gap  = round(abs(preds.mean() - y_te.mean()), 6)
    best_iter = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(
        f"OOS | AUC={auc:.4f} Brier={brier:.4f} LogLoss={ll:.4f} "
        f"H-L p={hl_p:.3f} cal_gap={cal_gap:.6f} best_iter={best_iter}"
    )
    return {
        "train_through":  train_through,
        "test_from":      test_from,
        "train_rows":     int(len(X_tr)),
        "test_rows":      int(len(X_te)),
        "auc_oos":        round(auc,   4),
        "brier_oos":      round(brier, 4),
        "logloss_oos":    round(ll,    4),
        "hl_p":           round(hl_p,  3),
        "cal_gap":        cal_gap,
        "best_iteration": best_iter,
    }


def _walk_forward_cv(df, features, scale_pos_weight):
    if os.getenv("HR_SKIP_CV") == "1":
        logger.info("HR walk-forward CV skipped (HR_SKIP_CV=1)")
        return {}, {}

    df_cv = df.copy()
    df_cv["_year"] = df_cv["game_date"].dt.year
    years = sorted(df_cv["_year"].unique())
    folds = [int(y) for y in years if y >= years[-1] - 2]
    logger.info(f"HR walk-forward CV folds: {folds}")

    params = {**XGB_PARAMS, "scale_pos_weight": scale_pos_weight}
    results = []
    for test_year in folds:
        tr = df_cv[df_cv["_year"] < test_year]
        te = df_cv[df_cv["_year"] == test_year]
        if len(tr) < 500 or len(te) < 200:
            continue
        X_tr = tr[features].apply(pd.to_numeric, errors="coerce")
        y_tr = tr[TARGET].astype(int)
        X_te = te[features].apply(pd.to_numeric, errors="coerce")
        y_te = te[TARGET].astype(int)
        dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
        dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)
        b = xgb.train(
            params, dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            evals=[(dtest, "test")],
            early_stopping_rounds=EARLY_STOPPING,
            verbose_eval=False,
        )
        preds = b.predict(dtest)
        try:
            auc   = round(float(roc_auc_score(y_te, preds)), 4)
            brier = round(float(brier_score_loss(y_te, preds)), 4)
        except Exception:
            continue
        results.append({"year": test_year, "auc": auc, "brier": brier,
                        "n_train": len(tr), "n_test": len(te)})
        logger.info(f"  CV {test_year}: AUC={auc} Brier={brier} "
                    f"n_train={len(tr):,} n_test={len(te):,}")

    if not results:
        return results, {}
    aucs   = [f["auc"]   for f in results]
    briers = [f["brier"] for f in results]
    ci_lo = ci_hi = None
    if len(aucs) >= 2:
        import scipy.stats as _st
        ci_lo, ci_hi = _st.t.interval(
            0.95, len(aucs) - 1,
            loc=float(np.mean(aucs)),
            scale=float(_st.sem(aucs)),
        )
    summary = {
        "cv_folds":      results,
        "cv_mean_auc":   round(float(np.mean(aucs)),   4),
        "cv_std_auc":    round(float(np.std(aucs)),    4),
        "cv_auc_ci_lo":  round(float(ci_lo),           4) if ci_lo is not None else None,
        "cv_auc_ci_hi":  round(float(ci_hi),           4) if ci_hi is not None else None,
        "cv_mean_brier": round(float(np.mean(briers)), 4),
    }
    logger.info(
        f"HR CV AUC {summary['cv_mean_auc']} ± {summary['cv_std_auc']} "
        f"95% CI [{summary['cv_auc_ci_lo']}, {summary['cv_auc_ci_hi']}]"
    )
    return results, summary


def _full_retrain(X, y, features, best_iter, scale_pos_weight):
    logger.info(f"HR full retrain | rows={len(X):,} features={len(features)} rounds={best_iter}")
    params = {**XGB_PARAMS, "eval_metric": "logloss",
              "scale_pos_weight": scale_pos_weight}
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    return xgb.train(params, dtrain, num_boost_round=best_iter, verbose_eval=False)


def _feature_stats(X, features):
    means, stds = {}, {}
    for f in features:
        m = X[f].mean(skipna=True)
        s = X[f].std(skipna=True)
        if not pd.isna(m):
            means[f] = float(m)
        if not pd.isna(s):
            stds[f]  = round(float(s), 6)
    return means, stds


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, features, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(int)

    # HR class imbalance weight: n_negative / n_positive
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    scale_pos_weight = round(n_neg / max(n_pos, 1), 2)
    logger.info(f"HR class balance: n_pos={n_pos:,} n_neg={n_neg:,} "
                f"scale_pos_weight={scale_pos_weight}")

    # Walk-forward CV (T10)
    _, cv_summary = _walk_forward_cv(df, features, scale_pos_weight)

    # OOS eval
    try:
        oos = _oos_eval(df, X, y, features, scale_pos_weight)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    # Full retrain
    try:
        booster = _full_retrain(X, y, features, oos["best_iteration"], scale_pos_weight)
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    fmeans, fstds = _feature_stats(X, features)

    ts = _ts()
    meta = {
        "version":           VERSION,
        "model_type":        "hr_binary",
        "trained_at":        datetime.now(timezone.utc).isoformat(),
        "full_retrain":      True,
        "features":          features,
        "feature_means":     fmeans,
        "feature_stds":      fstds,
        "scale_pos_weight":  scale_pos_weight,
        "hr_rate_train":     round(float(y.mean()), 6),
        **oos,
        **cv_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = Path(f.name)
    try:
        booster.save_model(str(tmp))
        for key, label in [
            (booster_archive_key, "booster archive"),
            (GCS_BOOSTER_LATEST,  "booster latest"),
        ]:
            try:
                upload_model(tmp, key)
                logger.info(f"  {label}: {key}")
            except Exception as e:
                return {"status": "error", "error": f"{label} write: {e}"}
        for key, data, label in [
            (meta_archive_key, meta_bytes, "meta archive"),
            (GCS_META_LATEST,  meta_bytes, "meta latest"),
        ]:
            try:
                write_bytes(data, key)
                logger.info(f"  {label}: {key}")
            except Exception as e:
                return {"status": "error", "error": f"{label} write: {e}"}
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "status":             "ok",
        "version":            VERSION,
        "features":           len(features),
        "feature_means":      len(fmeans),
        "train_rows":         oos["train_rows"],
        "test_rows":          oos["test_rows"],
        "auc_oos":            oos["auc_oos"],
        "brier_oos":          oos["brier_oos"],
        "best_iteration":     oos["best_iteration"],
        "scale_pos_weight":   scale_pos_weight,
        "cv_mean_auc":        cv_summary.get("cv_mean_auc"),
        "cv_std_auc":         cv_summary.get("cv_std_auc"),
        "booster_archive":    booster_archive_key,
        "meta_archive":       meta_archive_key,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
