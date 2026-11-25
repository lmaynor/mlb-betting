"""Shared internal XGBoost training/dispersion helpers for MOD-04 Cluster (d)."""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np
import pandas as pd
import xgboost as xgb

from mod04d.distributions import ALPHA_MAX, ALPHA_MIN, MU_MIN, negbin_logpmf
from mod04d.features import assert_no_leak_columns

DEFAULT_POISSON_PARAMS: dict = {
    "objective": "count:poisson",
    "eval_metric": "poisson-nloglik",
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.05,
    "min_child_weight": 15,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "reg_lambda": 5.0,
    "seed": 7,
    "nthread": max(1, min(4, (os.cpu_count() or 2))),
}

DEFAULT_DISP_PARAMS: dict = {
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.05,
    "min_child_weight": 10,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "reg_lambda": 2.0,
    "seed": 7,
    "nthread": max(1, min(4, (os.cpu_count() or 2))),
}


def prepare_X(df: pd.DataFrame, features: Sequence[str], feature_means: dict[str, float] | None = None) -> pd.DataFrame:
    """Validate against leak columns and build float32 feature frame."""
    assert_no_leak_columns(df)
    X = pd.DataFrame(index=df.index)
    for col in features:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
        else:
            s = pd.Series(np.nan, index=df.index, dtype=float)
        if feature_means is not None and col in feature_means:
            fill = feature_means[col]
            if np.isfinite(fill):
                s = s.fillna(fill)
        X[col] = s.astype(float)
    return X


def compute_feature_means(df: pd.DataFrame, features: Sequence[str]) -> dict[str, float]:
    means: dict[str, float] = {}
    for col in features:
        if col in df.columns:
            m = float(pd.to_numeric(df[col], errors="coerce").mean())
            means[col] = m if np.isfinite(m) else 0.0
        else:
            means[col] = 0.0
    return means


def chrono_split(df: pd.DataFrame, val_frac: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological train/val split without shuffling future into past."""
    d = df.copy()
    if "date" in d.columns:
        d = d.sort_values([c for c in ("date", "game_id", "player_id") if c in d.columns]).reset_index(drop=True)
        dates = np.sort(d["date"].astype(str).unique())
        if len(dates) >= 4:
            cut_idx = max(1, int(len(dates) * (1.0 - val_frac)))
            cut_idx = min(cut_idx, len(dates) - 1)
            split_date = dates[cut_idx]
            tr = d[d["date"].astype(str) < split_date].reset_index(drop=True)
            va = d[d["date"].astype(str) >= split_date].reset_index(drop=True)
            if len(tr) >= 10 and len(va) >= 10:
                return tr, va
    n = len(d)
    split_n = max(10, int(n * (1.0 - val_frac)))
    split_n = min(split_n, n - 5)
    return d.iloc[:split_n].reset_index(drop=True), d.iloc[split_n:].reset_index(drop=True)


def fit_global_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    """Grid MLE for scalar NegBin dispersion alpha on held-out (y, mu)."""
    grid = np.concatenate([[0.0], np.linspace(0.01, 1.5, 60)])
    scores = [float(negbin_logpmf(y, mu, a).mean()) for a in grid]
    return float(grid[int(np.argmax(scores))])


def fit_mean_and_dispersion(
    df: pd.DataFrame,
    target: str,
    features: Sequence[str],
    *,
    max_rounds: int = 200,
    early_stopping: int = 25,
    disp_rounds: int = 60,
    params: dict | None = None,
    disp_params: dict | None = None,
    val_frac: float = 0.25,
) -> dict:
    """Fit count:poisson mean GBDT + log(alpha(x)) dispersion GBDT on OOF/val Pearson residuals."""
    work = df[df[target].notna()].copy()
    feature_means = compute_feature_means(work, features)
    tr, va = chrono_split(work, val_frac=val_frac)

    p_mean = {**DEFAULT_POISSON_PARAMS, **(params or {})}
    p_mean["min_child_weight"] = min(int(p_mean["min_child_weight"]), max(2, len(tr) // 15))

    X_tr = prepare_X(tr, features, feature_means)
    y_tr = pd.to_numeric(tr[target], errors="coerce").to_numpy(dtype=float)
    X_va = prepare_X(va, features, feature_means)
    y_va = pd.to_numeric(va[target], errors="coerce").to_numpy(dtype=float)

    dtr = xgb.DMatrix(X_tr, label=y_tr, feature_names=list(features))
    dva = xgb.DMatrix(X_va, label=y_va, feature_names=list(features))

    val_booster = xgb.train(
        p_mean,
        dtr,
        num_boost_round=max_rounds,
        evals=[(dva, "val")],
        early_stopping_rounds=early_stopping,
        verbose_eval=False,
    )
    best_iter = max(1, int(getattr(val_booster, "best_iteration", max_rounds - 1)) + 1)
    mu_va = np.maximum(val_booster.predict(dva, iteration_range=(0, best_iter)), MU_MIN)

    # Fit dispersion GBDT on validation residuals: alpha_i = ((y - mu)^2 - mu) / mu^2
    global_alpha = max(fit_global_alpha(y_va, mu_va), ALPHA_MIN)
    pearson_excess = ((y_va - mu_va) ** 2 - mu_va) / np.maximum(mu_va ** 2, 0.1)
    # Smooth slightly toward global_alpha to prevent degenerate zero targets while preserving signal
    alpha_target = np.clip(0.8 * pearson_excess + 0.2 * global_alpha, 0.005, 3.0)
    log_alpha_target = np.log(alpha_target)

    disp_feats = [*features, "_mu_hat"]
    X_va_disp = X_va.assign(_mu_hat=mu_va)
    p_disp = {**DEFAULT_DISP_PARAMS, **(disp_params or {})}
    p_disp["min_child_weight"] = min(int(p_disp["min_child_weight"]), max(2, len(va) // 15))
    ddisp = xgb.DMatrix(X_va_disp, label=log_alpha_target, feature_names=disp_feats)
    disp_booster = xgb.train(p_disp, ddisp, num_boost_round=disp_rounds, verbose_eval=False)

    # Refit final mean booster on full training frame with `best_iter` rounds
    X_all = prepare_X(work, features, feature_means)
    y_all = pd.to_numeric(work[target], errors="coerce").to_numpy(dtype=float)
    dall = xgb.DMatrix(X_all, label=y_all, feature_names=list(features))
    final_booster = xgb.train(p_mean, dall, num_boost_round=best_iter, verbose_eval=False)

    # Validation metrics
    log_a_va = disp_booster.predict(ddisp, iteration_range=(0, disp_rounds))
    alpha_va = np.clip(np.exp(log_a_va), ALPHA_MIN, ALPHA_MAX)
    val_ll_disp = float(negbin_logpmf(y_va, mu_va, alpha_va).mean())
    val_ll_fixed = float(negbin_logpmf(y_va, mu_va, global_alpha).mean())

    return {
        "booster": final_booster,
        "disp_booster": disp_booster,
        "features": list(features),
        "disp_features": disp_feats,
        "feature_means": feature_means,
        "best_iteration": best_iter,
        "disp_rounds": disp_rounds,
        "global_alpha": float(global_alpha),
        "val_ll_disp": val_ll_disp,
        "val_ll_fixed": val_ll_fixed,
        "n_train": int(len(work)),
        "n_val": int(len(va)),
    }


def predict_mu_alpha_from_art(art: dict, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    features = art["features"]
    X = prepare_X(df, features, art.get("feature_means"))
    dm = xgb.DMatrix(X, feature_names=features)
    mu = np.maximum(
        art["booster"].predict(dm, iteration_range=(0, int(art["best_iteration"]))),
        MU_MIN,
    )
    if art.get("disp_booster") is not None:
        X_disp = X.assign(_mu_hat=mu)
        dd = xgb.DMatrix(X_disp, feature_names=art["disp_features"])
        log_a = art["disp_booster"].predict(dd, iteration_range=(0, int(art["disp_rounds"])))
        alpha = np.clip(np.exp(log_a), ALPHA_MIN, ALPHA_MAX)
    else:
        alpha = np.full(len(mu), float(art.get("global_alpha", 0.05)), dtype=float)
    return mu, alpha
