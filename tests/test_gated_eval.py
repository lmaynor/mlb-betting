"""Confidence-gated engine evaluation, offline: protocol statistics (planted signal / null), pre-registration guards, the
two-stage driver, and the leakage garble tests for the NFL and NBA replay loops (with injected leaks to prove the test has power)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from mlb_core.eval import garble as gb
from mlb_core.eval import gated_run as gr
from mlb_core.eval import gating as G
from mlb_core.eval import market_scoring as ms


# ---------------------------------------------------------------------------------------------------- synthetic events
def _events(n=4000, days=200, informative_when_confident=False, market_exact=False, seed=0, season=2021):
    """Spread-like events. The market is exact (market_exact) or the engine knows the truth only when it deviates a lot."""
    rng = np.random.default_rng(seed)
    p_mkt = np.clip(rng.normal(0.5, 0.08, n), 0.2, 0.8)
    e = rng.normal(0, 0.06, n)
    p_model = np.clip(p_mkt + e, 0.02, 0.98)
    if informative_when_confident:
        p_true = np.where(np.abs(e) > 0.05, p_model, p_mkt)
    else:
        p_true = p_mkt
    y = (rng.random(n) < p_true).astype(int)
    dec = 1 / p_mkt
    kal = rng.normal(0, 3, n)
    return pd.DataFrame({
        "date": [f"2021-{1 + (i % days) // 28:02d}-{1 + (i % days) % 28:02d}" for i in range(n)], "p_model": p_model,
        "p_mkt": p_mkt, "y": y, "season": season, "dec_a_med": 1.909, "dec_b_med": 1.909, "dec_a_best": 1.909,
        "dec_b_best": 1.909, "c_kal": kal, "c_feat": kal + rng.normal(0, 3, n), "c_mu": kal, "c_line": 0.0,
        "c_sd": rng.uniform(3, 6, n), "c_phase": rng.integers(1, 18, n), "c_dq": 0.0})


def test_planted_signal_is_found_by_the_edge_gate_and_survives_the_family_correction():
    ev = {"spread": _events(informative_when_confident=True, seed=1)}
    th = G.freeze_thresholds("nfl", ev)
    res = G.run_split("nfl", ev, th)
    edge, none = res[("nfl", "spread", "EDGE")], res[("nfl", "spread", "NONE")]
    assert edge["gain"] > 0 and edge["gain_z"] > 2.5 and edge["c"] > 0 and edge["c_z"] > 2.5
    assert edge["gain"] > none["gain"]                       # the gate rescues a signal the ungated book hides
    conf = G.confirm(res, G.screen_dev(res), B=500)
    assert conf["cells"][("nfl", "spread", "EDGE")]["p_maxt_all"] < 0.05 and conf["any_strict"]


def test_market_exact_world_has_no_gate_that_wins():
    ev = {"spread": _events(market_exact=True, seed=2)}
    th = G.freeze_thresholds("nfl", ev)
    res = G.run_split("nfl", ev, th)
    conf = G.confirm(res, G.screen_dev(res), B=500)
    assert not conf["any_strict"] and not conf["any_confirmed"]
    assert all(res[k]["gain"] < 0 for k in G.tried(res) if res[k].get("gain") is not None and res[k]["n"] > 500)
    assert G.verdict(conf, res) in ("NO", "INCONCLUSIVE")


def test_family_wise_error_under_the_null_is_controlled():
    hits = 0
    for s in range(30):
        ev = {"spread": _events(n=900, days=90, market_exact=True, seed=100 + s)}
        res = G.run_split("nfl", ev, G.freeze_thresholds("nfl", ev))
        hits += G.confirm(res, [], B=300)["any_strict"]
    assert hits <= 5            # nominal 5% of 30 = 1.5; allow Monte-Carlo slack


def test_thresholds_never_read_outcomes():
    ev = {"spread": _events(seed=3)}
    th1 = G.freeze_thresholds("nfl", ev)
    ev2 = {"spread": ev["spread"].assign(y=1 - ev["spread"]["y"])}
    assert G.freeze_thresholds("nfl", ev2) == th1
    ev3 = {"spread": ev["spread"].drop(columns=["y"])}
    assert G.freeze_thresholds("nfl", ev3) == th1            # works without a y column at all


def test_gate_masks_and_degenerate_dq_gate():
    ev = {"m": _events(seed=4)}
    th = G.freeze_thresholds("nfl", ev)
    mk = G.gate_masks(ev["m"], th, "m")
    cov = {k: float(v.mean()) for k, v in mk.items() if v is not None}
    assert abs(cov["EDGE"] - 0.25) < 0.02 and abs(cov["SD"] - 1 / 3) < 0.02
    assert mk["DQ"] is None                                  # c_dq all 0 -> removes no games -> degenerate, not tried
    d = G.derive_conf(ev["m"])
    assert (d["agree"].dropna().isin([0.0, 1.0])).all()
    # no second variant (total market in the NFL): AGREE is not applicable
    nf = {"t": ev["m"].assign(c_feat=np.nan)}
    assert G.gate_masks(nf["t"], G.freeze_thresholds("nfl", nf), "t")["AGREE"] is None


def test_holm_mde_and_blend_offset():
    adj = G.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    assert G.mde(0.01) == pytest.approx(0.02485, abs=1e-4)
    assert G.n_needed(1000, 0.01, 0.01) == pytest.approx(1000 * (0.02485 / 0.01) ** 2, rel=5e-3)
    rng = np.random.default_rng(5)
    n = 20000
    pm = np.clip(rng.normal(0.5, 0.1, n), 0.1, 0.9)
    pq = np.clip(pm + rng.normal(0, 0.05, n), 0.05, 0.95)
    y = (rng.random(n) < pq).astype(int)                     # the model IS the truth -> c = 1
    out = G.blend_c_offset(pd.DataFrame({"p_model": pq, "p_mkt": pm, "y": y, "date": rng.integers(0, 300, n).astype(str)}))
    assert 0.8 < out["c"] < 1.2 and out["z_c"] > 5


def test_split_must_be_chronological():
    ev = {"m": _events(seed=6)}
    with pytest.raises(ValueError):
        G.split_by_season(ev, [2022, 2023], [2023, 2024])
    with pytest.raises(ValueError):
        G.split_by_season(ev, [2024], [2021])


# ---------------------------------------------------------------------------------------------------- driver
def _nfl_records(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for season in range(2021, 2026):
        for i in range(260):
            wk = 1 + i // 15
            mkt = float(np.round(rng.normal(0, 6) * 2) / 2)
            mu = mkt + rng.normal(0, 2.5)
            tot_l = float(np.round(rng.normal(45, 3) * 2) / 2)
            margin = float(np.round(mkt + rng.normal(0, 13)))
            rows.append(dict(game_id=f"{season}_{i}", date=f"{season}-{9 + (i // 30) % 3:02d}-{1 + i % 28:02d}", season=season,
                             week=wk, home="A", away="B", mu_margin=mu, mu_total=tot_l + rng.normal(0, 2), p_home_ml=0.5,
                             p_home_cover=float(np.clip(0.5 + (mu - mkt) * 0.025, 0.05, 0.95)),
                             p_away_cover=float(np.clip(0.5 - (mu - mkt) * 0.025, 0.05, 0.95)), p_over=0.5, p_under=0.5,
                             mkt_margin=mkt, mkt_total=tot_l, margin=margin, total=float(np.round(tot_l + rng.normal(0, 13))),
                             home_ml=-150, away_ml=130, home_sp_odds=-110, away_sp_odds=-110, over_odds=-110, under_odds=-110,
                             kal_margin=mu + rng.normal(0, 2), kal_total=tot_l, feat_margin=mu + rng.normal(0, 2),
                             kal_std_margin=float(rng.uniform(3, 6)), kal_std_total=float(rng.uniform(3, 6)), n_prior_min=wk))
    return pd.DataFrame(rows)


def test_two_stage_driver_freezes_then_scores_once(tmp_path):
    rec = tmp_path / "nfl.csv"
    _nfl_records().to_csv(rec, index=False)
    out = str(tmp_path / "o")
    fz = gr.run_dev({"nfl": str(rec)}, out)
    assert fz["spec_version"] == G.SPEC_VERSION and fz["dev_cells_tried"] > 0
    assert json.load(open(f"{out}/frozen.json"))["frozen_sha"] == fz["frozen_sha"]
    res = gr.run_held({"nfl": str(rec)}, out, B=200)
    assert res["verdict"] in ("YES", "NO", "INCONCLUSIVE")
    with pytest.raises(SystemExit):                          # a second look at held-out is refused
        gr.run_held({"nfl": str(rec)}, out, B=200)
    gr.run_held({"nfl": str(rec)}, out, allow_rescore=True, B=200)
    assert "RE-SCORED" in open(f"{out}/held_report.md").read()
    fz2 = json.load(open(f"{out}/frozen.json"))
    fz2["survivors"] = []                                    # tampering after the freeze is refused
    json.dump(fz2, open(f"{out}/frozen.json", "w"))
    with pytest.raises(SystemExit):
        gr.run_held({"nfl": str(rec)}, out, allow_rescore=True, B=200)


def test_held_refuses_without_a_freeze(tmp_path):
    rec = tmp_path / "nfl.csv"
    _nfl_records().to_csv(rec, index=False)
    with pytest.raises(SystemExit):
        gr.run_held({"nfl": str(rec)}, str(tmp_path / "none"))


# ---------------------------------------------------------------------------------------------------- garble: primitives
def test_garble_primitives():
    df = pd.DataFrame({"a": np.arange(10.0), "k": list("abcdefghij")})
    g = gb.garble_columns(df, ["a"], [False] * 4 + [True] * 6, seed=1)
    assert g["a"].iloc[:4].tolist() == [0, 1, 2, 3] and sorted(g["a"].iloc[4:]) == [4, 5, 6, 7, 8, 9]
    assert g["a"].iloc[4:].tolist() != [4, 5, 6, 7, 8, 9] and g["k"].tolist() == df["k"].tolist()
    nn = pd.DataFrame({"a": [1.0, np.nan, 3.0, 4.0, np.nan, 6.0]})
    gn = gb.garble_columns(nn, ["a"], [True] * 6, seed=2)
    assert gn["a"].isna().tolist() == nn["a"].isna().tolist()          # missing stays missing: no game is dropped by the garble
    a = pd.DataFrame({"id": [1, 2, 3], "d": ["2024-01-01", "2024-01-02", "2024-01-03"], "x": [1.0, np.nan, 3.0]})
    b = a.assign(x=[1.0, np.nan, 9.0])
    assert len(gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02")) == 0       # NaN == NaN, change is after the cut-off
    assert len(gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02", after=True)) == 1


# ---------------------------------------------------------------------------------------------------- garble: NFL
def _nfl_fixture(seed=0):
    rng = np.random.default_rng(seed)
    teams = ["KC", "BAL", "BUF", "MIA", "DAL", "PHI", "SF", "SEA"]
    sched, pbp = [], []
    for wk in range(1, 9):
        order = rng.permutation(teams)
        for gi in range(4):
            home, away = order[2 * gi], order[2 * gi + 1]
            gid = f"2023_{wk:02d}_{away}_{home}"
            day = (pd.Timestamp("2023-09-07") + pd.Timedelta(days=7 * (wk - 1) + gi)).date().isoformat()
            hs, as_ = int(rng.integers(10, 38)), int(rng.integers(10, 38))
            sched.append(dict(game_id=gid, season=2023, week=wk, gameday=day, game_type="REG", home_team=home, away_team=away,
                              home_score=hs, away_score=as_, spread_line=float(rng.integers(-6, 7)) + 0.5,
                              total_line=float(rng.integers(40, 50)) + 0.5, home_moneyline=-140, away_moneyline=120,
                              home_spread_odds=-110, away_spread_odds=-110, over_odds=-110, under_odds=-110))
            n = 44
            pos = np.where(np.arange(n) % 2 == 0, home, away)
            pbp.append(pd.DataFrame({
                "game_id": gid, "season": 2023, "week": wk, "game_date": day, "home_team": home, "away_team": away,
                "posteam": pos, "defteam": np.where(pos == home, away, home), "home_score": hs, "away_score": as_,
                "play_type": np.where(np.arange(n) % 3 == 0, "run", "pass"), "pass": (np.arange(n) % 3 != 0).astype(int),
                "rush": (np.arange(n) % 3 == 0).astype(int), "down": rng.integers(1, 5, n), "epa": rng.normal(0, 1, n),
                "wp": rng.uniform(0.2, 0.8, n), "score_differential": rng.integers(-6, 7, n), "qtr": rng.integers(1, 4, n),
                "yardline_100": rng.integers(1, 99, n), "touchdown": (rng.random(n) < 0.05).astype(int),
                "sack": (rng.random(n) < 0.05).astype(int), "qb_hit": (rng.random(n) < 0.1).astype(int),
                "qb_dropback": 1, "fumble_lost": 0, "interception": (rng.random(n) < 0.03).astype(int),
                "drive": np.arange(n) // 6, "game_seconds_remaining": 3600 - 80 * np.arange(n), "play_clock": "10"}))
    return pd.concat(pbp, ignore_index=True), pd.DataFrame(sched)


def test_nfl_replay_passes_the_garble_test_and_the_test_has_power():
    from nfl.game import leak_check as lc
    pbp, sched = _nfl_fixture()
    res = lc.run_check(pbp, sched, [2023], ["2023-10-05", "2023-10-19"])
    for t, v in res.items():
        assert not v["leak"], (t, v)
        assert v["power_ok"] and v["n_checked_before"] > 0, (t, v)


def test_nfl_garble_test_catches_an_injected_leak(monkeypatch):
    from nfl.game import leak_check as lc
    from nfl.game import market_eval as me
    real_replay, real_price = me.replay, me.NFLGameEngine.price_game
    oracle = {}

    def leaky_replay(test_seasons, pbp=None, sched=None):
        oracle.clear()
        oracle.update({g.game_id: float(g.home_score - g.away_score) for g in sched.itertuples()})
        return real_replay(test_seasons, pbp=pbp, sched=sched)

    def leaky_price(self, game_key, kalman, pbp, market_odds=None):
        pg = real_price(self, game_key, kalman, pbp, market_odds)
        pg.mu_margin += 0.3 * oracle.get(game_key, 0.0)          # reads the game's own final margin
        return pg

    monkeypatch.setattr(me, "replay", leaky_replay)
    monkeypatch.setattr(me.NFLGameEngine, "price_game", leaky_price)
    pbp, sched = _nfl_fixture()
    res = lc.run_check(pbp, sched, [2023], ["2023-10-12"])
    assert res["2023-10-12"]["leak"] and res["2023-10-12"]["n_changed_before"] > 0


# ---------------------------------------------------------------------------------------------------- garble: NBA
def _nba_fixture(seed=0):
    rng = np.random.default_rng(seed)
    teams = ["BOS", "NYK", "LAL", "DEN", "MIA", "CHI", "DAL", "SAC"]
    rows, prows = [], []
    gid = 0
    for d in range(40):
        day = (pd.Timestamp("2024-11-01") + pd.Timedelta(days=d)).date().isoformat()
        order = rng.permutation(teams)
        for gi in range(4):
            gid += 1
            hm, aw = order[2 * gi], order[2 * gi + 1]
            for team, opp, ih in ((hm, aw, True), (aw, hm, False)):
                fga = int(rng.integers(80, 95))
                row = dict(game_id=f"g{gid:04d}", team_abbr=team, opp_abbr=opp, date=day, is_home=ih, season_year=2024,
                           season_type="regular", points=int(rng.integers(95, 125)), minutes=240,
                           field_goals_made=int(rng.integers(35, 48)), field_goals_attempted=fga,
                           three_points_made=int(rng.integers(9, 18)), three_points_attempted=int(rng.integers(28, 42)),
                           free_throws_attempted=int(rng.integers(15, 28)),
                           rebounds_offensive=int(rng.integers(7, 14)), rebounds_defensive=int(rng.integers(30, 40)),
                           turnovers=int(rng.integers(9, 18)))
                rows.append(row)
                prows.append(dict(game_id=row["game_id"], team_abbr=team, played=True, rebounds_offensive=row["rebounds_offensive"],
                                  rebounds_defensive=row["rebounds_defensive"], turnovers=row["turnovers"], points=row["points"]))
    return pd.DataFrame(rows), pd.DataFrame(prows)


def test_nba_replay_passes_the_garble_test_and_the_test_has_power():
    from nba.game import leak_check as lc
    raw, players = _nba_fixture()
    res = lc.run_check(raw, players, ["2024-11-15", "2024-11-28"], warmup_games=12)
    for t, v in res.items():
        assert not v["leak"], (t, v)
        assert v["power_ok"] and v["n_checked_before"] > 0, (t, v)


def test_nba_garble_test_catches_an_injected_leak(monkeypatch):
    from nba.game import leak_check as lc
    from nba.game import model as nm
    real_price = nm.DistributionalGameEngine.price_full_game
    oracle = {}
    real_eval = lc.evaluate_walk_forward

    def leaky_eval(games, closing_odds=None, warmup_games=50):
        by = {}
        for g in games:
            by.setdefault(g.game_id, {})["h" if g.is_home else "a"] = g
        oracle.clear()
        for v in by.values():
            if "h" in v and "a" in v:
                oracle[f"nba:{v['h'].game_date}:{v['a'].team}@{v['h'].team}"] = v["h"].points - v["a"].points
        return real_eval(games, closing_odds=closing_odds, warmup_games=warmup_games)

    def leaky_price(self, game_key, kalman_pred, matchup, market_odds=None):
        pg = real_price(self, game_key, kalman_pred, matchup, market_odds)
        pg.mu_margin += 0.2 * oracle.get(game_key, 0.0)
        return pg

    monkeypatch.setattr(lc, "evaluate_walk_forward", leaky_eval)
    monkeypatch.setattr(nm.DistributionalGameEngine, "price_full_game", leaky_price)
    raw, players = _nba_fixture()
    res = lc.run_check(raw, players, ["2024-11-15"], warmup_games=12)
    assert res["2024-11-15"]["leak"]


def test_nba_blend_weight_constant_matches_the_engine():
    """evaluate.W_KALMAN is mirrored from DistributionalGameEngine.compute_means; the feature-side margin the gates use relies on it."""
    from nba.game import evaluate as ev
    from nba.game.features import FeatureStore
    from nba.game.kalman_ratings import KalmanTeamTracker
    from nba.game.model import DistributionalGameEngine
    kal, store, eng = KalmanTeamTracker(), FeatureStore(), DistributionalGameEngine()
    k = kal.predict_game("BOS", "NYK", "2024-11-02")
    mf = store.extract_matchup_features("BOS", "NYK", "2024-11-02")
    m0 = eng.compute_means(k, mf)[0]
    from dataclasses import replace
    m1 = eng.compute_means(replace(k, exp_margin=k.exp_margin + 10.0), mf)[0]
    assert (m1 - m0) / 10.0 == pytest.approx(ev.W_KALMAN, abs=1e-9)


def test_nfl_blend_weight_constants_match_the_engine():
    from dataclasses import replace
    from nfl.game.kalman_ratings import KalmanNFLTracker
    from nfl.game.model import NFLGameEngine, W_KALMAN, W_ZFEAT
    from nfl.game.pbp_features import MatchupPBPFeatures
    assert W_KALMAN + W_ZFEAT == pytest.approx(1.0)
    k = KalmanNFLTracker().predict_game("KC", "BAL", 2024, 3, "2024-09-22")
    f = MatchupPBPFeatures("KC", "BAL", "2024-09-22", 2024, 3, z_ed_pass_diff=0.7)
    eng = NFLGameEngine()
    m0 = eng.compute_means(k, f)[0]
    m1 = eng.compute_means(replace(k, exp_margin=k.exp_margin + 2.0), f)[0]
    assert (m1 - m0) / 2.0 == pytest.approx(W_KALMAN)
    feat = (m0 - W_KALMAN * k.exp_margin) / W_ZFEAT
    assert feat == pytest.approx(k.hca_pts + 3.20 * 0.7)


def test_dropped_history_flags_marks_only_games_after_a_hole():
    from nba.game.market_eval import dropped_history_flags
    raw = pd.DataFrame({"game_id": ["a", "a", "b", "b", "c", "c", "d", "d"], "team_abbr": ["BOS", "NYK"] * 4,
                        "date": ["2024-11-01"] * 2 + ["2024-11-03"] * 2 + ["2024-11-05"] * 2 + ["2024-11-07"] * 2,
                        "season_type": "regular"})
    f = dropped_history_flags(raw, {"a", "c", "d"})            # game b was dropped
    assert f[("BOS", "2024-11-03")] is False or f[("BOS", "2024-11-03")] == False   # noqa: E712  b itself is not "before"
    assert f[("BOS", "2024-11-05")] and f[("NYK", "2024-11-07")]
    assert not f[("BOS", "2024-11-01")]
