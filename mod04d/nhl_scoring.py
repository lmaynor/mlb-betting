"""NHL Goals & Points shadow scoring candidate (MOD-04 Cluster d).

Covariate-dispersion Distributional NegBin GBDT mirroring `nhl/props/trained.py`:
- Mean GBDT (count:poisson, `nhl.props.countmodel.PARAMS` style)
- Dispersion GBDT for log(alpha(x)) fit on validation Pearson residuals
- Bundle train/save/load/canary pattern via caller-supplied byte readers/writers
- NEVER replaces serving trained models (`mod04_nhl_goals_shadow_v1` / `mod04_nhl_points_shadow_v1`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
import pandas as pd
import xgboost as xgb

from mod04d._xgb import (
    compute_feature_means,
    fit_mean_and_dispersion,
    predict_mu_alpha_from_art,
    prepare_X,
)
from mod04d.distributions import negbin_pmf, pmf_p_over
from mod04d.features import assert_no_leak_columns

SKATER_FEATURES_FALLBACK: list[str] = [
    "toi_l10", "toi_ewm", "ixg_p60_l20", "ixg_p60_l60", "ixg_ev_p60_l30", "ixg_pp_g_l30",
    "att_p60_l20", "att_p60_l60", "sog_p60_l30", "hd_p60_l30", "goals_p60_l60", "goals_p60_l120",
    "assists_p60_l60", "assists_p60_l120", "points_p60_l60", "points_p60_l120", "fin_ratio_l120",
    "pp_att_g_l30", "n_prior", "is_d", "is_c", "is_home", "rest_days",
    "t_xgf_l10", "t_xgf_l30", "t_goal_l30", "t_ppf_l30",
    "o_xga_l10", "o_xga_l30", "o_goala_l30", "o_ppa_l30",
    "opp_g_gsax100_l60", "opp_g_sv_l60", "opp_g_xga_shot_l30", "lg_goal_l30", "lg_xgf_l30",
]

try:
    from nhl.props.xgfeat import SKATER_FEATURES as _NHL_SKATER_FEATURES

    SKATER_FEATURES: list[str] = list(_NHL_SKATER_FEATURES)
except Exception:  # pragma: no cover - guarded fallback
    SKATER_FEATURES = list(SKATER_FEATURES_FALLBACK)

SCHEMA = 1
PREFIX = "Models/mod04d_nhl_shadow"
CANARY_ROWS = 8
CANARY_RTOL = 1e-6
ENGINES: dict[str, str] = {
    "goals": "mod04_nhl_goals_shadow_v1",
    "points": "mod04_nhl_points_shadow_v1",
}
DEFAULT_K_MAX: dict[str, int] = {"goals": 10, "points": 12}

NHL_MEAN_PARAMS: dict = {
    "objective": "count:poisson",
    "eval_metric": "poisson-nloglik",
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.03,
    "min_child_weight": 25,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 10.0,
    "seed": 7,
}


def _xgb_version() -> str:
    return str(xgb.__version__)


def _canary(art: dict, frame: pd.DataFrame, features: list[str]) -> dict:
    rows = frame.copy()
    sort_cols = [c for c in ("date", "game_id", "player_id") if c in rows.columns]
    if sort_cols:
        rows = rows.sort_values(sort_cols)
    rows = rows.tail(CANARY_ROWS)
    X = prepare_X(rows, features, art["feature_means"])
    mu, alpha = predict_mu_alpha_from_art(art, X)
    return {
        "X": X.values.tolist(),
        "mu": [float(v) for v in mu],
        "alpha": [float(v) for v in alpha],
    }


def check_canary(art: dict, stat: str) -> None:
    meta = art["meta"]
    c = meta.get("canary")
    if not c:
        raise ValueError(f"{stat}: bundle has no canary")
    X = pd.DataFrame(c["X"], columns=meta["features"])
    got_mu, got_alpha = predict_mu_alpha_from_art(art, X)
    want_mu = np.asarray(c["mu"], dtype=float)
    want_alpha = np.asarray(c["alpha"], dtype=float)
    if not np.allclose(got_mu, want_mu, rtol=CANARY_RTOL, atol=1e-9):
        raise ValueError(
            f"{stat}: loaded model mu disagrees with canary (trained under xgboost "
            f"{meta.get('xgboost')}, current {_xgb_version()})"
        )
    if not np.allclose(got_alpha, want_alpha, rtol=CANARY_RTOL, atol=1e-9):
        raise ValueError(f"{stat}: loaded dispersion model alpha disagrees with canary")


def _resolve_stat_frame(frames: dict | pd.DataFrame, stat: str) -> pd.DataFrame:
    if isinstance(frames, pd.DataFrame):
        return frames
    if stat in frames:
        return frames[stat]
    if "skater" in frames:
        return frames["skater"]
    raise KeyError(f"frames must contain '{stat}' or 'skater'")


def train_bundle(
    frames: dict | pd.DataFrame,
    stats: Sequence[str] = ("goals", "points"),
    features: Sequence[str] | None = None,
    *,
    params: dict | None = None,
    disp_params: dict | None = None,
    max_rounds: int = 200,
    disp_rounds: int = 60,
) -> dict:
    """Train covariate-dispersion NegBin GBDT bundle for NHL goals/points."""
    models: dict[str, dict] = {}
    mean_params = {**NHL_MEAN_PARAMS, **(params or {})}
    for stat in stats:
        if stat not in ENGINES:
            raise ValueError(f"Unsupported NHL stat {stat!r}; expected one of {tuple(ENGINES)}")
        df = _resolve_stat_frame(frames, stat).copy()
        assert_no_leak_columns(df)
        feat_list = list(features) if features is not None else [c for c in SKATER_FEATURES if c in df.columns]
        if not feat_list:
            raise ValueError(f"{stat}: no SKATER_FEATURES present in input frame")

        art = fit_mean_and_dispersion(
            df,
            stat,
            feat_list,
            max_rounds=max_rounds,
            disp_rounds=disp_rounds,
            params=mean_params,
            disp_params=disp_params,
        )
        d_col = df["date"].astype(str) if "date" in df.columns else pd.Series(["1970-01-01"] * len(df))
        meta = {
            "schema": SCHEMA,
            "stat": stat,
            "engine": ENGINES[stat],
            "shadow_only": True,
            "features": list(feat_list),
            "disp_features": list(art["disp_features"]),
            "feature_means": compute_feature_means(df, feat_list),
            "best_iteration": int(art["best_iteration"]),
            "disp_rounds": int(art["disp_rounds"]),
            "nb_alpha_global": float(art["global_alpha"]),
            "val_ll_disp": float(art["val_ll_disp"]),
            "val_ll_fixed": float(art["val_ll_fixed"]),
            "n_train": int(art["n_train"]),
            "n_val": int(art["n_val"]),
            "train_start": str(d_col.min()),
            "train_end": str(d_col.max()),
            "xgboost": _xgb_version(),
        }
        art["meta"] = meta
        meta["canary"] = _canary(art, df, feat_list)
        models[stat] = art

    end = max(m["meta"]["train_end"] for m in models.values())
    manifest = {
        "schema": SCHEMA,
        "shadow_only": True,
        "version": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "trained_through": end,
        "xgboost": _xgb_version(),
        "stats": list(models),
        "engines": {s: ENGINES[s] for s in models},
        "features": {s: models[s]["meta"]["features"] for s in models},
    }
    return {"models": models, "manifest": manifest}


def save_bundle(bundle: dict, write, prefix: str = PREFIX, publish: bool = True) -> str:
    """Serialize boosters + meta + manifest through caller-supplied `write(key, bytes)`."""
    man = bundle["manifest"]
    p = prefix.strip("/")
    base = f"{p}/{man['version']}" if p else man["version"]
    for stat, art in bundle["models"].items():
        write(f"{base}/xgb_{stat}.json", bytes(art["booster"].save_raw(raw_format="json")))
        write(f"{base}/xgb_{stat}_disp.json", bytes(art["disp_booster"].save_raw(raw_format="json")))
        write(f"{base}/meta_{stat}.json", json.dumps(art["meta"], indent=1).encode())
    write(f"{base}/manifest.json", json.dumps(man, indent=1).encode())
    if publish:
        latest_key = f"{p}/latest.json" if p else "latest.json"
        write(latest_key, json.dumps({"version": man["version"]}).encode())
    return man["version"]


def load_bundle(read, prefix: str = PREFIX, version: str | None = None) -> dict:
    """Deserialize bundle through caller-supplied `read(key) -> bytes` and verify canary."""
    p = prefix.strip("/")
    latest_key = f"{p}/latest.json" if p else "latest.json"
    version = version or json.loads(read(latest_key))["version"]
    base = f"{p}/{version}" if p else version
    man = json.loads(read(f"{base}/manifest.json"))
    if man.get("schema") != SCHEMA:
        raise ValueError(f"bundle schema {man.get('schema')} != {SCHEMA}")
    models: dict[str, dict] = {}
    for stat in man["stats"]:
        meta = json.loads(read(f"{base}/meta_{stat}.json"))
        b = xgb.Booster()
        b.load_model(bytearray(read(f"{base}/xgb_{stat}.json")))
        db = xgb.Booster()
        db.load_model(bytearray(read(f"{base}/xgb_{stat}_disp.json")))
        art = {
            "booster": b,
            "disp_booster": db,
            "features": meta["features"],
            "disp_features": meta["disp_features"],
            "feature_means": meta["feature_means"],
            "best_iteration": int(meta["best_iteration"]),
            "disp_rounds": int(meta["disp_rounds"]),
            "global_alpha": float(meta["nb_alpha_global"]),
            "meta": meta,
        }
        check_canary(art, stat)
        models[stat] = art
    return {"models": models, "manifest": man}


def predict(bundle_or_art: dict, df: pd.DataFrame, stat: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return `(mu, alpha)` arrays for `df` from either a full bundle (with `stat`) or a stat artifact."""
    if "models" in bundle_or_art:
        if stat is None:
            raise ValueError("stat must be specified when predicting from a bundle")
        art = bundle_or_art["models"][stat]
    else:
        art = bundle_or_art
    return predict_mu_alpha_from_art(art, df)


def predict_pmf(
    bundle_or_art: dict,
    df: pd.DataFrame,
    stat: str = "goals",
    k_max: int | None = None,
) -> np.ndarray:
    km = int(k_max or DEFAULT_K_MAX.get(stat, 12))
    mu, alpha = predict(bundle_or_art, df, stat=stat)
    return negbin_pmf(mu, alpha, k_max=km)


def predict_p_over(
    bundle_or_art: dict,
    df: pd.DataFrame,
    lines,
    stat: str = "goals",
    k_max: int | None = None,
) -> np.ndarray:
    return pmf_p_over(predict_pmf(bundle_or_art, df, stat=stat, k_max=k_max), lines)
