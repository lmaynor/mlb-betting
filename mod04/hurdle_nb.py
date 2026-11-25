"""MOD-04 Cluster (a) Distributional Hurdle-NB machinery with 5-point Gauss-Hermite quadrature.

Count model for NBA steals & blocks:
  * Stage 1 supplies a player-game minutes distribution ``M ~ N(mu_min, sigma_min^2)``
    clipped at ``M >= 0``.
  * Stage 2 supplies ``(pi, lambda_rate, alpha)`` where ``lambda_rate`` is the per-minute
    event rate, ``alpha > 0`` is the Negative Binomial overdispersion
    (``Var(Y | M) = mu + alpha * mu^2`` with ``mu = lambda_rate * M``), and
    ``pi in [0, 1)`` is the structural zero-hurdle mixture weight:
        ``P(Y = 0 | M) = pi + (1 - pi) * NB(0 | lambda_rate * M, alpha)``
        ``P(Y = k | M) = (1 - pi) * NB(k | lambda_rate * M, alpha)``   for ``k >= 1``.
  * Setting ``pi = 0.0`` recovers the exact plain Negative Binomial distribution (used for
    the ruling's mandatory blocks Hurdle-NB vs plain-NB ablation).
  * Marginal PMF/CDF integrates over a 5-point Gauss-Hermite quadrature of ``M``:
        ``M_q = max(0, mu_min + sqrt(2) * sigma_min * x_q)``, weights ``w_q / sqrt(pi)``.
  * Includes ``shift_lambda_for_p_over`` for U4 rung coherence: shifts ``lambda_rate`` so
    ``P(Y > L_main) == p_served`` while preserving ``alpha`` and ``pi``.
"""
from __future__ import annotations

from functools import lru_cache
import math

import numpy as np
from numpy.polynomial.hermite import hermgauss
from scipy.optimize import brentq
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

DEFAULT_GH_POINTS = 5
DEFAULT_K_MAX = 20
ALPHA_MIN = 1e-6
EPS = 1e-12


@lru_cache(maxsize=16)
def _hermgauss_norm(n_points: int) -> tuple[np.ndarray, np.ndarray]:
    xs, ws = hermgauss(n_points)
    weights = ws / math.sqrt(math.pi)
    weights = weights / weights.sum()
    return xs.astype(float), weights.astype(float)


def gh_minutes_quadrature(
    mu_min: float,
    sigma_min: float,
    n_points: int = DEFAULT_GH_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(minutes_nodes, weights)`` for Gauss-Hermite quadrature clipped at ``>= 0``."""
    mu = float(mu_min)
    sd = float(sigma_min)
    if not math.isfinite(mu) or mu <= 0.0:
        return np.array([0.0], dtype=float), np.array([1.0], dtype=float)
    if not math.isfinite(sd) or sd <= 1e-9:
        return np.array([mu], dtype=float), np.array([1.0], dtype=float)
    xs, weights = _hermgauss_norm(n_points)
    nodes = np.maximum(0.0, mu + math.sqrt(2.0) * sd * xs)
    return nodes.astype(float), weights


def _nb_pmf_grid(ks: np.ndarray, mu: np.ndarray, alpha: float) -> np.ndarray:
    """Evaluate NB(mu, alpha) PMF on ``ks`` (K,) x ``mu`` (Q,) -> (Q, K).

    Uses Poisson when ``alpha <= ALPHA_MIN`` and exact point mass at 0 when ``mu <= 0``.
    """
    ks = np.asarray(ks, dtype=float)
    mu = np.asarray(mu, dtype=float)
    out = np.zeros((len(mu), len(ks)), dtype=float)
    zero_mu = mu <= 0.0
    if zero_mu.any():
        out[zero_mu, 0] = 1.0
    pos = ~zero_mu
    if pos.any():
        m = np.maximum(mu[pos], EPS)[:, None]
        k_grid = ks[None, :]
        if alpha <= ALPHA_MIN:
            out[pos] = poisson.pmf(k_grid, m)
        else:
            r = 1.0 / float(alpha)
            # Log-space evaluation is stable even for very small m or large k
            log_p = (
                gammaln(k_grid + r)
                - gammaln(r)
                - gammaln(k_grid + 1.0)
                + r * np.log(r / (r + m))
                + k_grid * np.log(m / (r + m))
            )
            out[pos] = np.exp(log_p)
    return out


def hurdle_nb_pmf(
    pi: float,
    lambda_rate: float,
    alpha: float,
    mu_min: float,
    sigma_min: float,
    k_max: int = DEFAULT_K_MAX,
    n_quad: int = DEFAULT_GH_POINTS,
) -> np.ndarray:
    """Marginal PMF over ``k = 0..k_max`` via Gauss-Hermite minutes quadrature.

    Setting ``pi = 0.0`` gives the plain-NB (or Poisson if ``alpha <= ALPHA_MIN``) PMF.
    """
    pi_c = float(np.clip(pi, 0.0, 1.0 - 1e-9))
    lam = max(float(lambda_rate), 0.0)
    alp = max(float(alpha), 0.0)
    nodes, weights = gh_minutes_quadrature(mu_min, sigma_min, n_points=n_quad)
    mu_nodes = lam * nodes
    ks = np.arange(0, int(k_max) + 1, dtype=float)
    nb_mat = _nb_pmf_grid(ks, mu_nodes, alp)
    nb_marginal = weights @ nb_mat
    pmf = (1.0 - pi_c) * nb_marginal
    pmf[0] += pi_c
    total = float(pmf.sum())
    if total > 0.0:
        # Fold any tiny tail mass beyond k_max into exact normalization if needed only for numeric sum=1
        rem = max(0.0, 1.0 - total)
        if rem > 1e-12:
            pmf[-1] += rem
        else:
            pmf = pmf / total
    return pmf


def plain_nb_pmf(
    lambda_rate: float,
    alpha: float,
    mu_min: float,
    sigma_min: float,
    k_max: int = DEFAULT_K_MAX,
    n_quad: int = DEFAULT_GH_POINTS,
) -> np.ndarray:
    """Plain-NB PMF (``pi = 0.0``) integrated over the Stage 1 minutes quadrature."""
    return hurdle_nb_pmf(0.0, lambda_rate, alpha, mu_min, sigma_min, k_max=k_max, n_quad=n_quad)


def hurdle_nb_cdf(
    k: float,
    pi: float,
    lambda_rate: float,
    alpha: float,
    mu_min: float,
    sigma_min: float,
    n_quad: int = DEFAULT_GH_POINTS,
) -> float:
    """Exact marginal CDF ``P(Y <= floor(k))`` integrated over the minutes quadrature."""
    k_int = int(math.floor(float(k)))
    if k_int < 0:
        return 0.0
    pi_c = float(np.clip(pi, 0.0, 1.0 - 1e-9))
    lam = max(float(lambda_rate), 0.0)
    alp = max(float(alpha), 0.0)
    nodes, weights = gh_minutes_quadrature(mu_min, sigma_min, n_points=n_quad)
    mu_nodes = lam * nodes
    cdfs = np.empty(len(mu_nodes), dtype=float)
    for i, m in enumerate(mu_nodes):
        if m <= 0.0:
            cdfs[i] = 1.0
        elif alp <= ALPHA_MIN:
            cdfs[i] = float(poisson.cdf(k_int, m))
        else:
            r = 1.0 / alp
            cdfs[i] = float(nbinom.cdf(k_int, r, r / (r + m)))
    nb_cdf = float(np.dot(weights, cdfs))
    return float(np.clip(pi_c + (1.0 - pi_c) * nb_cdf, 0.0, 1.0))


def p_over(
    line: float,
    pi: float,
    lambda_rate: float,
    alpha: float,
    mu_min: float,
    sigma_min: float,
    k_max: int = DEFAULT_K_MAX,
    n_quad: int = DEFAULT_GH_POINTS,
) -> float:
    """``P(Y > line)`` computed from the marginal PMF/CDF (handles half and integer lines)."""
    k_int = int(math.floor(float(line)))
    if k_int < 0:
        return 1.0
    if k_int < k_max:
        pmf = hurdle_nb_pmf(pi, lambda_rate, alpha, mu_min, sigma_min, k_max=k_max, n_quad=n_quad)
        return float(np.clip(1.0 - pmf[: k_int + 1].sum(), 0.0, 1.0))
    return float(np.clip(1.0 - hurdle_nb_cdf(k_int, pi, lambda_rate, alpha, mu_min, sigma_min, n_quad=n_quad), 0.0, 1.0))


def hurdle_nb_loglik(
    y: np.ndarray,
    pi: np.ndarray | float,
    lambda_rate: np.ndarray | float,
    alpha: np.ndarray | float,
    mu_min: np.ndarray | float,
    sigma_min: np.ndarray | float,
    n_quad: int = DEFAULT_GH_POINTS,
) -> float:
    """Mean per-row log-likelihood over vectorized arrays using Gauss-Hermite quadrature."""
    y_arr = np.asarray(y, dtype=int)
    n = len(y_arr)
    pi_arr = np.broadcast_to(np.asarray(pi, dtype=float), (n,))
    lam_arr = np.broadcast_to(np.asarray(lambda_rate, dtype=float), (n,))
    alp_arr = np.broadcast_to(np.asarray(alpha, dtype=float), (n,))
    mu_arr = np.broadcast_to(np.asarray(mu_min, dtype=float), (n,))
    sd_arr = np.broadcast_to(np.asarray(sigma_min, dtype=float), (n,))

    xs, ws = hermgauss(n_quad)
    w_norm = ws / math.sqrt(math.pi)
    w_norm = w_norm / w_norm.sum()

    # Nodes shape: (n, n_quad)
    nodes = np.maximum(0.0, mu_arr[:, None] + math.sqrt(2.0) * sd_arr[:, None] * xs[None, :])
    no_sd = sd_arr <= 1e-9
    if no_sd.any():
        nodes[no_sd] = np.maximum(0.0, mu_arr[no_sd, None])
    mu_q = lam_arr[:, None] * nodes

    alp_q = alp_arr[:, None]
    y_q = y_arr[:, None].astype(float)
    nb_q = np.zeros_like(mu_q)

    zero_m = mu_q <= 0.0
    if zero_m.any():
        nb_q[zero_m] = np.where(y_q.repeat(n_quad, axis=1)[zero_m] == 0.0, 1.0, 0.0)

    pois_m = (~zero_m) & (alp_q <= ALPHA_MIN)
    if pois_m.any():
        m_p = np.maximum(mu_q[pois_m], EPS)
        y_p = y_q.repeat(n_quad, axis=1)[pois_m]
        nb_q[pois_m] = np.exp(-m_p + y_p * np.log(m_p) - gammaln(y_p + 1.0))

    nb_m = (~zero_m) & (alp_q > ALPHA_MIN)
    if nb_m.any():
        m_n = np.maximum(mu_q[nb_m], EPS)
        r_n = 1.0 / np.broadcast_to(alp_q, mu_q.shape)[nb_m]
        y_n = y_q.repeat(n_quad, axis=1)[nb_m]
        log_p = (
            gammaln(y_n + r_n)
            - gammaln(r_n)
            - gammaln(y_n + 1.0)
            + r_n * np.log(r_n / (r_n + m_n))
            + y_n * np.log(m_n / (r_n + m_n))
        )
        nb_q[nb_m] = np.exp(log_p)

    nb_marg = nb_q @ w_norm
    pi_c = np.clip(pi_arr, 0.0, 1.0 - 1e-9)
    p_y = (1.0 - pi_c) * nb_marg + np.where(y_arr == 0, pi_c, 0.0)
    return float(np.mean(np.log(np.maximum(p_y, EPS))))


def shift_lambda_for_p_over(
    line: float,
    target_p_over: float,
    pi: float,
    lambda_rate: float,
    alpha: float,
    mu_min: float,
    sigma_min: float,
    n_quad: int = DEFAULT_GH_POINTS,
) -> float:
    """Location-shift ``lambda_rate`` so ``P(Y > line) == target_p_over`` preserving ``alpha`` & ``pi``."""
    if float(mu_min) <= 0.0:
        return 0.0
    pi_c = float(np.clip(pi, 0.0, 1.0 - 1e-9))
    max_reachable = 1.0 - pi_c - 1e-7
    target = float(np.clip(target_p_over, 1e-7, max(max_reachable, 1e-7)))
    k_int = int(math.floor(float(line)))
    if k_int < 0:
        return max(float(lambda_rate), 1e-6)

    def obj(log_lam: float) -> float:
        p = 1.0 - hurdle_nb_cdf(k_int, pi_c, math.exp(log_lam), alpha, mu_min, sigma_min, n_quad=n_quad)
        return p - target

    lo, hi = -15.0, 8.0
    while obj(lo) > 0.0 and lo > -30.0:
        lo -= 5.0
    while obj(hi) < 0.0 and hi < 15.0:
        hi += 5.0
    if obj(lo) >= 0.0:
        return math.exp(lo)
    if obj(hi) <= 0.0:
        return math.exp(hi)
    sol = brentq(obj, lo, hi, xtol=1e-9, rtol=1e-9, maxiter=100)
    return float(math.exp(sol))
