"""Retrain script for the NHL SOG count model (XGBoost count:poisson + NegBin dispersion).

Artifact contract (CONTEXT s5 "Model artifact contract"): two files
    xgb_nhl_sog_v1.json            -- the booster
    model_meta_nhl_sog_v1.json     -- {version, features, feature_means, best_iteration, nb_alpha, ...}
`nb_alpha` is the NegBin dispersion (var = mu + alpha*mu^2) fit by held-out maximum likelihood; P(over) for a line is
`nhl.props.negbin.p_over_vec(line, mu, nb_alpha)`. The feature list is read from the meta, never hardcoded at the call site.

Output goes to a LOCAL directory by default. Uploading to GCS needs the explicit `--gcs` flag (not exercised in this PR).

    PYTHONPATH=. python -m nhl.props.train --source db --db /path/nhl_data.db --cutoff 2026-01-01 --out-dir /tmp/nhl_sog
    PYTHONPATH=. python -m nhl.props.train --source masters --out-dir /tmp/nhl_sog     # reads NHL/*_master.csv via mlb_core.storage
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from nhl.props.features import FEATURES, build_features
from nhl.props.negbin import fit_alpha, nb_loglik

log = logging.getLogger("nhl.props.train")

VERSION = "v1"
BOOSTER_NAME = f"xgb_nhl_sog_{VERSION}.json"
META_NAME = f"model_meta_nhl_sog_{VERSION}.json"
GCS_MODEL_PREFIX = "NHL/models"
MIN_PRIOR = 5            # appearances of history a training row must have (features are NaN-heavy before that)
VAL_FRAC = 0.12          # last 12% of dates (by time, never random) is the early-stopping + dispersion slice

PARAMS = {
    "objective": "count:poisson", "eval_metric": "poisson-nloglik", "max_depth": 3, "eta": 0.03,
    "min_child_weight": 25, "subsample": 0.8, "colsample_bytree": 0.8, "lambda": 10.0, "seed": 7, "nthread": 4,
}
MAX_ROUNDS, EARLY = 1500, 50


def training_frame(feat: pd.DataFrame, cutoff: str | None = None, min_prior: int = MIN_PRIOR) -> pd.DataFrame:
    """Rows with a realized outcome and enough history, strictly before `cutoff` (date < cutoff)."""
    d = feat[feat["sog"].notna() & (feat["n_prior"] >= min_prior)]
    if cutoff:
        d = d[d["date"] < cutoff]
    return d.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def _dm(df: pd.DataFrame, features, label=True):
    import xgboost as xgb
    X = df[features].apply(pd.to_numeric, errors="coerce").astype(float)
    return xgb.DMatrix(X, label=df["sog"].astype(float) if label else None, feature_names=list(features))


def fit(feat: pd.DataFrame, cutoff: str | None = None, features=None, params=None, verbose: bool = False) -> dict:
    """Train on date < cutoff. -> artifact dict {booster, meta}. Early stopping and dispersion use a time-ordered
    validation slice; the final booster is refit on ALL pre-cutoff rows for `best_iteration` rounds (repo convention)."""
    import xgboost as xgb
    features = list(features or FEATURES)
    params = {**PARAMS, **(params or {})}
    tr = training_frame(feat, cutoff)
    if len(tr) < 500:
        raise RuntimeError(f"only {len(tr)} training rows before {cutoff}; refusing to fit")
    dates = np.sort(tr["date"].unique())
    split = dates[int(len(dates) * (1 - VAL_FRAC))]
    a, v = tr[tr["date"] < split], tr[tr["date"] >= split]
    b = xgb.train(params, _dm(a, features), MAX_ROUNDS, evals=[(_dm(v, features), "val")],
                  early_stopping_rounds=EARLY, verbose_eval=verbose)
    best = int(b.best_iteration) + 1
    mu_v = b.predict(_dm(v, features, label=False), iteration_range=(0, best))
    alpha = fit_alpha(v["sog"].values, mu_v)
    final = xgb.train(params, _dm(tr, features), best)
    means = {f: float(pd.to_numeric(tr[f], errors="coerce").mean()) for f in features}
    naive = np.full(len(v), a["sog"].mean())
    meta = {
        "version": VERSION, "system": "NHL_SOG", "features": features, "feature_means": means,
        "best_iteration": best, "nb_alpha": alpha,
        "train_start": str(tr["date"].min()), "train_end": str(tr["date"].max()), "cutoff": cutoff,
        "n_train": int(len(tr)), "n_val": int(len(v)),
        "val_nb_loglik": nb_loglik(v["sog"].values, mu_v, alpha),
        "val_nb_loglik_constant_mean": nb_loglik(v["sog"].values, naive, fit_alpha(v["sog"].values, naive)),
        "val_mae": float(np.abs(v["sog"].values - mu_v).mean()),
        "auc_oos": None,           # count model: no AUC; kept so generic tooling reading the contract does not KeyError
        "params": {k: params[k] for k in ("max_depth", "eta", "min_child_weight", "subsample", "lambda")},
        "status": "LOG_ONLY -- see docs/analysis/nhl_sog_validation_2026-10-14.md",
    }
    return {"booster": final, "meta": meta}


def predict_mu(artifact: dict, feat: pd.DataFrame) -> np.ndarray:
    """Expected SOG per row. Missing/NaN features fall back to the training means recorded in the meta (the contract:
    never silently feed NaN into a model that was trained mostly without them)."""
    meta = artifact["meta"]
    X = feat.copy()
    for f in meta["features"]:
        if f not in X.columns:
            X[f] = np.nan
        X[f] = pd.to_numeric(X[f], errors="coerce").fillna(meta["feature_means"][f])
    return artifact["booster"].predict(_dm(X, meta["features"], label=False),
                                       iteration_range=(0, int(meta["best_iteration"])))


def save(artifact: dict, out_dir: str) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    bp, mp = d / BOOSTER_NAME, d / META_NAME
    artifact["booster"].save_model(str(bp))
    mp.write_text(json.dumps(artifact["meta"], indent=2))
    return bp, mp


def load(model_dir: str) -> dict:
    import xgboost as xgb
    d = Path(model_dir)
    b = xgb.Booster()
    b.load_model(str(d / BOOSTER_NAME))
    return {"booster": b, "meta": json.loads((d / META_NAME).read_text())}


def upload_gcs(out_dir: str) -> None:                                  # explicit --gcs only; never called implicitly
    from mlb_core import storage
    for name in (BOOSTER_NAME, META_NAME):
        storage.write_bytes((Path(out_dir) / name).read_bytes(), f"{GCS_MODEL_PREFIX}/{name}")


def load_gamelog(source: str, db: str | None = None) -> pd.DataFrame:
    from nhl.props import gamelog
    if source == "db":
        return gamelog.from_history_db(db)
    from mlb_core import storage
    from nhl.config import GAMES_MASTER, SKATER_BOX_MASTER
    games = storage.read_csv(GAMES_MASTER)
    sk = storage.read_csv(SKATER_BOX_MASTER)
    shots = _read_shots()
    return gamelog.from_masters(games, sk, shots)


def _read_shots() -> pd.DataFrame | None:
    from mlb_core import storage
    frames = []
    for y in range(2019, 2027):
        key = f"NHL/shots/shots_{y}.csv"
        if storage.exists(key):
            frames.append(storage.read_csv(key))
    return pd.concat(frames, ignore_index=True) if frames else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["masters", "db"], default="masters")
    ap.add_argument("--db", help="path to nhl_data.db (source=db)")
    ap.add_argument("--cutoff", default=None, help="train on date < cutoff (default: all history)")
    ap.add_argument("--out-dir", default=os.path.join("local_artifacts", "nhl_sog"))
    ap.add_argument("--gcs", action="store_true", help="ALSO upload to gs://.../NHL/models (explicit opt-in)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    feat = build_features(load_gamelog(a.source, a.db))
    art = fit(feat, a.cutoff)
    bp, mp = save(art, a.out_dir)
    log.info("saved %s %s  n_train=%d nb_alpha=%.3f best_iter=%d", bp, mp, art["meta"]["n_train"],
             art["meta"]["nb_alpha"], art["meta"]["best_iteration"])
    if a.gcs:
        upload_gcs(a.out_dir)
        log.info("uploaded to %s", GCS_MODEL_PREFIX)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
