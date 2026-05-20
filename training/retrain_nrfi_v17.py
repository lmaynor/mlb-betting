"""
training/retrain_nrfi_v17.py — NRFI Pro v17 full retrain (Cloud Run Job).

Mirrors NRFI_Pro_Complete_v17.ipynb Sections 8 + 8b:
  1. Load model_features.csv from GCS.
  2. Section 8: time-based 80/20 split, train with early stopping,
     capture OOS metrics (auc_oos, brier_oos, logloss_oos, best_iteration).
  3. Section 8b: retrain on 100% of data using best_iteration rounds.
  4. Compute feature_means from full training set (for runner NaN-fill).
  5. Write archive + latest pointer to GCS for both booster and meta.

Output GCS keys (matches NRFI_Pro_System/config_nrfi.py):
  - NRFI_Pro_System/models/xgb_halfinn_v17.json
  - NRFI_Pro_System/models/model_meta_v17.json
  - NRFI_Pro_System/models/archive/xgb_halfinn_v17.{ts}.json
  - NRFI_Pro_System/models/archive/model_meta_v17.{ts}.json

Notebook contract (HALFINN_FEATURES + XGB_PARAMS) is duplicated below so this
script is self-contained. If notebook cell `v17_02296` changes, mirror the
change here AND flag it in the next handoff. No automated drift check.

Entrypoint: `python -m training.retrain_nrfi_v17` (the Cloud Run Job command).
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
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Notebook contract (cell v17_02296). Keep in sync with NRFI v17 notebook ──

VERSION = "v17"

# NOTE: lineup_pct_L was removed from HALFINN_FEATURES on 2026-05-12 after
# diagnosing AUC inflation in the deployed retrain Job. With lineup_pct_L
# in the feature set, test AUC was 0.7763 (train 0.8069). With it removed,
# AUC drops to 0.5765 (train 0.7011), matching the notebook's documented
# v16/v17 OOS performance (~0.59). lineup_pct_L has only 50 unique values
# (fractions of LHB count in top 3 batters) and corr(lineup_pct_L, yrfi)
# = -0.0294 — pairwise signal is noise-level. The 0.20 AUC contribution
# came from non-linear interactions that, combined with weather/umpire/
# park features, fingerprint individual games. platoon_edge (a derived
# feature: woba_split_STD * (lineup_pct_L - 0.40)) was diagnosed
# separately — it adds ~0.04 AUC of independent signal (15k unique values,
# corr -0.10 with lineup_pct_L) so it stays. The other handedness
# features (woba_vs_L_STD, woba_vs_R_STD, woba_split_STD) contribute
# essentially zero on their own (~0.002 AUC) but stay for notebook parity.
# If reintroducing lineup_pct_L, re-run the leave-one-out diagnostic on
# fresh data first. See session transcript 2026-05-12 for full analysis.
HALFINN_FEATURES = [
    # Pitcher rolling (last 3 starts)
    "zone_pct_L3", "chase_pct_L3", "whiff_pct_L3", "k_pct_L3", "bb_pct_L3",
    "hard_hit_pct_L3", "barrel_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
    "primary_whiff_rate_L3", "called_strike_pct_L3",
    # Pitcher rolling (last 10 starts)
    "zone_pct_L10", "whiff_pct_L10", "k_pct_L10", "xwoba_allowed_L10", "velo_mean_L10",
    # Pitcher trend + context
    "velo_trend_L5", "days_rest", "short_rest", "arm_angle", "pitcher_is_home",
    # top3_batter_* features removed 2026-05-19 (T06): declared in the feature
    # contract but never joined by build_nrfi_features.py (live lineup
    # integration is TODO / T12). Using them caused XGBoost to silently route
    # all-NaN columns through default splits, contributing no signal while
    # occupying contract slots. Re-add when T12 is complete and the live/
    # historical batter rolling join is in place.
    # Weather
    "temperature_f", "wind_speed_mph", "is_outdoor", "wind_out", "wind_in",
    "is_cold", "is_hot", "high_wind",
    # Park
    "park_factor",
    # Umpire
    "ump_overall_accuracy_L30", "ump_total_run_impact_L30", "ump_consistency_L30",
    # Platoon splits
    "woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
    "platoon_edge",  # lineup_pct_L removed 2026-05-12 — see comment below
    # T13: Regime indicator — pitch clock rules took effect 2023-03-30.
    "post_pitch_clock",
]

XGB_PARAMS = {
    "objective":        "binary:logistic",
    "eval_metric":      ["logloss", "auc"],
    "max_depth":        3,
    "learning_rate":    0.03,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_alpha":        1.0,
    "reg_lambda":       3.0,
    "gamma":            0.5,
    "seed":             42,
}

NUM_BOOST_ROUND = 800
EARLY_STOPPING_ROUNDS = 50
TARGET = "yrfi"
TRAIN_TEST_SPLIT = 0.7  # 70% train, 10% val (early stopping), 20% test

# GCS keys — match NRFI_Pro_System/config_nrfi.py exactly
GCS_MODEL_FEATURES   = "NRFI_Pro_System/data/model_features.csv"
GCS_BOOSTER_LATEST   = f"NRFI_Pro_System/models/xgb_halfinn_{VERSION}.json"
GCS_META_LATEST      = f"NRFI_Pro_System/models/model_meta_{VERSION}.json"
GCS_BOOSTER_ARCHIVE  = f"NRFI_Pro_System/models/archive/xgb_halfinn_{VERSION}.{{ts}}.json"
GCS_META_ARCHIVE     = f"NRFI_Pro_System/models/archive/model_meta_{VERSION}.{{ts}}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found in GCS — run /build-features for NRFI first"
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
    logger.info(f"loaded features: {len(df):,} rows | "
                f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
                f"YRFI rate {df[TARGET].mean():.3f}")
    return df, None


def _oos_eval(df: pd.DataFrame, X: pd.DataFrame, y: pd.Series,
              features: list) -> dict:
    """Section 8: 80/20 time-split + early stopping. Returns OOS metrics dict."""
    # C03: 70/10/20 split -- val for early stopping, test never seen during training.
    test_idx  = int(len(X) * TRAIN_TEST_SPLIT)        # 80% boundary
    val_idx   = int(test_idx * (7/8))                 # 70% boundary (7/8 of 80% = 70%)
    X_tr, X_val, X_te = X.iloc[:val_idx], X.iloc[val_idx:test_idx], X.iloc[test_idx:]
    y_tr, y_val, y_te = y.iloc[:val_idx], y.iloc[val_idx:test_idx], y.iloc[test_idx:]
    dates = df["game_date"]
    train_through = dates.iloc[val_idx - 1].strftime("%Y-%m-%d")
    val_through   = dates.iloc[test_idx - 1].strftime("%Y-%m-%d")
    test_from     = dates.iloc[test_idx].strftime("%Y-%m-%d")

    logger.info(f"OOS split (70/10/20) | train={len(X_tr)} (thru {train_through}) | "
                f"val={len(X_val)} (thru {val_through}) | "
                f"test={len(X_te)} (from {test_from}) | features={len(features)}")
    logger.info(f"YRFI rate | train={y_tr.mean():.3f} | val={y_val.mean():.3f} | test={y_te.mean():.3f}")

    dtrain = xgb.DMatrix(X_tr,  label=y_tr,  feature_names=features)
    dval   = xgb.DMatrix(X_val, label=y_val, feature_names=features)
    dtest  = xgb.DMatrix(X_te,  label=y_te,  feature_names=features)

    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )

    preds = booster.predict(dtest)
    auc   = float(roc_auc_score(y_te, preds))
    brier = float(brier_score_loss(y_te, preds))
    ll    = float(log_loss(y_te, preds))

    # XGBoost's best_iteration is 0-indexed; the runner uses it as a count via
    # iteration_range=(0, ntree). Convert to count by adding 1, matching the
    # notebook's `best_iter = int(...best_iteration) + 1` convention.
    best_iter_count = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1

    logger.info(f"OOS results | AUC={auc:.4f} Brier={brier:.4f} "
                f"LogLoss={ll:.4f} best_iter={best_iter_count}")
    logger.info(f"OOS calibration | mean_pred={preds.mean():.4f} "
                f"mean_actual={y_te.mean():.4f} gap={preds.mean() - y_te.mean():+.4f}")

    return {
        "train_through":  train_through,
        "test_from":      test_from,
        "train_rows":     int(len(X_tr)),
        "test_rows":      int(len(X_te)),
        "auc_oos":        round(auc, 4),
        "brier_oos":      round(brier, 4),
        "logloss_oos":    round(ll, 4),
        "mean_pred":      round(float(preds.mean()), 4),
        "mean_actual":    round(float(y_te.mean()), 4),
        "best_iteration": best_iter_count,
    }


def _full_retrain(X: pd.DataFrame, y: pd.Series, features: list,
                  best_iter: int) -> xgb.Booster:
    """Section 8b: train on 100% of data using best_iteration rounds."""
    logger.info(f"full retrain | rows={len(X)} features={len(features)} rounds={best_iter}")
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    # Notebook uses single eval_metric in full retrain (avoids extra logging)
    params = {**XGB_PARAMS, "eval_metric": "logloss"}
    booster = xgb.train(
        params, dtrain,
        num_boost_round=best_iter,
        verbose_eval=False,
    )
    return booster


def _feature_means(X: pd.DataFrame, features: list) -> dict:
    """Per-column training-set means. NaN-only columns are skipped (logged)."""
    means, skipped = {}, []
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
                   threshold: float = 0.01) -> list[str]:
    """Warn if zeroing any single feature improves OOS AUC by > threshold.

    Ported from retrain_k_v1._leakage_check (T09, 2026-05-19).
    For binary:logistic we use AUC delta instead of MAE delta.
    A feature whose removal improves AUC by > 0.01 may carry target
    information or be a proxy for the outcome variable.

    Warning only — does not abort the retrain.
    Skip with env var NRFI_SKIP_LEAKAGE_CHECK=1 for fast reruns.

    Only checks features with > 50% non-NaN coverage to avoid false
    positives from sparse columns (e.g. umpire features early in season).
    """
    import os
    if os.getenv("NRFI_SKIP_LEAKAGE_CHECK") == "1":
        logger.info("leakage check skipped (NRFI_SKIP_LEAKAGE_CHECK=1)")
        return []

    from sklearn.metrics import roc_auc_score as _auc

    # Use the same OOS split as the main eval
    split_idx    = int(len(df) * TRAIN_TEST_SPLIT)
    df_tr        = df.iloc[:split_idx].copy()
    df_te        = df.iloc[split_idx:].copy()
    if len(df_te) < 50:
        logger.info("leakage check: OOS split too small — skipping")
        return []

    best_iter    = oos["best_iteration"]
    baseline_auc = oos["auc_oos"]
    y_tr = df_tr[TARGET].astype(int)
    y_te = df_te[TARGET].astype(int)
    suspicious = []

    logger.info(f"leakage check | baseline AUC={baseline_auc:.4f} | "
                f"threshold={threshold:.3f} | checking {len(features)} features")

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

        b     = xgb.train(XGB_PARAMS, dtrain_z,
                          num_boost_round=best_iter, verbose_eval=False)
        preds = b.predict(dtest_z)
        try:
            auc_z = float(_auc(y_te, preds))
        except Exception:
            continue
        improvement = auc_z - baseline_auc  # positive = zeroing improved AUC = suspect

        if improvement > threshold:
            suspicious.append(feat)
            logger.warning(
                f"  LEAKAGE SUSPECT: {feat!r} | "
                f"AUC baseline={baseline_auc:.4f} zeroed={auc_z:.4f} "
                f"(improvement={improvement:+.4f} > {threshold:.3f})"
            )

    if not suspicious:
        logger.info("  leakage check passed — no suspicious features")
    else:
        logger.warning(
            f"  leakage check: {len(suspicious)} suspicious feature(s): {suspicious}"
        )
    return suspicious


def run() -> dict:
    """Orchestrate the retrain. Returns structured result for the Cloud Run Job."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "MLB_GCS_BUCKET not set — this job requires GCS mode"}

    # 1. Load features
    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    available = [f for f in HALFINN_FEATURES if f in df.columns]
    missing = [f for f in HALFINN_FEATURES if f not in df.columns]
    if missing:
        logger.warning(f"missing features (will be skipped): {missing}")

    # T06: Hard contract check — features in HALFINN_FEATURES must be
    # producible by the feature builder. If any are missing it means the
    # live and training pipelines are misaligned. Fail loudly.
    REQUIRED = [
        f for f in HALFINN_FEATURES
        if not f.startswith("top3_batter_")  # top3 are excluded until T12
    ]
    contract_missing = [f for f in REQUIRED if f not in df.columns]
    if contract_missing:
        return {
            "status": "error",
            "error": (
                f"Feature contract violation: {len(contract_missing)} required features "
                f"not in model_features.csv: {contract_missing}. "
                f"Run /build-features for NRFI first, or update HALFINN_FEATURES."
            ),
        }

    X = df[available].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET].astype(int)

    # T10: Walk-forward CV across year folds — measures metric dispersion.
    # Uses the shared XGBModel.walk_forward_cv from mlb_core.models.base.
    # CV folds default to the last 3 available years so early seasons form
    # the growing train window. Skipped if NRFI_SKIP_CV=1.
    import os as _os
    cv_summary: dict = {}
    if _os.getenv("NRFI_SKIP_CV") != "1":
        try:
            from mlb_core.models.base import XGBModel
            cv_model = XGBModel(params=XGB_PARAMS, features=available,
                                meta={"version": VERSION})
            df_cv = df.copy()
            df_cv["game_date"] = pd.to_datetime(df_cv["game_date"])
            years_avail = sorted(df_cv["game_date"].dt.year.unique())
            cv_folds = [int(y) for y in years_avail if y >= years_avail[-1] - 2]
            logger.info(f"Walk-forward CV folds: {cv_folds}")
            raw_cv = cv_model.walk_forward_cv(
                df_cv, folds=cv_folds, date_col="game_date", target_col=TARGET
            )
            aucs   = [f["auc"]   for f in raw_cv["folds"] if "auc"   in f]
            briers = [f["brier"] for f in raw_cv["folds"] if "brier" in f]
            if len(aucs) >= 2:
                import scipy.stats as _st
                ci_lo, ci_hi = _st.t.interval(
                    0.95, len(aucs) - 1,
                    loc=float(np.mean(aucs)),
                    scale=float(_st.sem(aucs)),
                )
            else:
                ci_lo = ci_hi = float(np.mean(aucs)) if aucs else None
            cv_summary = {
                "cv_folds":     raw_cv["folds"],
                "cv_mean_auc":  round(float(np.mean(aucs)),   4) if aucs else None,
                "cv_std_auc":   round(float(np.std(aucs)),    4) if aucs else None,
                "cv_auc_ci_lo": round(float(ci_lo),           4) if ci_lo is not None else None,
                "cv_auc_ci_hi": round(float(ci_hi),           4) if ci_hi is not None else None,
                "cv_mean_brier": round(float(np.mean(briers)), 4) if briers else None,
            }
            logger.info(
                f"CV AUC {cv_summary['cv_mean_auc']} ± {cv_summary['cv_std_auc']} "
                f"95% CI [{cv_summary['cv_auc_ci_lo']}, {cv_summary['cv_auc_ci_hi']}]"
            )
        except Exception as e:
            logger.warning(f"Walk-forward CV failed (non-fatal): {e}")
    else:
        logger.info("Walk-forward CV skipped (NRFI_SKIP_CV=1)")

    # 2. OOS eval (Section 8)
    try:
        oos = _oos_eval(df, X, y, available)
    except Exception as e:
        return {"status": "error", "error": f"OOS eval: {e}"}

    # T09: Leakage check — warn if any feature improves AUC when zeroed.
    leakage_suspects = _leakage_check(df, available, oos)

    # 3. Full retrain (Section 8b)
    try:
        booster = _full_retrain(X, y, available, oos["best_iteration"])
    except Exception as e:
        return {"status": "error", "error": f"full retrain: {e}"}

    # 4. feature_means + feature_stds (T10/T14: stds needed for PSI drift monitor)
    fmeans = _feature_means(X, available)
    fstds: dict = {}
    for f in available:
        v = X[f].std(skipna=True)
        if not pd.isna(v):
            fstds[f] = round(float(v), 6)
    _feat_list = available

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
    _feat_list = available

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

    # 5. Build meta
    meta = {
        "version":       VERSION,
        "model_type":    "halfinn",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "full_retrain":  True,
        "features":      available,
        "feature_dists":  fpdists,
        "feature_dists":  fpdists,
        "feature_means": fmeans,
        "feature_stds":  fstds,
        # OOS metrics from Section 8 — never overwritten by full retrain
        **oos,
        # Walk-forward CV dispersion (T10)
        **cv_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    # 6. Serialize booster to tmp file (XGBoost save_model needs a path)
    ts = _ts()
    booster_archive_key = GCS_BOOSTER_ARCHIVE.format(ts=ts)
    meta_archive_key    = GCS_META_ARCHIVE.format(ts=ts)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        booster_tmp = Path(f.name)
    try:
        booster.save_model(str(booster_tmp))

        # 7. Write archives first (preserve history if latest write fails)
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

        # 8. Then write latest pointers
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
        "status":            "ok",
        "version":           VERSION,
        "features":          len(available),
        "feature_means":     len(fmeans),
        "feature_stds":      len(fstds),
        "train_rows":        oos["train_rows"],
        "test_rows":         oos["test_rows"],
        "auc_oos":           oos["auc_oos"],
        "brier_oos":         oos["brier_oos"],
        "best_iteration":    oos["best_iteration"],
        "cv_mean_auc":       cv_summary.get("cv_mean_auc"),
        "cv_std_auc":        cv_summary.get("cv_std_auc"),
        "cv_auc_ci_lo":      cv_summary.get("cv_auc_ci_lo"),
        "cv_auc_ci_hi":      cv_summary.get("cv_auc_ci_hi"),
        "leakage_suspects":  leakage_suspects,
        "booster_archive":   booster_archive_key,
        "meta_archive":      meta_archive_key,
        "booster_latest":    GCS_BOOSTER_LATEST,
        "meta_latest":       GCS_META_LATEST,
    }


def main():
    """Cloud Run Job entrypoint. Logs result and exits non-zero on failure."""
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
