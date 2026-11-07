"""NBA side of the confidence-gated evaluation, offline: event confidence columns, the adapter's two-stage run, the blend-weight constant the
agreement gate relies on, the data-quality flag, and the leakage garble test of the NBA replay (with an injected leak to prove it has power)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mlb_core.eval import gated_run
from nba.game import gated_eval as nba_gated_eval
from nba.game import leak_check as nba_leak_check
from nba.game import market_eval as nba_market_eval


# ---------------------------------------------------------------------------------------------------- records -> events -> driver
def _nba_records(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for season in range(2021, 2026):
        for i in range(300):
            d = f"{season + 1}-{1 + (i // 28) % 4:02d}-{1 + i % 28:02d}"          # Jan..Apr of the season's second year
            mkt = float(np.round(rng.normal(0, 5) * 2) / 2)
            mu = -mkt + rng.normal(0, 2.0)                                           # home margin; line is the home side's spread
            tot = float(np.round(rng.normal(225, 6) * 2) / 2)
            pm = float(np.clip(0.5 + mu * 0.025, 0.05, 0.95))
            mk = lambda p, line=None: {"line": line, "p": p, "dec_a_med": 1.91, "dec_b_med": 1.91,   # noqa: E731
                                       "dec_a_best": 1.95, "dec_b_best": 1.95, "n": 5}
            kal = mu + rng.normal(0, 1.5)
            rows.append(dict(game_id=f"{season}_{i}", date=d, home="BOS", away="NYK", p_home_ml=pm,
                             p_home_cover=float(np.clip(0.5 + (mu + mkt) * 0.03, 0.05, 0.95)),
                             p_over=float(np.clip(0.5 + rng.normal(0, 0.05), 0.05, 0.95)), mu_margin=mu, mu_total=tot + rng.normal(0, 3),
                             margin=float(np.round(mu + rng.normal(0, 12))), total=float(np.round(tot + rng.normal(0, 18))),
                             ml=mk(float(np.clip(0.5 + mkt * -0.03, 0.05, 0.95))), spread=mk(0.5, mkt), total_m=mk(0.5, tot),
                             kal_margin=kal, kal_total=tot, feat_margin=mu + rng.normal(0, 1.5), feat_total=tot + rng.normal(0, 3),
                             kal_std_margin=float(rng.uniform(3, 6)), kal_std_total=float(rng.uniform(3, 6)), sd_margin=12.0,
                             sd_total=18.0, n_prior_min=int(i % 60), dq_bad=False))
    return pd.DataFrame(rows)


def test_nba_events_carry_the_confidence_inputs():
    ev = nba_market_eval.to_events(_nba_records())
    for name in ("moneyline (home wins)", "spread (home covers)", "total (over)"):
        df = ev[name]
        assert {"season", "c_kal", "c_feat", "c_mu", "c_line", "c_sd", "c_phase", "c_dq"} <= set(df.columns)
        assert (df["c_dq"] == 0.0).all()
    sp = ev["spread (home covers)"]
    rec = _nba_records()
    line_by_game = {g: s_["line"] for g, s_ in zip(rec["game_id"], rec["spread"])}
    assert (sp["c_line"].to_numpy() == -sp["game_id"].map(line_by_game).to_numpy()).all()    # home margin the market implies
    assert set(sp["season"]) == {2021, 2022, 2023, 2024, 2025}


def test_nba_two_stage_run_freezes_then_scores_once(tmp_path):
    rec = tmp_path / "nba.pkl"
    _nba_records().to_pickle(rec)
    out = str(tmp_path / "o")
    nba_src = nba_gated_eval.nba_source(str(rec))
    assert nba_src.label == "nba" and nba_src.phase_min == 20 and nba_src.held_seasons == [2024, 2025]
    fz = gated_run.run_dev(nba_src, out)
    assert fz["label"] == "nba" and fz["dev_cells_tried"] > 0
    assert "nba|moneyline (home wins)|DQ" in fz["dev_cells_not_applicable"]        # no dropped history: degenerate, not tried
    res = gated_run.run_held(nba_src, out, B=200)
    assert res["verdict"] in ("YES", "NO", "INCONCLUSIVE")
    with pytest.raises(SystemExit):
        gated_run.run_held(nba_src, out, B=200)
    assert nba_gated_eval.main(["held", "--records", str(rec), "--out-dir", out, "--allow-rescore", "--B", "100"]) == 0


# ---------------------------------------------------------------------------------------------------- constants and flags
def test_nba_blend_weight_constant_matches_the_engine():
    """evaluate.W_KALMAN is mirrored from DistributionalGameEngine.compute_means; the feature-side margin the gates use relies on it."""
    from dataclasses import replace
    from nba.game import evaluate as nba_evaluate
    from nba.game.features import FeatureStore
    from nba.game.kalman_ratings import KalmanTeamTracker
    from nba.game.model import DistributionalGameEngine
    nba_kalman, nba_store, nba_engine = KalmanTeamTracker(), FeatureStore(), DistributionalGameEngine()
    nba_k = nba_kalman.predict_game("BOS", "NYK", "2024-11-02")
    nba_mf = nba_store.extract_matchup_features("BOS", "NYK", "2024-11-02")
    m0 = nba_engine.compute_means(nba_k, nba_mf)[0]
    m1 = nba_engine.compute_means(replace(nba_k, exp_margin=nba_k.exp_margin + 10.0), nba_mf)[0]
    assert (m1 - m0) / 10.0 == pytest.approx(nba_evaluate.W_KALMAN, abs=1e-9)


def test_dropped_history_flags_marks_only_games_after_a_hole():
    raw = pd.DataFrame({"game_id": ["a", "a", "b", "b", "c", "c", "d", "d"], "team_abbr": ["BOS", "NYK"] * 4,
                        "date": ["2024-11-01"] * 2 + ["2024-11-03"] * 2 + ["2024-11-05"] * 2 + ["2024-11-07"] * 2,
                        "season_type": "regular"})
    f = nba_market_eval.dropped_history_flags(raw, {"a", "c", "d"})            # game b was dropped
    assert not f[("BOS", "2024-11-03")]                                        # b itself is not "before" b
    assert f[("BOS", "2024-11-05")] and f[("NYK", "2024-11-07")]
    assert not f[("BOS", "2024-11-01")]


# ---------------------------------------------------------------------------------------------------- garble
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
                row = dict(game_id=f"g{gid:04d}", team_abbr=team, opp_abbr=opp, date=day, is_home=ih, season_year=2024,
                           season_type="regular", points=int(rng.integers(95, 125)), minutes=240,
                           field_goals_made=int(rng.integers(35, 48)), field_goals_attempted=int(rng.integers(80, 95)),
                           three_points_made=int(rng.integers(9, 18)), three_points_attempted=int(rng.integers(28, 42)),
                           free_throws_attempted=int(rng.integers(15, 28)),
                           rebounds_offensive=int(rng.integers(7, 14)), rebounds_defensive=int(rng.integers(30, 40)),
                           turnovers=int(rng.integers(9, 18)))
                rows.append(row)
                prows.append(dict(game_id=row["game_id"], team_abbr=team, played=True, rebounds_offensive=row["rebounds_offensive"],
                                  rebounds_defensive=row["rebounds_defensive"], turnovers=row["turnovers"], points=row["points"]))
    return pd.DataFrame(rows), pd.DataFrame(prows)


def test_nba_replay_passes_the_garble_test_and_the_test_has_power():
    raw, players = _nba_fixture()
    res = nba_leak_check.run_check(raw, players, ["2024-11-15", "2024-11-28"], warmup_games=12)
    for t, v in res.items():
        assert not v["leak"], (t, v)
        assert v["power_ok"] and v["n_checked_before"] > 0, (t, v)


def test_nba_garble_test_catches_an_injected_leak(monkeypatch):
    from nba.game import model as nba_model
    real_price = nba_model.DistributionalGameEngine.price_full_game
    real_eval = nba_leak_check.evaluate_walk_forward
    nba_oracle = {}

    def leaky_eval(games, closing_odds=None, warmup_games=50):
        by = {}
        for g in games:
            by.setdefault(g.game_id, {})["h" if g.is_home else "a"] = g
        nba_oracle.clear()
        for v in by.values():
            if "h" in v and "a" in v:
                nba_oracle[f"nba:{v['h'].game_date}:{v['a'].team}@{v['h'].team}"] = v["h"].points - v["a"].points
        return real_eval(games, closing_odds=closing_odds, warmup_games=warmup_games)

    def leaky_price(self, game_key, kalman_pred, matchup, market_odds=None):
        pg = real_price(self, game_key, kalman_pred, matchup, market_odds)
        pg.mu_margin += 0.2 * nba_oracle.get(game_key, 0.0)          # reads the game's own final margin
        return pg

    monkeypatch.setattr(nba_leak_check, "evaluate_walk_forward", leaky_eval)
    monkeypatch.setattr(nba_model.DistributionalGameEngine, "price_full_game", leaky_price)
    raw, players = _nba_fixture()
    res = nba_leak_check.run_check(raw, players, ["2024-11-15"], warmup_games=12)
    assert res["2024-11-15"]["leak"]
