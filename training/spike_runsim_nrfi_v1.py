"""
training/spike_runsim_nrfi_v1.py -- First-inning run-distribution spike (TRAIN).

VALIDATION SPIKE -- NOT a production model. See
handoffs/scope_first_inning_runsim_2026-06-10.md.

Thesis: NRFI is not a team binary; it is the aggregation of two half-inning
run COUNTS. Model runs-allowed-in-inning-1 per half as a count:poisson
regressor (the same paradigm that works for K / OUTS / BATTER_HITS), fit an
over-dispersion nb_alpha, then compose to a game-level NRFI probability via the
NegBin P(0) of each half. This script trains and saves the count model only;
spike_runsim_nrfi_eval.py evaluates it against the v18 binary baseline.

Isolation (non-negotiable, scope s3):
  - Reads the production model_features.csv READ-ONLY.
  - Re-derives the integer target `runs_against_i1` from scoring_master here
    (the production builder binarizes + drops it) -- production builder UNTOUCHED.
  - Writes ALL artifacts under NRFI_Pro_System/experimental/runsim_v1/.
  - No "latest" pointer updates, no archive rotation of production files.

Run:
  PYTHONPATH=. python3 -m training.spike_runsim_nrfi_v1
  (needs GCS access -- run in Cloud Shell or as a Cloud Run Job)
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import xgboost as xgb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Paths (production read-only inputs; experimental outputs) ----------------
GCS_MODEL_FEATURES = "NRFI_Pro_System/data/model_features.csv"
GCS_SCORING_MASTER = "Scoring/scoring_master.csv"
EXP_PREFIX         = "NRFI_Pro_System/experimental/runsim_v1"
EXP_TRAIN_FRAME    = f"{EXP_PREFIX}/train_frame.csv"
EXP_BOOSTER        = f"{EXP_PREFIX}/xgb_runsim_nrfi_v1.json"
EXP_META           = f"{EXP_PREFIX}/model_meta_runsim_nrfi_v1.json"

TARGET = "runs_against_i1"

# --- v18 feature set (reused verbatim -- scope: do NOT invent new features) ---
PITCHER_FEATURES = [
    "zone_pct_L3", "chase_pct_L3", "whiff_pct_L3", "k_pct_L3", "bb_pct_L3",
    "hard_hit_pct_L3", "barrel_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
    "primary_whiff_rate_L3", "called_strike_pct_L3",
    "zone_pct_L10", "whiff_pct_L10", "k_pct_L10", "xwoba_allowed_L10", "velo_mean_L10",
    "velo_trend_L5", "days_rest", "short_rest", "arm_angle",
    "pitches_per_pa_L5", "first_pitch_strike_pct_L5",
    "avg_max_inning_L5", "opener_flag",
    "i1_yrfi_rate_L5", "i1_yrfi_rate_L10",
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
ALL_FEATURES = PITCHER_FEATURES + LINEUP_FEATURES + CONTEXT_FEATURES

# count:poisson params -- mirror retrain_k_v1.py (proven count config)
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
NUM_BOOST_ROUND       = 2000
EARLY_STOPPING_ROUNDS = 50
TRAIN_TEST_SPLIT      = 0.7   # v18 split -- last 30% = test (apples-to-apples)


def _add_integer_target(df: pd.DataFrame) -> pd.DataFrame:
    """Re-derive integer 1st-inning runs-against from scoring_master.

    Replicates the join in build_nrfi_features.py (lines ~209-221) but KEEPS
    the integer `runs_against_i1` instead of binarizing to `yrfi`.
    pitcher_is_home == 1 -> faces AWAY batters (top half); else home (bot).
    """
    from mlb_core.storage import read_csv, exists
    if not exists(GCS_SCORING_MASTER):
        raise RuntimeError(f"{GCS_SCORING_MASTER} not found -- cannot derive target")
    scoring = read_csv(GCS_SCORING_MASTER, low_memory=False)
    scoring_1st = scoring[scoring["inning"] == 1][["game_pk", "half", "runs"]].copy()
    scoring_1st = scoring_1st.drop_duplicates(["game_pk", "half"], keep="first")

    df = df.copy()
    if "pitcher_is_home" not in df.columns:
        raise RuntimeError("model_features.csv missing pitcher_is_home -- cannot map half")
    df["_half_key"] = np.where(df["pitcher_is_home"] == 1, "top", "bot")
    df = df.merge(
        scoring_1st.rename(columns={"half": "_half_key", "runs": TARGET}),
        on=["game_pk", "_half_key"], how="left",
    )
    matched = df[TARGET].notna().sum()
    logger.info("integer target: matched %s/%s rows | mean runs=%.3f",
                f"{matched:,}", f"{len(df):,}", float(df[TARGET].mean(skipna=True)))
    df = df.drop(columns=["_half_key"])
    return df


def run() -> dict:
    from mlb_core.storage import read_csv, write_csv, write_bytes, exists

    if not exists(GCS_MODEL_FEATURES):
        return {"status": "error", "error": f"{GCS_MODEL_FEATURES} not found"}
    df = read_csv(GCS_MODEL_FEATURES, low_memory=False)
    if df.empty:
        return {"status": "error", "error": "model_features.csv empty"}
    logger.info("loaded %s feature rows", f"{len(df):,}")

    # Integer count target (additive; production builder untouched).
    df = _add_integer_target(df)
    before = len(df)
    df = df[df[TARGET].notna()].copy()
    df[TARGET] = df[TARGET].astype(float)
    logger.info("dropped %s rows with no scoring match (%s -> %s)",
                f"{before - len(df):,}", f"{before:,}", f"{len(df):,}")

    # Deterministic temporal sort + v18 split (so eval reproduces test slice).
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["game_date", "game_pk", "pitcher_is_home"]).reset_index(drop=True)
    write_csv(df, EXP_TRAIN_FRAME)
    logger.info("wrote augmented train frame -> %s", EXP_TRAIN_FRAME)

    n        = len(df)
    test_idx = int(n * TRAIN_TEST_SPLIT)
    val_idx  = int(test_idx * (7 / 8))
    logger.info("split | n=%d train=[:%d] val=[%d:%d] test=[%d:]",
                n, val_idx, val_idx, test_idx, test_idx)

    features = [f for f in ALL_FEATURES if f in df.columns]
    missing  = [f for f in ALL_FEATURES if f not in df.columns]
    if missing:
        logger.warning("%d features missing from frame: %s", len(missing), missing[:8])

    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[TARGET]

    X_tr,  y_tr  = X.iloc[:val_idx],        y.iloc[:val_idx]
    X_val, y_val = X.iloc[val_idx:test_idx], y.iloc[val_idx:test_idx]

    dtrain = xgb.DMatrix(X_tr,  label=y_tr,  feature_names=features)
    dval   = xgb.DMatrix(X_val, label=y_val, feature_names=features)
    booster = xgb.train(
        XGB_PARAMS, dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=False,
    )
    best_iter = int(getattr(booster, "best_iteration", 0) or 0) + 1
    logger.info("trained | best_iteration=%d", best_iter)

    # nb_alpha on train+val residuals ONLY (test stays pristine -- scope s3).
    # NB(mu, alpha): var = mu + alpha*mu^2 -> alpha = (var - mu)/mu^2, clamp.
    X_fit = X.iloc[:test_idx]
    y_fit = y.iloc[:test_idx].values
    dm_fit = xgb.DMatrix(X_fit, feature_names=features)
    preds_fit = booster.predict(dm_fit, iteration_range=(0, best_iter))
    resid_var = float(np.var(y_fit - preds_fit))
    mu_mean   = float(np.mean(preds_fit))
    nb_alpha  = float(np.clip((resid_var - mu_mean) / max(mu_mean ** 2, 1e-6), 0.01, 0.50))
    logger.info("nb_alpha | mu=%.3f resid_var=%.3f alpha=%.4f", mu_mean, resid_var, nb_alpha)

    # Save booster + meta to experimental prefix (no production pointers).
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, "booster.json")
        booster.save_model(local)
        with open(local, "rb") as fh:
            write_bytes(fh.read(), EXP_BOOSTER)

    meta = {
        "version":        "runsim_v1",
        "kind":           "halfinn_count_poisson",
        "target":         TARGET,
        "features":       features,
        "nb_alpha":       nb_alpha,
        "best_iteration": best_iter,
        "xgb_params":     XGB_PARAMS,
        "split":          {"n": n, "val_idx": val_idx, "test_idx": test_idx,
                            "train_test_split": TRAIN_TEST_SPLIT},
        "train_frame":    EXP_TRAIN_FRAME,
        "note":           "VALIDATION SPIKE -- not production. nb_alpha fit on "
                          "train+val only; test slice untouched.",
    }
    write_bytes(json.dumps(meta, indent=2).encode(), EXP_META)
    logger.info("wrote booster -> %s | meta -> %s", EXP_BOOSTER, EXP_META)

    return {"status": "ok", "n": n, "features": len(features),
            "nb_alpha": nb_alpha, "best_iteration": best_iter}


def main():
    import sys
    result = run()
    logger.info("RESULT: %s", json.dumps(result))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
