"""Discrete predictive PMFs, U2/U4 blend & calibration metrics for MOD-04 Cluster (d).

All models output a full discrete PMF over {0..K}. Every p_over(line) is computed from the PMF.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize, special, stats

ALPHA_MIN = 1e-4
ALPHA_MAX = 8.0
MU_MIN = 1e-6
PROB_EPS = 1e-6


def _normalize_pmf(pmf: np.ndarray) -> np.ndarray:
    pmf = np.where(np.isfinite(pmf) & (pmf > 0.0), pmf, 0.0)
    sums = pmf.sum(axis=-1, keepdims=True)
    zero_mask = sums <= 0.0
    if np.any(zero_mask):
        pmf = pmf.copy()
        if pmf.ndim == 1:
            pmf[0] = 1.0
        else:
            pmf[zero_mask.squeeze(-1), 0] = 1.0
        sums = pmf.sum(axis=-1, keepdims=True)
    return pmf / sums


def negbin_pmf(mu, alpha, k_max: int = 80) -> np.ndarray:
    """Discrete NegBin PMF over {0..k_max} with Var = mu + alpha*mu^2 (alpha<=0 -> Poisson)."""
    mu_arr = np.asarray(mu, dtype=float)
    alpha_arr = np.asarray(0.0 if alpha is None else alpha, dtype=float)
    scalar = mu_arr.ndim == 0 and alpha_arr.ndim == 0
    mu_b, alpha_b = np.broadcast_arrays(np.atleast_1d(mu_arr), np.atleast_1d(alpha_arr))
    mu_s = np.maximum(mu_b, MU_MIN)
    ks = np.arange(0, k_max + 1, dtype=float)[None, :]

    pois = stats.poisson.pmf(ks, mu_s[:, None])
    a_safe = np.where(alpha_b > 0.0, np.clip(alpha_b, ALPHA_MIN, ALPHA_MAX), 1.0)
    n = (1.0 / a_safe)[:, None]
    p = n / (n + mu_s[:, None])
    nb = stats.nbinom.pmf(ks, n, p)
    pmf = np.where((alpha_b > 0.0)[:, None], nb, pois)
    pmf = _normalize_pmf(pmf)
    return pmf[0] if scalar else pmf


def negbin_logpmf(y, mu, alpha) -> np.ndarray:
    """Pointwise NegBin log-PMF at observed integer counts y (alpha<=0 -> Poisson)."""
    y_arr = np.asarray(y, dtype=float)
    mu_arr = np.maximum(np.asarray(mu, dtype=float), MU_MIN)
    alpha_arr = np.asarray(0.0 if alpha is None else alpha, dtype=float)
    y_b, mu_b, a_b = np.broadcast_arrays(y_arr, mu_arr, alpha_arr)
    pois = stats.poisson.logpmf(y_b, mu_b)
    a_safe = np.where(a_b > 0.0, np.clip(a_b, ALPHA_MIN, ALPHA_MAX), 1.0)
    n = 1.0 / a_safe
    p = n / (n + mu_b)
    nb = stats.nbinom.logpmf(y_b, n, p)
    return np.where(a_b > 0.0, nb, pois)


def skewnorm_params_from_moments(mu, var, shape) -> tuple[np.ndarray, np.ndarray]:
    """Solve (loc, scale) so SkewNormal(a=shape, loc, scale) has mean `mu` and variance `var`."""
    mu_arr = np.asarray(mu, dtype=float)
    var_arr = np.maximum(np.asarray(var, dtype=float), 1e-4)
    a_arr = np.asarray(shape, dtype=float)
    delta = a_arr / np.sqrt(1.0 + a_arr ** 2)
    denom = np.maximum(1.0 - (2.0 / np.pi) * (delta ** 2), 1e-4)
    scale = np.sqrt(var_arr / denom)
    loc = mu_arr - scale * delta * np.sqrt(2.0 / np.pi)
    return loc, scale


def skewnorm_pmf(loc, scale, shape, k_max: int = 80) -> np.ndarray:
    """Discretized Skew-Normal PMF over {0..k_max} via F(k+0.5) - F(k-0.5), normalized."""
    loc_arr = np.asarray(loc, dtype=float)
    scale_arr = np.maximum(np.asarray(scale, dtype=float), 1e-3)
    shape_arr = np.asarray(shape, dtype=float)
    scalar = loc_arr.ndim == 0 and scale_arr.ndim == 0 and shape_arr.ndim == 0
    loc_b, scale_b, shape_b = np.broadcast_arrays(
        np.atleast_1d(loc_arr), np.atleast_1d(scale_arr), np.atleast_1d(shape_arr)
    )
    edges = np.arange(-0.5, k_max + 1.5, 1.0)[None, :]
    cdfs = stats.skewnorm.cdf(
        edges, a=shape_b[:, None], loc=loc_b[:, None], scale=scale_b[:, None]
    )
    pmf = np.diff(cdfs, axis=-1)
    pmf = _normalize_pmf(pmf)
    return pmf[0] if scalar else pmf


def betabinom_negbin_pmf(
    mu_att,
    alpha_att,
    p_shrunk,
    conc: float | np.ndarray | None = 25.0,
    k_max: int = 16,
    k_att_max: int = 45,
) -> np.ndarray:
    """Simulation-free PMF for makes over NegBin-distributed attempts.

    attempts ~ NegBin(mu_att, alpha_att) over 0..k_att_max.
    makes | attempts=a ~ BetaBinomial(a, conc*p, conc*(1-p)) (or Binomial(a, p) when conc is None/inf).
    """
    mu_a = np.asarray(mu_att, dtype=float)
    al_a = np.asarray(alpha_att, dtype=float)
    p_a = np.clip(np.asarray(p_shrunk, dtype=float), 1e-4, 1.0 - 1e-4)
    scalar = mu_a.ndim == 0 and al_a.ndim == 0 and p_a.ndim == 0
    mu_b, al_b, p_b = np.broadcast_arrays(
        np.atleast_1d(mu_a), np.atleast_1d(al_a), np.atleast_1d(p_a)
    )
    p_att = negbin_pmf(mu_b, al_b, k_max=k_att_max)  # (n, k_att_max + 1)

    att_grid = np.arange(0, k_att_max + 1, dtype=int)[None, :, None]  # (1, A, 1)
    k_grid = np.arange(0, k_max + 1, dtype=int)[None, None, :]  # (1, 1, K)

    if conc is None or (np.ndim(conc) == 0 and np.isinf(conc)):
        cond = stats.binom.pmf(k_grid, att_grid, p_b[:, None, None])
    else:
        c_b = np.maximum(np.broadcast_to(np.asarray(conc, dtype=float), mu_b.shape), 0.5)
        a_beta = (c_b * p_b)[:, None, None]
        b_beta = (c_b * (1.0 - p_b))[:, None, None]
        cond = stats.betabinom.pmf(k_grid, att_grid, a_beta, b_beta)
    cond = np.where(np.isfinite(cond), cond, 0.0)
    pmf = np.einsum("na,nak->nk", p_att, cond)
    pmf = _normalize_pmf(pmf)
    return pmf[0] if scalar else pmf


def scale_pmf(pmf: np.ndarray, multiplier: int, k_max: int) -> np.ndarray:
    """Transform PMF of X into PMF of (multiplier * X) on support {0..k_max}."""
    pmf = np.asarray(pmf, dtype=float)
    is_1d = pmf.ndim == 1
    p2 = pmf[None, :] if is_1d else pmf
    out = np.zeros((p2.shape[0], k_max + 1), dtype=float)
    max_src = min(p2.shape[1] - 1, k_max // multiplier)
    idx = np.arange(0, max_src + 1) * multiplier
    out[:, idx] = p2[:, : max_src + 1]
    out = _normalize_pmf(out)
    return out[0] if is_1d else out


def convolve_pmfs(pmf_a: np.ndarray, pmf_b: np.ndarray, k_max: int | None = None) -> np.ndarray:
    """Row-wise discrete convolution of two independent PMFs over {0..k_max}."""
    a = np.asarray(pmf_a, dtype=float)
    b = np.asarray(pmf_b, dtype=float)
    is_1d = a.ndim == 1 and b.ndim == 1
    a2 = a[None, :] if a.ndim == 1 else a
    b2 = b[None, :] if b.ndim == 1 else b
    full_len = a2.shape[1] + b2.shape[1] - 1
    out_len = (k_max + 1) if k_max is not None else full_len
    n_fft = int(2 ** np.ceil(np.log2(full_len)))
    conv = np.fft.irfft(np.fft.rfft(a2, n=n_fft, axis=-1) * np.fft.rfft(b2, n=n_fft, axis=-1), n=n_fft, axis=-1)
    conv = conv[:, :out_len]
    conv = _normalize_pmf(conv)
    return conv[0] if is_1d else conv


def pmf_p_over(pmf: np.ndarray, line) -> np.ndarray:
    """Compute P(Y > line) strictly from the discrete PMF (half-point and whole-number safe)."""
    p = np.asarray(pmf, dtype=float)
    is_1d = p.ndim == 1
    p2 = p[None, :] if is_1d else p
    lines = np.broadcast_to(np.asarray(line, dtype=float), (p2.shape[0],))
    k_cut = np.floor(lines).astype(int)
    cdf = np.cumsum(p2, axis=-1)
    max_k = p2.shape[1] - 1
    p_le = np.where(k_cut < 0, 0.0, np.where(k_cut >= max_k, 1.0, cdf[np.arange(p2.shape[0]), np.clip(k_cut, 0, max_k)]))
    out = np.clip(1.0 - p_le, 0.0, 1.0)
    return float(out[0]) if is_1d and np.ndim(line) == 0 else out


def pmf_mean(pmf: np.ndarray) -> np.ndarray:
    p = np.asarray(pmf, dtype=float)
    ks = np.arange(p.shape[-1], dtype=float)
    return np.sum(p * ks, axis=-1)


def pmf_log_loss(y_counts, pmf: np.ndarray, eps: float = 1e-12) -> float:
    """Mean negative log-likelihood (-log p(y)) of integer outcomes under discrete PMFs."""
    y = np.asarray(y_counts, dtype=int)
    p = np.asarray(pmf, dtype=float)
    y_clip = np.clip(y, 0, p.shape[1] - 1)
    probs = np.clip(p[np.arange(len(y)), y_clip], eps, 1.0)
    return float(-np.mean(np.log(probs)))


def pmf_log_likelihood(y_counts, pmf: np.ndarray, eps: float = 1e-12) -> float:
    """Mean log-likelihood (+log p(y)) of integer outcomes under discrete PMFs."""
    return -pmf_log_loss(y_counts, pmf, eps=eps)


# -----------------------------------------------------------------------------
# U4 blend & binary probability metrics
# -----------------------------------------------------------------------------
def _clip_prob(p) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=float), PROB_EPS, 1.0 - PROB_EPS)


def blend_probs(p_model, p_market, c: float) -> np.ndarray:
    """U4: logit(p_served) = logit(p_market) + c * (logit(p_model) - logit(p_market))."""
    p_mkt = np.asarray(p_market, dtype=float)
    if float(c) == 0.0:
        return p_mkt.copy() if p_mkt.ndim > 0 else float(p_mkt)
    lm = special.logit(_clip_prob(p_mkt))
    lmod = special.logit(_clip_prob(p_model))
    out = special.expit(lm + float(c) * (lmod - lm))
    return float(out) if np.ndim(p_model) == 0 and np.ndim(p_market) == 0 else out


def log_loss(y_true, p_pred, eps: float = 1e-12) -> float:
    """Binary log-loss for y_true in {0, 1}."""
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(p_pred, dtype=float), eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def fit_blend_c(y_true, p_model, p_market, step: float = 0.05) -> float:
    """Grid search c in [0, 1] with `step` minimizing OOF binary log-loss (U4)."""
    grid = np.round(np.arange(0.0, 1.0 + 1e-9, step), 6)
    best_c = 0.0
    best_ll = np.inf
    for c in grid:
        p = blend_probs(p_model, p_market, float(c))
        ll = log_loss(y_true, p)
        if ll < best_ll - 1e-12:
            best_ll = ll
            best_c = float(c)
    return best_c


def brier_score(y_true, p_pred) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p_pred, dtype=float)
    return float(np.mean((p - y) ** 2))


def ece(y_true, p_pred, n_bins: int = 10) -> float:
    """Expected Calibration Error across `n_bins` equal-width bins in [0, 1]."""
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(p_pred, dtype=float), 0.0, 1.0)
    if len(y) == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    total = 0.0
    n = float(len(y))
    for i in range(n_bins):
        if i == n_bins - 1:
            mask = (p >= edges[i]) & (p <= edges[i + 1])
        else:
            mask = (p >= edges[i]) & (p < edges[i + 1])
        if np.any(mask):
            acc = float(np.mean(y[mask]))
            conf = float(np.mean(p[mask]))
            total += (float(np.sum(mask)) / n) * abs(acc - conf)
    return float(total)


def calibration_params(y_true, p_pred) -> tuple[float, float]:
    """Fit logistic recalibration: logit(P(Y=1)) = intercept + slope * logit(p_pred) via scipy.optimize."""
    y = np.asarray(y_true, dtype=float)
    x = special.logit(_clip_prob(p_pred))

    def _nll(params: np.ndarray) -> float:
        a, b = params[0], params[1]
        z = a + b * x
        # stable binary cross-entropy with logits + tiny L2 to avoid divergence on separable toy inputs
        loss = np.mean(np.logaddexp(0.0, z) - y * z)
        return float(loss + 1e-6 * (a ** 2 + (b - 1.0) ** 2))

    res = optimize.minimize(_nll, x0=np.array([0.0, 1.0]), method="BFGS")
    return float(res.x[0]), float(res.x[1])


def calibration_slope(y_true, p_pred) -> float:
    """Return logistic recalibration slope of logit(p_pred) vs binary outcome."""
    return calibration_params(y_true, p_pred)[1]
