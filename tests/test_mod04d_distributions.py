from __future__ import annotations

import numpy as np
import pytest

from mod04d.distributions import (
    betabinom_negbin_pmf,
    blend_probs,
    brier_score,
    calibration_slope,
    convolve_pmfs,
    ece,
    fit_blend_c,
    log_loss,
    negbin_pmf,
    pmf_mean,
    pmf_p_over,
    scale_pmf,
    skewnorm_params_from_moments,
    skewnorm_pmf,
)


def test_negbin_pmf_sum_mean_and_monotone_p_over() -> None:
    mu = np.array([4.5, 18.0, 28.5])
    alpha = np.array([0.05, 0.12, 0.08])
    pmf = negbin_pmf(mu, alpha, k_max=90)
    assert pmf.shape == (3, 91)
    assert np.allclose(pmf.sum(axis=-1), 1.0, atol=1e-6)
    assert np.allclose(pmf_mean(pmf), mu, rtol=0.02, atol=0.1)

    lines = np.array([15.5, 18.5, 22.5, 27.5])
    p_row = pmf_p_over(np.repeat(pmf[1:2], len(lines), axis=0), lines)
    assert np.all(np.diff(p_row) <= 1e-12)


def test_skewnorm_pmf_sums_to_one_and_preserves_moments() -> None:
    mu = np.array([12.0, 24.0])
    var = np.array([25.0, 64.0])
    shape = np.array([1.5, 2.5])
    loc, scale = skewnorm_params_from_moments(mu, var, shape)
    pmf = skewnorm_pmf(loc, scale, shape, k_max=80)
    assert pmf.shape == (2, 81)
    assert np.allclose(pmf.sum(axis=-1), 1.0, atol=1e-6)
    assert np.allclose(pmf_mean(pmf), mu, atol=0.3)


def test_betabinom_negbin_pmf_sums_to_one() -> None:
    mu_att = np.array([2.5, 7.0, 10.5])
    alpha_att = np.array([0.08, 0.15, 0.10])
    p_shrunk = np.array([0.33, 0.38, 0.41])
    pmf = betabinom_negbin_pmf(mu_att, alpha_att, p_shrunk, conc=25.0, k_max=16, k_att_max=45)
    assert pmf.shape == (3, 17)
    assert np.allclose(pmf.sum(axis=-1), 1.0, atol=1e-6)
    # Binomial (conc=None) also sums to 1 and mean matches mu_att * p_shrunk
    pmf_bin = betabinom_negbin_pmf(mu_att, alpha_att, p_shrunk, conc=None, k_max=16, k_att_max=45)
    assert np.allclose(pmf_bin.sum(axis=-1), 1.0, atol=1e-6)
    assert np.allclose(pmf_mean(pmf_bin), mu_att * p_shrunk, atol=0.1)


def test_convolution_and_scaling() -> None:
    p_a = negbin_pmf(2.0, 0.05, k_max=20)
    p_b = scale_pmf(negbin_pmf(3.0, 0.05, k_max=20), 2, k_max=40)
    p_sum = convolve_pmfs(p_a, p_b, k_max=50)
    assert np.isclose(p_sum.sum(), 1.0, atol=1e-6)
    assert np.isclose(pmf_mean(p_sum), 2.0 + 2 * 3.0, atol=0.05)


def test_u4_blend_and_metrics() -> None:
    rng = np.random.default_rng(11)
    n = 400
    p_true = rng.uniform(0.15, 0.85, size=n)
    y = rng.binomial(1, p_true)
    p_mkt = np.clip(0.5 + 0.3 * (p_true - 0.5), 0.05, 0.95)
    p_mod = np.clip(p_true + rng.normal(0.0, 0.03, size=n), 0.05, 0.95)

    assert np.allclose(blend_probs(p_mod, p_mkt, 0.0), p_mkt)
    c = fit_blend_c(y, p_mod, p_mkt)
    assert 0.5 <= c <= 1.0
    p_srv = blend_probs(p_mod, p_mkt, c)
    assert log_loss(y, p_srv) <= log_loss(y, p_mkt)
    assert 0.0 <= brier_score(y, p_srv) <= 0.5
    assert 0.0 <= ece(y, p_srv, n_bins=10) <= 1.0
    slope = calibration_slope(y, p_srv)
    assert 0.5 <= slope <= 2.0
