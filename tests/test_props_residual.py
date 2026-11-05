"""Offline tests for the props market-residual model (mlb_core/odds/props_residual.py, nba/props/residual.py, nfl/props/residual.py).

The one that matters: the LEAKAGE GUARD. Garbling every row dated on or after a day t must leave the predictions for day t
unchanged, for the form features and for the whole walk-forward model.
"""
import math

import numpy as np
import pandas as pd
import pytest

from mlb_core.odds import props_residual as R


# ------------------------------------------------------------------------------------------------------- synthetic data
def _american(p):
    p = np.clip(np.asarray(p, dtype=float), 0.02, 0.98)
    return np.where(p > 0.5, -100.0 * p / (1 - p), 100.0 * (1 - p) / p)


def synth_table(signal=0.0, seed=0, seasons=(2021, 2022, 2023, 2024), dates_per_season=40, per_date=70):
    """Props whose true P(over) is sigmoid(logit(p_mkt) + signal * z_eng): signal = 0 -> the market is exactly right."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        for d in range(dates_per_season):
            day = pd.Timestamp(f"{s}-10-20") + pd.Timedelta(days=d)
            n = per_date
            pm = rng.uniform(0.35, 0.65, n)
            z = rng.normal(0, 1, n)
            line = rng.uniform(3.5, 25.5, n).round() + 0.5
            sd = 0.5 * np.sqrt(line)
            true = R.sigmoid(R.logit(pm) + signal * z)
            over = (rng.uniform(size=n) < true).astype(float)
            hold = 1.045
            rows.append(pd.DataFrame({
                "date": day.strftime("%Y-%m-%d"), "game_date": day.strftime("%Y-%m-%d"), "season": s,
                "stat": rng.choice(["a", "b"], n), "player": [f"p{i}" for i in range(n)], "line": line, "over": over, "p_mkt": pm,
                "n_books": rng.integers(3, 9, n), "disp": rng.uniform(0, 0.03, n),
                "best_over_odds": _american(pm / hold * 1.02), "best_under_odds": _american((1 - pm) / hold * 1.02),
                "med_over_odds": _american(pm * hold), "med_under_odds": _american((1 - pm) * hold),
                "eng_mu": line + z * sd, "eng_p": R.sigmoid(R.logit(pm) + 0.5 * z), "sd_l20": np.nan, "n_l20": 0.0, "n_season": 0.0,
                "m_season": np.nan, "m_prev": np.nan, "n_prev": 0.0, "m_l20": np.nan, "m_l10": np.nan, "m_l5": np.nan,
                "open_line": np.nan, "open_p": np.nan}))
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------------------------------------- numerics
def test_cluster_se_matches_bruteforce():
    rng = np.random.default_rng(1)
    x = rng.normal(size=300)
    cl = rng.integers(0, 12, 300)
    m, se = R.cluster_mean_se(x, cl)
    g = 12
    sums = np.array([(x[cl == c] - x.mean()).sum() for c in range(g)])
    assert m == pytest.approx(x.mean())
    assert se == pytest.approx(math.sqrt((sums ** 2).sum() * g / (g - 1)) / len(x))


def test_offset_ridge_recovers_the_residual_slope_and_ignores_the_offset():
    rng = np.random.default_rng(2)
    n = 40000
    off = rng.normal(0, 1, n)
    x = rng.normal(0, 1, n)
    y = (rng.uniform(size=n) < R.sigmoid(off + 0.5 * x)).astype(float)
    mdl = R.OffsetRidge(np.array([1e-3])).fit(x[:, None], y, off)
    w = mdl.w[1] / mdl.sd[0]
    assert w == pytest.approx(0.5, abs=0.06)
    assert abs(mdl.w[0]) < 0.06                                   # the intercept stays near 0: the offset carries the level
    pen = R.OffsetRidge(np.array([1e9])).fit(x[:, None], y, off)
    assert abs(pen.w[1] / pen.sd[0]) < 1e-3                       # an infinite penalty is exactly "the market"


# ------------------------------------------------------------------------------------------------ the protocol / guard
def test_assert_chronological_fails_closed():
    t = synth_table(seasons=(2021, 2022), dates_per_season=3, per_date=4)
    a, b = t[t["season"] == 2021], t[t["season"] == 2022]
    R.assert_chronological(a, b)
    with pytest.raises(ValueError):
        R.assert_chronological(t, b)
    with pytest.raises(ValueError):
        R.assert_chronological(b, b)                              # same-day rows may not be both training and test


def test_history_features_cannot_see_the_prop_date_or_later():
    rng = np.random.default_rng(3)
    days = pd.date_range("2023-10-20", periods=40).strftime("%Y-%m-%d")
    games = pd.DataFrame({"pid": "x", "date": days, "season": 2023, "value": rng.normal(10, 3, 40)})
    props = pd.DataFrame({"pid": ["x"] * 3, "date": [days[10], days[25], days[39]], "season": 2023})
    base = R.history_features(games, props)
    for k, t in enumerate(props["date"]):
        g2 = games.copy()
        g2.loc[g2["date"] >= t, "value"] = 1e6                     # garble the prop's own day and everything after
        out = R.history_features(g2, props)
        pd.testing.assert_series_equal(out.iloc[k], base.iloc[k], check_names=False)
    assert base.iloc[0]["n_season"] == 10                          # the 10 games BEFORE day 10, not the 11th
    assert base.iloc[0]["m_season"] == pytest.approx(games["value"].iloc[:10].mean())
    # previous season mean: only when that whole season precedes the prop
    g3 = pd.concat([games, games.assign(date=pd.to_datetime(games["date"]).sub(pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d"), season=2022)])
    h = R.history_features(g3, props)
    assert h["n_prev"].iloc[0] == 40 and h["m_prev"].iloc[0] == pytest.approx(games["value"].mean())


def test_first_game_of_a_season_has_zero_season_games():
    games = pd.DataFrame({"pid": ["x"] * 5, "date": ["2022-11-01", "2022-11-03", "2022-11-05", "2022-11-07", "2022-11-09"],
                          "season": 2022, "value": [1.0, 2.0, 3.0, 4.0, 5.0]})
    props = pd.DataFrame({"pid": ["x"], "date": ["2023-10-25"], "season": 2023})
    h = R.history_features(games, props)
    assert h["n_season"].iloc[0] == 0 and np.isnan(h["m_season"].iloc[0])
    assert h["m_prev"].iloc[0] == pytest.approx(3.0) and h["n_prev"].iloc[0] == 5
    assert h["m_l5"].iloc[0] == pytest.approx(3.0)


def test_walk_forward_predictions_do_not_move_when_the_future_is_garbled():
    """THE LEAKAGE GUARD. Take the test season, pick a day t inside it, garble every column of every row dated after t and the
    labels (and nothing but the labels) of day t itself: the predictions for day t must be bit-identical."""
    t = synth_table(signal=0.4, seed=5, seasons=(2021, 2022, 2023), dates_per_season=25, per_date=60)
    base_p, _ = R.walk_forward(R.prepare(t), [2023], nthread=1)
    prepared = R.prepare(t)
    day = "2023-10-31"
    assert (prepared["date"] == day).any()
    g = prepared.copy()
    after, same = g["date"] > day, g["date"] == day
    rng = np.random.default_rng(9)
    for c in ("p_mkt", "eng_mu", "eng_p", "line", "n_books", "disp", "med_over_odds", "med_under_odds", "best_over_odds", "best_under_odds"):
        g[c] = g[c].astype(float)
        g.loc[after, c] =rng.uniform(0.2, 0.8, int(after.sum())) * (g.loc[after, c].abs().mean() or 1.0)
    g.loc[after | same, "over"] = rng.integers(0, 2, int((after | same).sum())).astype(float)
    new_p, _ = R.walk_forward(g, [2023], nthread=1)
    a = base_p.loc[prepared.index[same]]
    b = new_p.loc[g.index[same]]
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_nothing_is_learned_when_the_market_is_exactly_right():
    t = R.prepare(synth_table(signal=0.0, seed=11))
    preds, folds = R.walk_forward(t, [2023, 2024], nthread=1)
    te = t.loc[preds.index]
    y, pm = te["over"].to_numpy(), te["p_mkt"].to_numpy()
    for col in ("p_ridge", "p_gbdt"):
        r = R.paired_logloss(y, preds[col].to_numpy(), pm, te["game_date"].to_numpy())
        assert r["z"] > -2.5                                       # never "significantly better than a market that is exact"
        assert abs(r["d"]) < 0.01
    assert len(folds) == 2


def test_a_planted_residual_is_found_and_the_slope_is_near_one():
    t = R.prepare(synth_table(signal=0.5, seed=12, dates_per_season=60, per_date=100))
    preds, _ = R.walk_forward(t, [2023, 2024], nthread=1)
    te = t.loc[preds.index]
    y, pm, cl = te["over"].to_numpy(), te["p_mkt"].to_numpy(), te["game_date"].to_numpy()
    r = R.paired_logloss(y, preds["p_ridge"].to_numpy(), pm, cl)
    assert r["d"] < 0 and r["z"] < -4
    s = R.residual_slope(y, preds["p_ridge"].to_numpy(), pm, cl)
    assert 0.6 < s["b"] < 1.4 and s["t"] > 4
    res = R.evaluate(te, preds, n_boot=60)
    assert res["models"]["p_ridge"]["logloss"]["z"] < -4
    assert "p_auto" in res["models"]


# ------------------------------------------------------------------------------------------------------- ROI / control
def test_bet_profit_and_market_expected_roi_by_hand():
    t = pd.DataFrame({"over": [1.0, 0.0], "p_mkt": [0.5, 0.5], "game_date": ["d1", "d2"], "stat": "a",
                      "best_over_odds": [110.0, 110.0], "best_under_odds": [-130.0, -130.0],
                      "med_over_odds": [-110.0, -110.0], "med_under_odds": [-110.0, -110.0]})
    b = R._bets(t, np.array([0.60, 0.60]), "best")
    assert list(b["profit"]) == [pytest.approx(1.1), -1.0]       # over at +110: win 1.10, lose 1
    assert b["mkt_ev"].iloc[0] == pytest.approx(0.5 * 2.1 - 1.0)  # the market's own value of that bet
    assert b["edge"].iloc[0] == pytest.approx(0.60 - 100 / 210)
    rows = R.roi_buckets(t, np.array([0.60, 0.60]), "best", control=t["p_mkt"].to_numpy(), n_boot=20)
    assert sum(r["n"] for r in rows) == 2 and rows[-1]["n"] == 2   # edge 12.4% -> the 8%+ bucket
    assert rows[-1]["excess"] == pytest.approx((1.1 - 1.0) / 2 - (0.5 * 2.1 - 1.0))


def test_line_shopping_control_has_no_excess_when_the_market_is_exact():
    """A no-model rule that only shops for the best price earns the market-expected ROI: its EXCESS is ~0 by construction."""
    t = R.prepare(synth_table(signal=0.0, seed=21, dates_per_season=30, per_date=120))
    rows = R.roi_threshold(t, t["p_mkt"].to_numpy(), "best", 0.0, n_boot=80)
    assert rows["n"] > 1000
    lo, hi = rows["excess_ci"]
    assert lo < 0 < hi


# --------------------------------------------------------------------------------------------------------- features
def test_make_features_is_finite_with_no_history_and_no_open_quote():
    t = R.prepare(synth_table(seasons=(2021,), dates_per_season=3, per_date=20))
    x = R.make_features(t)
    assert list(x.columns) == R.FEATURES
    assert np.isfinite(x.to_numpy()).all()
    assert (x["has_open"] == 0).all() and (x["line_move_z"] == 0).all()


# -------------------------------------------------------------------------------------------------------- market extras
def _long_row(ev, pid, line, side, book_id, book, odds, kind="book", is_open=False, upd="2024-01-02 20:00:00"):
    return {"date": "2024-01-02", "sport": "NBA", "market": "points", "market_id": 1, "event_id": ev, "scheduled": "2024-01-03 00:00:00",
            "away": "AAA", "home": "BBB", "player_id": pid, "player": "P One", "team": "AAA", "line": line, "side": side, "book_id": book_id,
            "book": book, "book_kind": kind, "odds": odds, "is_open": is_open, "updated": upd}


def test_market_extras_median_open_and_sharp():
    rows = []
    for bid, name, o, u in ((10, "FanDuel", -110, -110), (12, "DraftKings", -115, -105), (13, "Caesars", -105, -115), (60, "Novig", -120, 100)):
        rows += [_long_row(1, 7, 24.5, "over", bid, name, o), _long_row(1, 7, 24.5, "under", bid, name, u)]
    rows += [_long_row(1, 7, 23.5, "over", 10, "FanDuel", -110, kind="open", is_open=True),
             _long_row(1, 7, 23.5, "under", 10, "FanDuel", -110, kind="open", is_open=True)]
    rows += [_long_row(1, 7, 24.5, "over", 36, "Underdog", -120, kind="dfs"), _long_row(1, 7, 24.5, "under", 36, "Underdog", -120, kind="dfs")]
    long_df = pd.DataFrame(rows)
    from mlb_core.odds import bettingpros_loader as L
    cons = L.consensus_from_long(long_df, min_books=3, stat_map={"points": "pts"}, sport="NBA")
    assert len(cons) == 1 and cons["n_books"].iloc[0] == 4
    ex = R.market_extras(long_df, cons)
    assert len(ex) == 1
    r = ex.iloc[0]
    assert r["open_line"] == 23.5 and r["open_p"] == pytest.approx(0.5)
    assert r["p_novig"] == pytest.approx((120 / 220) / (120 / 220 + 0.5), abs=1e-9)        # Novig -120 / +100, proportional de-vig
    assert r["med_over_odds"] < 0 and r["med_under_odds"] < 0       # the median of -110 / -115 / -105 / -120 is a minus price
    assert bool(ex["p_prophetx"].isna().iloc[0])


# ----------------------------------------------------------------------------------------------- sport table builders
def test_nba_master_games_filters_like_the_engine():
    from nba.props import residual as NR
    m = pd.DataFrame({"season_type": ["Regular Season", "Preseason", "Regular Season", "Regular Season"],
                      "played": [True, True, False, True], "minutes": ["30:00", "20:00", "0:00", "0:00"], "player_id": list("abcd"),
                      "date": ["2024-01-01"] * 4, "season_year": 2023, "points": [10, 5, 0, 0], "rebounds": [3, 1, 0, 0],
                      "assists": [2, 1, 0, 0], "three_points_made": [1, 0, 0, 0]})
    g = NR.master_games(m)
    assert list(g["pid"]) == ["a"] and g["pra"].iloc[0] == 15 and g["threes"].iloc[0] == 1 and g["minutes"].iloc[0] == pytest.approx(30.0)


def test_study_does_not_touch_the_holdout_without_the_flag():
    t = synth_table(signal=0.3, seed=31, dates_per_season=25, per_date=60)
    res, preds = R.study(t, holdout=2024, report_holdout=False, nthread=1, n_boot=20)
    assert res["report_holdout"] is False and 2024 not in res["per_season"]
    assert preds.index.isin(R.prepare(t)[R.prepare(t)["season"] == 2024].index).sum() == 0
    res2, _ = R.study(t, holdout=2024, report_holdout=True, nthread=1, n_boot=20)
    assert 2024 in res2["per_season"] and "verdict" in res2
    md = R.render_study("TEST", res2)
    assert "HELD OUT" in md and "EXCESS" in md
