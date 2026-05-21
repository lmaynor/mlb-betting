"""
training/retrain_f5_v5.py — F5 Pro v5 full retrain (Cloud Run Job). (T11)

Replaces retrain_f5_meta.py (meta-patch shim). Mirrors the NRFI retrain
pattern:
  1. Load model_features.csv from GCS.
  2. OOS eval: 80/20 time-split, early stopping, capture AUC/Brier/HL.
  3. Walk-forward CV across last 3 available years (T10).
  4. Full retrain on 100% of data using best_iteration rounds.
  5. Compute feature_means + feature_stds from full training set.
  6. Write archive + latest pointer to GCS.

retrain_f5_meta.py is now a deprecated stub. Do not run both.

Target: home_wins_f5 (binary: 1 = home team wins innings 1-5)
Feature list: loaded dynamically from model_features.csv — all numeric
columns except target, id cols, and date cols. This matches the notebook
behavior where the feature set is whatever the builder produces.

Output GCS keys (match F5_Pro_System/config_f5.py):
  - F5_Pro_System/models/xgb_f5_v5.json
  - F5_Pro_System/models/model_meta_f5_v5.json
  - F5_Pro_System/models/archive/xgb_f5_v5.{ts}.json
  - F5_Pro_System/models/archive/model_meta_f5_v5.{ts}.json

Entrypoint: python -m training.retrain_f5_v5
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


VERSION          = "v5"
TARGET           = "home_wins_f5"
TRAIN_TEST_SPLIT = 0.8
NUM_BOOST_ROUND  = 1500
EARLY_STOPPING   = 50

# Columns that are never model features
_NON_FEATURE_COLS = {
    "game_pk", "game_date", "home_team", "away_team",
    "season", "year", "date", "home_wins_f5",
    "home_runs_f5", "away_runs_f5",
    # NRFI-origin columns that aren't F5 features
    "pitcher", "player_name", "inning_topbot", "p_throws",
}

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      ["logloss", "auc"],
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

GCS_MODEL_FEATURES  = "F5_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = "F5_Pro_System/models/xgb_f5_v5.json"
GCS_META_LATEST     = "F5_Pro_System/models/model_meta_f5_v5.json"
GCS_BOOSTER_ARCHIVE = "F5_Pro_System/models/archive/xgb_f5_v5.{ts}.json"
GCS_META_ARCHIVE    = "F5_Pro_System/models/archive/model_meta_f5_v5.{ts}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _hosmer_lemeshow(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
    try:
        df = pd.DataFrame({"y": y_true, "p": y_pred})
        df["bin"] = pd.qcut(df["p"], q=n_bins, duplicates="drop")
        grouped = df.groupby("bin").agg(
            obs=("y", "sum"), exp=("p", "sum"), n=("y", "count")
        )
        hl = ((grouped["obs"] - grouped["exp"]) ** 2 /
              (grouped["exp"] * (1 - grouped["exp"] / grouped["n"]))).sum()
        return float(chi2.sf(hl, df=n_bins - 2))
    except Exception:
        return float("nan")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, None, f"{GCS_MODEL_FEATURES} not found — run /build-features for F5"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, None, f"features load: {e}"
    if df.empty:
        return None, None, f"{GCS_MODEL_FEATURES} is empty"
    if TARGET not in df.columns:
        return None, None, f"target column '{TARGET}' missing"

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = df[TARGET].astype(int)

    # Derive feature list: all numeric columns not in exclusion set
    candidate = [
        c for c in df.columns
        if c not in _NON_FEATURE_COLS
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    # Drop features with < 10% non-NaN coverage — not usable
    features = [
        c for c in candidate
        if df[c].notna().mean() >= 0.10
    ]
    logger.info(
        f"F5 features: {len(df):,} training rows | "
        f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
        f"home win rate {df[TARGET].mean():.3f} | {len(features)} features"
    )
    return df, features, None


def _oos_eval(df, X, y, features):
    # C03: 70/10/20 split -- val for early stopping, test never seen during training.
    test_idx  = int(len(X) * TRAIN_TEST_SPLIT)
    val_idx   = int(test_idx * (7/8))
    X_tr, X_val, X_te = X.iloc[:val_idx], X.iloc[val_idx:test_idx], X.iloc[test_idx:]
    y_tr, y_val, y_te = y.iloc[:val_idx], y.iloc[val_idx:test_idx], y.iloc[test_idx:]
    train_through = df["game_date"].iloc[val_idx - 1].strftime("%Y-%m-%d")
    val_through   = df["game_date"].iloc[test_idx - 1].strftime("%Y-%m-%d")
    test_from     = df["game_date"].iloc[test_idx].strftime("%Y-%m-%d")

    logger.info(
        f"OOS split (70/10/20) | train={len(X_tr)} (thru {train_through}) | "
        f"val={len(X_val)} (thru {val_through}) | test={len(X_te)} (from {test_from})"
    )
    dtrain = xgb.DMatrix(X_tr,  label=y_tr,  feature_names=features)
    dval   = xgb.DMatrix(X_val, label=y_val, feature_names=features)
    dtest  = xgb.DMatrix(X_te,  label=y_te,  feature_names=features)
    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING,
        verbose_eval=100,
    )
    preds    = booster.predict(dtest)
    auc      = float(roc_auc_score(y_te, preds))
    brier    = float(brier_score_loss(y_te, preds))
    ll       = float(log_loss(y_te, preds))
    hl_p     = _hosmer_lemeshow(y_te.values, preds)
    cal_gap  = round(abs(preds.mean() - y_te.mean()), 4)
    best_iter = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(
        f"OOS | AUC={auc:.4f} Brier={brier:.4f} LogLoss={ll:.4f} "
        f"H-L p={hl_p:.3f} cal_gap={cal_gap} best_iter={best_iter}"
    )
    return {
        "train_through": train_through,
        "test_from":     test_from,
        "train_rows":    int(len(X_tr)),
        "test_rows":     int(len(X_te)),
        "auc_oos":       round(auc,    4),
        "brier_oos":     round(brier,  4),
        "logloss_oos":   round(ll,     4),
        "hl_p":          round(hl_p,   3),
        "cal_gap":       cal_gap,
        "best_iteration": best_iter,
    }


def _walk_forward_cv(df, features):
    """Year-based walk-forward CV. Returns list of per-fold dicts + summary."""
    if os.getenv("F5_SKIP_CV") == "1":
        logger.info("F5 walk-forward CV skipped (F5_SKIP_CV=1)")
        return {}, {}

    df_cv = df.copy()
    df_cv["_year"] = df_cv["game_date"].dt.year
    years = sorted(df_cv["_year"].unique())
    folds = [int(y) for y in years if y >= years[-1] - 2]
    logger.info(f"F5 walk-forward CV folds: {folds}")

    results = []
    for test_year in folds:
        tr = df_cv[df_cv["_year"] < test_year]
        te = df_cv[df_cv["_year"] == test_year]
        if len(tr) < 100 or len(te) < 50:
            continue
        # C03: carve val from train -- never use test during early stopping
        val_size     = max(50, len(tr) // 10)
        tr_inner     = tr.iloc[:-val_size]
        val_inner    = tr.iloc[-val_size:]
        X_tr_inner   = tr_inner[features].apply(pd.to_numeric, errors="coerce")
        y_tr_inner   = tr_inner[TARGET].astype(int)
        X_val_inner  = val_inner[features].apply(pd.to_numeric, errors="coerce")
        y_val_inner  = val_inner[TARGET].astype(int)
        X_te         = te[features].apply(pd.to_numeric, errors="coerce")
        y_te         = te[TARGET].astype(int)
        dtrain_inner = xgb.DMatrix(X_tr_inner, label=y_tr_inner, feature_names=features)
        dval_inner   = xgb.DMatrix(X_val_inner, label=y_val_inner, feature_names=features)
        dtest        = xgb.DMatrix(X_te,        label=y_te,        feature_names=features)
        b = xgb.train(
            XGB_PARAMS, dtrain_inner,
            num_boost_round=NUM_BOOST_ROUND,
            evals=[(dtrain_inner, "train"), (dval_inner, "val")],
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
        f"F5 CV AUC {summary['cv_mean_auc']} ± {summary['cv_std_auc']} "
        f"95% CI [{summary['cv_auc_ci_lo']}, {summary['cv_auc_ci_hi']}]"
    )
    return results, summary


def _full_retrain(X, y, features, best_iter):
    logger.info(f"F5 full retrain | rows={len(X)} features={len(features)} rounds={best_iter}")
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    params = {**XGB_PARAMS, "eval_metric": "logloss"}
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

    # Walk-forward CV (T10)
    _, cv_summary = _walk_forward_cv(df, features)

    # OOS eval
    try:
        oos = _oos_eval(df, X, y, features)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    # Full retrain
    try:
        booster = _full_retrain(X, y, features, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    fmeans, fstds = _feature_stats(X, features)
    _feat_list = features

    # C04: empirical percentiles for PSI drift monitor.
    # Avoids Gaussian misfit for binary/bounded/bimodal features.
    fpdists: dict = {}
    for _f in _feat_list:
        try:
            _col = df[_f] if _f in df.columns else None
            if _col is None:
                continue
            _col_num = pd.to_numeric(_col, errors="coerce").dropna()
            if len(_col_num) < 10:
                continue
            fpdists[_f] = {
                "p5":     round(float(np.percentile(_col_num,  5)), 6),
                "p10":    round(float(np.percentile(_col_num, 10)), 6),
                "p25":    round(float(np.percentile(_col_num, 25)), 6),
                "p50":    round(float(np.percentile(_col_num, 50)), 6),
                "p75":    round(float(np.percentile(_col_num, 75)), 6),
                "p90":    round(float(np.percentile(_col_num, 90)), 6),
                "p95":    round(float(np.percentile(_col_num, 95)), 6),
                "prop_1": round(float((_col_num == 1).mean()), 6),
            }
        except Exception:
            continue

    ts = _ts()
    meta = {
        "version":       VERSION,
        "model_type":    "f5_binary",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      features,
        "feature_dists":  fpdists,
        "feature_means": fmeans,
        "feature_stds":  fstds,
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
        for key, src in [
            (booster_archive_key, "booster archive"),
            (GCS_BOOSTER_LATEST,  "booster latest"),
        ]:
            try:
                upload_model(tmp, key)
                logger.info(f"  {src}: {key}")
            except Exception as e:
                return {"status": "error", "error": f"{src} write: {e}"}
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
        "status":         "ok",
        "version":        VERSION,
        "features":       len(features),
        "feature_means":  len(fmeans),
        "train_rows":     oos["train_rows"],
        "test_rows":      oos["test_rows"],
        "auc_oos":        oos["auc_oos"],
        "brier_oos":      oos["brier_oos"],
        "best_iteration": oos["best_iteration"],
        "cv_mean_auc":    cv_summary.get("cv_mean_auc"),
        "cv_std_auc":     cv_summary.get("cv_std_auc"),
        "booster_archive": booster_archive_key,
        "meta_archive":    meta_archive_key,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
