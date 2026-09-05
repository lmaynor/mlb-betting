"""
mlb.training._retrain_common -- shared CV/eval/persist mechanics for the count
and binary full-retrain scripts (retrain_k_v1.py, retrain_sb_v1.py,
retrain_outs_v1.py, retrain_batter_hits_v1.py, retrain_game_v1.py).

This module holds ONLY the mechanics that were verified byte-for-byte (or
formula-for-formula, via `diff` on extracted function bodies) identical
across those five scripts: the walk-forward CV loop, the OOS train/test
split, the leakage-check zero-and-retrain loop, the NB dispersion fit, the
feature_means/feature_stds/feature_dists percentile block, the CI-bootstrap
+ wf_* summary block, and the archive-then-latest GCS artifact write.

Everything genuinely per-system stays in each retrain_*.py file: the TARGET
column, the feature list, XGB_PARAMS, GCS paths, the metric family (count
MAE/RMSE/R2 vs GAME's binary AUC/Brier/LogLoss -- passed in as an ordered
`metrics` list of (name, fn) pairs, not hardcoded here), and any real
per-system logic (Optuna tuned-param GCS pickup, OUTS's inline isotonic
self-calibration, GAME's binary:logistic objective).

Two real, pre-existing behavioral asymmetries between the "near-identical"
callers are preserved here via explicit parameters rather than silently
unified -- see `min_train`/`min_test`, `filter_train_years`, and
`require_ci_for_summary` below, and the retrain_outs_v1.py caller for the
`include_train_metrics=False` case (OUTS's OOS meta lacks mae_train/
overfit_gap -- flagged prominently in the refactor report, not decided here).

Log lines emitted from here use the CALLER's own logger (passed in
explicitly) so Cloud Run log output still shows e.g. "mlb.training.
retrain_k_v1", not "mlb.training._retrain_common" -- only exact log-message
text/precision was normalized (harmless: never part of a GCS key, meta.json
field, or model artifact).
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb


# ---------------------------------------------------------------------------
# Small standalone helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    """UTC timestamp used for archive GCS keys, e.g. '20260904_213000'."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def cv_folds(df: pd.DataFrame, n: int = 3) -> list[int]:
    """Walk-forward test years: the most recent `n` years actually present in
    the data (finding C3.3), not a hardcoded literal. A fixed year list goes
    silently stale every offseason, quietly excluding the newest season from
    CV, the OOS train/test split, and the leakage check, with no error or
    warning (this bit main on 2026-08-17 when [2023, 2024, 2025] was still
    sitting in five retrain scripts as a literal)."""
    years = sorted(int(y) for y in df["year"].dropna().unique())
    return years[-n:] if len(years) >= n else years


# ---------------------------------------------------------------------------
# Count-target metrics (K / SB / OUTS / BATTER_HITS)
# ---------------------------------------------------------------------------

def mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))


def rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))


def r2(y_true, y_pred) -> float:
    ss_res = float(np.sum((np.array(y_true) - np.array(y_pred)) ** 2))
    ss_tot = float(np.sum((np.array(y_true) - np.mean(y_true)) ** 2))
    return 1.0 - ss_res / max(ss_tot, 1e-9)


# ---------------------------------------------------------------------------
# Walk-forward CV (Section 7 pattern)
# ---------------------------------------------------------------------------

def walk_forward_cv(
    df: pd.DataFrame,
    features: list,
    target: str,
    xgb_params: dict,
    num_boost_round: int,
    early_stopping_rounds: int,
    *,
    metrics: list[tuple[str, "callable"]],
    min_train: int,
    min_test: int,
    cv_folds_fn=cv_folds,
    filter_train_years: bool = False,
    logger=None,
) -> list[dict]:
    """Walk-forward CV: train on the prior 2 years, test on the held-out
    year, for each year `cv_folds_fn(df)` returns. C03: carves a validation
    tail (last 1/8 of the train window) for early stopping; the test fold is
    never seen during training.

    metrics: ordered (name, fn) pairs applied to (y_te, y_pred) -- e.g.
        [("mae", mae), ("rmse", rmse), ("r2", r2)] for the four count
        systems, or [("auc", auc), ("brier", brier), ("logloss", logloss)]
        for GAME. Values land in the per-fold dict under `name`.
    min_train/min_test: fold-size floor below which a fold is skipped.
        K/OUTS use 50/10; SB/BATTER_HITS/GAME use 100/20 -- a real,
        pre-existing difference between these "near-identical" scripts,
        preserved verbatim rather than unified.
    filter_train_years: SB-only quirk -- filters [test_year-2, test_year-1]
        down to years actually present in df before selecting df_tr. Every
        other caller uses the two years unconditionally (matches, in the
        rare case a year is wholly absent, a possibly-smaller df_tr rather
        than an empty-year slice that silently contributes nothing anyway).
    """
    results = []
    for test_year in cv_folds_fn(df):
        train_years = [test_year - 2, test_year - 1]
        if filter_train_years:
            train_years = [y for y in train_years if y in df["year"].unique()]
        df_tr = df[df["year"].isin(train_years)]
        df_te = df[df["year"] == test_year]
        if len(df_tr) < min_train or len(df_te) < min_test:
            if logger:
                logger.info("  fold %s: insufficient data (train=%d, test=%d) -- skip",
                            test_year, len(df_tr), len(df_te))
            continue

        X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
        y_tr = df_tr[target].astype(float)
        X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
        y_te = df_te[target].astype(float)

        nval = int(len(X_tr) * (7 / 8))
        dtrain_s = xgb.DMatrix(X_tr.iloc[:nval], label=y_tr.iloc[:nval], feature_names=features)
        dval_s   = xgb.DMatrix(X_tr.iloc[nval:], label=y_tr.iloc[nval:], feature_names=features)
        dtest    = xgb.DMatrix(X_te, label=y_te, feature_names=features)

        booster = xgb.train(
            xgb_params, dtrain_s,
            num_boost_round=num_boost_round,
            evals=[(dtrain_s, "train"), (dval_s, "val")],
            early_stopping_rounds=early_stopping_rounds,
            verbose_eval=False,
        )
        y_pred = booster.predict(dtest)

        fold = {"test_year": test_year, "n_train": len(df_tr), "n_test": len(df_te)}
        for name, fn in metrics:
            fold[name] = fn(y_te, y_pred)
        fold["cal_gap"] = float(np.mean(y_pred) - np.mean(y_te))
        fold["best_iteration"] = int(getattr(booster, "best_iteration", num_boost_round - 1)) + 1
        results.append(fold)

        if logger:
            metric_str = " ".join(f"{name}={val:.4f}" for name, val in
                                   ((n, fold[n]) for n, _ in metrics))
            logger.info("  fold %s: %s cal=%+.4f best_iter=%d n_train=%d n_test=%d",
                        test_year, metric_str, fold["cal_gap"], fold["best_iteration"],
                        len(df_tr), len(df_te))
    return results


# ---------------------------------------------------------------------------
# OOS eval (Section 7 pattern: train pre-last-fold, test on last-fold)
# ---------------------------------------------------------------------------

def oos_eval(
    df: pd.DataFrame,
    features: list,
    target: str,
    xgb_params: dict,
    num_boost_round: int,
    early_stopping_rounds: int,
    *,
    metrics: list[tuple[str, "callable"]],
    min_test: int,
    cv_folds_fn=cv_folds,
    include_train_metrics: bool,
    logger=None,
) -> dict:
    """OOS split: train on years < last fold, test on the last fold.

    metrics[0] is the "primary" metric: it drives `{primary}_train` /
    `overfit_gap` (only emitted when `include_train_metrics=True` -- OUTS is
    the one caller that sets this False; its model_meta_outs_v1.json has
    always had 2 fewer keys than K/SB/BATTER_HITS/GAME's, a real pre-existing
    difference this refactor deliberately preserves rather than "fixing").
    """
    last = cv_folds_fn(df)[-1]
    df_tr = df[df["year"] < last]
    df_te = df[df["year"] == last]
    if len(df_te) < min_test:
        raise RuntimeError(f"OOS test fold ({last}) has too few rows ({len(df_te)})")

    X_tr = df_tr[features].apply(pd.to_numeric, errors="coerce")
    y_tr = df_tr[target].astype(float)
    X_te = df_te[features].apply(pd.to_numeric, errors="coerce")
    y_te = df_te[target].astype(float)

    if logger:
        logger.info("OOS split | train=%d (years <%s) | test=%d (year=%s) | features=%d",
                    len(df_tr), last, len(df_te), last, len(features))

    nval = int(len(X_tr) * (7 / 8))
    dtrain = xgb.DMatrix(X_tr.iloc[:nval], label=y_tr.iloc[:nval], feature_names=features)
    dval   = xgb.DMatrix(X_tr.iloc[nval:], label=y_tr.iloc[nval:], feature_names=features)
    dtest  = xgb.DMatrix(X_te, label=y_te, feature_names=features)

    booster = xgb.train(
        xgb_params, dtrain,
        num_boost_round=num_boost_round,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=early_stopping_rounds,
        verbose_eval=100,
    )
    y_pred    = booster.predict(dtest)
    y_tr_pred = booster.predict(dtrain)

    result: dict = {
        "train_rows": int(len(df_tr)),
        "test_rows":  int(len(df_te)),
    }
    raw_oos = {}
    for name, fn in metrics:
        raw_oos[name] = fn(y_te, y_pred)
        result[f"{name}_oos"] = round(raw_oos[name], 4)
    result["cal_oos"] = round(float(np.mean(y_pred) - np.mean(y_te)), 4)

    best_iter = int(getattr(booster, "best_iteration", num_boost_round - 1)) + 1

    if include_train_metrics:
        primary_name, primary_fn = metrics[0]
        train_metric = primary_fn(y_tr.iloc[:nval], y_tr_pred)
        result[f"{primary_name}_train"] = round(train_metric, 4)
        # overfit_gap uses the UNROUNDED oos metric, matching every original
        # script (rounding happens only in the returned dict, not the diff).
        result["overfit_gap"] = round(abs(train_metric - raw_oos[primary_name]), 4)

    result["best_iteration"] = best_iter
    result["test_year"] = int(last)

    if logger:
        metric_str = " ".join(f"{name}={result[f'{name}_oos']:.4f}" for name, _ in metrics)
        logger.info("OOS results | %s cal=%+.4f best_iter=%d", metric_str, result["cal_oos"], best_iter)

    return result


# ---------------------------------------------------------------------------
# Full retrain (Section 7b: retrain on 100% of data)
# ---------------------------------------------------------------------------

def full_retrain(df: pd.DataFrame, features: list, target: str, xgb_params: dict,
                  best_iter: int, *, logger=None) -> xgb.Booster:
    if logger:
        logger.info("full retrain | rows=%d features=%d rounds=%d", len(df), len(features), best_iter)
    X = df[features].apply(pd.to_numeric, errors="coerce")
    y = df[target].astype(float)
    dtrain = xgb.DMatrix(X, label=y, feature_names=features)
    return xgb.train(xgb_params, dtrain, num_boost_round=best_iter, verbose_eval=False)


# ---------------------------------------------------------------------------
# Leakage check (zero-one-feature-and-retrain, C3.x pattern)
# ---------------------------------------------------------------------------

def leakage_check(
    df: pd.DataFrame,
    features: list,
    oos: dict,
    target: str,
    xgb_params: dict,
    *,
    baseline_key: str,
    metric_fn,
    higher_is_better: bool,
    skip_env_var: str,
    cv_folds_fn=cv_folds,
    threshold: float = 0.10,
    logger=None,
) -> list[str]:
    """Warn if zeroing any single feature improves the OOS metric by more
    than `threshold`, which may indicate the feature is carrying target
    leakage. Warning only -- never aborts the retrain. Only checks features
    with >50% non-NaN coverage to avoid false positives from sparse columns.

    higher_is_better: False for count systems (MAE -- zeroing a leaky
    feature LOWERS MAE, i.e. improves it), True for GAME (AUC -- zeroing a
    leaky feature RAISES AUC). This is the one genuine metric-direction
    difference between the count and binary callers.
    """
    import os
    if os.getenv(skip_env_var) == "1":
        if logger:
            logger.info("leakage check skipped (%s=1)", skip_env_var)
        return []

    last  = cv_folds_fn(df)[-1]
    df_tr = df[df["year"] < last].copy()
    df_te = df[df["year"] == last].copy()
    if len(df_te) < 10:
        return []

    best_iter = oos["best_iteration"]
    baseline  = oos[baseline_key]
    y_tr = df_tr[target].astype(float)
    y_te = df_te[target].astype(float)
    suspicious = []

    if logger:
        logger.info("leakage check | baseline %s=%.4f | threshold=%.0f%%",
                    baseline_key, baseline, threshold * 100)

    for feat in features:
        coverage = df_tr[feat].notna().mean() if feat in df_tr.columns else 0.0
        if coverage < 0.5:
            continue

        df_tr_z = df_tr.copy(); df_tr_z[feat] = 0.0
        df_te_z = df_te.copy(); df_te_z[feat] = 0.0

        dtrain_z = xgb.DMatrix(
            df_tr_z[features].apply(pd.to_numeric, errors="coerce"),
            label=y_tr, feature_names=features)
        dtest_z = xgb.DMatrix(
            df_te_z[features].apply(pd.to_numeric, errors="coerce"),
            label=y_te, feature_names=features)

        b = xgb.train(xgb_params, dtrain_z, num_boost_round=best_iter, verbose_eval=False)
        metric_z = metric_fn(y_te, b.predict(dtest_z))

        if higher_is_better:
            improvement = (metric_z - baseline) / max(baseline, 1e-9)
        else:
            improvement = (baseline - metric_z) / max(baseline, 1e-9)

        if improvement > threshold:
            suspicious.append(feat)
            if logger:
                logger.warning("  LEAKAGE SUSPECT: %r | %s %.4f -> zeroed %.4f (improvement=%+.1f%%)",
                               feat, baseline_key, baseline, metric_z, improvement * 100)

    if logger:
        if not suspicious:
            logger.info("  leakage check passed -- no suspicious features")
        else:
            logger.warning("  leakage check: %d suspicious features: %s", len(suspicious), suspicious)
    return suspicious


# ---------------------------------------------------------------------------
# NB dispersion fit (C07 pattern)
# ---------------------------------------------------------------------------

def fit_nb_alpha(booster: xgb.Booster, df: pd.DataFrame, features: list, target: str,
                  *, logger=None, default: float = 0.10) -> float:
    """NB(mu, alpha): var = mu + alpha*mu^2 -> alpha = (var - mu) / mu^2,
    clamped to [0.01, 0.50]. Falls back to `default` on any failure."""
    try:
        X = df[features].apply(pd.to_numeric, errors="coerce")
        dm = xgb.DMatrix(X, feature_names=features)
        y = df[target].astype(float).values
        preds = booster.predict(dm)
        resid_var = float(np.var(y - preds))
        mu_mean = float(np.mean(preds))
        nb_alpha = float(np.clip((resid_var - mu_mean) / max(mu_mean ** 2, 1e-6), 0.01, 0.50))
        if logger:
            logger.info("NB dispersion | mu=%.4f resid_var=%.4f nb_alpha=%.4f", mu_mean, resid_var, nb_alpha)
        return nb_alpha
    except Exception as e:
        if logger:
            logger.warning("nb_alpha fit failed (%s) -- using default %.2f", e, default)
        return default


# ---------------------------------------------------------------------------
# Feature stats: means / stds / empirical percentile dists (T10, C04)
# ---------------------------------------------------------------------------

def feature_means(df: pd.DataFrame, features: list, *, warn_on_skip: bool = False,
                   logger=None) -> dict:
    means, skipped = {}, []
    X = df[features].apply(pd.to_numeric, errors="coerce")
    for f in features:
        v = X[f].mean(skipna=True)
        if pd.isna(v):
            skipped.append(f)
        else:
            means[f] = float(v)
    if logger:
        if warn_on_skip and skipped:
            logger.warning("feature_means could not be computed for %d features: %s",
                           len(skipped), sorted(skipped)[:5])
        logger.info("feature_means computed for %d/%d features", len(means), len(features))
    return means


def feature_stds(df: pd.DataFrame, features: list) -> dict:
    """T10: feature_stds for the PSI drift monitor (T14)."""
    fstds: dict = {}
    X = df[features].apply(pd.to_numeric, errors="coerce")
    for f in features:
        v = X[f].std(skipna=True)
        if not pd.isna(v):
            fstds[f] = round(float(v), 6)
    return fstds


def feature_dists(df: pd.DataFrame, features: list) -> dict:
    """C04: empirical percentiles (p5/p10/p25/p50/p75/p90/p95 + prop_1) for
    the PSI drift monitor. Avoids a Gaussian misfit for binary/bounded/
    bimodal features. `monitor_drift.py` interpolates over these; falls back
    to Gaussian if a model's meta predates this (C04, 2026-05-20)."""
    fpdists: dict = {}
    for f in features:
        try:
            col = df[f] if f in df.columns else None
            if col is None:
                continue
            col_num = pd.to_numeric(col, errors="coerce").dropna()
            if len(col_num) < 10:
                continue
            fpdists[f] = {
                "p5":     round(float(np.percentile(col_num,  5)), 6),
                "p10":    round(float(np.percentile(col_num, 10)), 6),
                "p25":    round(float(np.percentile(col_num, 25)), 6),
                "p50":    round(float(np.percentile(col_num, 50)), 6),
                "p75":    round(float(np.percentile(col_num, 75)), 6),
                "p90":    round(float(np.percentile(col_num, 90)), 6),
                "p95":    round(float(np.percentile(col_num, 95)), 6),
                "prop_1": round(float((col_num == 1).mean()), 6),
            }
        except Exception:
            continue
    return fpdists


# ---------------------------------------------------------------------------
# CI-bootstrap + wf_* summary (T10 pattern)
# ---------------------------------------------------------------------------

def bootstrap_ci_95(values: list) -> tuple:
    """95% CI on the mean of `values` via a t-distribution (T10). Returns
    (None, None) if fewer than 2 values."""
    if not values or len(values) < 2:
        return None, None
    import scipy.stats as _st
    lo, hi = _st.t.interval(
        0.95, len(values) - 1,
        loc=float(np.mean(values)),
        scale=float(_st.sem(values)),
    )
    return round(float(lo), 4), round(float(hi), 4)


def wf_ci_and_summary(
    wf: list[dict],
    *,
    primary_metric: str,
    metric_names: list[str],
    require_ci_for_summary: bool = False,
) -> tuple:
    """Bootstrap a 95% CI on the CV mean of `primary_metric`, and build the
    wf_* summary dict (per-metric means + wf_cal + wf_{primary}_std +
    wf_folds) from the walk-forward fold list.

    require_ci_for_summary: retrain_outs_v1.py-only quirk -- its wf_summary
    is nested INSIDE the `len(wf) >= 2` CI branch in the original script, so
    OUTS only gets a wf_* summary when there are >=2 folds; every other
    caller (K/SB/BATTER_HITS/GAME) populates wf_summary whenever wf is
    non-empty, independent of whether a CI could be computed. A real,
    pre-existing difference, preserved rather than unified -- see the
    refactor report for why this was flagged instead of silently fixed.
    """
    cv_ci_lo = cv_ci_hi = None
    wf_summary: dict = {}
    has_ci = bool(wf) and len(wf) >= 2
    if has_ci:
        cv_ci_lo, cv_ci_hi = bootstrap_ci_95([f[primary_metric] for f in wf])
    if wf and (has_ci or not require_ci_for_summary):
        wf_df = pd.DataFrame(wf)
        wf_summary = {f"wf_{m}": round(float(wf_df[m].mean()), 4) for m in metric_names}
        wf_summary["wf_cal"] = round(float(wf_df["cal_gap"].mean()), 4)
        wf_summary[f"wf_{primary_metric}_std"] = round(float(wf_df[primary_metric].std()), 4)
        wf_summary["wf_folds"] = wf
    return cv_ci_lo, cv_ci_hi, wf_summary


# ---------------------------------------------------------------------------
# Persist: archive-then-latest GCS artifact write
# ---------------------------------------------------------------------------

def persist_model_artifacts(booster: xgb.Booster, meta_bytes: bytes, steps: list[dict],
                             *, logger=None):
    """Write the booster (via a temp file) and meta bytes to GCS in the
    given order, using each retrain script's own independent nested
    try/except-per-write pattern -- returns an error dict at the FIRST
    failing step, exactly matching each original script's `"{label} write:
    {e}"` message, or None once every step succeeds.

    steps: ordered list of {"kind": "booster"|"meta", "key": str, "label": str}.
    Order matters for partial-failure semantics: retrain_outs_v1.py writes
    both booster keys before either meta key, while K/SB/BATTER_HITS/GAME
    alternate archive-then-latest per artifact type -- pass the caller's own
    exact order, do not assume they're interchangeable.
    """
    from mlb_core.storage import write_bytes, upload_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        booster_tmp = Path(f.name)
    try:
        booster.save_model(str(booster_tmp))
        for step in steps:
            try:
                if step["kind"] == "booster":
                    upload_model(booster_tmp, step["key"])
                else:
                    write_bytes(meta_bytes, step["key"])
                if logger:
                    logger.info("%s: %s", step["label"], step["key"])
            except Exception as e:
                return {"status": "error", "error": f"{step['label']} write: {e}"}
    finally:
        booster_tmp.unlink(missing_ok=True)
    return None
