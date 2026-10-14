"""NegBin / Poisson helpers for a SOG count. The scalar `p_over` is the repo's canonical one (mlb.analysis.gen_preds.p_over,
shared with the MLB count models) -- `p_over_vec` is its vectorised twin and a test pins them equal."""
from __future__ import annotations

import numpy as np
from scipy.stats import nbinom, poisson

from mlb.analysis.gen_preds import p_over  # noqa: F401  (re-exported canonical scalar)


def p_over_vec(line, mu, alpha) -> np.ndarray:
    """P(X > line) for X ~ NegBin(mu, alpha) (var = mu + alpha*mu^2); alpha <= 0 degrades to Poisson. Vectorised; `alpha`
    may be a scalar or an array. mu <= 0 -> 0.5 (same degenerate convention as the canonical scalar)."""
    line, mu, alpha = np.broadcast_arrays(np.asarray(line, dtype=float), np.asarray(mu, dtype=float),
                                          np.asarray(0.0 if alpha is None else alpha, dtype=float))
    k = np.floor(line)
    mu_s = np.where(mu > 0, mu, 1.0)
    pois = 1.0 - poisson.cdf(k, mu_s)
    n = 1.0 / np.where(alpha > 0, alpha, 1.0)
    nb = 1.0 - nbinom.cdf(k, n, n / (n + mu_s))
    return np.where(mu > 0, np.where(alpha > 0, nb, pois), 0.5)


def nb_loglik(y, mu, alpha) -> float:
    """Mean per-row NegBin log-likelihood (alpha <= 0 -> Poisson). Used to fit alpha."""
    y, mu = np.asarray(y, dtype=float), np.maximum(np.asarray(mu, dtype=float), 1e-6)
    if alpha <= 0:
        return float(poisson.logpmf(y, mu).mean())
    n = 1.0 / alpha
    return float(nbinom.logpmf(y, n, n / (n + mu)).mean())


def fit_alpha(y, mu, grid=None) -> float:
    """Dispersion by maximum likelihood over a grid on HELD-OUT (y, mu). SOG is mildly over-dispersed vs Poisson."""
    grid = np.concatenate([[0.0], np.linspace(0.01, 0.6, 60)]) if grid is None else grid
    ll = [nb_loglik(y, mu, a) for a in grid]
    return float(grid[int(np.argmax(ll))])
