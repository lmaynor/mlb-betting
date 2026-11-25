"""MOD-04 Cluster (a) Training CLI — Walk-Forward XGBoost NB for NHL Blocked Shots.

Architecture (per RUL-20261124-sota-models + plan §5a item A5):
  * Expanding-window monthly walk-forward OOF evaluation from ``2024-11-01``
    (each month ``[s0, s1)`` is scored by boosters trained strictly on ``date < s0``).
  * Stage 1 exposure ``(mu_exp_toi, sigma_exp_toi)`` = EV + PK TOI from
    ``feature_store_nhl`` / ``backbone_nhl``.
  * Stage 2 fits plain Negative Binomial (``pi = 0.0`` — NO hurdle for NHL):
      1. Rate head (``lambda``): GBDT with custom Negative Binomial log-likelihood
         objective with ``base_margin = log(blk_rate_eb * mu_exp_toi)``, outputting
         ``log(lambda_per_min)``.
      2. Dispersion head (``alpha``): 1D MLE ``alpha_mle`` on validation residuals
         plus shallow L2-regularized GBDT around ``log(alpha_mle)``; rate head is
         iterated once with per-row ``alpha_hat``.
  * Evaluates OOF log-likelihood of plain-NB vs Poisson over 5-point Gauss-Hermite
    quadrature (via ``mod04.hurdle_nb.hurdle_nb_loglik`` with ``pi=0.0``).
  * Fits U4 blend coefficient ``c in [0, 1]`` on OOF rows at half-point lines
    (0.5, 1.5, 2.5) and writes a versioned bundle (with canary verification)
    locally and optionally to GCS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
import xgboost as xgb

from mod04.feature_store_nhl import (
    FEATURE_COLUMNS,
    NHL_ARCHETYPE_TABLE,
    assert_nhl_feature_schema,
    build_training_features_nhl,
    load_labels_nhl,
)
from mod04.hurdle_nb import DEFAULT_GH_POINTS, hurdle_nb_loglik
from mod04.train_clustera_nba import (
    ALPHA_BOUNDS,
    ALPHA_INIT,
    ALPHA_PARAMS,
    EARLY_STOP,
    MAX_ROUNDS,
    MIN_TRAIN_ROWS,
    RATE_PARAMS,
    SEED,
    VAL_FRAC,
    _fit_alpha_mle,
    _logit,
    _month_starts,
    _nb_alpha_obj,
    _nb_eval,
    _nb_rate_obj,
    _sigmoid,
    _vec_p_over_half,
    _xgb_matrix,
)

log = logging.getLogger("mod04.train_clustera_nhl")

SCHEMA = 1
STATS = ("blocked_shots",)
PRIOR_COL = {"blocked_shots": "blk_rate_eb"}
GCS_MODEL_PREFIX = "Models/mod04/cluster_a/nhl"
GCLOUD_BIN = Path.home() / "google-cloud-sdk/bin/gcloud"
GCS_BUCKET = "concrete-crow-445205-m4-mlb-data"

CANARY_ROWS = 8
CANARY_RTOL = 1e-4


def _fit_stat_models(
    train_df: pd.DataFrame,
    stat: str,
    features: list[str],
    max_rounds: int = MAX_ROUNDS,
) -> dict[str, Any]:
    """Fit plain-NB (lambda, alpha) boosters on ``train_df`` (time-ordered tail validation)."""
    prior_col = PRIOR_COL[stat]
    tr = train_df[train_df["mu_exp_toi"] > 0.0].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
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
    off_a = np.log(np.maximum(a_df[prior_col].to_numpy(float) * a_df["mu_exp_toi"].to_numpy(float), 1e-6))
    off_v = np.log(np.maximum(v_df[prior_col].to_numpy(float) * v_df["mu_exp_toi"].to_numpy(float), 1e-6))
    off_all = np.log(np.maximum(tr[prior_col].to_numpy(float) * tr["mu_exp_toi"].to_numpy(float), 1e-6))

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
    v_mu_exp_tail = v_df["mu_exp_toi"].to_numpy(float)[v_half:]
    v_sd_exp_tail = v_df["sigma_exp_toi"].to_numpy(float)[v_half:]
    lam_v_tail = mu_v1[v_half:] / np.maximum(v_mu_exp_tail, 1e-6)
    ll_const = hurdle_nb_loglik(yv[v_half:], 0.0, lam_v_tail, alpha_mle, v_mu_exp_tail, v_sd_exp_tail)
    ll_boost_a = hurdle_nb_loglik(yv[v_half:], 0.0, lam_v_tail, alp_ev_pred, v_mu_exp_tail, v_sd_exp_tail)
    use_alpha_booster = bool(ll_boost_a > ll_const)

    if use_alpha_booster:
        alpha_booster = xgb.train(
            ALPHA_PARAMS,
            dm_alp_v,
            num_boost_round=40,
            obj=_nb_alpha_obj(mu_v1),
            verbose_eval=False,
        )
        alpha_rounds = 40
    else:
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

    meta = {
        "stat": stat,
        "prior_col": prior_col,
        "features": list(features),
        "feature_means": feat_means,
        "best_rate_iter": int(best_rate),
        "alpha_rounds": int(alpha_rounds),
        "alpha_mle": float(alpha_mle),
        "use_alpha_booster": use_alpha_booster,
        "shipped_variant": "plain_nb",
        "train_start": str(tr["date"].min()),
        "train_end": str(tr["date"].max()),
        "n_train": int(len(tr)),
        "xgboost": xgb.__version__,
    }
    return {
        "lambda_booster": lambda_booster,
        "alpha_booster": alpha_booster,
        "meta": meta,
    }


def predict_params(art: dict[str, Any], df: pd.DataFrame) -> pd.DataFrame:
    """Predict ``(pi=0.0, lambda_per_min, lambda_per60, alpha)`` for every row of ``df``."""
    meta = art["meta"]
    features = list(meta["features"])
    means = meta["feature_means"]
    prior_col = meta["prior_col"]

    X = df.reindex(columns=features).copy()
    for f in features:
        m = means.get(f, 0.0)
        X[f] = pd.to_numeric(X[f], errors="coerce").fillna(m if np.isfinite(m) else 0.0)

    prior_rate = np.maximum(pd.to_numeric(X[prior_col], errors="coerce").fillna(0.05).to_numpy(float), 1e-5)
    mu_exp = np.maximum(pd.to_numeric(df["mu_exp_toi"], errors="coerce").fillna(0.0).to_numpy(float), 0.0)

    bm_lam = np.log(prior_rate)
    dm_lam = _xgb_matrix(X, features, None, bm_lam)
    log_lam = art["lambda_booster"].predict(
        dm_lam, iteration_range=(0, int(meta["best_rate_iter"])), output_margin=True
    )
    lam = np.clip(np.exp(log_lam), 1e-5, 0.5)

    if meta.get("use_alpha_booster"):
        bm_alp = np.full(len(X), math.log(float(meta["alpha_mle"])), dtype=float)
        dm_alp = _xgb_matrix(X, features, None, bm_alp)
        log_alp = art["alpha_booster"].predict(
            dm_alp, iteration_range=(0, int(meta["alpha_rounds"])), output_margin=True
        )
        alpha = np.clip(np.exp(log_alp), ALPHA_BOUNDS[0], ALPHA_BOUNDS[1])
    else:
        alpha = np.full(len(X), float(meta["alpha_mle"]), dtype=float)

    pi = np.zeros(len(X), dtype=float)
    out_mask = mu_exp <= 0.0
    if out_mask.any():
        lam[out_mask] = 0.0

    return pd.DataFrame({
        "pi": pi,
        "lambda_per_min": lam,
        "lambda_per60": 60.0 * lam,
        "alpha": alpha,
    }, index=df.index)


def _fit_oof_blend_c(oof_df: pd.DataFrame, stat: str) -> dict[str, Any]:
    """Fit U4 blend coefficient ``c in [0, 1]`` on OOF rows at lines (0.5, 1.5, 2.5)."""
    if oof_df.empty:
        return {"a": 0.0, "b": 1.0, "c": 0.25, "fitted_on_market": False, "n_oof_blend": 0}

    prior_col = PRIOR_COL[stat]
    pi_col = np.zeros(len(oof_df), dtype=float)
    lam_col = oof_df["lambda_per_min"].to_numpy(float)
    alp_col = oof_df["alpha"].to_numpy(float)
    mu_exp_col = oof_df["mu_exp_toi"].to_numpy(float)
    sd_exp_col = oof_df["sigma_exp_toi"].to_numpy(float)
    prior_lam = np.maximum(oof_df[prior_col].to_numpy(float), 1e-5)
    y_col = oof_df[stat].to_numpy(float)
    med_alp = float(np.median(alp_col))

    p_mod_list, p_base_list, over_list = [], [], []
    for k_int, line in ((0, 0.5), (1, 1.5), (2, 2.5)):
        p_mod_list.append(_vec_p_over_half(k_int, pi_col, lam_col, alp_col, mu_exp_col, sd_exp_col))
        p_base_list.append(_vec_p_over_half(k_int, 0.0, prior_lam, med_alp, mu_exp_col, sd_exp_col))
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
    test_from: str = "2024-11-01",
    max_rounds: int = MAX_ROUNDS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run expanding-window monthly walk-forward OOF scoring for NHL ``blocked_shots``."""
    starts = _month_starts(data_df["date"], test_from)
    oof_parts: list[pd.DataFrame] = []
    for idx, s0 in enumerate(starts):
        s1 = starts[idx + 1] if idx + 1 < len(starts) else pd.Timestamp("2100-01-01")
        s0_str, s1_str = s0.strftime("%Y-%m-%d"), s1.strftime("%Y-%m-%d")
        mask = (data_df["date"] >= s0_str) & (data_df["date"] < s1_str) & (data_df["mu_exp_toi"] > 0.0)
        if not mask.any():
            continue
        tr = data_df[data_df["date"] < s0_str]
        if len(tr[tr["mu_exp_toi"] > 0.0]) < MIN_TRAIN_ROWS:
            continue
        art = _fit_stat_models(tr, stat, features, max_rounds=max_rounds)
        te = data_df.loc[mask].copy()
        preds = predict_params(art, te)
        part = te[["game_id", "player_id", "date", "mu_exp_toi", "sigma_exp_toi", PRIOR_COL[stat], stat]].copy()
        part["pi"] = 0.0
        part["lambda_per_min"] = preds["lambda_per_min"].values
        part["alpha"] = preds["alpha"].values
        part["fold_month"] = s0_str[:7]
        oof_parts.append(part)

    if not oof_parts:
        raise RuntimeError(f"{stat}: no OOF folds could be trained from {test_from}")

    oof = pd.concat(oof_parts, ignore_index=True)
    y = oof[stat].to_numpy(float)
    mu_e = oof["mu_exp_toi"].to_numpy(float)
    sd_e = oof["sigma_exp_toi"].to_numpy(float)
    lam = oof["lambda_per_min"].to_numpy(float)
    alp = oof["alpha"].to_numpy(float)

    ll_poisson = hurdle_nb_loglik(y, 0.0, lam, 0.0, mu_e, sd_e, n_quad=DEFAULT_GH_POINTS)
    ll_plain_nb = hurdle_nb_loglik(y, 0.0, lam, alp, mu_e, sd_e, n_quad=DEFAULT_GH_POINTS)

    mu_pred = lam * mu_e
    mae = float(np.mean(np.abs(y - mu_pred)))
    metrics = {
        "n_oof": int(len(oof)),
        "n_folds": int(oof["fold_month"].nunique()),
        "ll_poisson": round(ll_poisson, 6),
        "ll_plain_nb": round(ll_plain_nb, 6),
        "nb_gain_vs_poisson": round(float(ll_plain_nb - ll_poisson), 6),
        "oof_mae": round(mae, 6),
        "mean_y": round(float(np.mean(y)), 4),
        "mean_pred": round(float(np.mean(mu_pred)), 4),
        "shipped_variant": "plain_nb",
    }
    return oof, metrics


# --------------------------------------------------------------------------- Bundle Save / Load + Canary
def _build_canary(art: dict[str, Any], df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    rows = df.sort_values(["date", "game_id", "player_id"]).tail(CANARY_ROWS).copy()
    preds = predict_params(art, rows)
    return {
        "X": rows[list(features)].astype(float).fillna(0.0).values.tolist(),
        "mu_exp_toi": rows["mu_exp_toi"].astype(float).tolist(),
        "lambda_per_min": preds["lambda_per_min"].astype(float).tolist(),
        "alpha": preds["alpha"].astype(float).tolist(),
    }


def check_canary(art: dict[str, Any], stat: str) -> None:
    c = art["meta"].get("canary")
    if not c:
        raise ValueError(f"{stat}: missing canary in meta")
    df_c = pd.DataFrame(c["X"], columns=art["meta"]["features"])
    df_c["mu_exp_toi"] = c["mu_exp_toi"]
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
        lb, ab = xgb.Booster(), xgb.Booster()
        lb.load_model(str(base / f"xgb_{stat}_lambda.json"))
        ab.load_model(str(base / f"xgb_{stat}_alpha.json"))
        art = {
            "lambda_booster": lb,
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
    """Train walk-forward OOF + final production plain-NB bundle for NHL blocked shots."""
    feat_df = build_training_features_nhl(data_dir, min_date=min_date)
    assert_nhl_feature_schema(feat_df)
    labels = load_labels_nhl(data_dir)
    merged = feat_df.merge(
        labels[["game_id", "player_id", "blocked_shots"]],
        on=["game_id", "player_id"],
        how="inner",
    )
    merged = merged[merged["n_prior_games"] >= 3].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)

    ver = version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    models: dict[str, Any] = {}
    oof_metrics: dict[str, Any] = {}
    blend_table: dict[str, Any] = {}

    for stat in STATS:
        oof_df, metrics = walk_forward_oof(merged, stat, FEATURE_COLUMNS, test_from=test_from, max_rounds=max_rounds)
        blend_info = _fit_oof_blend_c(oof_df, stat)
        art = _fit_stat_models(merged, stat, FEATURE_COLUMNS, max_rounds=max_rounds)
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
        "archetype_table": {str(k): v for k, v in NHL_ARCHETYPE_TABLE.items()},
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
    ap = argparse.ArgumentParser(description="Train MOD-04 Cluster (a) NHL blocked shots plain-NB bundle.")
    ap.add_argument("--data-dir", default="data/mod04", help="Directory containing NHL/ masters, toi_splits, shots, etc.")
    ap.add_argument("--out-dir", default="data/mod04/models/nhl", help="Local bundle output directory")
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
