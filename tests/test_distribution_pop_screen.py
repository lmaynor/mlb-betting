"""RES-10 machinery tests: fair pricing, firing arithmetic, settlement, clustered
inference, multiplicity. Pure functions only - no panels, no I/O."""
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import distribution_pop_screen as S


# ------------------------------------------------------------------ odds
def test_american_to_prob():
    assert S.american_to_prob(-110) == pytest.approx(110 / 210)
    assert S.american_to_prob(150) == pytest.approx(100 / 250)
    assert S.american_to_prob(-100) == pytest.approx(0.5)
    assert S.american_to_prob(100) == pytest.approx(0.5)


def test_american_profit():
    assert S.american_profit(150) == pytest.approx(1.5)
    assert S.american_profit(-200) == pytest.approx(0.5)
    assert S.bet_profit(-110, "win") == pytest.approx(100 / 110)
    assert S.bet_profit(-110, "loss") == -1.0
    assert S.bet_profit(-110, "push") == 0.0


# ------------------------------------------------------------------ settlement
def test_settle_main():
    assert S.settle_main("over", 25, 24.5) == "win"
    assert S.settle_main("over", 24, 24.5) == "loss"
    assert S.settle_main("under", 24, 24.5) == "win"
    assert S.settle_main("over", 24, 24.0) == "push"
    assert S.settle_main("under", 24, 24.0) == "push"


def test_settle_yes():
    assert S.settle_yes(3, 3) == "win"
    assert S.settle_yes(2, 3) == "loss"


# ------------------------------------------------------------------ fair probabilities
def test_negbin_main_probs_half_line():
    mu, alpha, line = 5.0, 0.1, 4.5
    p_over, p_push, p_under = S.main_win_probs("negbin", [mu], [alpha], [line])
    n = 1 / alpha
    expect_over = 1 - stats.nbinom.cdf(4, n, n / (n + mu))
    assert p_over[0] == pytest.approx(expect_over)
    assert p_push[0] == 0.0
    assert p_over[0] + p_under[0] == pytest.approx(1.0)


def test_negbin_main_probs_integer_line_push():
    mu, alpha, line = 5.0, 0.1, 5.0
    p_over, p_push, p_under = S.main_win_probs("negbin", [mu], [alpha], [line])
    n = 1 / alpha
    assert p_push[0] == pytest.approx(stats.nbinom.pmf(5, n, n / (n + mu)))
    assert p_over[0] == pytest.approx(1 - stats.nbinom.cdf(5, n, n / (n + mu)))
    assert p_over[0] + p_push[0] + p_under[0] == pytest.approx(1.0)


def test_normal_main_probs():
    p_over, p_push, p_under = S.main_win_probs("normal", [20.0], [5.0], [19.5])
    assert p_over[0] == pytest.approx(1 - stats.norm.cdf((19.5 - 20) / 5))
    assert p_push[0] == 0.0
    p_over, p_push, p_under = S.main_win_probs("normal", [20.0], [5.0], [20.0])
    assert p_push[0] == pytest.approx(stats.norm.cdf(0.5 / 5) - stats.norm.cdf(-0.5 / 5))
    assert p_over[0] + p_push[0] + p_under[0] == pytest.approx(1.0)


def test_exceedance():
    mu, alpha = 2.5, 0.2
    p = S.exceedance_prob("negbin", [mu], [alpha], [3.0])
    n = 1 / alpha
    assert p[0] == pytest.approx(1 - stats.nbinom.cdf(2, n, n / (n + mu)))
    p = S.exceedance_prob("normal", [20.0], [5.0], [25.0])
    assert p[0] == pytest.approx(1 - stats.norm.cdf((24.5 - 20) / 5))


def test_poisson_limit():
    # alpha -> 0 recovers Poisson exceedance
    p = S.exceedance_prob("negbin", [3.0], [1e-6], [4.0])
    assert p[0] == pytest.approx(1 - stats.poisson.cdf(3, 3.0), abs=1e-3)


# ------------------------------------------------------------------ price_frame end to end
def _joined(rows):
    return pd.DataFrame(rows)


def test_price_frame_fires_and_settles():
    df = _joined([
        # over fairly priced at 0.60 vs implied 0.476 (+150): gap 12.4pp, y=30 wins
        {"surface": "main", "dist_family": "normal", "mu": 27.0, "disp": 5.0, "y": 30,
         "side": "over", "line": 24.5, "threshold": np.nan, "odds": 150,
         "league": "nba", "stat": "points", "book_id": 68, "game_date": "2025-01-05",
         "name_key": "a", "date": "2025-01-05"},
        # milestone: P(X>=3) under NegBin(2.5, 0.2) ~ 0.468 vs implied 0.333 (+200)
        {"surface": "milestone", "dist_family": "negbin", "mu": 2.5, "disp": 0.2, "y": 2,
         "side": "yes", "line": np.nan, "threshold": 3.0, "odds": 200,
         "league": "nhl", "stat": "sog", "book_id": 68, "game_date": "2025-11-05",
         "name_key": "b", "date": "2025-11-05"},
    ])
    out = S.price_frame(df)
    r0 = out.iloc[0]
    assert r0["fair"] == pytest.approx(1 - stats.norm.cdf((24.5 - 27) / 5))
    assert r0["outcome"] == "win"
    assert r0["profit"] == pytest.approx(1.5)
    assert r0["gap"] > 0.08
    r1 = out.iloc[1]
    assert r1["outcome"] == "loss"
    assert r1["profit"] == -1.0
    assert r1["gap"] == pytest.approx(r1["fair"] - 100 / 300)


# ------------------------------------------------------------------ inference
def test_cluster_bootstrap_deterministic_and_sane():
    rng = np.random.default_rng(7)
    dates = np.repeat([f"2025-01-{d:02d}" for d in range(1, 31)], 10)
    profits = rng.normal(0.10, 1.0, size=len(dates))
    lo1, hi1 = S.cluster_bootstrap_ci(dates, profits, reps=500)
    lo2, hi2 = S.cluster_bootstrap_ci(dates, profits, reps=500)
    assert (lo1, hi1) == (lo2, hi2)
    assert lo1 < profits.mean() < hi1
    assert hi1 - lo1 > 0


def test_cluster_bootstrap_single_date_is_nan():
    lo, hi = S.cluster_bootstrap_ci(["2025-01-01"] * 5, [1.0, -1.0, 1.0, -1.0, 1.0])
    assert math.isnan(lo) and math.isnan(hi)


def test_cluster_robust_se_positive_skill():
    dates = np.repeat([f"2025-02-{d:02d}" for d in range(1, 21)], 5)
    profits = np.tile([1.0, 1.0, -1.0, 1.0, -1.0], 20)
    se = S.cluster_robust_se(dates, profits)
    assert se > 0
    assert S.cluster_robust_se(["2025-01-01"], [1.0]) != S.cluster_robust_se(["2025-01-01"], [1.0])  # nan != nan


def test_bh_qvalues_monotone():
    p = np.array([0.001, 0.01, 0.04, 0.20, 0.90])
    q = S.bh_qvalues(p)
    assert np.all(np.diff(q) >= -1e-12)
    assert q[0] == pytest.approx(0.001 * 5)
    assert q[-1] == pytest.approx(0.90)


def test_cell_stats_empty_and_pushes():
    empty = pd.DataFrame(columns=["outcome", "profit", "fair", "implied", "game_date"])
    st = S.cell_stats(empty)
    assert st["n_fired"] == 0 and math.isnan(st["roi"])
    bets = pd.DataFrame({
        "outcome": ["win", "loss", "push"], "profit": [0.9, -1.0, 0.0],
        "fair": [0.6, 0.4, 0.5], "implied": [0.5, 0.35, 0.45],
        "game_date": ["2025-01-01", "2025-01-02", "2025-01-03"]})
    st = S.cell_stats(bets)
    assert st["n_settled"] == 3 and st["n_push"] == 1
    assert st["roi"] == pytest.approx((0.9 - 1.0) / 3)
    assert st["hit_rate"] == pytest.approx(0.5)


def test_pop_standard_wiring():
    # a cell with n >= 100 and CI entirely above zero must pop; n < 100 must not
    rows = []
    for d in range(40):
        for i in range(3):
            rows.append({"surface": "main", "league": "nba", "stat": "points",
                         "book_id": 68, "side": "over", "gap": 0.10,
                         "outcome": "win", "profit": 1.2, "fair": 0.7, "implied": 0.45,
                         "game_date": f"2025-03-{(d % 28) + 1:02d}", "odds": 120})
    priced = pd.DataFrame(rows)
    grid = S.build_grid(priced)
    cell = grid[(grid["league"] == "nba") & (grid["stat"] == "points") & (grid["book_id"] == 68)
                & (grid["side"] == "over") & (grid["delta"] == 0.03)]
    assert len(cell) == 1
    assert bool(cell.iloc[0]["pop"]) is True
    small = grid[(grid["league"] == "nba") & (grid["stat"] == "points") & (grid["book_id"] == 15)
                 & (grid["side"] == "over") & (grid["delta"] == 0.03)]
    assert int(small.iloc[0]["n_scanned"]) == 0 and not bool(small.iloc[0]["pop"])
