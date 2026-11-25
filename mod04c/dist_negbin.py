"""U2/U4 discrete NegBin distribution, convolution, logit blend, and rung shift."""
from __future__ import annotations

import numpy as np
from scipy.special import expit, logit
from scipy.stats import nbinom, poisson

from nhl.props.negbin import nb_loglik, p_over_vec

PROB_CLIP = 1e-6


def nb_pmf(mu, alpha, kmax: int = 40) -> np.ndarray:
    """Discrete NegBin PMF on support 0..kmax (Var = mu + alpha*mu^2; alpha <= 0 -> Poisson).

    Scalar inputs return shape `(kmax + 1,)`; array inputs return shape `(N, kmax + 1)`.
    Each row is defensively normalized to sum to 1.0.
    """
    scalar = np.ndim(mu) == 0 and np.ndim(alpha) == 0
    mu_arr, alpha_arr = np.broadcast_arrays(
        np.atleast_1d(np.asarray(mu, dtype=float)),
        np.atleast_1d(np.asarray(0.0 if alpha is None else alpha, dtype=float)),
    )
    ks = np.arange(kmax + 1, dtype=float)[None, :]
    n_rows = mu_arr.shape[0]
    pmf = np.zeros((n_rows, kmax + 1), dtype=float)

    valid_mu = np.isfinite(mu_arr) & (mu_arr > 0.0)
    pmf[~valid_mu, 0] = 1.0

    pois_mask = valid_mu & (~np.isfinite(alpha_arr) | (alpha_arr <= 0.0))
    if pois_mask.any():
        pmf[pois_mask] = poisson.pmf(ks, mu_arr[pois_mask, None])

    nb_mask = valid_mu & np.isfinite(alpha_arr) & (alpha_arr > 0.0)
    if nb_mask.any():
        n = 1.0 / alpha_arr[nb_mask, None]
        p = n / (n + mu_arr[nb_mask, None])
        pmf[nb_mask] = nbinom.pmf(ks, n, p)

    pmf = np.nan_to_num(pmf, nan=0.0, posinf=0.0, neginf=0.0)
    pmf = np.maximum(pmf, 0.0)
    row_sums = pmf.sum(axis=1, keepdims=True)
    zero_sum = row_sums[:, 0] <= 0.0
    if zero_sum.any():
        pmf[zero_sum, 0] = 1.0
        row_sums = pmf.sum(axis=1, keepdims=True)
    pmf = pmf / row_sums
    return pmf[0] if scalar else pmf


def pmf_p_over(pmf: np.ndarray, line) -> np.ndarray | float:
    """Compute P(Y > line) from a discrete PMF (1D `(K,)` or 2D `(N, K)`)."""
    arr = np.asarray(pmf, dtype=float)
    scalar_pmf = arr.ndim == 1
    if scalar_pmf:
        arr = arr[None, :]
    kmax = arr.shape[1] - 1
    ks = np.arange(kmax + 1, dtype=float)
    line_arr = np.broadcast_to(np.asarray(line, dtype=float), (arr.shape[0],))
    thresh = np.floor(line_arr)[:, None]
    mask = ks[None, :] > thresh
    out = (arr * mask).sum(axis=1)
    if scalar_pmf and np.ndim(line) == 0:
        return float(out[0])
    return out


def convolve_pmf(pmf_a: np.ndarray, pmf_b: np.ndarray) -> np.ndarray:
    """Discrete convolution of two independent count PMFs (1D or row-wise 2D)."""
    a = np.asarray(pmf_a, dtype=float)
    b = np.asarray(pmf_b, dtype=float)
    if a.ndim == 1 and b.ndim == 1:
        conv = np.convolve(a, b)
        conv = np.maximum(conv, 0.0)
        s = conv.sum()
        return conv / s if s > 0 else conv
    if a.ndim == 1:
        a = np.broadcast_to(a[None, :], (b.shape[0], a.shape[0]))
    if b.ndim == 1:
        b = np.broadcast_to(b[None, :], (a.shape[0], b.shape[0]))
    if a.shape[0] != b.shape[0]:
        raise ValueError(f"Row count mismatch in convolve_pmf: {a.shape[0]} vs {b.shape[0]}")
    n_out = a.shape[1] + b.shape[1] - 1
    fft_len = 1 << (n_out - 1).bit_length()
    conv = np.fft.irfft(np.fft.rfft(a, n=fft_len, axis=1) * np.fft.rfft(b, n=fft_len, axis=1), n=fft_len, axis=1)[:, :n_out]
    conv = np.maximum(conv, 0.0)
    sums = conv.sum(axis=1, keepdims=True)
    zero = sums[:, 0] <= 0.0
    if zero.any():
        conv[zero, 0] = 1.0
        sums = conv.sum(axis=1, keepdims=True)
    return conv / sums


def fit_alpha_grid(y, mu, grid: np.ndarray | None = None) -> float:
    """Fit NegBin dispersion alpha by grid maximum likelihood using `nhl.props.negbin.nb_loglik`."""
    y_arr = np.asarray(y, dtype=float)
    mu_arr = np.asarray(mu, dtype=float)
    if grid is None:
        grid = np.concatenate([[0.0], np.linspace(0.01, 1.0, 100)])
    best_a = 0.0
    best_ll = -np.inf
    for a in grid:
        ll = nb_loglik(y_arr, mu_arr, float(a))
        if np.isfinite(ll) and ll > best_ll:
            best_ll = ll
            best_a = float(a)
    return best_a


def blend_p(p_model, p_market, c: float, a: float = 0.0, b: float = 1.0):
    """U4 logit blend: logit(p_served) = a + b*logit(p_market) + c*(logit(p_model) - logit(p_market))."""
    scalar = np.ndim(p_model) == 0 and np.ndim(p_market) == 0 and np.ndim(c) == 0
    pm = np.clip(np.asarray(p_model, dtype=float), PROB_CLIP, 1.0 - PROB_CLIP)
    pk = np.clip(np.asarray(p_market, dtype=float), PROB_CLIP, 1.0 - PROB_CLIP)
    lm = logit(pm)
    lk = logit(pk)
    served = np.clip(expit(a + b * lk + c * (lm - lk)), PROB_CLIP, 1.0 - PROB_CLIP)
    return float(served) if scalar else served


def _shift_mu_scalar(mu: float, alpha: float, line: float, p_target: float, tol: float = 1e-6, max_iter: int = 80) -> float:
    pt = float(np.clip(p_target, PROB_CLIP, 1.0 - PROB_CLIP))
    if line < 0:
        return max(float(mu), 1e-6)
    lo = 1e-9
    hi = max(float(mu), float(line) + 1.0, 1.0) * 2.0
    for _ in range(40):
        if float(p_over_vec(line, hi, alpha)) >= pt:
            break
        hi *= 2.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = float(p_over_vec(line, mid, alpha))
        if abs(p_mid - pt) < 1e-9 or (hi - lo) < tol:
            return mid
        if p_mid < pt:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def shift_mu_to_p_over(mu, alpha, line, p_target, tol: float = 1e-6, max_iter: int = 80):
    """Bisection root-find for location-shifted mu so P(Y > line) = p_target, preserving alpha."""
    if np.ndim(mu) == 0 and np.ndim(alpha) == 0 and np.ndim(line) == 0 and np.ndim(p_target) == 0:
        return _shift_mu_scalar(float(mu), float(alpha), float(line), float(p_target), tol=tol, max_iter=max_iter)
    mu_a, al_a, ln_a, pt_a = np.broadcast_arrays(
        np.asarray(mu, dtype=float),
        np.asarray(alpha, dtype=float),
        np.asarray(line, dtype=float),
        np.asarray(p_target, dtype=float),
    )
    out = np.array([
        _shift_mu_scalar(float(m), float(a), float(l), float(p), tol=tol, max_iter=max_iter)
        for m, a, l, p in zip(mu_a.ravel(), al_a.ravel(), ln_a.ravel(), pt_a.ravel())
    ], dtype=float)
    return out.reshape(mu_a.shape)
