"""NHL xG: shot preparation, populations, previous-attempt context, walk-forward leak safety, aggregate identities."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("xgboost")

from nhl.xg import aggregate as A
from nhl.xg import features as F
from nhl.xg import model as M

FAST = {"eta": 0.3, "max_depth": 3, "min_child_weight": 5, "nthread": 2}


def synth_shots(seasons=(2021, 2022, 2023, 2024), per_season=4000, seed=1):
    """Shots with a goal probability that falls with distance. Two teams (ids 1, 2), goalies 91 / 92."""
    rng = np.random.default_rng(seed)
    rows = []
    for si, season in enumerate(seasons):
        for i in range(per_season):
            gid = season * 1000000 + 20000 + i // 40
            eid = 10 + (i % 40)
            dist = float(rng.uniform(4, 60))
            angle = float(rng.uniform(0, 70))
            team = 1 if rng.random() < 0.5 else 2
            u = rng.random()
            ev = "block" if u < 0.2 else "miss" if u < 0.45 else "sog"
            if ev == "sog" and rng.random() < 0.25 * np.exp(-dist / 25.0) * 2.0:
                ev = "goal"
            rows.append({"game_id": gid, "event_id": eid, "period": 1 + (i % 3), "period_type": "REG", "time_sec": int(rng.integers(0, 1200)),
                         "event": ev, "team_id": team, "is_home": team == 1, "shooter_id": 1000 + (i % 30), "goalie_id": 91 if team == 2 else 92,
                         "x": 90.0 - dist, "y": 0.0, "dist_ft": dist, "angle_deg": angle, "shot_type": "wrist", "skaters_for": 5,
                         "skaters_against": 5, "empty_net": False})
    return pd.DataFrame(rows)


def test_prepare_populations_exclude_empty_net_shootout_preseason_and_blocks():
    s = synth_shots(seasons=(2022,), per_season=200)
    s["empty_net"] = s["empty_net"].astype(object)
    s.loc[0, "empty_net"] = "True"                               # CSV round-trip string
    s.loc[1, "period_type"] = "SO"
    pre = s.iloc[:5].copy()
    pre["game_id"] = 2022010001                                  # game type 01 = preseason
    pre["event_id"] = range(5)
    p = F.prepare_shots(pd.concat([s, pre], ignore_index=True))
    assert not p.loc[p["empty_net"], "in_attempt"].any()
    assert not p.loc[p["period_type"] == "SO", "in_attempt"].any()
    assert not p.loc[p["gtype"] == 1, "in_attempt"].any()
    assert not p.loc[p["event"] == "block", "in_attempt"].any() and not p.loc[p["event"] == "miss", "in_sog"].any()
    assert (p.loc[p["in_sog"], "on_target"] == 1).all()
    assert set(p.loc[p["event"] == "goal", "goal"]) == {1}


def test_previous_attempt_context_is_within_game_and_period_and_flags_rebounds():
    rows = [
        {"game_id": 2022020001, "event_id": 1, "period": 1, "time_sec": 100, "event": "sog", "team_id": 1},
        {"game_id": 2022020001, "event_id": 2, "period": 1, "time_sec": 102, "event": "goal", "team_id": 1},   # rebound: same team, 2s
        {"game_id": 2022020001, "event_id": 3, "period": 1, "time_sec": 150, "event": "sog", "team_id": 2},    # other team, 48s
        {"game_id": 2022020001, "event_id": 4, "period": 2, "time_sec": 5, "event": "sog", "team_id": 2},      # new period
        {"game_id": 2022020002, "event_id": 1, "period": 1, "time_sec": 9, "event": "sog", "team_id": 1},      # new game
    ]
    base = {"period_type": "REG", "is_home": True, "shooter_id": 7, "goalie_id": 9, "x": 60.0, "y": 5.0, "dist_ft": 30.0,
            "angle_deg": 10.0, "shot_type": "snap", "skaters_for": 5, "skaters_against": 5, "empty_net": False}
    p = F.prepare_shots(pd.DataFrame([{**base, **r} for r in rows])).set_index(["game_id", "event_id"])
    g1 = p.loc[2022020001]
    assert g1.loc[2, "rebound"] == 1.0 and g1.loc[2, "dt_prev"] == 2.0 and g1.loc[2, "prev_same_team"] == 1.0
    assert g1.loc[3, "rebound"] == 0.0 and g1.loc[3, "prev_same_team"] == 0.0 and g1.loc[3, "prev_is_goal"] == 1.0
    assert np.isnan(g1.loc[4, "dt_prev"]) and np.isnan(g1.loc[4, "prev_is_sog"])        # period boundary
    assert np.isnan(p.loc[(2022020002, 1), "dt_prev"])                                  # game boundary
    assert np.isnan(g1.loc[1, "dt_prev"])


def test_walk_forward_season_prediction_never_sees_its_own_outcomes():
    shots = synth_shots()
    prep = F.prepare_shots(shots)
    base = M.walk_forward(prep, "attempt", params=FAST)
    # corrupt the LAST season's goal labels completely: its out-of-season xG must not move (model trained on < 2024 only)
    bad = prep.copy()
    last = bad["season"] == 2024
    bad.loc[last, "goal"] = 1 - bad.loc[last, "goal"]
    alt = M.walk_forward(bad, "attempt", params=FAST)
    idx = base.index[base["xg_src"] == "oos"]
    assert (base.loc[idx, "xg_src"] == "oos").all()
    s24 = prep.index[(prep["season"] == 2024) & prep["in_attempt"]]
    np.testing.assert_allclose(base.loc[s24, "xg"].to_numpy(), alt.loc[s24, "xg"].to_numpy())
    # but an EARLIER season's labels do feed later seasons (so the test is not vacuous)
    bad2 = prep.copy()
    m21 = bad2["season"] == 2021
    bad2.loc[m21, "goal"] = 1 - bad2.loc[m21, "goal"]
    alt2 = M.walk_forward(bad2, "attempt", params=FAST)
    assert not np.allclose(base.loc[s24, "xg"].to_numpy(), alt2.loc[s24, "xg"].to_numpy())
    # warm-up seasons are labelled, never presented as out-of-season
    assert set(base["xg_src"]) == {"warmup", "oos"}
    assert set(base.loc[prep.index[prep["season"].isin([2021, 2022]) & prep["in_attempt"]], "xg_src"]) == {"warmup"}


def test_xg_is_monotone_in_distance_on_synthetic_truth():
    prep = F.prepare_shots(synth_shots())
    art = M.fit(prep, "attempt", [2021, 2022, 2023], val_season=2024, params=FAST)
    probe = prep[prep["in_attempt"]].iloc[:2000].copy()
    near, far = probe.copy(), probe.copy()
    near["dist_ft"], far["dist_ft"] = 8.0, 55.0
    assert M.predict(art, near).mean() > M.predict(art, far).mean() * 1.5
    p = M.predict(art, probe)
    assert ((p > 0) & (p < 1)).all()


def test_artifact_roundtrip(tmp_path):
    prep = F.prepare_shots(synth_shots(per_season=2500))
    art = M.fit(prep, "sog", [2021, 2022, 2023], val_season=2024, params=FAST)
    M.save(art, str(tmp_path))
    back = M.load(str(tmp_path), "sog")
    probe = prep[prep["in_sog"]].iloc[:500]
    np.testing.assert_allclose(M.predict(art, probe), M.predict(back, probe), rtol=1e-6)
    assert back["meta"]["features"] == F.FEATURES and back["meta"]["population"] == "sog"


def test_aggregates_conserve_expected_goals_and_pair_teams():
    prep = F.prepare_shots(synth_shots(seasons=(2022,), per_season=1200))
    rng = np.random.default_rng(3)
    xa = pd.Series(rng.uniform(0.01, 0.3, int(prep["in_attempt"].sum())), index=prep.index[prep["in_attempt"]])
    xs = pd.Series(rng.uniform(0.01, 0.4, int(prep["in_sog"].sum())), index=prep.index[prep["in_sog"]])
    s = A.attach_xg(prep, xa, xs)
    sk, gl, tm = A.skater_game(s), A.goalie_game(s), A.team_game(s)
    assert np.isclose(sk["ixg"].sum(), xa.sum()) and np.isclose(gl["xga"].sum(), xs.sum())
    assert gl["shots_faced"].sum() == int(prep["in_sog"].sum())
    assert (gl["gsax"] == gl["xga"] - gl["ga_pop"]).all()
    # team table: each team's xgf is its opponent's xga, goals pair up
    j = tm.merge(tm[["game_id", "team_id", "xgf", "goals_for_pop"]].rename(columns={"team_id": "opp_id", "xgf": "opp_xgf", "goals_for_pop": "opp_g"}),
                 on=["game_id", "opp_id"])
    np.testing.assert_allclose(j["xga"], j["opp_xgf"])
    assert (j["goals_against_pop"] == j["opp_g"]).all()
