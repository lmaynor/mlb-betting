"""MOD-03a candidate ``mod03_joint_v1``: NegBin joint combo distribution for NBA combo props.

Provides pure, scipy-only helpers to:
- Recover served combo variance from archived (p10, p90) quantiles.
- Fit shrunk overdispersion multiplier kappa across fit-window residuals.
- Moment-match Negative Binomial (r, p) or Poisson fallback when var <= mu.
- Compute exact discrete tail P(X > line) and ev.daily_projections-compatible alpha.
"""
from __future__ import annotations

import math

from scipy.stats import nbinom, poisson

Z_P90_MINUS_P10 = 2.5632


def var_from_p10_p90(p10: float, p90: float) -> float:
    """Recover served combo variance from archived p10 and p90 quantiles (MOD-03a)."""
    sd = (float(p90) - float(p10)) / Z_P90_MINUS_P10
    return sd ** 2


def fit_kappa(
    sum_sq_resid: float,
    sum_var_served: float,
    n: int | float,
    shrink: float = 20.0,
) -> tuple[float, float]:
    """Fit raw and shrunk variance multiplier kappa from residual sums (MOD-03a).

    Guards ``n <= 0`` or ``sum_var_served <= 0`` (or non-finite inputs) by returning
    ``(nan, 1.0)``.
    """
    if (
        n <= 0
        or sum_var_served <= 0
        or not math.isfinite(sum_sq_resid)
        or not math.isfinite(sum_var_served)
    ):
        return (float("nan"), 1.0)
    kappa_raw = float(sum_sq_resid) / float(sum_var_served)
    kappa_shrunk = (n * kappa_raw + shrink * 1.0) / (n + shrink)
    return (kappa_raw, float(kappa_shrunk))


def negbin_params(mu: float, var: float) -> tuple[float, float] | None:
    """Moment-match NegBin ``(r, p)`` from ``(mu, var)``, or ``None`` if ``var <= mu`` (MOD-03a)."""
    if (
        mu is None
        or var is None
        or not math.isfinite(mu)
        or not math.isfinite(var)
        or mu <= 0.0
        or var <= mu
    ):
        return None
    mu_f = float(mu)
    var_f = float(var)
    r = (mu_f ** 2) / (var_f - mu_f)
    p = r / (r + mu_f)
    return (float(r), float(p))


def p_over_joint(mu: float | None, var: float | None, line: float | None) -> float | None:
    """Exact discrete tail ``P(X > line)`` under NegBin or Poisson fallback (MOD-03a).

    Matches ``ev.daily_projections.p_over`` conventions: returns ``None`` for invalid
    ``mu`` or ``line`` (``None`` or non-finite), and returns ``0.0 if line >= 0 else 1.0``
    when ``mu <= 0``. Uses ``scipy.stats.nbinom.sf`` when ``var > mu``, else
    ``scipy.stats.poisson.sf``.
    """
    if mu is None or line is None or not math.isfinite(mu) or not math.isfinite(line):
        return None
    mu_f = float(mu)
    line_f = float(line)
    if mu_f <= 0.0:
        return 0.0 if line_f >= 0.0 else 1.0
    if var is None or not math.isfinite(var):
        return None
    k = math.floor(line_f)
    params = negbin_params(mu_f, float(var))
    if params is not None:
        r, p = params
        return float(nbinom.sf(k, r, p))
    return float(poisson.sf(k, mu_f))


def alpha_from_mu_var(mu: float, var: float) -> float:
    """Convert ``(mu, var)`` to ``ev.daily_projections`` NegBin dispersion ``alpha`` (MOD-03a).

    Follows ``var = mu + alpha * mu**2``; returns ``0.0`` when ``var <= mu`` or ``mu <= 0``.
    """
    if (
        mu is None
        or var is None
        or not math.isfinite(mu)
        or not math.isfinite(var)
        or mu <= 0.0
        or var <= mu
    ):
        return 0.0
    return float((float(var) - float(mu)) / (float(mu) ** 2))


def combo_alpha(mu: float, sd: float, kappa: float) -> float:
    """Compute NegBin ``alpha`` from ``(mu, sd, kappa)`` via ``var = kappa * sd**2`` (MOD-03a)."""
    return alpha_from_mu_var(mu, float(kappa) * (float(sd) ** 2))
