"""MOD-04 Cluster (a) Training CLI — Walk-Forward XGBoost Hurdle-NB for NBA Steals & Blocks.

Architecture:
  * Expanding-window monthly walk-forward OOF evaluation (each month ``[s0, s1)`` is
    scored by boosters trained strictly on ``date < s0``).
  * Stage 1 exposure ``(mu_min, sigma_min)`` comes from ``feature_store`` / ``backbone_nba``.
  * Stage 2 per stat (``steals``, ``blocks``) fits three boosters with custom objectives:
      1. Hurdle head (``pi``): custom binary logistic GBDT predicting ``P(Y == 0)``;
         excess zero-inflation weight ``pi = clip((p_zero - E[NB(0)]) / (1 - E[NB(0)]), 0, 0.99)``.
      2. Rate head (``lambda``): GBDT with custom Negative Binomial log-likelihood objective
         with ``base_margin = log(prior_rate_eb * mu_min)``, outputting ``log(lambda)``.
      3. Dispersion head (``alpha``): 1D MLE ``alpha_mle`` on held-out validation residuals,
         plus an L2-regularized shallow GBDT around ``log(alpha_mle)`` using the exact
         NB dispersion gradient; rate head is then iterated once with per-row ``alpha_hat``.
  * Mandatory Blocks Ablation: compares OOF log-likelihood of Hurdle-NB vs plain-NB (``pi=0``)
    vs Poisson (5-point Gauss-Hermite minutes quadrature). If Hurdle-NB gain on ``blocks``
    is ``<= 0``, ``blocks`` ships as ``plain_nb`` (recorded in ``manifest.json``).
  * Fits U4 blend coefficient ``c in [0, 1]`` on OOF rows and writes a versioned bundle
    (with canary verification) locally and optionally to GCS.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.optimize import minimize_scalar
from scipy.special import digamma, gammaln, polygamma

from mod04.feature_store import (
    ARCHETYPE_TABLE,
    FEATURE_COLUMNS,
    assert_feature_schema,
    build_training_features,
    load_labels,
)
from mod04.hurdle_nb import DEFAULT_GH_POINTS, hurdle_nb_loglik, p_over as hurdle_p_over

log = logging.getLogger("mod04.train_clustera_nba")

SEED = 20261124
SCHEMA = 1
STATS = ("steals", "blocks")
PRIOR_COL = {"steals": "stl_rate_eb", "blocks": "blk_rate_eb"}
GCS_MODEL_PREFIX = "Models/mod04/cluster_a/nba"
GCLOUD_BIN = Path.home() / "google-cloud-sdk/bin/gcloud"
GCS_BUCKET = "concrete-crow-445205-m4-mlb-data"

VAL_FRAC = 0.12
MIN_TRAIN_ROWS = 400
MAX_ROUNDS = 300
EARLY_STOP = 30
ALPHA_INIT = 0.25
ALPHA_BOUNDS = (0.01, 3.0)

RATE_PARAMS: dict[str, Any] = {
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.04,
    "min_child_weight": 30,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 10.0,
    "seed": SEED,
    "nthread": max(1, min(4, os.cpu_count() or 2)),
    "disable_default_eval_metric": 1,
}
HURDLE_PARAMS: dict[str, Any] = {
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.04,
    "min_child_weight": 40,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 12.0,
    "seed": SEED + 1,
    "nthread": RATE_PARAMS["nthread"],
    "disable_default_eval_metric": 1,
}
ALPHA_PARAMS: dict[str, Any] = {
    "tree_method": "hist",
    "max_depth": 2,
    "eta": 0.03,
    "min_child_weight": 80,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "reg_lambda": 30.0,
    "seed": SEED + 2,
    "nthread": RATE_PARAMS["nthread"],
    "disable_default_eval_metric": 1,
}


# --------------------------------------------------------------------------- Custom XGBoost Objectives
def _sigmoid(x: np.ndarray) -> np.ndarray:
    x_c = np.clip(x, -20.0, 20.0)
    return 1.0 / (1.0 + np.exp(-x_c))


def _logit(p: np.ndarray) -> np.ndarray:
    p_c = np.clip(p, 1e-6, 1.0 - 1e-6)
    return np.log(p_c / (1.0 - p_c))


def _logistic_obj(preds: np.ndarray, dtrain: xgb.DMatrix) -> tuple[np.ndarray, np.ndarray]:
    """Custom binary logistic NLL objective (preds = raw log-odds margin)."""
    y = dtrain.get_label()
    p = _sigmoid(preds)
    grad = p - y
    hess = np.maximum(p * (1.0 - p), 1e-6)
    return grad, hess


def _nb_rate_obj(alpha_vec: np.ndarray):
    """Custom Negative Binomial NLL objective w.r.t. ``eta = log(mu_count)``."""
    alp = np.asarray(alpha_vec, dtype=float)

    def obj(preds: np.ndarray, dtrain: xgb.DMatrix) -> tuple[np.ndarray, np.ndarray]:
        y = dtrain.get_label()
        mu = np.exp(np.clip(preds, -12.0, 5.0))
        denom = 1.0 + alp * mu
        grad = (mu - y) / denom
        hess = np.maximum((1.0 + alp * y) * mu / (denom ** 2), 1e-6)
        return grad, hess

    return obj


def _nb_eval(alpha_vec: np.ndarray):
    alp = np.asarray(alpha_vec, dtype=float)

    def metric(preds: np.ndarray, dtrain: xgb.DMatrix) -> tuple[str, float]:
        y = dtrain.get_label()
        mu = np.exp(np.clip(preds, -12.0, 5.0))
        r = 1.0 / np.maximum(alp, 1e-5)
        ll = (
            gammaln(y + r)
            - gammaln(r)
            - gammaln(y + 1.0)
            + r * np.log(r / (r + mu))
            + y * np.log(np.maximum(mu, 1e-9) / (r + mu))
        )
        return "nb-nll", float(-np.mean(ll))

    return metric


def _nb_alpha_obj(mu_vec: np.ndarray):
    """Custom NB dispersion objective w.r.t. ``theta = log(alpha)`` (``r = exp(-theta)``)."""
    mu = np.maximum(np.asarray(mu_vec, dtype=float), 1e-6)

    def obj(preds: np.ndarray, dtrain: xgb.DMatrix) -> tuple[np.ndarray, np.ndarray]:
        y = dtrain.get_label()
        theta = np.clip(preds, math.log(ALPHA_BOUNDS[0]), math.log(ALPHA_BOUNDS[1]))
        alpha = np.exp(theta)
        r = 1.0 / alpha
        dl_dr = -digamma(y + r) + digamma(r) - np.log(r) - 1.0 + np.log(r + mu) + (y + r) / (r + mu)
        grad = -r * dl_dr
        # Positive regularized Fisher-style curvature for stable tree steps
        curv = (r ** 2) * np.abs(polygamma(1, r) - polygamma(1, y + r) - 1.0 / r + 1.0 / (r + mu))
        hess = np.clip(curv, 1e-3, 10.0)
        return grad, hess

    return obj


def _fit_alpha_mle(y: np.ndarray, mu: np.ndarray) -> float:
    """1D MLE of global NB dispersion ``alpha`` on ``(y, mu)``."""
    y_f = np.asarray(y, dtype=float)
    mu_f = np.maximum(np.asarray(mu, dtype=float), 1e-6)

    def neg_ll(log_a: float) -> float:
        a = math.exp(log_a)
        r = 1.0 / a
        ll = (
            gammaln(y_f + r)
            - gammaln(r)
            - gammaln(y_f + 1.0)
            + r * np.log(r / (r + mu_f))
            + y_f * np.log(mu_f / (r + mu_f))
        )
        return float(-np.mean(ll))

    res = minimize_scalar(
        neg_ll,
        bounds=(math.log(ALPHA_BOUNDS[0]), math.log(ALPHA_BOUNDS[1])),
        method="bounded",
    )
    return float(np.clip(math.exp(res.x), ALPHA_BOUNDS[0], ALPHA_BOUNDS[1]))


# --------------------------------------------------------------------------- Model Fit / Predict
def _xgb_matrix(
    df: pd.DataFrame,
    features: list[str],
    y: np.ndarray | None = None,
    base_margin: np.ndarray | None = None,
) -> xgb.DMatrix:
    X = df[features].apply(pd.to_numeric, errors="coerce").astype("float32")
    dm = xgb.DMatrix(X, label=y, feature_names=features)
    if base_margin is not None:
        dm.set_base_margin(np.asarray(base_margin, dtype="float32"))
    return dm


def _fit_stat_models(
    train_df: pd.DataFrame,
    stat: str,
    features: list[str],
    max_rounds: int = MAX_ROUNDS,
) -> dict[str, Any]:
    """Fit Hurdle + NB (lambda, alpha) boosters on ``train_df`` (time-ordered tail validation)."""
    prior_col = PRIOR_COL[stat]
    tr = train_df[train_df["mu_min"] > 0.0].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
    if len(tr) < MIN_TRAIN_ROWS:
        raise RuntimeError(f"{stat}: only {len(tr)} training rows (< {MIN_TRAIN_ROWS})")

    dates = np.sort(tr["date"].unique())
    split_idx = max(1, min(len(dates) - 1, int(len(dates) * (1.0 - VAL_FRAC))))
    split_date = dates[split_idx]
    a_df = tr[tr["date"] < split_date].reset_index(drop=True)
    v_df = tr[tr["date"] >= split_date].reset_index(drop=True)
    if len(v_df) < 50:
        cut = max(1, int(len(tr) * (1.0 - VAL_FRAC)))
        a_df = tr.iloc[:cut].reset_index(drop=True)
        v_df = tr.iloc[cut:].reset_index(drop=True)

    feat_means = {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in features}

    def _fill_x(d: pd.DataFrame) -> pd.DataFrame:
        out = d[features].copy()
        for f in features:
            out[f] = pd.to_numeric(out[f], errors="coerce").fillna(feat_means[f] if np.isfinite(feat_means[f]) else 0.0)
        return out

    Xa, Xv, Xall = _fill_x(a_df), _fill_x(v_df), _fill_x(tr)
    ya, yv, yall = a_df[stat].to_numpy(float), v_df[stat].to_numpy(float), tr[stat].to_numpy(float)
    off_a = np.log(np.maximum(a_df[prior_col].to_numpy(float) * a_df["mu_min"].to_numpy(float), 1e-6))
    off_v = np.log(np.maximum(v_df[prior_col].to_numpy(float) * v_df["mu_min"].to_numpy(float), 1e-6))
    off_all = np.log(np.maximum(tr[prior_col].to_numpy(float) * tr["mu_min"].to_numpy(float), 1e-6))

    # 1. Pass 1: lambda booster at fixed ALPHA_INIT
    dm_a1 = _xgb_matrix(Xa, features, ya, off_a)
    dm_v1 = _xgb_matrix(Xv, features, yv, off_v)
    alp_a1 = np.full(len(a_df), ALPHA_INIT, dtype=float)
    alp_v1 = np.full(len(v_df), ALPHA_INIT, dtype=float)
    b1 = xgb.train(
        RATE_PARAMS,
        dm_a1,
        num_boost_round=max_rounds,
        obj=_nb_rate_obj(alp_a1),
        custom_metric=_nb_eval(alp_v1),
        evals=[(dm_v1, "val")],
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=False,
    )
    best1 = int(b1.best_iteration) + 1
    mu_v1 = np.exp(np.clip(b1.predict(dm_v1, iteration_range=(0, best1), output_margin=True), -12.0, 5.0))
    alpha_mle = _fit_alpha_mle(yv, mu_v1)

    # 2. Alpha booster on validation residuals around log(alpha_mle)
    base_log_a_v = np.full(len(v_df), math.log(alpha_mle), dtype=float)
    dm_alp_v = _xgb_matrix(Xv, features, yv, base_log_a_v)
    # Split validation in half for alpha booster early check so it doesn't overfit
    v_half = max(1, len(v_df) // 2)
    dm_alp_tr = _xgb_matrix(Xv.iloc[:v_half], features, yv[:v_half], base_log_a_v[:v_half])
    dm_alp_ev = _xgb_matrix(Xv.iloc[v_half:], features, yv[v_half:], base_log_a_v[v_half:])
    alpha_booster = xgb.train(
        ALPHA_PARAMS,
        dm_alp_tr,
        num_boost_round=60,
        obj=_nb_alpha_obj(mu_v1[:v_half]),
        evals=[(dm_alp_ev, "val")],
        verbose_eval=False,
    )
    raw_log_a_ev = alpha_booster.predict(dm_alp_ev, iteration_range=(0, 60), output_margin=True)
    alp_ev_pred = np.clip(np.exp(raw_log_a_ev), ALPHA_BOUNDS[0], ALPHA_BOUNDS[1])
    ll_const = hurdle_nb_loglik(
        yv[v_half:],
        0.0,
        mu_v1[v_half:] / np.maximum(v_df["mu_min"].to_numpy(float)[v_half:], 1e-6),
        alpha_mle,
        v_df["mu_min"].to_numpy(float)[v_half:],
        v_df["sigma_min"].to_numpy(float)[v_half:],
    )
    ll_boost_a = hurdle_nb_loglik(
        yv[v_half:],
        0.0,
        mu_v1[v_half:] / np.maximum(v_df["mu_min"].to_numpy(float)[v_half:], 1e-6),
        alp_ev_pred,
        v_df["mu_min"].to_numpy(float)[v_half:],
        v_df["sigma_min"].to_numpy(float)[v_half:],
    )
    use_alpha_booster = bool(ll_boost_a > ll_const)
    if use_alpha_booster:
        # Refit alpha booster on full validation tail for 40 rounds
        alpha_booster = xgb.train(
            ALPHA_PARAMS,
            dm_alp_v,
            num_boost_round=40,
            obj=_nb_alpha_obj(mu_v1),
            verbose_eval=False,
        )
        alpha_rounds = 40
    else:
        # Degenerate 1-round zero-gain placeholder if constant alpha_mle wins
        alpha_booster = xgb.train(
            {**ALPHA_PARAMS, "eta": 0.0},
            dm_alp_v,
            num_boost_round=1,
            obj=_nb_alpha_obj(mu_v1),
            verbose_eval=False,
        )
        alpha_rounds = 1

    def _pred_alpha(X_in: pd.DataFrame) -> np.ndarray:
        bm = np.full(len(X_in), math.log(alpha_mle), dtype=float)
        dm_a = _xgb_matrix(X_in, features, None, bm)
        log_a = alpha_booster.predict(dm_a, iteration_range=(0, alpha_rounds), output_margin=True)
        return np.clip(np.exp(log_a), ALPHA_BOUNDS[0], ALPHA_BOUNDS[1])

    alp_a2 = _pred_alpha(Xa) if use_alpha_booster else np.full(len(a_df), alpha_mle)
    alp_v2 = _pred_alpha(Xv) if use_alpha_booster else np.full(len(v_df), alpha_mle)
    alp_all = _pred_alpha(Xall) if use_alpha_booster else np.full(len(tr), alpha_mle)

    # 3. Pass 2: iterate lambda booster once using predicted alpha_hat
    b2 = xgb.train(
        RATE_PARAMS,
        dm_a1,
        num_boost_round=max_rounds,
        obj=_nb_rate_obj(alp_a2),
        custom_metric=_nb_eval(alp_v2),
        evals=[(dm_v1, "val")],
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=False,
    )
    best_rate = int(b2.best_iteration) + 1
    dm_all = _xgb_matrix(Xall, features, yall, off_all)
    lambda_booster = xgb.train(
        RATE_PARAMS,
        dm_all,
        num_boost_round=best_rate,
        obj=_nb_rate_obj(alp_all),
        verbose_eval=False,
    )

    # 4. Hurdle head: binary logistic GBDT on is_zero = (y == 0)
    z_a = (ya == 0).astype(float)
    z_v = (yv == 0).astype(float)
    z_all = (yall == 0).astype(float)
    dm_za = _xgb_matrix(Xa, features, z_a)
    dm_zv = _xgb_matrix(Xv, features, z_v)
    bh = xgb.train(
        {**HURDLE_PARAMS, "eval_metric": "logloss"},
        dm_za,
        num_boost_round=max_rounds,
        obj=_logistic_obj,
        evals=[(dm_zv, "val")],
        early_stopping_rounds=EARLY_STOP,
        verbose_eval=False,
    )
    best_hurdle = int(bh.best_iteration) + 1
    hurdle_booster = xgb.train(
        HURDLE_PARAMS,
        _xgb_matrix(Xall, features, z_all),
        num_boost_round=best_hurdle,
        obj=_logistic_obj,
        verbose_eval=False,
    )

    meta = {
        "stat": stat,
        "prior_col": prior_col,
        "features": list(features),
        "feature_means": feat_means,
        "best_rate_iter": int(best_rate),
        "best_hurdle_iter": int(best_hurdle),
        "alpha_rounds": int(alpha_rounds),
        "alpha_mle": float(alpha_mle),
        "use_alpha_booster": use_alpha_booster,
        "shipped_variant": "hurdle_nb",
        "train_start": str(tr["date"].min()),
        "train_end": str(tr["date"].max()),
        "n_train": int(len(tr)),
        "xgboost": xgb.__version__,
    }
    return {
        "lambda_booster": lambda_booster,
        "hurdle_booster": hurdle_booster,
        "alpha_booster": alpha_booster,
        "meta": meta,
    }


def predict_params(art: dict[str, Any], df: pd.DataFrame) -> pd.DataFrame:
    """Predict ``(pi, lambda_per_min, alpha)`` for every row of ``df``."""
    meta = art["meta"]
    features = list(meta["features"])
    means = meta["feature_means"]
    prior_col = meta["prior_col"]

    X = df.reindex(columns=features).copy()
    for f in features:
        m = means.get(f, 0.0)
        X[f] = pd.to_numeric(X[f], errors="coerce").fillna(m if np.isfinite(m) else 0.0)

    prior_rate = np.maximum(pd.to_numeric(X[prior_col], errors="coerce").fillna(0.03).to_numpy(float), 1e-5)
    mu_min = np.maximum(pd.to_numeric(df["mu_min"], errors="coerce").fillna(0.0).to_numpy(float), 0.0)

    # Lambda booster outputs log(lambda) when base_margin = log(prior_rate)
    bm_lam = np.log(prior_rate)
    dm_lam = _xgb_matrix(X, features, None, bm_lam)
    log_lam = art["lambda_booster"].predict(
        dm_lam, iteration_range=(0, int(meta["best_rate_iter"])), output_margin=True
    )
    lam = np.clip(np.exp(log_lam), 1e-5, 0.5)

    # Alpha booster
    if meta.get("use_alpha_booster"):
        bm_alp = np.full(len(X), math.log(float(meta["alpha_mle"])), dtype=float)
        dm_alp = _xgb_matrix(X, features, None, bm_alp)
        log_alp = art["alpha_booster"].predict(
            dm_alp, iteration_range=(0, int(meta["alpha_rounds"])), output_margin=True
        )
        alpha = np.clip(np.exp(log_alp), ALPHA_BOUNDS[0], ALPHA_BOUNDS[1])
    else:
        alpha = np.full(len(X), float(meta["alpha_mle"]), dtype=float)

    # Hurdle pi: excess structural-zero weight above NB(0)
    if meta.get("shipped_variant", "hurdle_nb") == "plain_nb":
        pi = np.zeros(len(X), dtype=float)
    else:
        dm_h = _xgb_matrix(X, features)
        logits = art["hurdle_booster"].predict(
            dm_h, iteration_range=(0, int(meta["best_hurdle_iter"])), output_margin=True
        )
        p_zero = _sigmoid(logits)
        # NB(0 | lam * mu_min, alpha) = (1 + alpha * mu)^(-1/alpha)
        mu_c = lam * mu_min
        nb_zero = np.power(1.0 + alpha * mu_c, -1.0 / np.maximum(alpha, 1e-5))
        pi = np.clip((p_zero - nb_zero) / np.maximum(1.0 - nb_zero, 1e-6), 0.0, 0.99)

    out_mask = mu_min <= 0.0
    if out_mask.any():
        pi[out_mask] = 1.0 - 1e-9

    return pd.DataFrame({
        "pi": pi,
        "lambda_per_min": lam,
        "alpha": alpha,
    }, index=df.index)


# --------------------------------------------------------------------------- Walk-forward OOF & Blend Fit
def _month_starts(dates: pd.Series, test_from: str) -> list[pd.Timestamp]:
    lo = pd.Timestamp(test_from).replace(day=1)
    hi = pd.to_datetime(dates).max()
    cur = lo
    out: list[pd.Timestamp] = []
    while cur <= hi:
        out.append(cur)
        cur = cur + pd.DateOffset(months=1)
    return out


def _vec_p_over_half(
    k_int: int,
    pi: np.ndarray | float,
    lam: np.ndarray,
    alpha: np.ndarray | float,
    mu_min: np.ndarray,
    sigma_min: np.ndarray,
    n_quad: int = DEFAULT_GH_POINTS,
) -> np.ndarray:
    """Vectorized ``P(Y > k_int)`` over GH quadrature for integer cutoff ``k_int >= 0``."""
    from numpy.polynomial.hermite import hermgauss

    n = len(lam)
    pi_arr = np.broadcast_to(np.asarray(pi, dtype=float), (n,))
    alp_arr = np.broadcast_to(np.asarray(alpha, dtype=float), (n,))
    xs, ws = hermgauss(n_quad)
    w_norm = ws / math.sqrt(math.pi)
    w_norm = w_norm / w_norm.sum()

    nodes = np.maximum(0.0, mu_min[:, None] + math.sqrt(2.0) * sigma_min[:, None] * xs[None, :])
    no_sd = sigma_min <= 1e-9
    if no_sd.any():
        nodes[no_sd] = np.maximum(0.0, mu_min[no_sd, None])
    mu_q = lam[:, None] * nodes  # (n, Q)

    # Sum NB(k) for k = 0..k_int
    cdf_q = np.zeros_like(mu_q)
    r_q = 1.0 / np.maximum(alp_arr[:, None], 1e-5)
    for k in range(k_int + 1):
        m_pos = np.maximum(mu_q, 1e-12)
        log_p = (
            gammaln(k + r_q)
            - gammaln(r_q)
            - gammaln(k + 1.0)
            + r_q * np.log(r_q / (r_q + m_pos))
            + k * np.log(m_pos / (r_q + m_pos))
        )
        p_k = np.where(mu_q <= 0.0, 1.0 if k == 0 else 0.0, np.exp(log_p))
        cdf_q += p_k
    cdf_nb = cdf_q @ w_norm
    pi_c = np.clip(pi_arr, 0.0, 1.0 - 1e-9)
    return np.clip(1.0 - (pi_c + (1.0 - pi_c) * cdf_nb), 0.0, 1.0)


def _fit_oof_blend_c(
    oof_df: pd.DataFrame,
    stat: str,
    shipped_variant: str,
) -> dict[str, Any]:
    """Fit U4 blend coefficient ``c in [0, 1]`` on OOF rows at standard half-point lines (0.5, 1.5).

    Uses ``p_market`` if present in ``oof_df``, otherwise calibrates against the
    hierarchical EB prior baseline (``a=0.0, b=1.0, c in [0, 1]``).
    """
    if oof_df.empty:
        return {"a": 0.0, "b": 1.0, "c": 0.25, "fitted_on_market": False, "n_oof_blend": 0}

    prior_col = PRIOR_COL[stat]
    pi_col = np.zeros(len(oof_df)) if shipped_variant == "plain_nb" else oof_df["pi"].to_numpy(float)
    lam_col = oof_df["lambda_per_min"].to_numpy(float)
    alp_col = oof_df["alpha"].to_numpy(float)
    mu_min_col = oof_df["mu_min"].to_numpy(float)
    sd_min_col = oof_df["sigma_min"].to_numpy(float)
    prior_lam = np.maximum(oof_df[prior_col].to_numpy(float), 1e-5)
    y_col = oof_df[stat].to_numpy(float)
    med_alp = float(np.median(alp_col))

    p_mod_list, p_base_list, over_list = [], [], []
    for k_int, line in ((0, 0.5), (1, 1.5)):
        p_mod_list.append(_vec_p_over_half(k_int, pi_col, lam_col, alp_col, mu_min_col, sd_min_col))
        p_base_list.append(_vec_p_over_half(k_int, 0.0, prior_lam, med_alp, mu_min_col, sd_min_col))
        over_list.append((y_col > line).astype(float))

    p_mod = np.clip(np.concatenate(p_mod_list), 0.01, 0.99)
    p_base = np.clip(np.concatenate(p_base_list), 0.01, 0.99)
    y_bin = np.concatenate(over_list)

    lm = _logit(p_base)
    ld = _logit(p_mod) - lm

    def nll_c(c_val: float) -> float:
        p = _sigmoid(lm + c_val * ld)
        p = np.clip(p, 1e-6, 1.0 - 1e-6)
        return float(-np.mean(y_bin * np.log(p) + (1.0 - y_bin) * np.log(1.0 - p)))

    res = minimize_scalar(nll_c, bounds=(0.0, 1.0), method="bounded")
    c_opt = float(np.clip(res.x, 0.0, 1.0))
    return {
        "a": 0.0,
        "b": 1.0,
        "c": round(c_opt, 4),
        "fitted_on_market": False,
        "n_oof_blend": int(len(oof_df)),
    }


def walk_forward_oof(
    data_df: pd.DataFrame,
    stat: str,
    features: list[str],
    test_from: str,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run expanding-window monthly walk-forward OOF scoring for ``stat``."""
    starts = _month_starts(data_df["date"], test_from)
    oof_parts: list[pd.DataFrame] = []
    for idx, s0 in enumerate(starts):
        s1 = starts[idx + 1] if idx + 1 < len(starts) else pd.Timestamp("2100-01-01")
        s0_str, s1_str = s0.strftime("%Y-%m-%d"), s1.strftime("%Y-%m-%d")
        mask = (data_df["date"] >= s0_str) & (data_df["date"] < s1_str) & (data_df["mu_min"] > 0.0)
        if not mask.any():
            continue
        tr = data_df[data_df["date"] < s0_str]
        if len(tr[tr["mu_min"] > 0.0]) < MIN_TRAIN_ROWS:
            continue
        art = _fit_stat_models(tr, stat, features, max_rounds=max_rounds)
        te = data_df.loc[mask].copy()
        preds = predict_params(art, te)
        part = te[["game_id", "player_id", "date", "mu_min", "sigma_min", PRIOR_COL[stat], stat]].copy()
        part["pi"] = preds["pi"].values
        part["lambda_per_min"] = preds["lambda_per_min"].values
        part["alpha"] = preds["alpha"].values
        part["fold_month"] = s0_str[:7]
        oof_parts.append(part)

    if not oof_parts:
        raise RuntimeError(f"{stat}: no OOF folds could be trained from {test_from}")

    oof = pd.concat(oof_parts, ignore_index=True)
    y = oof[stat].to_numpy(float)
    mu_m = oof["mu_min"].to_numpy(float)
    sd_m = oof["sigma_min"].to_numpy(float)
    lam = oof["lambda_per_min"].to_numpy(float)
    alp = oof["alpha"].to_numpy(float)
    pi = oof["pi"].to_numpy(float)

    ll_poisson = hurdle_nb_loglik(y, 0.0, lam, 0.0, mu_m, sd_m, n_quad=DEFAULT_GH_POINTS)
    ll_plain_nb = hurdle_nb_loglik(y, 0.0, lam, alp, mu_m, sd_m, n_quad=DEFAULT_GH_POINTS)
    ll_hurdle_nb = hurdle_nb_loglik(y, pi, lam, alp, mu_m, sd_m, n_quad=DEFAULT_GH_POINTS)
    hurdle_gain = float(ll_hurdle_nb - ll_plain_nb)

    # Mandatory ruling ablation gate on blocks (and consistent selection for both heads)
    shipped_variant = "hurdle_nb" if hurdle_gain > 0.0 else "plain_nb"
    metrics = {
        "n_oof": int(len(oof)),
        "ll_poisson": round(ll_poisson, 6),
        "ll_plain_nb": round(ll_plain_nb, 6),
        "ll_hurdle_nb": round(ll_hurdle_nb, 6),
        "hurdle_gain_vs_plain_nb": round(hurdle_gain, 6),
        "nb_gain_vs_poisson": round(float(ll_plain_nb - ll_poisson), 6),
        "shipped_variant": shipped_variant,
    }
    return oof, metrics


# --------------------------------------------------------------------------- Bundle Save / Load + Canary
CANARY_ROWS = 8
CANARY_RTOL = 1e-4


def _build_canary(art: dict[str, Any], df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    rows = df.sort_values(["date", "game_id", "player_id"]).tail(CANARY_ROWS).copy()
    preds = predict_params(art, rows)
    keep_cols = list(features)
    return {
        "X": rows[keep_cols].astype(float).fillna(0.0).values.tolist(),
        "mu_min": rows["mu_min"].astype(float).tolist(),
        "lambda_per_min": preds["lambda_per_min"].astype(float).tolist(),
        "alpha": preds["alpha"].astype(float).tolist(),
        "pi": preds["pi"].astype(float).tolist(),
    }


def check_canary(art: dict[str, Any], stat: str) -> None:
    c = art["meta"].get("canary")
    if not c:
        raise ValueError(f"{stat}: missing canary in meta")
    df_c = pd.DataFrame(c["X"], columns=art["meta"]["features"])
    df_c["mu_min"] = c["mu_min"]
    got = predict_params(art, df_c)
    want_lam = np.asarray(c["lambda_per_min"], dtype=float)
    got_lam = got["lambda_per_min"].to_numpy(float)
    if not np.allclose(got_lam, want_lam, rtol=CANARY_RTOL, atol=1e-7):
        raise ValueError(f"{stat}: canary lambda mismatch (xgb version skew)")


def save_bundle(bundle: dict[str, Any], out_dir: str | Path, publish_latest: bool = True) -> Path:
    man = bundle["manifest"]
    ver = man["version"]
    root = Path(out_dir)
    base = root / ver
    base.mkdir(parents=True, exist_ok=True)
    for stat, art in bundle["models"].items():
        art["lambda_booster"].save_model(str(base / f"xgb_{stat}_lambda.json"))
        art["hurdle_booster"].save_model(str(base / f"xgb_{stat}_hurdle.json"))
        art["alpha_booster"].save_model(str(base / f"xgb_{stat}_alpha.json"))
        (base / f"meta_{stat}.json").write_text(json.dumps(art["meta"], indent=1))
    (base / "manifest.json").write_text(json.dumps(man, indent=1))
    if publish_latest:
        root.mkdir(parents=True, exist_ok=True)
        (root / "latest.json").write_text(json.dumps({"version": ver}, indent=1))
    return base


def load_bundle(bundle_dir: str | Path, version: str | None = None) -> dict[str, Any]:
    p = Path(bundle_dir)
    if (p / "manifest.json").exists():
        base = p
    else:
        ver = version or json.loads((p / "latest.json").read_text())["version"]
        base = p / ver
    man = json.loads((base / "manifest.json").read_text())
    if man.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported bundle schema {man.get('schema')} != {SCHEMA}")
    models: dict[str, Any] = {}
    for stat in man["stats"]:
        meta = json.loads((base / f"meta_{stat}.json").read_text())
        if list(meta["features"]) != list(FEATURE_COLUMNS):
            raise ValueError(f"{stat}: bundle feature list differs from FEATURE_COLUMNS")
        lb, hb, ab = xgb.Booster(), xgb.Booster(), xgb.Booster()
        lb.load_model(str(base / f"xgb_{stat}_lambda.json"))
        hb.load_model(str(base / f"xgb_{stat}_hurdle.json"))
        ab.load_model(str(base / f"xgb_{stat}_alpha.json"))
        art = {
            "lambda_booster": lb,
            "hurdle_booster": hb,
            "alpha_booster": ab,
            "meta": meta,
        }
        check_canary(art, stat)
        models[stat] = art
    return {"models": models, "manifest": man, "bundle_dir": str(base)}


def upload_bundle_gcs(local_bundle_dir: Path, version: str) -> str:
    prefix = f"{GCS_MODEL_PREFIX}/{version}"
    if os.environ.get("MLB_GCS_BUCKET") or os.environ.get("GCS_BUCKET"):
        from mlb_core import storage

        for f in sorted(local_bundle_dir.iterdir()):
            if f.is_file():
                storage.write_bytes(f.read_bytes(), f"{prefix}/{f.name}")
        storage.write_bytes(json.dumps({"version": version}).encode(), f"{GCS_MODEL_PREFIX}/latest.json")
    else:
        dst = f"gs://{GCS_BUCKET}/{prefix}/"
        subprocess.run([str(GCLOUD_BIN), "storage", "cp", f"{local_bundle_dir}/*", dst], check=True)
        latest_tmp = local_bundle_dir.parent / "latest.json"
        if latest_tmp.exists():
            subprocess.run(
                [str(GCLOUD_BIN), "storage", "cp", str(latest_tmp), f"gs://{GCS_BUCKET}/{GCS_MODEL_PREFIX}/latest.json"],
                check=True,
            )
    return f"gs://{GCS_BUCKET}/{prefix}/"


# --------------------------------------------------------------------------- End-to-end Trainer
def train_pipeline(
    data_dir: str | Path,
    out_dir: str | Path,
    min_date: str = "2022-10-01",
    test_from: str = "2024-11-01",
    version: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    write_gcs: bool = False,
) -> dict[str, Any]:
    """Train walk-forward OOF + final production bundle for steals & blocks."""
    feat_df = build_training_features(data_dir, min_date=min_date)
    assert_feature_schema(feat_df)
    labels = load_labels(data_dir)
    merged = feat_df.merge(
        labels[["game_id", "player_id", "steals", "blocks"]],
        on=["game_id", "player_id"],
        how="inner",
    )
    # Require >= 3 prior games so rolling priors are warm
    merged = merged[merged["n_prior_games"] >= 3].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)

    ver = version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    models: dict[str, Any] = {}
    oof_metrics: dict[str, Any] = {}
    blend_table: dict[str, Any] = {}

    for stat in STATS:
        oof_df, metrics = walk_forward_oof(merged, stat, FEATURE_COLUMNS, test_from=test_from, max_rounds=max_rounds)
        blend_info = _fit_oof_blend_c(oof_df, stat, metrics["shipped_variant"])
        art = _fit_stat_models(merged, stat, FEATURE_COLUMNS, max_rounds=max_rounds)
        art["meta"]["shipped_variant"] = metrics["shipped_variant"]
        art["meta"]["canary"] = _build_canary(art, merged, FEATURE_COLUMNS)
        models[stat] = art
        oof_metrics[stat] = metrics
        blend_table[stat] = blend_info

    manifest = {
        "schema": SCHEMA,
        "version": ver,
        "trained_through": str(merged["date"].max()),
        "min_date": min_date,
        "test_from": test_from,
        "n_rows": int(len(merged)),
        "xgboost": xgb.__version__,
        "stats": list(STATS),
        "features": list(FEATURE_COLUMNS),
        "archetype_table": {str(k): v for k, v in ARCHETYPE_TABLE.items()},
        "oof_metrics": oof_metrics,
        "blend": blend_table,
    }
    bundle = {"models": models, "manifest": manifest}
    local_dir = save_bundle(bundle, out_dir, publish_latest=True)
    gcs_uri = upload_bundle_gcs(local_dir, ver) if write_gcs else None
    manifest["local_bundle_dir"] = str(local_dir)
    manifest["gcs_uri"] = gcs_uri
    return bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train MOD-04 Cluster (a) NBA steals & blocks Hurdle-NB bundle.")
    ap.add_argument("--data-dir", default="data/mod04", help="Directory containing NBA/ masters, pbp, shots, etc.")
    ap.add_argument("--out-dir", default="data/mod04/models", help="Local bundle output directory")
    ap.add_argument("--min-date", default="2022-10-01")
    ap.add_argument("--test-from", default="2024-11-01")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--version", default=None)
    ap.add_argument("--write-gcs", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bundle = train_pipeline(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        min_date=args.min_date,
        test_from=args.test_from,
        version=args.version,
        max_rounds=args.max_rounds,
        write_gcs=args.write_gcs,
    )
    print(json.dumps(bundle["manifest"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
