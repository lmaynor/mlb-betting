"""Servable trained NHL prop models: train, serialise, load, predict.

The research models (nhl.props.countmodel: XGBoost count:poisson + NegBin dispersion; features from nhl.props.xgfeat) used to live
only as walk-forward fits in a research run -- nothing a server could load. This module is the missing middle:

    train_bundle(frames)            fit ONE final model per stat on every row in the frames (early stopping + alpha on the last
                                    12% of dates, exactly countmodel.fit with no cutoff)
    save_bundle / load_bundle       booster JSON + meta JSON per stat + a manifest, through caller-supplied byte readers /
                                    writers (GCS via mlb_core.storage in production, a dict in tests)
    predict_live(bundle, live)      mu / alpha per row of nhl.props.live_features.build_live output (points mu = goals + assists)

What is served (chosen by docs/analysis/nhl_trained_serving_2026-11-01.md): saves = the NO-xG feature set (matched the xG set on
the market comparison and needs no xG input to its own features), goals / assists / points = the xG feature set trained with the
EXPECTED opposing starter (the only starter a live scorer can know; it kept the research model's blend coefficient).

Every artifact carries its feature list, training means (the NaN contract), best iteration, alpha, training window, the xgboost
version it was written with and a CANARY (real training rows + the model's own predictions); load_bundle refuses a bundle whose
manifest or feature list does not match this code or whose canary does not reproduce (cross-version xgboost skew).
TRAIN IN THE SERVING ENVIRONMENT (the image's pinned xgboost / pandas): the xG tables the features come from are themselves
xgboost outputs, so a bundle trained elsewhere also sees slightly different inputs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from nhl.props import countmodel as CM
from nhl.props import xgfeat as X

PREFIX = "Models/nhl_props"
SCHEMA = 1
SPECS = {
    "saves": {"frame": "saves", "features": X.SAVES_FEATURES_NOXG, "target": "saves", "prior": "g_n_prior"},
    "goals": {"frame": "skater", "features": X.SKATER_FEATURES, "target": "goals", "prior": "n_prior"},
    "assists": {"frame": "skater", "features": X.SKATER_FEATURES, "target": "assists", "prior": "n_prior"},
    "points": {"frame": "skater", "features": X.SKATER_FEATURES, "target": "points", "prior": "n_prior"},
}


def _xgb_version() -> str:
    import xgboost
    return xgboost.__version__


CANARY_ROWS = 8
CANARY_RTOL = 1e-4


def _canary(art: dict, frame: pd.DataFrame, features: list) -> dict:
    """A few REAL training rows and the model's own predictions on them, stored in the artifact. A loader that gets different
    numbers back (a different xgboost version parses / applies the booster differently) must refuse the bundle: found 2026-11-01,
    a booster trained with xgboost 3.4.1 loaded under the image's pinned 2.1.4 and predicted up to 28 saves away."""
    rows = frame.dropna(subset=features).sort_values(["date", "game_id", "player_id"]).tail(CANARY_ROWS)
    X = rows[features].astype(float)
    return {"X": X.values.tolist(), "mu": [float(v) for v in CM.predict_mu(art, X)]}


def check_canary(art: dict, stat: str) -> None:
    c = art["meta"].get("canary")
    if not c:
        raise ValueError(f"{stat}: bundle has no canary (written by an older trainer); retrain")
    X = pd.DataFrame(c["X"], columns=art["meta"]["features"])
    got, want = CM.predict_mu(art, X), np.asarray(c["mu"], dtype=float)
    if not np.allclose(got, want, rtol=CANARY_RTOL, atol=1e-9):
        raise ValueError(f"{stat}: loaded model disagrees with its own canary (max rel diff "
                         f"{float(np.max(np.abs(got - want) / np.maximum(np.abs(want), 1e-9))):.3g}); the bundle was trained under "
                         f"xgboost {art['meta'].get('xgboost')} and this process runs {_xgb_version()}")


def train_bundle(frames: dict, stats=tuple(SPECS), params: dict | None = None) -> dict:
    """frames = {"saves": saves_features(...), "skater": skater_features(..., "expected")} -> {"models": {stat: artifact}, "manifest"}.
    Trains on every row with a realized target (the caller decides how far the frames reach)."""
    models = {}
    for stat in stats:
        sp = SPECS[stat]
        art = CM.fit(frames[sp["frame"]], sp["target"], sp["features"], cutoff=None, params=params,
                     prior_col=sp["prior"])
        art["meta"]["xgboost"] = _xgb_version()
        art["meta"]["canary"] = _canary(art, frames[sp["frame"]], sp["features"])
        models[stat] = art
    end = max(m["meta"]["train_end"] for m in models.values())
    manifest = {"schema": SCHEMA, "version": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "trained_through": end, "xgboost": _xgb_version(), "stats": list(models),
                "features": {s: SPECS[s]["features"] for s in models}}
    return {"models": models, "manifest": manifest}


def save_bundle(bundle: dict, write, prefix: str = PREFIX, publish: bool = True) -> str:
    """Write `{prefix}/{version}/xgb_{stat}.json`, `meta_{stat}.json`, `manifest.json`; with publish also `{prefix}/latest.json`
    ({"version"}) so a loader needs no listing. `write(key, bytes)`. -> the version."""
    man = bundle["manifest"]
    base = f"{prefix}/{man['version']}"
    for stat, art in bundle["models"].items():
        write(f"{base}/xgb_{stat}.json", bytes(art["booster"].save_raw(raw_format="json")))
        write(f"{base}/meta_{stat}.json", json.dumps(art["meta"], indent=1).encode())
    write(f"{base}/manifest.json", json.dumps(man, indent=1).encode())
    if publish:
        write(f"{prefix}/latest.json", json.dumps({"version": man["version"]}).encode())
    return man["version"]


def load_bundle(read, prefix: str = PREFIX, version: str | None = None) -> dict:
    """`read(key) -> bytes`. version None -> the one `{prefix}/latest.json` names. Raises ValueError if the artifacts do not
    match this code's feature lists (a stale bundle must never score live rows silently)."""
    import xgboost as xgb
    version = version or json.loads(read(f"{prefix}/latest.json"))["version"]
    base = f"{prefix}/{version}"
    man = json.loads(read(f"{base}/manifest.json"))
    if man.get("schema") != SCHEMA:
        raise ValueError(f"bundle schema {man.get('schema')} != {SCHEMA}")
    models = {}
    for stat in man["stats"]:
        meta = json.loads(read(f"{base}/meta_{stat}.json"))
        if list(meta["features"]) != list(SPECS[stat]["features"]):
            raise ValueError(f"{stat}: bundle features differ from this code's feature set; retrain")
        b = xgb.Booster()
        b.load_model(bytearray(read(f"{base}/xgb_{stat}.json")))
        models[stat] = {"booster": b, "meta": meta}
        check_canary(models[stat], stat)
    return {"models": models, "manifest": man}


def predict_live(bundle: dict, live: dict) -> pd.DataFrame:
    """live = build_live(...) -> one row per (player, upcoming game, stat): game_id, player_id, name, team, opp, date, stat, mu,
    alpha. Saves rows exist for the expected starter AND backup of each team (the board picks the quoted goalie)."""
    out = []
    for stat, art in bundle["models"].items():
        f = live[SPECS[stat]["frame"]]
        if f is None or not len(f):
            continue
        d = f[["game_id", "player_id", "name", "team", "opp", "date"]].copy()
        d["stat"] = stat
        d["mu"] = CM.predict_mu(art, f)
        d["alpha"] = float(art["meta"]["nb_alpha"])
        out.append(d)
    cols = ["game_id", "player_id", "name", "team", "opp", "date", "stat", "mu", "alpha"]
    if not out:
        return pd.DataFrame(columns=cols)
    res = pd.concat(out, ignore_index=True)[cols]
    return coherent_points(res)


def coherent_points(preds: pd.DataFrame) -> pd.DataFrame:
    """points mu := goals mu + assists mu for every (game, player) that has both (the separately trained points model scored the
    same on the market: log loss 0.67355 vs 0.67348, blend c +0.188 vs +0.175), which keeps the board's exact additivity law.
    The points model's own alpha is kept (the spread of a points count). A row with no goals or assists mu keeps its own mu."""
    if preds.empty or "points" not in set(preds["stat"]):
        return preds
    k = ["game_id", "player_id"]
    g = preds[preds["stat"] == "goals"][k + ["mu"]].rename(columns={"mu": "mu_g"})
    a = preds[preds["stat"] == "assists"][k + ["mu"]].rename(columns={"mu": "mu_a"})
    m = preds.merge(g, on=k, how="left").merge(a, on=k, how="left")
    both = (m["stat"] == "points") & m["mu_g"].notna() & m["mu_a"].notna()
    m.loc[both, "mu"] = m.loc[both, "mu_g"] + m.loc[both, "mu_a"]
    return m.drop(columns=["mu_g", "mu_a"])
