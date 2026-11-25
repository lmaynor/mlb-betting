import math

import pytest
from scipy.stats import nbinom, poisson

from nba.props.joint_combo import (
    alpha_from_mu_var,
    combo_alpha,
    fit_kappa,
    negbin_params,
    p_over_joint,
    var_from_p10_p90,
)


def test_p_over_joint_negbin_matches_scipy_reference():
    cases = [
        (24.5, 60.0, 23.5),
        (24.5, 60.0, 24.5),
        (12.0, 30.0, 10.5),
        (12.0, 30.0, 12.0),
        (8.3, 15.2, 0.5),
        (15.0, 40.0, -0.5),
    ]
    for mu, var, line in cases:
        r = mu ** 2 / (var - mu)
        p = r / (r + mu)
        expected = float(nbinom.sf(math.floor(line), r, p))
        assert p_over_joint(mu, var, line) == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_p_over_joint_poisson_fallback_when_var_le_mu():
    for mu, var, line in [
        (10.0, 10.0, 9.5),
        (10.0, 6.0, 10.5),
        (5.5, 3.0, 5.0),
        (4.0, 0.0, 3.5),
    ]:
        expected = float(poisson.sf(math.floor(line), mu))
        assert p_over_joint(mu, var, line) == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_p_over_joint_handles_invalid_and_nonpositive_mu():
    assert p_over_joint(None, 10.0, 5.5) is None
    assert p_over_joint(10.0, 20.0, None) is None
    assert p_over_joint(float("nan"), 10.0, 5.5) is None
    assert p_over_joint(10.0, 20.0, float("inf")) is None
    assert p_over_joint(10.0, float("nan"), 5.5) is None
    assert p_over_joint(0.0, 5.0, 0.5) == 0.0
    assert p_over_joint(0.0, 5.0, 0.0) == 0.0
    assert p_over_joint(-1.0, 5.0, -0.5) == 1.0


def test_fit_kappa_shrinkage_and_guards():
    raw0, shrunk0 = fit_kappa(100.0, 50.0, 0)
    assert math.isnan(raw0)
    assert shrunk0 == 1.0

    raw_zero_var, shrunk_zero_var = fit_kappa(100.0, 0.0, 10)
    assert math.isnan(raw_zero_var)
    assert shrunk_zero_var == 1.0

    raw, shrunk = fit_kappa(60.0, 40.0, 20, shrink=20.0)
    assert raw == pytest.approx(1.5)
    assert shrunk == pytest.approx(1.25)

    raw_big, shrunk_big = fit_kappa(150_000.0, 100_000.0, 100_000, shrink=20.0)
    assert raw_big == pytest.approx(1.5)
    assert abs(shrunk_big - raw_big) < 5e-4


def test_negbin_params_moment_recovery():
    for mu, var in [(18.4, 42.7), (31.2, 95.5), (6.5, 11.0)]:
        params = negbin_params(mu, var)
        assert params is not None
        r, p = params
        recovered_mu = r * (1.0 - p) / p
        recovered_var = r * (1.0 - p) / (p ** 2)
        assert recovered_mu == pytest.approx(mu, rel=1e-10)
        assert recovered_var == pytest.approx(var, rel=1e-10)

    assert negbin_params(10.0, 10.0) is None
    assert negbin_params(10.0, 8.0) is None
    assert negbin_params(0.0, 5.0) is None


def test_var_from_p10_p90_normal_recovery():
    mu = 22.0
    sd = 5.0
    p10 = mu - 1.2816 * sd
    p90 = mu + 1.2816 * sd
    assert var_from_p10_p90(p10, p90) == pytest.approx(25.0, rel=1e-6)


def test_alpha_from_mu_var_and_combo_alpha():
    mu = 20.0
    var = 68.0
    alpha = alpha_from_mu_var(mu, var)
    assert mu + alpha * (mu ** 2) == pytest.approx(var, rel=1e-12)
    assert alpha_from_mu_var(mu, mu) == 0.0
    assert alpha_from_mu_var(mu, mu - 5.0) == 0.0
    assert combo_alpha(mu, sd=6.0, kappa=1.5) == pytest.approx(
        alpha_from_mu_var(mu, 1.5 * 36.0), rel=1e-12
    )
