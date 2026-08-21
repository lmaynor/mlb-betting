"""
training/retrain_sb_v1.py -- SB (stolen base) Pro v1 full retrain.

NegBin count regressor: XGBoost count:poisson predicting lambda (expected
stolen bases per game). At score time, P(SB > line) = 1 - NegBin_CDF(floor(line),
lambda, nb_alpha). Mirrors retrain_batter_hits_v1.py exactly -- same NegBin
architecture, same market shape (real O/U, confirmed live 2026-08-20).

Trains on 2023+ data ONLY (see SB_Pro_System/config_sb.py's season_start) --
the 2023-03-30 pitch-clock/bigger-base rules shifted stolen-base behavior
materially, so pre-2023 data is a different game, not just more history.

Output GCS keys (matches SB_Pro_System/config_sb.py):
  - SB_Pro_System/models/xgb_sb_v1.json
  - SB_Pro_System/models/model_meta_sb_v1.json
  - SB_Pro_System/models/archive/...{ts}...

Entrypoint: python -m mlb.training.retrain_sb_v1
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

from mlb.systems.SB_Pro_System.config_sb import SB_FEATURES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)


VERSION = "v1"
TARGET  = "stolen_bases"

XGB_PARAMS = {
    "objective":        "count:poisson",
    "eval_metric":      "poisson-nloglik",
    "max_depth":        4,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha":        1.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
}

NUM_BOOST_ROUND       = 2000
EARLY_STOPPING_ROUNDS = 50


def _cv_folds(df: pd.DataFrame, n: int = 3) -> list[int]:
    """Walk-forward test years: the most recent `n` years actually present in
    the data, not a hardcoded literal (see retrain_batter_hits_v1.py's C3.3
    fix for why -- a fixed year list goes silently stale every offseason)."""
    years = sorted(int(y) for y in df["year"].dropna().unique())
    return years[-n:] if len(years) >= n else years


GCS_MODEL_FEATURES  = "SB_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"SB_Pro_System/models/xgb_sb_{VERSION}.json"
GCS_META_LATEST     = f"SB_Pro_System/models/model_meta_sb_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"SB_Pro_System/models/archive/xgb_sb_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"SB_Pro_System/models/archive/model_meta_sb_{VERSION}.{{ts}}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found -- run /build-features for SB first"
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

    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    logger.info(
        f"loaded features: {len(df):,} training rows ({dropped} slate rows excluded) | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"mean SB={df[TARGET].mean():.4f} | pct games with SB>=1={100*(df[TARGET]>=1).mean():.2f}%"
    )
    return df, None


def _mae(y_true, y_pred):  return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))
def _rmse(y_true, y_pred): return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))
def _r2(y_true, y_pred):
    ss_res = float(np.sum((np.array(y_true) - np.array(y_pred)) ** 2))
    ss_tot = float(np.sum((np.array(y_true) - np.mean(y_true)) ** 2))
    return 1 - ss_res / max(ss_tot, 1e-9)


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    results = []
    logger.info("=== Walk-forward CV ===")
    for test_year in _cv_folds(df):
        train_years = [y for y in (test_year - 2, test_year - 1) if y in df["year"].unique()]
        df_tr = df[df["year"].isin(train_years)]
        df_te = df[df["year"] == test_year]
        if len(df_tr) < 100 or len(df_te) < 20:
            logger.info(f"  fold {test_year}: insufficient data "
                        f"(train={len(df_tr)}, test={len(df_te)}) -- skip")
            continue

        X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
        y_tr = df_tr[TARGET].astype(float)
        X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
        y_te = df_te[TARGET].astype(float)

        _nval    = int(len(X_tr) * (7 / 8))
        dtrain_s = xgb.DMatrix(X_tr.iloc[:_nval],  label=y_tr.iloc[:_nval],  feature_names=features)
        dval_s   = xgb.DMatrix(X_tr.iloc[_nval:],  label=y_tr.iloc[_nval:],  feature_names=features)
        dtest    = xgb.DMatrix(X_te,                label=y_te,               feature_names=features)

        booster = xgb.train(
            XGB_PARAMS, dtrain_s,
            num_boost_round=NUM_BOOST_ROUND,
            evals=[(dtrain_s, "train"), (dval_s, "val")],
            early_stopping_rounds=EARLY_STOPPING_ROUNDS,
            verbose_eval=False,
        )
        y_pred = booster.predict(dtest)
        mae  = _mae(y_te, y_pred)
        rmse = _rmse(y_te, y_pred)
        r2   = _r2(y_te, y_pred)
        cal  = float(np.mean(y_pred) - np.mean(y_te))
        best = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
        results.append({
            "test_year": test_year, "n_train": len(df_tr), "n_test": len(df_te),
            "mae": mae, "rmse": rmse, "r2": r2, "cal_gap": cal, "best_iteration": best,
        })
        logger.info(f"  fold {test_year}: MAE={mae:.4f} RMSE={rmse:.4f} R2={r2:.3f} "
                    f"cal={cal:+.4f} best_iter={best}")
    return results


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    last  = _cv_folds(df)[-1]
    df_tr = df[df["year"] < last]
    df_te = df[df["year"] == last]
    if len(df_te) < 20:
        raise RuntimeError(f"OOS test fold ({last}) has too few rows ({len(df_te)})")

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[TARGET].astype(float)

    logger.info(f"OOS split | train={len(df_tr)} (years <{last}) | test={len(df_te)} (year={last})")

    _nval   = int(len(X_tr) * (7 / 8))
    dtrain  = xgb.DMatrix(X_tr.iloc[:_nval],  label=y_tr.iloc[:_nval],  feature_names=features)
    dval    = xgb.DMatrix(X_tr.iloc[_nval:],  label=y_tr.iloc[_nval:],  feature_names=features)
    dtest   = xgb.DMatrix(X_te,               label=y_te,               feature_names=features)

    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )
    y_pred    = booster.predict(dtest)
    y_tr_pred = booster.predict(dtrain)

    mae_oos   = _mae(y_te, y_pred)
    rmse_oos  = _rmse(y_te, y_pred)
    r2_oos    = _r2(y_te, y_pred)
    cal_oos   = float(np.mean(y_pred) - np.mean(y_te))
    best_iter = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(f"OOS | MAE={mae_oos:.4f} RMSE={rmse_oos:.4f} R2={r2_oos:.3f} "
                f"cal={cal_oos:+.4f} best_iter={best_iter}")
    return {
        "train_rows":     int(len(df_tr)),
        "test_rows":      int(len(df_te)),
        "mae_oos":        round(mae_oos, 4),
        "rmse_oos":       round(rmse_oos, 4),
        "r2_oos":         round(r2_oos, 4),
        "cal_oos":        round(cal_oos, 4),
        "mae_train":      round(_mae(y_tr.iloc[:_nval], y_tr_pred), 4),
        "overfit_gap":    round(abs(_mae(y_tr.iloc[:_nval], y_tr_pred) - mae_oos), 4),
        "best_iteration": best_iter,
        "test_year":      int(last),
    }


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    logger.info(f"full retrain | rows={len(df)} features={len(features)} rounds={best_iter}")
    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(float)
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    return xgb.train(XGB_PARAMS, dtrain, num_boost_round=best_iter, verbose_eval=False)


def _feature_means(df: pd.DataFrame, features: list) -> dict:
    means = {}
    X = df[features].apply(pd.to_numeric, errors="coerce")
    for f in features:
        v = X[f].mean(skipna=True)
        if not pd.isna(v):
            means[f] = float(v)
    logger.info(f"feature_means computed for {len(means)}/{len(features)} features")
    return means


def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.10) -> list[str]:
    import os
    if os.getenv("SB_SKIP_LEAKAGE_CHECK") == "1":
        logger.info("leakage check skipped")
        return []

    last         = _cv_folds(df)[-1]
    df_tr        = df[df["year"] < last].copy()
    df_te        = df[df["year"] == last].copy()
    if len(df_te) < 10:
        return []

    best_iter    = oos["best_iteration"]
    baseline_mae = oos["mae_oos"]
    y_tr = df_tr[TARGET].astype(float)
    y_te = df_te[TARGET].astype(float)
    suspicious = []

    logger.info(f"leakage check | baseline MAE={baseline_mae:.4f} | threshold={threshold:.0%}")
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
        b = xgb.train(XGB_PARAMS, dtrain_z, num_boost_round=best_iter, verbose_eval=False)
        mae_z = _mae(y_te, b.predict(dtest_z))
        if (baseline_mae - mae_z) / max(baseline_mae, 1e-9) > threshold:
            suspicious.append(feat)
            logger.warning(f"  LEAKAGE SUSPECT: {feat!r} | "
                           f"MAE {baseline_mae:.4f} -> {mae_z:.4f}")

    if not suspicious:
        logger.info("  leakage check passed")
    return suspicious


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    try:
        from mlb.training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params("SB")
        if tuned:
            global XGB_PARAMS
            XGB_PARAMS = tuned["xgb_params"]
            logger.info(f"Using Optuna-tuned params (score={tuned['best_score']})")
    except Exception as e:
        logger.warning(f"Could not load tuned params: {e}")

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in SB_FEATURES if f in df.columns]
    missing   = [f for f in SB_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (skipped): {missing}")

    try:
        wf = _walk_forward_cv(df, available)
    except Exception as e:
        return {"status": "error", "error": f"walk-forward CV: {e}"}

    try:
        oos = _oos_eval(df, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    leakage_suspects = _leakage_check(df, available, oos)

    try:
        booster = _full_retrain(df, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    # NB dispersion: var = mu + alpha*mu^2  =>  alpha = (var - mu) / mu^2.
    # Stolen bases are a rare, highly right-skewed count (most games are 0) --
    # expect nb_alpha to land near the high end of the clip range.
    try:
        X_all  = df[available].apply(pd.to_numeric, errors="coerce")
        dm_all = xgb.DMatrix(X_all, feature_names=available)
        y_all  = df[TARGET].astype(float).values
        preds  = booster.predict(dm_all)
        resid_var = float(np.var(y_all - preds))
        mu_mean   = float(np.mean(preds))
        nb_alpha  = float(np.clip((resid_var - mu_mean) / max(mu_mean ** 2, 1e-6), 0.01, 0.50))
        logger.info(f"NB dispersion | mu={mu_mean:.4f} resid_var={resid_var:.4f} nb_alpha={nb_alpha:.4f}")
    except Exception as e:
        nb_alpha = 0.10
        logger.warning(f"nb_alpha fit failed ({e}) -- using default 0.10")

    fmeans = _feature_means(df, available)

    fstds: dict = {}
    X_all2 = df[available].apply(pd.to_numeric, errors="coerce")
    for f in available:
        v = X_all2[f].std(skipna=True)
        if not pd.isna(v):
            fstds[f] = round(float(v), 6)

    fpdists: dict = {}
    for _f in available:
        try:
            _col = pd.to_numeric(df[_f], errors="coerce").dropna()
            if len(_col) < 10:
                continue
            fpdists[_f] = {
                "p5":     round(float(np.percentile(_col,  5)), 6),
                "p10":    round(float(np.percentile(_col, 10)), 6),
                "p25":    round(float(np.percentile(_col, 25)), 6),
                "p50":    round(float(np.percentile(_col, 50)), 6),
                "p75":    round(float(np.percentile(_col, 75)), 6),
                "p90":    round(float(np.percentile(_col, 90)), 6),
                "p95":    round(float(np.percentile(_col, 95)), 6),
                "prop_1": round(float((_col == 1).mean()), 6),
            }
        except Exception:
            continue

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
            "wf_mae":     round(float(wf_df["mae"].mean()), 4),
            "wf_rmse":    round(float(wf_df["rmse"].mean()), 4),
            "wf_r2":      round(float(wf_df["r2"].mean()), 4),
            "wf_cal":     round(float(wf_df["cal_gap"].mean()), 4),
            "wf_mae_std": round(float(wf_df["mae"].std()), 4),
            "wf_folds":   wf,
        }

    meta = {
        "version":       VERSION,
        "model_type":    "sb_poisson",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      available,
        "nb_alpha":      nb_alpha,
        "feature_dists": fpdists,
        "feature_means": fmeans,
        "feature_stds":  fstds,
        "cv_folds":      _cv_folds(df),
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
        try:
            upload_model(booster_tmp, booster_archive_key)
            logger.info(f"archive booster: {booster_archive_key}")
        except Exception as e:
            return {"status": "error", "error": f"archive booster write: {e}"}
        try:
            write_bytes(meta_bytes, meta_archive_key)
        except Exception as e:
            return {"status": "error", "error": f"archive meta write: {e}"}
        try:
            upload_model(booster_tmp, GCS_BOOSTER_LATEST)
            logger.info(f"latest booster: {GCS_BOOSTER_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest booster write: {e}"}
        try:
            write_bytes(meta_bytes, GCS_META_LATEST)
            logger.info(f"latest meta: {GCS_META_LATEST}")
        except Exception as e:
            return {"status": "error", "error": f"latest meta write: {e}"}
    finally:
        booster_tmp.unlink(missing_ok=True)

    return {
        "status":           "ok",
        "version":          VERSION,
        "features":         len(available),
        "train_rows":       oos["train_rows"],
        "test_rows":        oos["test_rows"],
        "mae_oos":          oos["mae_oos"],
        "rmse_oos":         oos["rmse_oos"],
        "r2_oos":           oos["r2_oos"],
        "best_iteration":   oos["best_iteration"],
        "nb_alpha":         nb_alpha,
        "wf_mae":           wf_summary.get("wf_mae"),
        "leakage_suspects": leakage_suspects,
        "booster_archive":  booster_archive_key,
        "meta_archive":     meta_archive_key,
        "booster_latest":   GCS_BOOSTER_LATEST,
        "meta_latest":      GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
