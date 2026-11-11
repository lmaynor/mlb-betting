"""scripts/kalshi_sgp_rho.py: bivariate-normal CDF, implied rho from a joint price, copula MLE recovery."""
import importlib.util
import os

import numpy as np
import pytest
from scipy.stats import multivariate_normal, norm


def _mod():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("kalshi_sgp_rho", os.path.join(root, "scripts", "kalshi_sgp_rho.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_bvn_cdf_matches_scipy():
    m = _mod()
    for h, k, rho in ((0.3, -0.5, 0.4), (-1.2, 0.8, -0.6), (1.5, 1.5, 0.9), (0.0, 0.0, 0.0), (-2.0, -1.0, 0.7)):
        want = multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]]).cdf([h, k])
        assert float(m.bvn_cdf(h, k, rho)) == pytest.approx(want, abs=2e-4)


def test_implied_rho_round_trips_the_joint():
    m = _mod()
    q1, q2 = np.array([0.5, 0.3, 0.7]), np.array([0.4, 0.2, 0.6])
    for rho in (-0.4, 0.0, 0.5):
        z1, z2 = norm.ppf(q1), norm.ppf(q2)
        y = m.bvn_cdf(z1, z2, np.full(3, rho))
        got, ok = m.implied_rho(q1, q2, y)
        assert ok.all() and got == pytest.approx(np.full(3, rho), abs=0.01)


def test_implied_rho_flags_prices_outside_the_frechet_bounds():
    m = _mod()
    rho, ok = m.implied_rho([0.5], [0.4], [0.45])                        # joint above min(q1, q2) = 0.4: impossible
    assert not ok[0]


def test_mle_rho_recovers_a_planted_correlation():
    m = _mod()
    rng = np.random.default_rng(0)
    n, rho = 6000, 0.35
    z = rng.multivariate_normal([0, 0], [[1, rho], [rho, 1]], n)
    p1, p2 = rng.uniform(0.25, 0.75, n), rng.uniform(0.25, 0.75, n)
    o1, o2 = (z[:, 0] > norm.ppf(1 - p1)).astype(int), (z[:, 1] > norm.ppf(1 - p2)).astype(int)
    assert m.mle_rho(p1, p2, o1, o2) == pytest.approx(rho, abs=0.06)
    o2_indep = (rng.uniform(size=n) < p2).astype(int)
    assert abs(m.mle_rho(p1, p2, o1, o2_indep)) < 0.06


def test_pricer_rho_is_relation_keyed_with_per_relation_defaults():
    """scripts/kalshi_sgp_rho.pricer_rho reads the taxonomy table: same stat pair, different relations, different rho;
    unmeasured different-player -> pooled 0.081; unmeasured same-player -> None (no default prices it)."""
    m = _mod()
    assert m.pricer_rho("points", "threes", "same_player") == pytest.approx(0.70)
    assert m.pricer_rho("points", "threes", "same_team_diff_player") == pytest.approx(0.18)
    assert m.pricer_rho("points", "threes", "cross_team") == pytest.approx(0.18)
    assert m.pricer_rho("points", "threes") == pytest.approx(0.18)                    # default relation = pooled different-player bucket
    assert m.pricer_rho("blocks", "steals", "cross_team") == pytest.approx(0.081)
    assert m.pricer_rho("blocks", "steals", "same_player") is None
    l1 = {"player": "p1", "team": "BUF"}
    assert m._pair_relation(l1, {"player": "p1", "team": "BUF"}) == "same_player"
    assert m._pair_relation(l1, {"player": "p2", "team": "KC"}) == "cross_team"
    assert m._pair_relation({"player": "p1"}, {"player": "p2"}) == "same_team_diff_player"   # legacy legs files carry no team


def test_fetch_results_maps_yes_no_and_skips_unsettled():
    m = _mod()

    class R:
        status_code = 200
        def json(self):
            return {"markets": [{"ticker": "A", "result": "yes"}, {"ticker": "B", "result": "no"}, {"ticker": "C", "result": ""}]}

    class S:
        def get(self, *a, **k):
            return R()
    assert m.fetch_results(["A", "B", "C"], session=S(), pause=0) == {"A": 1, "B": 0}
