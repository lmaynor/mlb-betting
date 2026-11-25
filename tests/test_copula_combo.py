"""Unit tests for nba.props.copula_combo (MOD-04 Cluster (g)).

SHADOW-ONLY: combo weight c=0.00.
Per RUL-20261124, market-anchored marginals (recovered from served board quantiles)
are a serving-input fallback ONLY -- NOT training features. Prop lines and odds are
banned as training features and must never enter any fit.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from nba.props.copula_combo import (
    ARCHETYPES,
    BoardMarginalProvider,
    CopulaEngine,
    DictMarginalProvider,
    Marginal,
    archetype_for_position,
    derive_from_draws,
    fit_sigma_role,
    gaussianize,
    marginal_cdf,
    marginal_gate,
    p_over_from_pmf,
    pit_residual,
    recover_marginal_from_board_row,
    student_t_ablation,
)


def _fixed_sigma():
    mat = np.array([
        [1.00, 0.35, 0.30],
        [0.35, 1.00, 0.25],
        [0.30, 0.25, 1.00],
    ])
    return {arch: mat.copy() for arch in ARCHETYPES}


def test_marginal_consistency():
    profiles = [
        ("primary_guard", {
            "points": Marginal("points", "normal", 22.0, 50.0),
            "rebounds": Marginal("rebounds", "negbin", 4.5, 7.5),
            "assists": Marginal("assists", "negbin", 8.0, 15.0),
        }),
        ("wing", {
            "points": Marginal("points", "normal", 18.0, 40.0),
            "rebounds": Marginal("rebounds", "negbin", 5.5, 9.5),
            "assists": Marginal("assists", "negbin", 3.5, 6.0),
        }),
        ("stretch_big", {
            "points": Marginal("points", "normal", 16.0, 36.0),
            "rebounds": Marginal("rebounds", "negbin", 8.0, 15.0),
            "assists": Marginal("assists", "negbin", 2.5, 4.5),
        }),
        ("rim_big", {
            "points": Marginal("points", "normal", 14.0, 30.0),
            "rebounds": Marginal("rebounds", "negbin", 11.0, 22.0),
            "assists": Marginal("assists", "negbin", 2.0, 3.8),
        }),
    ]
    engine = CopulaEngine(
        sigma_by_archetype=_fixed_sigma(),
        cv_min_by_archetype=0.18,
        n_draws=20000,
        seed=20261125,
    )
    ks = np.arange(0, 41)
    for arch, margs in profiles:
        draws = engine.sample_player(margs, arch)
        assert draws.shape == (20000, 3)
        assert np.issubdtype(draws.dtype, np.integer)
        assert np.all(draws >= 0)
        for j, stat in enumerate(("points", "rebounds", "assists")):
            m = margs[stat]
            col = draws[:, j]
            emp_cdf = np.mean(col[:, None] <= ks[None, :], axis=0)
            true_cdf = marginal_cdf(ks, m)
            sup_diff = float(np.max(np.abs(emp_cdf - true_cdf)))
            assert sup_diff <= 0.03, f"{arch}/{stat} sup_diff={sup_diff:.4f}"
            assert abs(float(np.mean(col)) - m.mu) / m.mu <= 0.05
            assert abs(float(np.var(col)) - m.var) / m.var <= 0.05


def test_combo_pmfs_sum_and_ddtd():
    margs = {
        "points": Marginal("points", "normal", 21.0, 45.0),
        "rebounds": Marginal("rebounds", "negbin", 9.0, 17.0),
        "assists": Marginal("assists", "negbin", 7.0, 13.0),
    }
    engine = CopulaEngine(_fixed_sigma(), 0.18, n_draws=2000, seed=12345)
    draws = engine.sample_player(margs, "wing")
    derived = derive_from_draws(draws)

    for combo in ("pr", "pa", "pra", "ra"):
        pmf = derived["pmfs"][combo]
        assert abs(sum(pmf.values()) - 1.0) < 1e-9

    assert derived["p_dd"] >= derived["p_td"]
    expected_td = float(np.mean((draws[:, 0] >= 10) & (draws[:, 1] >= 10) & (draws[:, 2] >= 10)))
    assert math.isclose(derived["p_td"], expected_td, abs_tol=1e-12)
    assert derived["mean_pra"] > 0.0
    assert derived["var_pra"] > 0.0


def test_g1_gate():
    ece_bad = {
        ("points", "wing"): 0.041,
        ("rebounds", "wing"): 0.020,
        ("assists", "wing"): 0.015,
    }
    ok, failing = marginal_gate(ece_bad, "wing", threshold=0.035)
    assert not ok
    assert failing == ["points"]

    ece_good = {
        ("points", "wing"): 0.025,
        ("rebounds", "wing"): 0.030,
        ("assists", "wing"): 0.035,
    }
    ok2, failing2 = marginal_gate(ece_good, "wing", threshold=0.035)
    assert ok2
    assert failing2 == []

    # Fallback key hierarchy: (stat, archetype) -> (stat, '*') -> stat, plus string pipe keys
    ece_fallback = {
        ("points", "*"): 0.010,
        "rebounds|*": 0.040,
        "assists": 0.020,
    }
    ok3, failing3 = marginal_gate(ece_fallback, "rim_big", threshold=0.035)
    assert not ok3
    assert failing3 == ["rebounds"]


def test_determinism():
    margs = {
        "points": Marginal("points", "normal", 19.0, 38.0),
        "rebounds": Marginal("rebounds", "negbin", 6.0, 11.0),
        "assists": Marginal("assists", "negbin", 4.0, 7.5),
    }
    sigma = _fixed_sigma()
    e1 = CopulaEngine(sigma, 0.18, n_draws=2000, seed=20261125)
    e2 = CopulaEngine(sigma, 0.18, n_draws=2000, seed=20261125)
    e3 = CopulaEngine(sigma, 0.18, n_draws=2000, seed=999999)

    d1 = e1.sample_player(margs, "primary_guard")
    d2 = e2.sample_player(margs, "primary_guard")
    d3 = e3.sample_player(margs, "primary_guard")

    assert np.array_equal(d1, d2)
    assert not np.array_equal(d1, d3)


def test_board_marginal_recovery():
    mu_pts, var_pts = 24.5, 49.0
    z90 = 1.2815515655446004
    sd_pts = math.sqrt(var_pts)
    pts_row = {
        "player": "Test Guard",
        "player_norm": "test guard",
        "stat": "points",
        "proj_mean": mu_pts,
        "proj_p10": mu_pts - z90 * sd_pts,
        "proj_p90": mu_pts + z90 * sd_pts,
    }
    m_pts = recover_marginal_from_board_row(pts_row)
    assert m_pts.family == "normal"
    assert m_pts.mu == mu_pts
    assert abs(m_pts.var - var_pts) / var_pts < 1e-9

    mu_reb, var_reb = 12.0, 30.0
    alpha_true = (var_reb - mu_reb) / (mu_reb ** 2)
    n_true = 1.0 / alpha_true
    p_true = n_true / (n_true + mu_reb)
    reb_row = {
        "player": "Test Big",
        "player_norm": "test big",
        "stat": "rebounds",
        "proj_mean": mu_reb,
        "proj_p10": float(stats.nbinom.ppf(0.10, n_true, p_true)),
        "proj_p90": float(stats.nbinom.ppf(0.90, n_true, p_true)),
    }
    m_reb = recover_marginal_from_board_row(reb_row)
    assert m_reb.family == "negbin"
    assert m_reb.mu == mu_reb
    assert abs(m_reb.var - var_reb) / var_reb <= 0.10

    provider = BoardMarginalProvider([pts_row, reb_row], cv=0.17)
    assert provider.minutes_cv("test guard") == 0.17
    assert provider.marginals("test guard")["points"].mu == mu_pts
    assert provider.marginals("test big")["rebounds"].family == "negbin"


def test_p_over_convention():
    pmf = {8: 0.10, 9: 0.25, 10: 0.30, 11: 0.20, 12: 0.15}
    # Half-point line 9.5 -> P(X >= 10) = 0.30 + 0.20 + 0.15 = 0.65
    assert math.isclose(p_over_from_pmf(pmf, 9.5), 0.65, abs_tol=1e-12)
    # Whole line 10.0 -> P(X >= 11) = 0.20 + 0.15 = 0.35 (push at 10 excluded)
    assert math.isclose(p_over_from_pmf(pmf, 10.0), 0.35, abs_tol=1e-12)


def test_archetype_mapping():
    assert archetype_for_position("PG") == "primary_guard"
    assert archetype_for_position("SG") == "wing"
    assert archetype_for_position("SF") == "wing"
    assert archetype_for_position("PF") == "stretch_big"
    assert archetype_for_position("C") == "rim_big"
    assert archetype_for_position("PG-SG") == "primary_guard"
    assert archetype_for_position("PF/C") == "stretch_big"
    assert archetype_for_position("C-PF") == "rim_big"
    assert archetype_for_position("G") == "wing"
    assert archetype_for_position("unknown") == "wing"
    assert archetype_for_position(None) == "wing"


def test_fit_sigma_and_ablation_smoke():
    rng = np.random.default_rng(7)
    recs = []
    for i in range(60):
        z = rng.multivariate_normal([0, 0, 0], [[1, 0.3, 0.2], [0.3, 1, 0.2], [0.2, 0.2, 1]])
        recs.append({"archetype": "wing", "z": z})
    for i in range(10):
        z = rng.multivariate_normal([0, 0, 0], [[1, -0.5, 0.0], [-0.5, 1, 0.0], [0.0, 0.0, 1]])
        recs.append({"archetype": "rim_big", "z": z})

    sigma, counts = fit_sigma_role(recs, prior_weight=200.0)
    assert counts["wing"] == 60
    assert counts["rim_big"] == 10
    # n < 30 falls back to global matrix (same as primary_guard which has n=0)
    assert np.allclose(sigma["rim_big"], sigma["primary_guard"])
    for mat in sigma.values():
        assert np.allclose(np.diag(mat), 1.0)
        assert np.min(np.linalg.eigvalsh(mat)) >= 1e-7

    # PIT + gaussianize sanity
    m = Marginal("rebounds", "negbin", 7.0, 12.0)
    u = pit_residual(7, m, rng=rng)
    assert 0.0 <= u <= 1.0
    assert math.isfinite(gaussianize(u))

    # DictMarginalProvider + student_t_ablation smoke
    margs = {
        "points": Marginal("points", "normal", 18.0, 38.0),
        "rebounds": Marginal("rebounds", "negbin", 7.0, 12.0),
        "assists": Marginal("assists", "negbin", 5.0, 9.0),
    }
    prov = DictMarginalProvider({"p1": margs}, cv=0.18, archetypes={"p1": "wing"})
    assert prov.minutes_cv("p1") == 0.18
    ab = student_t_ablation(
        [{"marginals": margs, "archetype": "wing", "actual": (20, 8, 6)}],
        sigma,
        0.18,
        nu_grid=(6, 12),
    )
    assert ab["n_records"] == 1
    assert "gaussian" in ab["variants"]
    assert "t_6" in ab["variants"]
    assert isinstance(ab["adopt_student_t"], bool)
