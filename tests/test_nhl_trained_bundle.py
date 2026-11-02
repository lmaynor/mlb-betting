"""nhl.props.trained: train -> save -> load -> predict round trip, and the guards that stop a stale bundle scoring live rows."""
import json

import numpy as np
import pandas as pd
import pytest

from nhl.props import countmodel as CM
from nhl.props import trained as T
from nhl.props import xgfeat as X


def synth_frames(n=1200, seed=5):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=60).strftime("%Y-%m-%d")
    base = {"game_id": np.arange(n), "player_id": rng.integers(1, 80, n), "date": rng.choice(dates, n),
            "team": "AAA", "opp": "BBB", "name": "x", "team_id": 1, "opp_id": 2, "season_year": 2025, "game_type": 2}
    sv = pd.DataFrame(base)
    for f in X.SAVES_FEATURES:
        sv[f] = rng.normal(size=n)
    sv["g_n_prior"] = 20.0
    sv["saves"] = rng.poisson(np.exp(3.2 + 0.1 * sv["g_sa_l10"].clip(-2, 2))).astype(float)
    sv["shots_against"] = sv["saves"] + 3
    sv["toi_sec"] = 3600.0
    sk = pd.DataFrame(base)
    sk["position"] = "C"
    for f in X.SKATER_FEATURES:
        sk[f] = rng.normal(size=n)
    sk["n_prior"] = 20.0
    lam = np.exp(-1.6 + 0.2 * sk["toi_l10"].clip(-2, 2))
    sk["goals"] = rng.poisson(lam).astype(float)
    sk["assists"] = rng.poisson(lam * 1.4).astype(float)
    sk["points"] = sk["goals"] + sk["assists"]
    sk["toi_sec"] = 1000.0
    return {"saves": sv, "skater": sk}


@pytest.fixture(scope="module")
def trained():
    frames = synth_frames()
    return frames, T.train_bundle(frames, params={"eta": 0.3, "nthread": 2})


def mem_store():
    d = {}
    return d, (lambda k, b: d.__setitem__(k, bytes(b))), (lambda k: d[k])


def test_bundle_has_the_four_markets_with_the_chosen_feature_sets(trained):
    _, b = trained
    assert set(b["models"]) == {"saves", "goals", "assists", "points"}
    assert b["models"]["saves"]["meta"]["features"] == X.SAVES_FEATURES_NOXG       # no xG-derived input for saves
    assert b["models"]["goals"]["meta"]["features"] == X.SKATER_FEATURES
    assert b["manifest"]["schema"] == T.SCHEMA and b["manifest"]["stats"] == list(b["models"])


def test_round_trip_predictions_are_identical(trained):
    frames, b = trained
    d, write, read = mem_store()
    v = T.save_bundle(b, write)
    assert f"{T.PREFIX}/latest.json" in d and json.loads(d[f"{T.PREFIX}/latest.json"])["version"] == v
    loaded = T.load_bundle(read)
    assert loaded["manifest"]["version"] == v
    for stat in b["models"]:
        f = frames[T.SPECS[stat]["frame"]].head(200)
        np.testing.assert_allclose(CM.predict_mu(loaded["models"][stat], f), CM.predict_mu(b["models"][stat], f), rtol=1e-6)
        assert loaded["models"][stat]["meta"]["nb_alpha"] == b["models"][stat]["meta"]["nb_alpha"]


def test_predict_live_rows_and_nan_contract(trained):
    frames, b = trained
    live = {"saves": frames["saves"].head(5).copy(), "skater": frames["skater"].head(7).copy()}
    live["skater"].loc[live["skater"].index[0], "toi_l10"] = np.nan                  # a NaN feature -> the training mean
    out = T.predict_live(b, live)
    assert set(out["stat"]) == {"saves", "goals", "assists", "points"} and len(out) == 5 + 3 * 7
    assert out["mu"].notna().all() and (out["mu"] > 0).all() and (out["alpha"] >= 0).all()
    assert list(out.columns) == ["game_id", "player_id", "name", "team", "opp", "date", "stat", "mu", "alpha"]
    empty = T.predict_live(b, {"saves": pd.DataFrame(), "skater": pd.DataFrame()})
    assert empty.empty


def test_points_mu_is_exactly_goals_plus_assists(trained):
    frames, b = trained
    out = T.predict_live(b, {"saves": pd.DataFrame(), "skater": frames["skater"].head(20).copy()})
    w = out.pivot_table(index=["game_id", "player_id"], columns="stat", values="mu")
    np.testing.assert_allclose(w["points"], w["goals"] + w["assists"], rtol=0, atol=1e-12)
    own = CM.predict_mu(b["models"]["points"], frames["skater"].head(20))
    assert not np.allclose(own, w["points"].to_numpy())                       # it really replaced the model's own mu
    lone = out[out["stat"] != "goals"]                                        # no goals row for the pair: points keeps its own mu
    assert np.allclose(T.coherent_points(lone).query("stat == 'points'")["mu"], lone.query("stat == 'points'")["mu"])


def test_loader_refuses_stale_or_mismatched_bundles(trained):
    _, b = trained
    d, write, read = mem_store()
    v = T.save_bundle(b, write)
    man = json.loads(d[f"{T.PREFIX}/{v}/manifest.json"])
    man["schema"] = T.SCHEMA + 1
    d[f"{T.PREFIX}/{v}/manifest.json"] = json.dumps(man).encode()
    with pytest.raises(ValueError, match="schema"):
        T.load_bundle(read)
    man["schema"] = T.SCHEMA
    d[f"{T.PREFIX}/{v}/manifest.json"] = json.dumps(man).encode()
    meta = json.loads(d[f"{T.PREFIX}/{v}/meta_goals.json"])
    meta["features"] = meta["features"][:-1]                                          # the feature set changed since training
    d[f"{T.PREFIX}/{v}/meta_goals.json"] = json.dumps(meta).encode()
    with pytest.raises(ValueError, match="features differ"):
        T.load_bundle(read)


def test_save_without_publish_leaves_latest_alone(trained):
    _, b = trained
    d, write, _ = mem_store()
    T.save_bundle(b, write, publish=False)
    assert f"{T.PREFIX}/latest.json" not in d


def test_loader_refuses_a_bundle_whose_canary_does_not_reproduce(trained):
    """The cross-version failure mode: the booster loads fine but predicts different numbers."""
    _, b = trained
    d, write, read = mem_store()
    v = T.save_bundle(b, write)
    T.load_bundle(read)                                                        # control: an untouched bundle loads
    meta = json.loads(d[f"{T.PREFIX}/{v}/meta_saves.json"])
    meta["canary"]["mu"] = [m * 1.5 for m in meta["canary"]["mu"]]               # what a mis-parsed base_score looks like
    d[f"{T.PREFIX}/{v}/meta_saves.json"] = json.dumps(meta).encode()
    with pytest.raises(ValueError, match="canary"):
        T.load_bundle(read)
    meta.pop("canary")
    d[f"{T.PREFIX}/{v}/meta_saves.json"] = json.dumps(meta).encode()
    with pytest.raises(ValueError, match="no canary"):
        T.load_bundle(read)


def test_artifacts_record_the_xgboost_version_and_a_canary(trained):
    _, b = trained
    for stat, art in b["models"].items():
        m = art["meta"]
        assert m["xgboost"] and len(m["canary"]["mu"]) == T.CANARY_ROWS and len(m["canary"]["X"][0]) == len(m["features"])
