"""
training/retrain_batter_tb_v1.py - BATTER_TB Pro v1 full retrain.

Count model predicting expected total bases per batter-game.
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

from BATTER_TB_System.config_batter_tb import BATTER_TB_FEATURES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger(__name__)

VERSION = "v1"
TARGET = "batter_total_bases"
GCS_MODEL_FEATURES = "BATTER_TB_System/data/model_features.csv"
GCS_BOOSTER_LATEST = "BATTER_TB_System/models/xgb_batter_tb_v1.json"
GCS_META_LATEST = "BATTER_TB_System/models/model_meta_batter_tb_v1.json"
GCS_BOOSTER_ARCHIVE = "BATTER_TB_System/models/archive/xgb_batter_tb_v1.{ts}.json"
GCS_META_ARCHIVE = "BATTER_TB_System/models/archive/model_meta_batter_tb_v1.{ts}.json"

XGB_PARAMS = {
    "objective": "count:poisson",
    "eval_metric": "poisson-nloglik",
    "max_depth": 4,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha": 1.0,
    "reg_lambda": 3.0,
    "gamma": 0.5,
    "seed": 42,
}
NUM_BOOST_ROUND = 2000
EARLY_STOPPING_ROUNDS = 50
CV_FOLDS = [2023, 2024, 2025]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import exists, read_csv

    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found - run BATTER_TB feature build first"
    df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    if df.empty:
        return None, "model_features.csv is empty"
    if TARGET not in df.columns:
        return None, f"target column {TARGET!r} missing"
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    df["year"] = df["game_date"].dt.year
    logger.info(
        "loaded %s rows | %s -> %s | mean TB=%.3f",
        f"{len(df):,}",
        df["game_date"].min().date(),
        df["game_date"].max().date(),
        df[TARGET].mean(),
    )
    return df, None


def _mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))


def _rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))


def _r2(y_true, y_pred) -> float:
    y = np.array(y_true)
    ss_res = float(np.sum((y - np.array(y_pred)) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / max(ss_tot, 1e-9)


def _fit_eval(df_tr: pd.DataFrame, df_te: pd.DataFrame, features: list[str]) -> tuple[xgb.Booster, dict]:
    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[TARGET].astype(float)

    nval = max(1, int(len(X_tr) * (7 / 8)))
    dtrain = xgb.DMatrix(X_tr.iloc[:nval], label=y_tr.iloc[:nval], feature_names=features)
    dval = xgb.DMatrix(X_tr.iloc[nval:], label=y_tr.iloc[nval:], feature_names=features)
    dtest = xgb.DMatrix(X_te, label=y_te, feature_names=features)
    booster = xgb.train(
        XGB_PARAMS,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=False,
    )
    pred = booster.predict(dtest)
    best_iter = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
    return booster, {
        "test_rows": len(df_te),
        "mae": _mae(y_te, pred),
        "rmse": _rmse(y_te, pred),
        "r2": _r2(y_te, pred),
        "cal_gap": float(np.mean(pred) - np.mean(y_te)),
        "best_iteration": best_iter,
    }


def _walk_forward_cv(df: pd.DataFrame, features: list[str]) -> list[dict]:
    out = []
    for year in CV_FOLDS:
        df_tr = df[df["year"].isin([year - 2, year - 1])]
        df_te = df[df["year"] == year]
        if len(df_tr) < 100 or len(df_te) < 20:
            logger.info("fold %s skipped: train=%s test=%s", year, len(df_tr), len(df_te))
            continue
        _, metrics = _fit_eval(df_tr, df_te, features)
        metrics = {"test_year": year, "n_train": len(df_tr), **metrics}
        out.append(metrics)
        logger.info(
            "fold %s: MAE=%.3f RMSE=%.3f R2=%.3f cal=%+.3f best_iter=%s",
            year, metrics["mae"], metrics["rmse"], metrics["r2"],
            metrics["cal_gap"], metrics["best_iteration"],
        )
    return out


def _oos_eval(df: pd.DataFrame, features: list[str]) -> dict:
    last = CV_FOLDS[-1]
    df_tr = df[df["year"] < last]
    df_te = df[df["year"] == last]
    if len(df_te) < 20:
        raise RuntimeError(f"OOS test fold {last} too small ({len(df_te)} rows)")
    _, m = _fit_eval(df_tr, df_te, features)
    m.update({"train_rows": len(df_tr), "test_year": last})
    logger.info(
        "OOS: MAE=%.3f RMSE=%.3f R2=%.3f cal=%+.3f best_iter=%s",
        m["mae"], m["rmse"], m["r2"], m["cal_gap"], m["best_iteration"],
    )
    return {
        "train_rows": int(m["train_rows"]),
        "test_rows": int(m["test_rows"]),
        "mae_oos": round(m["mae"], 4),
        "rmse_oos": round(m["rmse"], 4),
        "r2_oos": round(m["r2"], 4),
        "cal_oos": round(m["cal_gap"], 4),
        "best_iteration": int(m["best_iteration"]),
        "test_year": int(last),
    }


def _full_retrain(df: pd.DataFrame, features: list[str], best_iter: int) -> xgb.Booster:
    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(float)
    return xgb.train(
        XGB_PARAMS,
        xgb.DMatrix(X, label=y, feature_names=features),
        num_boost_round=best_iter,
        verbose_eval=False,
    )


def _feature_stats(df: pd.DataFrame, features: list[str]) -> tuple[dict, dict, dict]:
    X = df[features].apply(pd.to_numeric, errors="coerce")
    means = {f: float(X[f].mean()) for f in features if not pd.isna(X[f].mean())}
    stds = {f: round(float(X[f].std()), 6) for f in features if not pd.isna(X[f].std())}
    dists = {}
    for f in features:
        col = X[f].dropna()
        if len(col) >= 10:
            dists[f] = {
                "p5": round(float(np.percentile(col, 5)), 6),
                "p10": round(float(np.percentile(col, 10)), 6),
                "p25": round(float(np.percentile(col, 25)), 6),
                "p50": round(float(np.percentile(col, 50)), 6),
                "p75": round(float(np.percentile(col, 75)), 6),
                "p90": round(float(np.percentile(col, 90)), 6),
                "p95": round(float(np.percentile(col, 95)), 6),
            }
    return means, stds, dists


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import upload_model, write_bytes

    try:
        from training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params("BATTER_TB")
        if tuned:
            global XGB_PARAMS
            XGB_PARAMS = tuned["xgb_params"]
            logger.info("Using Optuna-tuned params (score=%s)", tuned.get("best_score"))
    except Exception as e:
        logger.warning("Could not load tuned params: %s", e)

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}
    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    features = [f for f in BATTER_TB_FEATURES if f in df.columns]
    missing = [f for f in BATTER_TB_FEATURES if f not in df.columns]
    if missing:
        logger.warning("missing features skipped: %s", missing)
    if len(features) < 5:
        return {"status": "error", "error": f"too few usable features ({len(features)})"}

    try:
        wf = _walk_forward_cv(df, features)
        oos = _oos_eval(df, features)
        booster = _full_retrain(df, features, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": str(e)}

    X_all = df[features].apply(pd.to_numeric, errors="coerce")
    y_all = df[TARGET].astype(float).values
    pred = booster.predict(xgb.DMatrix(X_all, feature_names=features))
    resid_var = float(np.var(y_all - pred))
    mu_mean = float(np.mean(pred))
    nb_alpha = float(np.clip((resid_var - mu_mean) / max(mu_mean ** 2, 1e-6), 0.01, 0.75))
    means, stds, dists = _feature_stats(df, features)

    wf_summary = {}
    if wf:
        wdf = pd.DataFrame(wf)
        wf_summary = {
            "wf_mae": round(float(wdf["mae"].mean()), 4),
            "wf_rmse": round(float(wdf["rmse"].mean()), 4),
            "wf_r2": round(float(wdf["r2"].mean()), 4),
            "wf_cal": round(float(wdf["cal_gap"].mean()), 4),
            "wf_folds": wf,
        }

    meta = {
        "version": VERSION,
        "model_type": "batter_tb_poisson",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "features": features,
        "feature_means": means,
        "feature_stds": stds,
        "feature_dists": dists,
        "nb_alpha": nb_alpha,
        "cv_folds": CV_FOLDS,
        **oos,
        **wf_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")
    ts = _ts()

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        booster_tmp = Path(f.name)
    try:
        booster.save_model(str(booster_tmp))
        upload_model(booster_tmp, GCS_BOOSTER_ARCHIVE.format(ts=ts))
        write_bytes(meta_bytes, GCS_META_ARCHIVE.format(ts=ts))
        upload_model(booster_tmp, GCS_BOOSTER_LATEST)
        write_bytes(meta_bytes, GCS_META_LATEST)
    finally:
        booster_tmp.unlink(missing_ok=True)

    return {
        "status": "ok",
        "version": VERSION,
        "features": len(features),
        "train_rows": oos["train_rows"],
        "test_rows": oos["test_rows"],
        "mae_oos": oos["mae_oos"],
        "rmse_oos": oos["rmse_oos"],
        "r2_oos": oos["r2_oos"],
        "best_iteration": oos["best_iteration"],
        "nb_alpha": nb_alpha,
        "wf_mae": wf_summary.get("wf_mae"),
        "booster_latest": GCS_BOOSTER_LATEST,
        "meta_latest": GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
