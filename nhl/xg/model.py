"""XGBoost xG classifiers (binary:logistic on `goal`) + a walk-forward predictor.

Two models share one feature list (nhl.xg.features.FEATURES): population 'attempt' (unblocked attempts) and 'sog'
(on target). Fitting is ALWAYS time-ordered: train seasons < val season for early stopping, never a random split (the
nhl-xG-model archive's random 80/20 split mixes games from the same season/shooters across the split).

`walk_forward(prep, population)` gives every shot an OUT-OF-SEASON xG: season s is scored by a booster trained only on
seasons < s. The first `MIN_TRAIN_SEASONS` seasons have no earlier training data; they are scored by a model fit on those
seasons themselves (in-sample, `xg_src == 'warmup'`) and exist only so rolling features have history -- they are never used
to evaluate anything.

Artifact contract (CONTEXT s5): booster json + meta json with {version, features, feature_means, best_iteration, ...}.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from nhl.xg.features import FEATURES, population_mask

VERSION = "v1"
MIN_TRAIN_SEASONS = 2
MAX_ROUNDS, EARLY = 1200, 40
PARAMS = {
    "objective": "binary:logistic", "eval_metric": "logloss", "tree_method": "hist", "max_depth": 5, "eta": 0.05,
    "min_child_weight": 60, "subsample": 0.8, "colsample_bytree": 0.8, "lambda": 20.0, "gamma": 0.0, "seed": 11,
    "nthread": 4,
}


def booster_name(pop: str) -> str:
    return f"xgb_nhl_xg_{pop}_{VERSION}.json"


def meta_name(pop: str) -> str:
    return f"model_meta_nhl_xg_{pop}_{VERSION}.json"


def _dm(df: pd.DataFrame, features, label: bool):
    import xgboost as xgb
    X = df[list(features)].apply(pd.to_numeric, errors="coerce").astype(float)
    return xgb.DMatrix(X, label=df["goal"].astype(float) if label else None, feature_names=list(features))


def fit(prep: pd.DataFrame, population: str, train_seasons, val_season=None, features=None, params=None) -> dict:
    """Train on `train_seasons` (list of season start years). Early stopping on `val_season` when given, else on the last
    train season (then the final booster is refit on everything for best_iteration rounds). -> {booster, meta}."""
    import xgboost as xgb
    features = list(features or FEATURES)
    params = {**PARAMS, **(params or {})}
    m = population_mask(prep, population)
    train_seasons = sorted(int(x) for x in train_seasons)
    tr = prep[m & prep["season"].isin(train_seasons)]
    if len(tr) < 2000:
        raise RuntimeError(f"only {len(tr)} training shots for seasons {train_seasons}")
    if val_season is not None:
        va = prep[m & (prep["season"] == int(val_season))]
        fit_df = tr
    else:
        last = train_seasons[-1]
        va = tr[tr["season"] == last]
        fit_df = tr[tr["season"] < last] if len(train_seasons) > 1 else tr
        if len(fit_df) < 2000:
            fit_df = tr
    b = xgb.train(params, _dm(fit_df, features, True), MAX_ROUNDS, evals=[(_dm(va, features, True), "val")],
                  early_stopping_rounds=EARLY, verbose_eval=False)
    best = int(b.best_iteration) + 1
    full = tr if val_season is None else pd.concat([tr, va], ignore_index=True)
    final = xgb.train(params, _dm(full, features, True), best)
    meta = {
        "version": VERSION, "system": f"NHL_XG_{population.upper()}", "population": population, "features": features,
        "feature_means": {f: float(pd.to_numeric(full[f], errors="coerce").mean()) for f in features},
        "best_iteration": best, "train_seasons": train_seasons,
        "val_season": None if val_season is None else int(val_season), "n_train": int(len(full)),
        "goal_rate": float(full["goal"].mean()), "auc_oos": None,
        "params": {k: params[k] for k in ("max_depth", "eta", "min_child_weight", "subsample", "lambda")},
    }
    return {"booster": final, "meta": meta}


def predict(artifact: dict, prep: pd.DataFrame) -> np.ndarray:
    """P(goal) for every row of `prep` (population is the caller's concern). NaN features stay NaN: XGBoost handles
    missing values natively and the trained booster saw the same NaN pattern (previous-attempt context)."""
    meta = artifact["meta"]
    return artifact["booster"].predict(_dm(prep, meta["features"], False),
                                       iteration_range=(0, int(meta["best_iteration"])))


def walk_forward(prep: pd.DataFrame, population: str, features=None, params=None, min_train: int = MIN_TRAIN_SEASONS) -> pd.DataFrame:
    """Per-shot out-of-season xG. Returns a frame indexed like `prep` rows IN the population with columns
    xg (float) and xg_src ('oos' | 'warmup')."""
    m = population_mask(prep, population)
    seasons = sorted(prep.loc[m, "season"].unique())
    if len(seasons) <= min_train:
        raise RuntimeError(f"need more than {min_train} seasons, have {seasons}")
    out = pd.DataFrame({"xg": np.nan, "xg_src": ""}, index=prep.index[m])
    warm = seasons[:min_train]
    art = fit(prep, population, warm, features=features, params=params)
    idx = prep.index[m & prep["season"].isin(warm)]
    out.loc[idx, "xg"] = predict(art, prep.loc[idx])
    out.loc[idx, "xg_src"] = "warmup"
    for s in seasons[min_train:]:
        prior = [x for x in seasons if x < s]
        art = fit(prep, population, prior[:-1] or prior, val_season=prior[-1] if len(prior) > 1 else None,
                  features=features, params=params)
        idx = prep.index[m & (prep["season"] == s)]
        out.loc[idx, "xg"] = predict(art, prep.loc[idx])
        out.loc[idx, "xg_src"] = "oos"
    return out


def save(artifact: dict, out_dir: str) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    pop = artifact["meta"]["population"]
    bp, mp = d / booster_name(pop), d / meta_name(pop)
    artifact["booster"].save_model(str(bp))
    mp.write_text(json.dumps(artifact["meta"], indent=2))
    return bp, mp


def load(model_dir: str, population: str) -> dict:
    import xgboost as xgb
    d = Path(model_dir)
    b = xgb.Booster()
    b.load_model(str(d / booster_name(population)))
    return {"booster": b, "meta": json.loads((d / meta_name(population)).read_text())}
