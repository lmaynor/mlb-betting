"""Offline tests for the N1 QB kneel feature (nfl/props/kneel.py).

Spec under test (Argon game-state consult, Nov 11, 2026): E[kneel_yards] = P(win) x team_kneel_rate_given_win x (-1.12)
from pregame inputs only; Q4 win-state kneels ONLY (Q2 end-of-half kneels excluded); NO >= 10 rush-yard / >= 3 rush
attempt filter on the graded population (all starting QBs with posted rush lines); team rate walk-forward honest.
"""
import numpy as np
import pandas as pd
import pytest

from nfl.props import kneel as K


# ------------------------------------------------------------------------------------------------------------- fixtures
def _schedules(rows):
    """rows: (game_id, gameday, home, away, home_score, away_score, spread_line, home_ml, away_ml, home_qb, away_qb)"""
    return pd.DataFrame([{
        "game_id": g, "gameday": d, "season": int(d[:4]), "home_team": h, "away_team": a, "home_score": hs,
        "away_score": as_, "spread_line": sp, "home_moneyline": hm, "away_moneyline": am,
        "home_qb_id": hq, "away_qb_id": aq} for g, d, h, a, hs, as_, sp, hm, am, hq, aq in rows])


def _pbp(plays):
    """plays: (game_id, posteam, qtr, qb_kneel)"""
    return pd.DataFrame([{"game_id": g, "posteam": t, "qtr": q, "qb_kneel": k} for g, t, q, k in plays])


def _history():
    """Team AAA wins g1 (3 Q4 kneels, 2 Q2 kneels) and g2 (1 Q4 kneel); loses g3 (its Q4 kneels are NOT win-state).
    Team BBB loses g1, wins g2 with 0 kneels. Dates strictly increasing."""
    sched = _schedules([
        ("g1", "2023-09-10", "AAA", "BBB", 24, 17, 3.5, -160, 140, "QB_A", "QB_B"),
        ("g2", "2023-09-17", "BBB", "AAA", 20, 27, -2.5, 120, -140, "QB_B", "QB_A"),
        ("g3", "2023-09-24", "AAA", "BBB", 10, 31, 6.5, -260, 220, "QB_A", "QB_B"),
        ("g4", "2023-10-01", "AAA", "BBB", 21, 20, 1.5, -120, 100, "QB_A", "QB_B"),
    ])
    pbp = _pbp(
        [("g1", "AAA", 4, 1)] * 3 + [("g1", "AAA", 2, 1)] * 2 + [("g1", "BBB", 4, 1)] +      # BBB kneel in a loss: not win-state
        [("g2", "AAA", 4, 1)] + [("g2", "AAA", 2, 1)] +                                       # Q2 excluded here too
        [("g3", "AAA", 4, 1)] * 4 +                                                           # AAA lost g3: not win-state
        [("g1", "AAA", 4, 0), ("g2", "BBB", 3, 1)])                                           # non-kneel; Q3 kneel: excluded
    return sched, pbp


# ---------------------------------------------------------------------------------------------------------- feature math
def test_expected_kneel_yards_formula():
    assert K.expected_kneel_yards(0.5, 2.0) == pytest.approx(-1.12)
    assert K.expected_kneel_yards(1.0, 1.0) == pytest.approx(-1.12)
    assert K.expected_kneel_yards(0.0, 3.0) == pytest.approx(0.0)
    assert K.expected_kneel_attempts(0.5, 2.0) == pytest.approx(1.0)
    v = K.expected_kneel_yards(np.array([0.25, 0.75]), np.array([2.0, 2.0]))
    assert v == pytest.approx(np.array([-0.56, -1.68]))


def test_win_prob_from_moneyline_devigs():
    assert K.win_prob_from_moneyline(-110, -110) == pytest.approx(0.5)
    p = K.win_prob_from_moneyline(-200, 170)
    assert p == pytest.approx((200 / 300) / (200 / 300 + 100 / 270))
    assert np.isnan(K.win_prob_from_moneyline(None, -110))
    assert np.isnan(K.win_prob_from_moneyline(-110, np.nan))


def test_win_prob_from_spread_monotone_and_symmetric():
    assert K.win_prob_from_spread(0.0) == pytest.approx(0.5)
    assert K.win_prob_from_spread(7.0) > K.win_prob_from_spread(3.0) > 0.5
    assert K.win_prob_from_spread(-7.0) == pytest.approx(1.0 - K.win_prob_from_spread(7.0))
    assert np.isnan(K.win_prob_from_spread(np.nan))


def test_game_win_probs_moneyline_first_then_spread():
    sched = _schedules([
        ("g1", "2023-09-10", "AAA", "BBB", 24, 17, 3.5, -160, 140, "QB_A", "QB_B"),
        ("g2", "2023-09-17", "BBB", "AAA", 20, 27, -2.5, np.nan, np.nan, "QB_B", "QB_A"),
    ])
    probs = K.game_win_probs(sched).set_index(["game_id", "team"])
    assert probs.loc[("g1", "AAA"), "p_win_source"] == "moneyline"
    assert probs.loc[("g1", "AAA"), "p_win_pregame"] + probs.loc[("g1", "BBB"), "p_win_pregame"] == pytest.approx(1.0)
    assert probs.loc[("g2", "BBB"), "p_win_source"] == "spread"          # home, spread_line -2.5 = home underdog
    assert probs.loc[("g2", "BBB"), "p_win_pregame"] < 0.5 < probs.loc[("g2", "AAA"), "p_win_pregame"]


# --------------------------------------------------------------- Q4-only, win-state-only, no population filter
def test_kneels_per_team_game_q4_win_state_only():
    sched, pbp = _history()
    tg = K.kneels_per_team_game(pbp, sched).set_index(["game_id", "team"])
    assert tg.loc[("g1", "AAA"), "q4_kneels"] == 3        # 2 Q2 kneels excluded
    assert tg.loc[("g1", "BBB"), "q4_kneels"] == 0        # knelt in Q4 but LOST: not win-state
    assert tg.loc[("g2", "AAA"), "q4_kneels"] == 1
    assert tg.loc[("g3", "AAA"), "q4_kneels"] == 0        # 4 Q4 kneels in a loss: not win-state
    assert tg.loc[("g2", "BBB"), "q4_kneels"] == 0        # Q3 kneel excluded


def test_walk_forward_rate_uses_only_prior_games_and_shrinks():
    sched, pbp = _history()
    tg = K.walk_forward_kneel_rates(K.kneels_per_team_game(pbp, sched)).set_index(["game_id", "team"])
    # g1: no history at all -> cold-start league prior, for both teams
    assert tg.loc[("g1", "AAA"), "team_kneel_rate_given_win"] == pytest.approx(K.LEAGUE_RATE_PRIOR)
    # g2 AAA: 1 prior win with 3 kneels; league before g2 = 3 kneels / 1 win; rate = (3 + 8*3) / (1 + 8) = 3.0
    assert tg.loc[("g2", "AAA"), "team_kneel_rate_given_win"] == pytest.approx(3.0)
    # g4 AAA: prior wins g1 (3) + g2 (1) = 4 kneels / 2 wins; league before g4 = 4 kneels / 3 wins (BBB won g3 scoreless)
    league = 4.0 / 3.0
    assert tg.loc[("g4", "AAA"), "team_kneel_rate_given_win"] == pytest.approx((4 + K.PRIOR_WINS * league) / (2 + K.PRIOR_WINS))


def test_walk_forward_no_lookahead_under_garbling():
    """Garbling every game on/after a date must not move any rate dated before it (and same-date games never see
    each other)."""
    sched, pbp = _history()
    base = K.walk_forward_kneel_rates(K.kneels_per_team_game(pbp, sched))
    sched2 = sched.copy()
    late = sched2["gameday"] >= "2023-09-24"
    sched2.loc[late, "home_score"] = 99                       # flip g3/g4 winners
    sched2.loc[late, "away_score"] = 0
    pbp2 = pd.concat([pbp, _pbp([("g3", "BBB", 4, 1)] * 9 + [("g4", "BBB", 4, 1)] * 9)], ignore_index=True)
    garbled = K.walk_forward_kneel_rates(K.kneels_per_team_game(pbp2, sched2))
    key = ["game_id", "team"]
    a = base[base["date"] < "2023-09-24"].sort_values(key).reset_index(drop=True)
    b = garbled[garbled["date"] < "2023-09-24"].sort_values(key).reset_index(drop=True)
    assert a["team_kneel_rate_given_win"].tolist() == b["team_kneel_rate_given_win"].tolist()


# -------------------------------------------------------------------------------------------------------- attachment
def _residual_rows():
    """A minimal residual-table-shaped frame: a pocket-passer starter with a 5.5-yard line (the population the dropped
    >= 10-yard filter used to exclude), his backup, and a non-QB row."""
    base = {"game_id": "g4", "player": "Qb A", "pid": "QB_A", "date": "2023-10-01", "game_date": "2023-10-01",
            "season": 2023, "week": 5, "line": 5.5, "y": 3.0, "over": 0.0, "push": False, "p_mkt": 0.5, "n_books": 5,
            "disp": 0.01, "best_over_odds": -110.0, "best_under_odds": -110.0, "med_over_odds": -110.0,
            "med_under_odds": -110.0, "open_line": np.nan, "open_p": np.nan, "p_novig": np.nan, "p_prophetx": np.nan,
            "p_pinnacle": np.nan, "eng_mu": 6.0, "eng_p": 0.52, "eng0_p": 0.5,
            "n_season": 3.0, "m_season": 4.0, "n_prev": 0.0, "m_prev": np.nan, "m_l5": 4.0, "m_l10": 4.0,
            "m_l20": 4.0, "sd_l20": 3.0, "n_l20": 3.0}
    rows = [dict(base, stat="rush_yds"),
            dict(base, stat="rush_att", line=1.5),
            dict(base, stat="rush_yds", pid="QB_A2", player="Qb Backup"),          # not the listed starter
            dict(base, stat="pass_yds", pid="QB_A", line=250.5)]                   # starter, but not a rush market
    return pd.DataFrame(rows)


def test_attach_features_pregame_only_and_starter_population():
    sched, pbp = _history()
    out = K.attach_kneel_features(_residual_rows(), sched, pbp)
    r = out[(out["stat"] == "rush_yds") & (out["pid"] == "QB_A")].iloc[0]
    assert bool(r["kneel_eligible"]) and r["team"] == "AAA"
    # P(win) for g4 AAA from the closing moneyline (-120 vs +100), NOT from the realised 21-20 win
    p = K.win_prob_from_moneyline(-120, 100)
    assert r["p_win_pregame"] == pytest.approx(p)
    rate = (4 + K.PRIOR_WINS * (4.0 / 3.0)) / (2 + K.PRIOR_WINS)
    assert r["team_kneel_rate_given_win"] == pytest.approx(rate)
    assert r["e_kneel_yards"] == pytest.approx(p * rate * -1.12)
    att = out[(out["stat"] == "rush_att") & (out["pid"] == "QB_A")].iloc[0]
    assert att["e_kneel_att"] == pytest.approx(p * rate) and att["e_kneel_yards"] == pytest.approx(p * rate * -1.12)
    assert not bool(out[out["pid"] == "QB_A2"].iloc[0]["kneel_eligible"])           # backup: excluded, feature 0
    assert out[out["pid"] == "QB_A2"].iloc[0]["e_kneel_yards"] == 0.0
    assert not bool(out[out["stat"] == "pass_yds"].iloc[0]["kneel_eligible"])      # only rush markets carry the feature


def test_attach_does_not_use_realised_score_for_p_win():
    sched, pbp = _history()
    sched2 = sched.copy()
    g4 = sched2["game_id"] == "g4"
    sched2.loc[g4, ["home_score", "away_score"]] = [0, 45]    # flip the realised result; pregame inputs unchanged
    a = K.attach_kneel_features(_residual_rows(), sched, pbp)
    b = K.attach_kneel_features(_residual_rows(), sched2, pbp)
    ra = a[(a["stat"] == "rush_yds") & (a["pid"] == "QB_A")].iloc[0]
    rb = b[(b["stat"] == "rush_yds") & (b["pid"] == "QB_A")].iloc[0]
    assert ra["p_win_pregame"] == rb["p_win_pregame"]         # P(win) is the pregame market, full stop
    assert ra["e_kneel_yards"] == rb["e_kneel_yards"]         # rate history is also untouched (g4 is the last game)


# ------------------------------------------------------------------------------------------------- ablation / verdict
def _ablation_table(with_signal=True, seed=3):
    """Synthetic residual table where the kneel feature genuinely moves P(over): low-line QBs kneel games away."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in (2021, 2022, 2023, 2024, 2025):
        for i in range(160):
            line = float(rng.choice([1.5, 3.5, 5.5, 7.5, 10.5, 13.5, 15.5, 22.5]))
            e_yds = float(rng.uniform(-2.2, 0.0)) if with_signal else float(rng.uniform(-2.2, 0.0))
            pm = 0.5
            from mlb_core.odds import props_residual as R
            shift = (0.55 * e_yds / 2.0) if with_signal else 0.0
            true = R.sigmoid(R.logit(pm) + shift)
            over = float(rng.uniform() < true)
            rows.append({"sport": "nfl", "game_id": f"{s}_{i}", "player": f"qb{i}", "pid": f"QB{i}",
                         "date": f"{s}-10-05", "game_date": f"{s}-10-05", "season": s, "week": 5, "stat": "rush_yds",
                         "line": line, "y": 0.0, "over": over, "push": False, "p_mkt": pm, "n_books": 5, "disp": 0.01,
                         "best_over_odds": -110.0, "best_under_odds": -110.0, "med_over_odds": -110.0,
                         "med_under_odds": -110.0, "open_line": np.nan, "open_p": np.nan,
                         "eng_mu": line, "eng_p": pm, "n_season": 5.0, "m_season": line, "n_prev": 0.0,
                         "m_prev": np.nan, "m_l5": line, "m_l10": line, "m_l20": line, "sd_l20": 4.0, "n_l20": 10.0,
                         "team": "AAA", "p_win_pregame": 0.5, "team_kneel_rate_given_win": 1.5,
                         "e_kneel_yards": e_yds, "e_kneel_att": -e_yds / 1.12, "kneel_eligible": True})
    return pd.DataFrame(rows)


def test_ablation_recovers_a_real_kneel_signal():
    res = K.kneel_ablation(_ablation_table(with_signal=True), holdout=2025)
    assert res["by_stat"]["rush_yds"]["ll_gain"] > 0
    assert res["ll_gain_pooled"] == pytest.approx(res["by_stat"]["rush_yds"]["ll_gain"])
    assert res["calibration_le_15_5"]["n"] > 0
    assert res["verdict"] in ("KEEP", "AUTO-KILL")            # the bar is encoded, not narrated


def test_ablation_requires_feature_columns():
    t = _ablation_table().drop(columns=["e_kneel_yards"])
    with pytest.raises(ValueError, match="kneel feature columns"):
        K.kneel_ablation(t, holdout=2025)


def test_verdict_bar_is_argons():
    assert K.LL_BAR == 0.0015
    assert K.CAL_BUCKET_MAX_LINE == 15.5
    assert K.KNEEL_YARDS_PER_PLAY == -1.12
