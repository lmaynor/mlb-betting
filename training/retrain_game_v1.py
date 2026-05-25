"""
training/retrain_game_v1.py -- GAME Pro v1 full retrain.

Binary classifier: XGBoost binary:logistic predicting P(home team wins
full game). Key differentiator from F5: dedicated bullpen features.

NOTE: The feature builder (runners/build_game_features.py) must include a
      `home_win` column in model_features.csv. This is derived from
      Scoring/scoring_master.csv: sum runs for home team across all innings,
      sum for away team, home > away -> 1, else -> 0.
      If `home_win` is absent, this retrain will abort with a clear error.

Output GCS keys (matches GAME_Pro_System/config_game.py):
  - GAME_Pro_System/models/xgb_game_v1.json
  - GAME_Pro_System/models/model_meta_game_v1.json
  - GAME_Pro_System/models/archive/...{ts}...

Entrypoint: python -m training.retrain_game_v1
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
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)


VERSION = "v1"
TARGET  = "home_win"
SYSTEM  = "GAME"

# Feature contract -- keep in sync with GAME_Pro_System/config_game.py
GAME_FEATURES = [
    # Home starter
    "home_k_pct_L3",
    "home_xwoba_allowed_L3",
    "home_velo_mean_L3",
    "home_bb_pct_L3",
    "home_starter_ip_avg_L5",
    "home_starter_days_rest",
    "home_whiff_pct_L3",
    "home_hard_hit_allowed_L3",
    # Away starter
    "away_k_pct_L3",
    "away_xwoba_allowed_L3",
    "away_velo_mean_L3",
    "away_bb_pct_L3",
    "away_starter_ip_avg_L5",
    "away_starter_days_rest",
    "away_whiff_pct_L3",
    "away_hard_hit_allowed_L3",
    # Home bullpen (key differentiator from F5)
    "home_bullpen_xwoba_L14",
    "home_bullpen_k_pct_L14",
    "home_bullpen_bb_pct_L14",
    "home_bullpen_ip_L7",
    "home_bullpen_whiff_pct_L14",
    "home_bullpen_hard_hit_L14",
    # Away bullpen
    "away_bullpen_xwoba_L14",
    "away_bullpen_k_pct_L14",
    "away_bullpen_bb_pct_L14",
    "away_bullpen_ip_L7",
    "away_bullpen_whiff_pct_L14",
    "away_bullpen_hard_hit_L14",
    # Team offense
    "home_team_woba_L20",
    "away_team_woba_L20",
    "home_team_k_pct_L20",
    "away_team_k_pct_L20",
    "home_run_diff_L20",
    "home_team_hard_hit_L20",
    "away_team_hard_hit_L20",
    # Park / weather / context
    "park_factor",
    "temperature_f",
    "wind_speed_mph",
    "wind_out",
    "wind_in",
    "is_dome",
    "post_pitch_clock",
]

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      "auc",
    "max_depth":        3,
    "learning_rate":    0.05,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "gamma":            0.1,
    "seed":             42,
}

NUM_BOOST_ROUND       = 2000
EARLY_STOPPING_ROUNDS = 50
CV_FOLDS              = [2023, 2024, 2025]

GCS_MODEL_FEATURES  = "GAME_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST  = f"GAME_Pro_System/models/xgb_game_{VERSION}.json"
GCS_META_LATEST     = f"GAME_Pro_System/models/model_meta_game_{VERSION}.json"
GCS_BOOSTER_ARCHIVE = f"GAME_Pro_System/models/archive/xgb_game_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE    = f"GAME_Pro_System/models/archive/model_meta_game_{VERSION}.{{ts}}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found -- run /build-features for GAME first"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"
    if df.empty:
        return None, f"{GCS_MODEL_FEATURES} is empty"
    if TARGET not in df.columns:
        return None, (
            f"target column {TARGET!r} missing from features CSV. "
            "The feature builder must derive home_win from scoring_master.csv "
            "(sum home runs > sum away runs -> 1, else -> 0) and include it."
        )

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date").reset_index(drop=True)

    before = len(df)
    df = df.dropna(subset=[TARGET]).copy()
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    df[TARGET] = df[TARGET].astype(int)
    dropped = before - len(df)
    df["year"] = df["game_date"].dt.year

    win_rate = df[TARGET].mean()
    logger.info(
        "loaded features: %d training rows (%d slate rows excluded) | "
        "%s -> %s | home_win_rate=%.3f",
        len(df), dropped,
        df["game_date"].min().date(), df["game_date"].max().date(),
        win_rate,
    )
    return df, None


def _auc(y_true, y_pred):
    """Simple AUC via sklearn."""
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(np.array(y_true), np.array(y_pred)))
    except Exception:
        return float("nan")


def _brier(y_true, y_pred):
    return float(np.mean((np.array(y_pred) - np.array(y_true)) ** 2))


def _logloss(y_true, y_pred):
    eps = 1e-7
    p = np.clip(np.array(y_pred), eps, 1 - eps)
    y = np.array(y_true)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _walk_forward_cv(df: pd.DataFrame, features: list) -> list[dict]:
    results = []
    logger.info("=== Walk-forward CV ===")
    for test_year in CV_FOLDS:
        train_years = [test_year - 2, test_year - 1]
        df_tr = df[df["year"].isin(train_years)]
        df_te = df[df["year"] == test_year]
        if len(df_tr) < 100 or len(df_te) < 20:
            logger.info("  fold %d: insufficient data (train=%d, test=%d) -- skip",
                        test_year, len(df_tr), len(df_te))
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
        auc    = _auc(y_te, y_pred)
        brier  = _brier(y_te, y_pred)
        ll     = _logloss(y_te, y_pred)
        cal    = float(np.mean(y_pred) - np.mean(y_te))
        best   = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
        results.append({
            "test_year": test_year, "n_train": len(df_tr), "n_test": len(df_te),
            "auc": auc, "brier": brier, "logloss": ll, "cal_gap": cal,
            "best_iteration": best,
        })
        logger.info("  fold %d: AUC=%.4f Brier=%.4f LogLoss=%.4f cal=%+.4f best_iter=%d",
                    test_year, auc, brier, ll, cal, best)
    return results


def _oos_eval(df: pd.DataFrame, features: list) -> dict:
    last  = CV_FOLDS[-1]
    df_tr = df[df["year"] < last]
    df_te = df[df["year"] == last]
    if len(df_te) < 20:
        raise RuntimeError(f"OOS test fold ({last}) has too few rows ({len(df_te)})")

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[TARGET].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[TARGET].astype(float)

    logger.info("OOS split | train=%d (years <%d) | test=%d (year=%d)",
                len(df_tr), last, len(df_te), last)

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
    y_pred       = booster.predict(dtest)
    y_tr_pred    = booster.predict(dtrain)

    auc_oos      = _auc(y_te, y_pred)
    brier_oos    = _brier(y_te, y_pred)
    ll_oos       = _logloss(y_te, y_pred)
    cal_oos      = float(np.mean(y_pred) - np.mean(y_te))
    best_iter    = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
    auc_train    = _auc(y_tr.iloc[:_nval], y_tr_pred)
    overfit_gap  = round(abs(auc_train - auc_oos), 4)

    logger.info("OOS | AUC=%.4f Brier=%.4f LogLoss=%.4f cal=%+.4f best_iter=%d",
                auc_oos, brier_oos, ll_oos, cal_oos, best_iter)
    return {
        "train_rows":     int(len(df_tr)),
        "test_rows":      int(len(df_te)),
        "auc_oos":        round(auc_oos, 4),
        "brier_oos":      round(brier_oos, 4),
        "logloss_oos":    round(ll_oos, 4),
        "cal_oos":        round(cal_oos, 4),
        "auc_train":      round(auc_train, 4),
        "overfit_gap":    overfit_gap,
        "best_iteration": best_iter,
        "test_year":      int(last),
    }


def _full_retrain(df: pd.DataFrame, features: list, best_iter: int) -> xgb.Booster:
    logger.info("full retrain | rows=%d features=%d rounds=%d", len(df), len(features), best_iter)
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
    logger.info("feature_means computed for %d/%d features", len(means), len(features))
    return means


def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.10) -> list[str]:
    import os
    if os.getenv("GAME_SKIP_LEAKAGE_CHECK") == "1":
        logger.info("leakage check skipped (GAME_SKIP_LEAKAGE_CHECK=1)")
        return []

    last         = CV_FOLDS[-1]
    df_tr        = df[df["year"] < last].copy()
    df_te        = df[df["year"] == last].copy()
    if len(df_te) < 10:
        return []

    best_iter    = oos["best_iteration"]
    baseline_auc = oos["auc_oos"]
    y_tr = df_tr[TARGET].astype(float)
    y_te = df_te[TARGET].astype(float)
    suspicious = []

    logger.info("leakage check | baseline AUC=%.4f | threshold=%.0f%%",
                baseline_auc, threshold * 100)
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
        b      = xgb.train(XGB_PARAMS, dtrain_z, num_boost_round=best_iter, verbose_eval=False)
        auc_z  = _auc(y_te, b.predict(dtest_z))
        # For AUC: leakage -> baseline > zeroed (we'd expect AUC to FALL, not rise)
        # Flag if zeroing the feature IMPROVES AUC by more than threshold (suspiciously high)
        if (auc_z - baseline_auc) / max(baseline_auc, 1e-9) > threshold:
            suspicious.append(feat)
            logger.warning("  LEAKAGE SUSPECT: %r | AUC %.4f -> zeroed %.4f", feat, baseline_auc, auc_z)

    if not suspicious:
        logger.info("  leakage check passed")
    return suspicious


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    # Load Optuna-tuned params if available
    try:
        from training.tune_hyperparams import load_tuned_params
        tuned = load_tuned_params(SYSTEM)
        if tuned:
            global XGB_PARAMS
            XGB_PARAMS = tuned["xgb_params"]
            logger.info("Using Optuna-tuned params (score=%s)", tuned["best_score"])
    except Exception as e:
        logger.warning("Could not load tuned params: %s", e)

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in GAME_FEATURES if f in df.columns]
    missing   = [f for f in GAME_FEATURES if f not in df.columns]
    if missing:
        logger.warning("missing features (skipped): %s", missing)

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

    fmeans = _feature_means(df, available)

    # Feature stds for PSI drift
    fstds: dict = {}
    X_all2 = df[available].apply(pd.to_numeric, errors="coerce")
    for f in available:
        v = X_all2[f].std(skipna=True)
        if not pd.isna(v):
            fstds[f] = round(float(v), 6)

    # Empirical percentile distributions for PSI
    fpdists: dict = {}
    for _f in available:
        try:
            _col = pd.to_numeric(df[_f], errors="coerce").dropna()
            if len(_col) < 10:
                continue
            fpdists[_f] = {
                "p5":  round(float(np.percentile(_col,  5)), 6),
                "p10": round(float(np.percentile(_col, 10)), 6),
                "p25": round(float(np.percentile(_col, 25)), 6),
                "p50": round(float(np.percentile(_col, 50)), 6),
                "p75": round(float(np.percentile(_col, 75)), 6),
                "p90": round(float(np.percentile(_col, 90)), 6),
                "p95": round(float(np.percentile(_col, 95)), 6),
            }
        except Exception:
            continue

    # Bootstrap CI on CV mean AUC
    cv_ci_lo = cv_ci_hi = None
    if wf and len(wf) >= 2:
        import scipy.stats as _st
        aucs = [f["auc"] for f in wf]
        cv_ci_lo, cv_ci_hi = _st.t.interval(
            0.95, len(aucs) - 1,
            loc=float(np.mean(aucs)),
            scale=float(_st.sem(aucs)),
        )
        cv_ci_lo = round(float(cv_ci_lo), 4)
        cv_ci_hi = round(float(cv_ci_hi), 4)

    wf_summary = {}
    if wf:
        wf_df = pd.DataFrame(wf)
        wf_summary = {
            "wf_auc":      round(float(wf_df["auc"].mean()), 4),
            "wf_brier":    round(float(wf_df["brier"].mean()), 4),
            "wf_logloss":  round(float(wf_df["logloss"].mean()), 4),
            "wf_cal":      round(float(wf_df["cal_gap"].mean()), 4),
            "wf_auc_std":  round(float(wf_df["auc"].std()), 4),
            "wf_folds":    wf,
        }

    meta = {
        "version":        VERSION,
        "model_type":     "game_binary_logistic",
        "trained_at":     datetime.now(timezone.utc).isoformat(),
        "full_retrain":   True,
        "features":       available,
        "feature_dists":  fpdists,
        "feature_means":  fmeans,
        "feature_stds":   fstds,
        "cv_folds":       CV_FOLDS,
        "cv_auc_ci_lo":   cv_ci_lo,
        "cv_auc_ci_hi":   cv_ci_hi,
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
            logger.info("archive booster: %s", booster_archive_key)
        except Exception as e:
            return {"status": "error", "error": f"archive booster write: {e}"}
        try:
            write_bytes(meta_bytes, meta_archive_key)
        except Exception as e:
            return {"status": "error", "error": f"archive meta write: {e}"}
        try:
            upload_model(booster_tmp, GCS_BOOSTER_LATEST)
            logger.info("latest booster: %s", GCS_BOOSTER_LATEST)
        except Exception as e:
            return {"status": "error", "error": f"latest booster write: {e}"}
        try:
            write_bytes(meta_bytes, GCS_META_LATEST)
            logger.info("latest meta: %s", GCS_META_LATEST)
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
        "auc_oos":          oos["auc_oos"],
        "brier_oos":        oos["brier_oos"],
        "logloss_oos":      oos["logloss_oos"],
        "best_iteration":   oos["best_iteration"],
        "wf_auc":           wf_summary.get("wf_auc"),
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
