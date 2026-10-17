"""Generic XGBoost count model (count:poisson) + NegBin dispersion with TIME-ORDERED fitting and walk-forward scoring.

nhl/props/train.py is the SOG-specific twin of this module (target hard-coded to `sog`); it is left untouched. Here the
target is a parameter, so one code path serves goalie saves and skater goals / assists / points.

    fit(feat, target, features, cutoff)          train on date < cutoff, early stop + alpha on the last VAL_FRAC of dates
    predict_mu(artifact, feat)                   expected count per row (NaN features -> training means, the repo contract)
    walk_forward(feat, target, features, ...)    every row on/after `test_from` gets a mu from a model that never saw its
                                                 date: retrained every `step_months` months on date < the period's first day
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from nhl.props.negbin import fit_alpha, nb_loglik

VAL_FRAC = 0.12
MAX_ROUNDS, EARLY = 1500, 50
MIN_TRAIN_ROWS = 800
PARAMS = {
    "objective": "count:poisson", "eval_metric": "poisson-nloglik", "tree_method": "hist", "max_depth": 3, "eta": 0.03,
    "min_child_weight": 25, "subsample": 0.8, "colsample_bytree": 0.8, "lambda": 10.0, "seed": 7, "nthread": 4,
}


def _dm(df: pd.DataFrame, features, target: str | None):
    import xgboost as xgb
    X = df[list(features)].apply(pd.to_numeric, errors="coerce").astype(float)
    y = df[target].astype(float) if target else None
    return xgb.DMatrix(X, label=y, feature_names=list(features))


def fit(feat: pd.DataFrame, target: str, features, cutoff: str | None = None, params=None, min_prior: int = 5,
        prior_col: str | None = None) -> dict:
    """Train on rows with a realized `target` and date < cutoff. `prior_col` (optional, e.g. 'n_prior' / 'g_n_prior') drops
    rows with fewer than `min_prior` earlier appearances (their rolling features are mostly NaN)."""
    import xgboost as xgb
    features = list(features)
    params = {**PARAMS, **(params or {})}
    tr = feat[feat[target].notna()]
    if prior_col:
        tr = tr[tr[prior_col] >= min_prior]
    if cutoff:
        tr = tr[tr["date"] < cutoff]
    tr = tr.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
    if len(tr) < MIN_TRAIN_ROWS:
        raise RuntimeError(f"only {len(tr)} training rows before {cutoff}")
    dates = np.sort(tr["date"].unique())
    split = dates[int(len(dates) * (1 - VAL_FRAC))]
    a, v = tr[tr["date"] < split], tr[tr["date"] >= split]
    b = xgb.train(params, _dm(a, features, target), MAX_ROUNDS, evals=[(_dm(v, features, target), "val")],
                  early_stopping_rounds=EARLY, verbose_eval=False)
    best = int(b.best_iteration) + 1
    mu_v = b.predict(_dm(v, features, None), iteration_range=(0, best))
    alpha = fit_alpha(v[target].values, mu_v)
    final = xgb.train(params, _dm(tr, features, target), best)
    naive = np.full(len(v), a[target].mean())
    meta = {
        "target": target, "features": features,
        "feature_means": {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in features},
        "best_iteration": best, "nb_alpha": alpha, "cutoff": cutoff, "train_start": str(tr["date"].min()),
        "train_end": str(tr["date"].max()), "n_train": int(len(tr)), "n_val": int(len(v)),
        "val_nb_loglik": nb_loglik(v[target].values, mu_v, alpha),
        "val_nb_loglik_constant_mean": nb_loglik(v[target].values, naive, fit_alpha(v[target].values, naive)),
        "val_mae": float(np.abs(v[target].values - mu_v).mean()), "auc_oos": None,
    }
    return {"booster": final, "meta": meta}


def predict_mu(artifact: dict, feat: pd.DataFrame) -> np.ndarray:
    meta = artifact["meta"]
    X = feat.copy()
    for f in meta["features"]:
        if f not in X.columns:
            X[f] = np.nan
        X[f] = pd.to_numeric(X[f], errors="coerce").fillna(meta["feature_means"][f])
    return artifact["booster"].predict(_dm(X, meta["features"], None), iteration_range=(0, int(meta["best_iteration"])))


def _period_starts(dates: pd.Series, test_from: str, step_months: int) -> list:
    d = pd.to_datetime(dates)
    lo = pd.Timestamp(test_from)
    starts, cur = [], pd.Timestamp(year=lo.year, month=lo.month, day=1)
    end = d.max()
    while cur <= end:
        starts.append(cur)
        cur = cur + pd.DateOffset(months=step_months)
    return starts


def walk_forward(feat: pd.DataFrame, target: str, features, test_from: str, step_months: int = 3, params=None,
                 min_prior: int = 5, prior_col: str | None = None) -> pd.DataFrame:
    """Score every row with date >= test_from. Period p covers [start_p, start_{p+1}); its model is fit on date < start_p.
    -> frame (game_id, player_id, date, mu, alpha, period_start). Rows with no usable model (too little history) are
    omitted, never filled."""
    starts = _period_starts(feat["date"], test_from, step_months)
    out = []
    for i, s0 in enumerate(starts):
        s1 = starts[i + 1] if i + 1 < len(starts) else pd.Timestamp("2100-01-01")
        m = (feat["date"] >= s0.strftime("%Y-%m-%d")) & (feat["date"] < s1.strftime("%Y-%m-%d"))
        if not m.any():
            continue
        try:
            art = fit(feat, target, features, cutoff=s0.strftime("%Y-%m-%d"), params=params, min_prior=min_prior,
                      prior_col=prior_col)
        except RuntimeError:
            continue
        part = feat.loc[m, ["game_id", "player_id", "date"]].copy()
        part["mu"] = predict_mu(art, feat.loc[m])
        part["alpha"] = art["meta"]["nb_alpha"]
        part["period_start"] = s0.strftime("%Y-%m-%d")
        out.append(part)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["game_id", "player_id", "date", "mu", "alpha", "period_start"])


def save(artifact: dict, out_dir: str, name: str) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    bp, mp = d / f"xgb_{name}.json", d / f"model_meta_{name}.json"
    artifact["booster"].save_model(str(bp))
    mp.write_text(json.dumps(artifact["meta"], indent=2))
    return bp, mp
