"""
training/retrain_nrfi_v18.py -- NRFI Pro v18 ensemble retrain (Cloud Run Job).

E05 + E08:
  E05: adds opener_flag, avg_max_inning_L5, pitches_per_pa_L5,
       first_pitch_strike_pct_L5 to address 2026 AUC drift
       (walk-forward CV shows 2024=0.5985, 2025=0.5876, 2026=0.5394)
  E08: sub-model ensemble -- pitcher mechanics / lineup-platoon / park-context
       sub-models each trained independently, then stacked via a logistic
       regression meta-learner trained on val-set OOS predictions

Architecture:
  Sub-model A (pitcher): pitcher rolling metrics + E05 features
  Sub-model B (lineup):  platoon splits + home/away
  Sub-model C (context): weather + park + umpire
  Stacker: LogisticRegression(coef, intercept) serialized in model_meta_v18.json

Training procedure (no test leakage):
  1. Same time split as v17: last 30% held out as test; the first 70% is
     further split 7/8 train (61.25% of total) : 1/8 val (8.75% of total).
     (Fixed 2026-09-04: this and the log line in run() below used to say
     "70/10/20", which doesn't match the actual test_idx/val_idx math --
     see the accurate breakdown in the split comment inside run().)
  2. Train A, B, C on the 61.25% train slice with val for early stopping.
  3. Get A, B, C predictions on the 10% val set (OOS for sub-models).
  4. Fit logistic stacker on those val predictions.
  5. Evaluate stacked model on untouched 20% test set.
  6. Full retrain A, B, C on 100% of data using best_iteration counts.
     Stacker coef unchanged (fitted on val-based OOS predictions).

GCS artifacts:
  NRFI_Pro_System/models/xgb_pitcher_v18.json
  NRFI_Pro_System/models/xgb_lineup_v18.json
  NRFI_Pro_System/models/xgb_context_v18.json
  NRFI_Pro_System/models/model_meta_v18.json

Run:
  python -m training.retrain_nrfi_v18
  gcloud run jobs execute mlb-retrain-nrfi-v18 --region=us-central1
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
)
logger = logging.getLogger(__name__)


# ── Sub-model feature groups ──────────────────────────────────────────────────

VERSION = "v18"
TARGET  = "yrfi"
TRAIN_TEST_SPLIT   = 0.7   # last 30% = test; same as v17
NUM_BOOST_ROUND    = 800
EARLY_STOPPING_ROUNDS = 50

# E05 new features: pitches_per_pa_L5, first_pitch_strike_pct_L5,
#   avg_max_inning_L5, opener_flag -- all built by build_nrfi_features.py.

PITCHER_FEATURES = [
    # L3 rolling pitch mechanics
    "zone_pct_L3", "chase_pct_L3", "whiff_pct_L3", "k_pct_L3", "bb_pct_L3",
    "hard_hit_pct_L3", "barrel_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
    "primary_whiff_rate_L3", "called_strike_pct_L3",
    # L10 rolling pitch mechanics
    "zone_pct_L10", "whiff_pct_L10", "k_pct_L10", "xwoba_allowed_L10", "velo_mean_L10",
    # Pitcher context
    "velo_trend_L5", "days_rest", "short_rest", "arm_angle",
    # E05: efficiency and opener detection
    "pitches_per_pa_L5", "first_pitch_strike_pct_L5",
    "avg_max_inning_L5", "opener_flag",
    # Direct 1i outcome history (added 2026-05-28): fraction of recent starts where
    # pitcher gave up a 1st-inning run -- most direct signal for P(NRFI/YRFI)
    "i1_yrfi_rate_L5", "i1_yrfi_rate_L10",
    # Regime
    "post_pitch_clock",
]

LINEUP_FEATURES = [
    "woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD", "platoon_edge",
    "pitcher_is_home",
]

CONTEXT_FEATURES = [
    "temperature_f", "wind_speed_mph", "is_outdoor", "wind_out", "wind_in",
    "is_cold", "is_hot", "high_wind",
    "park_factor",
    "ump_overall_accuracy_L30", "ump_total_run_impact_L30", "ump_consistency_L30",
]

SUB_MODEL_ORDER = ["pitcher", "lineup", "context"]
FEATURE_GROUPS  = {
    "pitcher": PITCHER_FEATURES,
    "lineup":  LINEUP_FEATURES,
    "context": CONTEXT_FEATURES,
}

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

GCS_MODEL_FEATURES  = "NRFI_Pro_System/data/model_features.csv"
GCS_PITCHER_LATEST  = "NRFI_Pro_System/models/xgb_pitcher_v18.json"
GCS_LINEUP_LATEST   = "NRFI_Pro_System/models/xgb_lineup_v18.json"
GCS_CONTEXT_LATEST  = "NRFI_Pro_System/models/xgb_context_v18.json"
GCS_META_LATEST     = "NRFI_Pro_System/models/model_meta_v18.json"
GCS_BOOSTER_KEYS    = {
    "pitcher": GCS_PITCHER_LATEST,
    "lineup":  GCS_LINEUP_LATEST,
    "context": GCS_CONTEXT_LATEST,
}
GCS_ARCHIVE_TMPL    = "NRFI_Pro_System/models/archive/{name}_v18.{ts}.json"
GCS_META_ARCHIVE    = "NRFI_Pro_System/models/archive/model_meta_v18.{ts}.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_features():
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_MODEL_FEATURES):
        return None, f"{GCS_MODEL_FEATURES} not found"
    try:
        df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    except Exception as e:
        return None, f"features load: {e}"
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


def _resolve_available(df: pd.DataFrame, features: list[str]) -> list[str]:
    """Return features that exist in df, warn on missing."""
    avail   = [f for f in features if f in df.columns]
    missing = [f for f in features if f not in df.columns]
    if missing:
        logger.warning(f"  missing {len(missing)} features: {missing[:5]}")
    return avail


def _train_submodel(
    X_tr: pd.DataFrame, y_tr: pd.Series,
    X_val: pd.DataFrame, y_val: pd.Series,
    features: list[str],
    label: str,
) -> tuple[xgb.Booster, int]:
    """Train one sub-model with early stopping. Returns (booster, best_iter_count)."""
    dtrain = xgb.DMatrix(X_tr,  label=y_tr,  feature_names=features)
    dval   = xgb.DMatrix(X_val, label=y_val, feature_names=features)
    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=100,
    )
    best_iter = int(getattr(booster, "best_iteration", NUM_BOOST_ROUND - 1)) + 1
    preds_val = booster.predict(dval)
    auc_val   = float(roc_auc_score(y_val, preds_val))
    logger.info(f"  [{label}] best_iter={best_iter} | val AUC={auc_val:.4f}")
    return booster, best_iter


def _predict(booster: xgb.Booster, X: pd.DataFrame, features: list[str],
             feature_means: dict, best_iter: int) -> np.ndarray:
    X_in = X.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    for col in features:
        m = feature_means.get(col)
        if m is not None:
            X_in[col] = X_in[col].fillna(float(m))
    dm = xgb.DMatrix(X_in.astype(float), feature_names=features)
    if best_iter:
        return booster.predict(dm, iteration_range=(0, best_iter))
    return booster.predict(dm)


def _leakage_check(df: pd.DataFrame, features: list, oos: dict,
                   threshold: float = 0.01) -> list[str]:
    """Warn if zeroing any single feature improves OOS AUC by > threshold.

    Ported from retrain_nrfi_v17._leakage_check (originally retrain_k_v1,
    T09 2026-05-19). Added to v18 2026-09-04 -- the v17->v18 ensemble rewrite
    dropped this guard entirely (every sibling retrain script -- K, SB, GAME,
    BATTER_HITS, and v17 itself -- has one; v18, the current production
    model, did not). Run once per sub-model (pitcher/lineup/context), each
    with its own features/booster/OOS split.

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

    # Use the same 70/30 OOS split as the main eval (test_idx, not val_idx --
    # matches v17's split_idx = n*TRAIN_TEST_SPLIT exactly).
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


def _apply_stacker(sub_preds: list[np.ndarray], coef: list[float],
                   intercept: float) -> np.ndarray:
    meta_X = np.column_stack(sub_preds)
    logit   = meta_X @ np.array(coef) + intercept
    return 1.0 / (1.0 + np.exp(-logit))


def run() -> dict:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import write_bytes, upload_model

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "MLB_GCS_BUCKET not set -- this job requires GCS mode"}

    # 1. Load features
    df, err = _load_features()
    if err:
        return {"status": "error", "error": err}

    y = df[TARGET].astype(int)

    # 2. Time split: 70% train (61.25% sub-train + 8.75% val) | 30% test
    #    Mirrors v17 exactly: test_idx = n*0.7, val_idx = test_idx * 7/8
    n         = len(df)
    test_idx  = int(n * TRAIN_TEST_SPLIT)
    val_idx   = int(test_idx * (7 / 8))

    train_through = df["game_date"].iloc[val_idx - 1].strftime("%Y-%m-%d")
    val_through   = df["game_date"].iloc[test_idx - 1].strftime("%Y-%m-%d")
    test_from     = df["game_date"].iloc[test_idx].strftime("%Y-%m-%d")

    logger.info(
        f"split (61.25/8.75/30) | train={val_idx} (thru {train_through}) | "
        f"val={test_idx - val_idx} (thru {val_through}) | "
        f"test={n - test_idx} (from {test_from})"
    )

    # 3. Train sub-models and collect OOS predictions
    sub_info:      dict[str, dict] = {}  # feature/iter/means info per sub-model
    val_preds:     dict[str, np.ndarray] = {}
    test_preds:    dict[str, np.ndarray] = {}

    for name in SUB_MODEL_ORDER:
        logger.info(f"=== Sub-model: {name} ===")
        features = _resolve_available(df, FEATURE_GROUPS[name])
        if not features:
            return {"status": "error",
                    "error": f"Sub-model '{name}' has no available features -- "
                             f"run /build-features NRFI first"}

        X = df[features].apply(pd.to_numeric, errors="coerce")
        X_tr  = X.iloc[:val_idx]
        y_tr  = y.iloc[:val_idx]
        X_val = X.iloc[val_idx:test_idx]
        y_val = y.iloc[val_idx:test_idx]
        X_te  = X.iloc[test_idx:]
        y_te  = y.iloc[test_idx:]

        booster, best_iter = _train_submodel(X_tr, y_tr, X_val, y_val, features, name)

        fmeans = {f: float(X[f].mean(skipna=True))
                  for f in features if not pd.isna(X[f].mean())}
        fstds  = {f: float(X[f].std(skipna=True))
                  for f in features if not pd.isna(X[f].std())}

        # C3.4 / C04: empirical percentiles for the PSI drift monitor.
        # This ensemble's sub-models computed feature_means/feature_stds
        # but never feature_dists at all, unlike every other system's
        # retrain script (see retrain_nrfi_v17.py / retrain_k_v1.py etc.) --
        # a real gap given this file's own docstring documents live AUC
        # drift (2024=0.5985, 2025=0.5876, 2026=0.5394) as the reason this
        # ensemble architecture exists. Percentiles avoid the Gaussian
        # misfit mean/std alone would have for binary/bounded/bimodal
        # features. Keyed per-sub-model (not a flat top-level dict) since
        # sub_info[name] -- not a module-level dict -- is what actually
        # lands in meta["sub_models"] below.
        fdists: dict = {}
        for _f in features:
            try:
                _col = X[_f].dropna()
                if len(_col) < 10:
                    continue
                fdists[_f] = {
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

        sub_info[name] = {
            "features":      features,
            "best_iteration": best_iter,
            "feature_means": fmeans,
            "feature_stds":  fstds,
            "feature_dists": fdists,
        }

        val_preds[name]  = _predict(booster, X_val, features, fmeans, best_iter)
        test_preds[name] = _predict(booster, X_te,  features, fmeans, best_iter)

        auc_te = float(roc_auc_score(y_te, test_preds[name]))
        logger.info(f"  [{name}] test AUC={auc_te:.4f}")

        leakage_suspects = _leakage_check(
            df, features, {"best_iteration": best_iter, "auc_oos": auc_te}
        )
        sub_info[name]["leakage_suspects"] = leakage_suspects

    # 4. Fit logistic stacker on val-set OOS predictions (no test leakage)
    meta_X_val  = np.column_stack([val_preds[n] for n in SUB_MODEL_ORDER])
    y_val_arr   = y.iloc[val_idx:test_idx].values

    stacker = LogisticRegression(C=1.0, solver="liblinear", max_iter=1000, random_state=42)
    stacker.fit(meta_X_val, y_val_arr)

    stacker_coef      = stacker.coef_[0].tolist()
    stacker_intercept = float(stacker.intercept_[0])
    logger.info(
        f"stacker coef: {[round(c, 4) for c in stacker_coef]} | "
        f"intercept: {stacker_intercept:.4f}"
    )

    # 5. Evaluate stacked model on test set
    meta_X_test  = np.column_stack([test_preds[n] for n in SUB_MODEL_ORDER])
    y_te_arr     = y.iloc[test_idx:].values

    stacked_test = _apply_stacker(
        [test_preds[n] for n in SUB_MODEL_ORDER],
        stacker_coef, stacker_intercept,
    )
    auc_stacked   = float(roc_auc_score(y_te_arr, stacked_test))
    brier_stacked = float(brier_score_loss(y_te_arr, stacked_test))
    ll_stacked    = float(log_loss(y_te_arr, stacked_test))

    sub_aucs = {
        name: round(float(roc_auc_score(y_te_arr, test_preds[name])), 4)
        for name in SUB_MODEL_ORDER
    }

    logger.info(
        f"Stacked OOS | AUC={auc_stacked:.4f} Brier={brier_stacked:.4f} "
        f"LogLoss={ll_stacked:.4f}"
    )
    logger.info(f"Sub-model test AUCs: {sub_aucs}")

    # 6. Walk-forward CV for dispersion metrics (mirrors v17 T10)
    import os as _os
    cv_summary: dict = {}
    if _os.getenv("NRFI_SKIP_CV") != "1":
        try:
            from mlb_core.models.base import XGBModel
            # Use combined pitcher+lineup+context features as a flat set for CV
            all_features = _resolve_available(
                df, sorted(set(PITCHER_FEATURES + LINEUP_FEATURES + CONTEXT_FEATURES))
            )
            cv_model = XGBModel(params=XGB_PARAMS, features=all_features,
                                meta={"version": VERSION})
            df_cv = df.copy()
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
                "cv_folds":      raw_cv["folds"],
                "cv_mean_auc":   round(float(np.mean(aucs)),    4) if aucs else None,
                "cv_std_auc":    round(float(np.std(aucs)),     4) if aucs else None,
                "cv_auc_ci_lo":  round(float(ci_lo),            4) if ci_lo is not None else None,
                "cv_auc_ci_hi":  round(float(ci_hi),            4) if ci_hi is not None else None,
                "cv_mean_brier": round(float(np.mean(briers)),  4) if briers else None,
            }
            logger.info(
                f"CV AUC {cv_summary['cv_mean_auc']} +/- {cv_summary['cv_std_auc']} "
                f"95% CI [{cv_summary['cv_auc_ci_lo']}, {cv_summary['cv_auc_ci_hi']}]"
            )
        except Exception as e:
            logger.warning(f"Walk-forward CV failed (non-fatal): {e}")
    else:
        logger.info("Walk-forward CV skipped (NRFI_SKIP_CV=1)")

    # 7. Full retrain sub-models on 100% of data
    logger.info("=== Full retrain (100% data) ===")
    full_boosters: dict[str, xgb.Booster] = {}
    for name in SUB_MODEL_ORDER:
        features  = sub_info[name]["features"]
        best_iter = sub_info[name]["best_iteration"]
        X_full    = df[features].apply(pd.to_numeric, errors="coerce")
        for col in features:
            m = sub_info[name]["feature_means"].get(col)
            if m is not None:
                X_full[col] = X_full[col].fillna(float(m))
        dtrain_full = xgb.DMatrix(X_full.astype(float), label=y, feature_names=features)
        params_full = {**XGB_PARAMS, "eval_metric": "logloss"}
        b = xgb.train(params_full, dtrain_full,
                      num_boost_round=best_iter, verbose_eval=False)
        full_boosters[name] = b
        logger.info(f"  [{name}] full retrain done | rows={len(X_full)} rounds={best_iter}")

    # 8. Build meta
    ts = _ts()
    meta = {
        "version":          VERSION,
        "model_type":       "halfinn_ensemble",
        "architecture":     "3_submodels_logistic_stacker",
        "trained_at":       datetime.now(timezone.utc).isoformat(),
        "train_through":    train_through,
        "val_through":      val_through,
        "test_from":        test_from,
        "train_rows":       val_idx,
        "val_rows":         test_idx - val_idx,
        "test_rows":        n - test_idx,
        "auc_oos":          round(auc_stacked, 4),
        "brier_oos":        round(brier_stacked, 4),
        "logloss_oos":      round(ll_stacked, 4),
        "sub_model_aucs":   sub_aucs,
        "stacker": {
            "type":      "logistic_regression",
            "sub_order": SUB_MODEL_ORDER,
            "coef":      stacker_coef,
            "intercept": stacker_intercept,
            "C":         1.0,
        },
        "sub_models": sub_info,
        **cv_summary,
    }
    meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    # 9. Archive + upload
    errors = []
    for name in SUB_MODEL_ORDER:
        booster      = full_boosters[name]
        gcs_latest   = GCS_BOOSTER_KEYS[name]
        gcs_archive  = GCS_ARCHIVE_TMPL.format(name=f"xgb_{name}", ts=ts)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            booster.save_model(str(tmp))
            upload_model(tmp, gcs_archive)
            logger.info(f"  archive [{name}]: {gcs_archive}")
            upload_model(tmp, gcs_latest)
            logger.info(f"  latest  [{name}]: {gcs_latest}")
        except Exception as e:
            errors.append(f"upload {name}: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    meta_archive = GCS_META_ARCHIVE.format(ts=ts)
    try:
        write_bytes(meta_bytes, meta_archive)
        logger.info(f"  archive meta: {meta_archive}")
        write_bytes(meta_bytes, GCS_META_LATEST)
        logger.info(f"  latest  meta: {GCS_META_LATEST}")
    except Exception as e:
        errors.append(f"meta upload: {e}")

    if errors:
        return {"status": "error", "errors": errors}

    return {
        "status":           "ok",
        "version":          VERSION,
        "auc_oos":          round(auc_stacked, 4),
        "brier_oos":        round(brier_stacked, 4),
        "sub_model_aucs":   sub_aucs,
        "train_rows":       val_idx,
        "test_rows":        n - test_idx,
        "cv_mean_auc":      cv_summary.get("cv_mean_auc"),
        "cv_std_auc":       cv_summary.get("cv_std_auc"),
        "stacker_coef":     stacker_coef,
        "gcs_meta":         GCS_META_LATEST,
    }


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
