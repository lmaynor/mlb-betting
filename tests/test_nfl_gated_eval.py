"""NFL side of the confidence-gated evaluation, offline: event confidence columns, the adapter's two-stage run, the blend-weight constants the
agreement gate relies on, and the leakage garble test of the NFL replay (with an injected leak to prove the test has power)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mlb_core.eval import gated_run
from nfl.game import gated_eval as nfl_gated_eval
from nfl.game import leak_check as nfl_leak_check
from nfl.game import market_eval as nfl_market_eval


# ---------------------------------------------------------------------------------------------------- records -> events -> driver
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


def test_nfl_events_carry_the_confidence_inputs():
    ev = nfl_market_eval.to_events(_nfl_records())
    for name, kind in (("spread (home covers)", "margin"), ("total (over)", "total"), ("moneyline (home wins)", "margin")):
        df = ev[name]
        assert {"season", "c_kal", "c_feat", "c_mu", "c_line", "c_sd", "c_phase", "c_dq"} <= set(df.columns)
        assert df["c_dq"].isna().all()                        # the NFL replay has no data-quality flag
    assert ev["total (over)"]["c_feat"].isna().all()           # one variant for the total: AGREE not applicable
    assert ev["spread (home covers)"]["c_feat"].notna().all()


def test_nfl_two_stage_run_freezes_then_scores_once(tmp_path):
    rec = tmp_path / "nfl.csv"
    _nfl_records().to_csv(rec, index=False)
    out = str(tmp_path / "o")
    nfl_src = nfl_gated_eval.nfl_source(str(rec))
    assert nfl_src.label == "nfl" and nfl_src.phase_min == 6 and nfl_src.held_seasons == [2024, 2025]
    fz = gated_run.run_dev(nfl_src, out)
    assert fz["label"] == "nfl" and fz["dev_cells_tried"] > 0
    assert "nfl|total (over)|AGREE" in fz["dev_cells_not_applicable"]
    res = gated_run.run_held(nfl_src, out, B=200)
    assert res["verdict"] in ("YES", "NO", "INCONCLUSIVE")
    with pytest.raises(SystemExit):
        gated_run.run_held(nfl_src, out, B=200)
    assert nfl_gated_eval.main(["held", "--records", str(rec), "--out-dir", out, "--allow-rescore", "--B", "100"]) == 0


# ---------------------------------------------------------------------------------------------------- blend constants
def test_nfl_blend_weight_constants_match_the_engine():
    from dataclasses import replace
    from nfl.game.kalman_ratings import KalmanNFLTracker
    from nfl.game.model import NFLGameEngine, W_KALMAN, W_ZFEAT
    from nfl.game.pbp_features import MatchupPBPFeatures
    assert W_KALMAN + W_ZFEAT == pytest.approx(1.0)
    nfl_k = KalmanNFLTracker().predict_game("KC", "BAL", 2024, 3, "2024-09-22")
    nfl_f = MatchupPBPFeatures("KC", "BAL", "2024-09-22", 2024, 3, z_ed_pass_diff=0.7)
    nfl_engine = NFLGameEngine()
    m0 = nfl_engine.compute_means(nfl_k, nfl_f)[0]
    m1 = nfl_engine.compute_means(replace(nfl_k, exp_margin=nfl_k.exp_margin + 2.0), nfl_f)[0]
    assert (m1 - m0) / 2.0 == pytest.approx(W_KALMAN)
    assert (m0 - W_KALMAN * nfl_k.exp_margin) / W_ZFEAT == pytest.approx(nfl_k.hca_pts + 3.20 * 0.7)


# ---------------------------------------------------------------------------------------------------- garble
def _nfl_fixture(seed=0, nan_score_game=True):
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
    sched = pd.DataFrame(sched)
    if nan_score_game:                  # an unplayed game: the replay skips it, and the garble must not move its NaN onto a real game
        sched.loc[len(sched) - 1, ["home_score", "away_score"]] = np.nan
    return pd.concat(pbp, ignore_index=True), sched


def test_nfl_replay_passes_the_garble_test_and_the_test_has_power():
    pbp, sched = _nfl_fixture()
    res = nfl_leak_check.run_check(pbp, sched, [2023], ["2023-10-05", "2023-10-19"])
    for t, v in res.items():
        assert not v["leak"], (t, v)
        assert v["power_ok"] and v["n_checked_before"] > 0, (t, v)


def test_nfl_garble_test_catches_an_injected_leak(monkeypatch):
    real_replay, real_price = nfl_market_eval.replay, nfl_market_eval.NFLGameEngine.price_game
    nfl_oracle = {}

    def leaky_replay(test_seasons, pbp=None, sched=None):
        nfl_oracle.clear()
        nfl_oracle.update({g.game_id: float(g.home_score - g.away_score) for g in sched.itertuples()})
        return real_replay(test_seasons, pbp=pbp, sched=sched)

    def leaky_price(self, game_key, kalman, pbp, market_odds=None):
        pg = real_price(self, game_key, kalman, pbp, market_odds)
        pg.mu_margin += 0.3 * nfl_oracle.get(game_key, 0.0)       # reads the game's own final margin
        return pg

    monkeypatch.setattr(nfl_market_eval, "replay", leaky_replay)
    monkeypatch.setattr(nfl_market_eval.NFLGameEngine, "price_game", leaky_price)
    pbp, sched = _nfl_fixture(nan_score_game=False)
    res = nfl_leak_check.run_check(pbp, sched, [2023], ["2023-10-12"])
    assert res["2023-10-12"]["leak"] and res["2023-10-12"]["n_changed_before"] > 0
