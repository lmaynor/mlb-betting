"""nba.props.dist -- discrete predictive distributions for box-score counts, parameterised by (mean, variance).

Two families (chosen per stat by out-of-sample log score, see train.py / the evaluation doc):
  * 'nbinom' : NegBin with Var = mu + alpha*mu^2 (alpha from the supplied variance). The repo's count convention
               (mlb/analysis/gen_preds.p_over, run_batter_hits._negbin_p_over). Cannot be under-dispersed: a variance
               at or below the mean is clipped to ~Poisson.
  * 'gamma'  : Gamma(mean, var) discretised with a continuity correction, P(Y=k) = F(k+0.5) - F(k-0.5). Right-skewed,
               zero-bounded, flexible variance: the "suitable continuous/discretised" option for points and PRA.

All functions are vectorised over rows. `line` follows the book convention: a half-point line L -> P(over) = P(Y >= ceil(L)).
A whole-number line would have a push; props that carry one are handled by the scorer (push mass reported separately).
"""
from __future__ import annotations

import numpy as np
from scipy import stats

ALPHA_MIN, ALPHA_MAX = 1e-4, 8.0
MU_MIN = 1e-3
FAMILIES = ("nbinom", "gamma")


def _as_arr(x):
    return np.atleast_1d(np.asarray(x, dtype=float))


def nb_alpha(mu, var):
    mu = np.maximum(_as_arr(mu), MU_MIN)
    return np.clip((_as_arr(var) - mu) / mu ** 2, ALPHA_MIN, ALPHA_MAX)


def cdf(k, mu, var, family: str = "nbinom"):
    """P(Y <= k) for integer-valued k (array-broadcast with mu/var)."""
    k = np.floor(np.asarray(k, dtype=float))
    mu = np.maximum(np.asarray(mu, dtype=float), MU_MIN)
    var = np.asarray(var, dtype=float)
    if family == "nbinom":
        a = np.clip((var - mu) / mu ** 2, ALPHA_MIN, ALPHA_MAX)
        n = 1.0 / a
        p = n / (n + mu)
        out = stats.nbinom.cdf(k, n, p)
    elif family == "gamma":
        v = np.maximum(var, 1e-6)
        shape = mu ** 2 / v
        scale = v / mu
        out = np.where(k < 0, 0.0, stats.gamma.cdf(k + 0.5, shape, scale=scale))
    else:
        raise ValueError(family)
    return np.where(k < 0, 0.0, out)


def logpmf(y, mu, var, family: str = "nbinom", floor: float = 1e-12):
    y = np.asarray(y, dtype=float)
    mu = np.maximum(np.asarray(mu, dtype=float), MU_MIN)
    var = np.asarray(var, dtype=float)
    if family == "nbinom":
        a = np.clip((var - mu) / mu ** 2, ALPHA_MIN, ALPHA_MAX)
        n = 1.0 / a
        p = n / (n + mu)
        pm = stats.nbinom.pmf(y, n, p)
    else:
        pm = cdf(y, mu, var, family) - cdf(y - 1, mu, var, family)
    return np.log(np.maximum(pm, floor))


def p_over(line, mu, var, family: str = "nbinom"):
    """P(Y > line): half-point line -> P(Y >= ceil(line)); a whole-number line -> P(Y >= line + 1) (a tie is a push)."""
    return 1.0 - cdf(np.floor(np.asarray(line, dtype=float)), mu, var, family)


def p_push(line, mu, var, family: str = "nbinom"):
    """P(Y == line) for a whole-number line, else 0."""
    line = np.asarray(line, dtype=float)
    whole = np.isclose(line, np.round(line))
    k = np.round(line)
    pm = cdf(k, mu, var, family) - cdf(k - 1, mu, var, family)
    return np.where(whole, pm, 0.0)


def crps(y, mu, var, family: str, kmax: int) -> np.ndarray:
    """Discrete CRPS: sum_k (F(k) - 1[y <= k])^2 over k = 0..kmax (unit spacing). Lower is better."""
    y = np.asarray(y, dtype=float)
    mu = np.asarray(mu, dtype=float)
    var = np.asarray(var, dtype=float)
    ks = np.arange(0, kmax + 1, dtype=float)[None, :]
    F = cdf(ks, mu[:, None], var[:, None], family)
    ind = (y[:, None] <= ks).astype(float)
    return ((F - ind) ** 2).sum(axis=1)


# ----------------------------------------------------------------------------- PIT recalibration
# A parametric family can be systematically off in shape (found offline: NegBin for points/PRA under-states P(over) by ~3pp
# around the centre because real point totals are less right-skewed). Standard predictive-distribution recalibration: learn
# the monotone map G from the model's own (randomised) PIT values on OUT-OF-SAMPLE rows, then use F_cal(k) = G(F(k)).
# If the family were perfectly calibrated G would be the identity.
def pit_values(y, mu, var, family: str, seed: int = 0) -> np.ndarray:
    """Randomised PIT for a discrete forecast: U = F(y-1) + V * (F(y) - F(y-1)), V ~ Uniform(0,1)."""
    y = np.asarray(y, dtype=float)
    lo = cdf(y - 1, mu, var, family)
    hi = cdf(y, mu, var, family)
    v = np.random.default_rng(seed).random(len(y))
    return lo + v * (hi - lo)


def fit_pit_table(y, mu, var, family: str, knots: int = 101, seed: int = 0) -> dict:
    """{'x': [...], 'y': [...]}: G(f) = empirical CDF of the PIT values, stored as `knots` quantile knots."""
    u = pit_values(y, mu, var, family, seed)
    q = np.linspace(0.0, 1.0, knots)
    xs = np.quantile(u, q)
    xs = np.maximum.accumulate(xs)
    return {"x": [float(v) for v in xs], "y": [float(v) for v in q]}


def recal(F, table) -> np.ndarray:
    """Apply G to CDF values (identity when no table)."""
    F = np.asarray(F, dtype=float)
    if not table:
        return F
    xs, ys = np.asarray(table["x"], dtype=float), np.asarray(table["y"], dtype=float)
    # np.interp needs increasing xs; flat runs (ties) are nudged apart without changing the map materially
    xs = xs + np.arange(len(xs)) * 1e-12
    return np.interp(F, xs, ys)


def cdf_cal(k, mu, var, family: str = "nbinom", table=None) -> np.ndarray:
    return np.where(np.asarray(k, dtype=float) < 0, 0.0, recal(cdf(k, mu, var, family), table))


def p_over_cal(line, mu, var, family: str = "nbinom", table=None) -> np.ndarray:
    return 1.0 - cdf_cal(np.floor(np.asarray(line, dtype=float)), mu, var, family, table)


def p_push_cal(line, mu, var, family: str = "nbinom", table=None) -> np.ndarray:
    line = np.asarray(line, dtype=float)
    k = np.round(line)
    whole = np.isclose(line, k)
    return np.where(whole, cdf_cal(k, mu, var, family, table) - cdf_cal(k - 1, mu, var, family, table), 0.0)


def crps_cal(y, mu, var, family: str, kmax: int, table=None) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    ks = np.arange(0, kmax + 1, dtype=float)[None, :]
    F = cdf_cal(ks, np.asarray(mu, float)[:, None], np.asarray(var, float)[:, None], family, table)
    return ((F - (y[:, None] <= ks).astype(float)) ** 2).sum(axis=1)


# ----------------------------------------------------------------------------- variance functions
def fit_phi_table(mu, y, bins: int = 12, min_per_bin: int = 200) -> dict:
    """Variance-to-mean ratio as a function of the predicted mean: phi(mu) = Var(y | mu) / mu, estimated in quantile
    bins of mu from OUT-OF-SAMPLE residuals (the caller passes validation-season predictions, never training rows,
    whose residuals are shrunk by fitting)."""
    mu = np.asarray(mu, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.unique(np.quantile(mu, np.linspace(0, 1, bins + 1)))
    knots, phis = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (mu >= lo) & (mu <= hi if hi == edges[-1] else mu < hi)
        if sel.sum() < min_per_bin:
            continue
        m = mu[sel]
        knots.append(float(np.log(max(m.mean(), MU_MIN))))
        phis.append(float(((y[sel] - m) ** 2).mean() / m.mean()))
    if not knots:
        return {"log_mu": [float(np.log(max(np.mean(mu), MU_MIN)))], "phi": [1.0]}
    return {"log_mu": knots, "phi": phis}


def phi_from_table(mu, table: dict) -> np.ndarray:
    return np.interp(np.log(np.maximum(np.asarray(mu, dtype=float), MU_MIN)), table["log_mu"], table["phi"])


def var_from_table(mu, table: dict, phi_floor: float = 1.0) -> np.ndarray:
    """Var = phi(mu) * mu with phi floored (a NegBin/Gamma needs Var > mu for the chosen family to be well behaved)."""
    mu = np.maximum(np.asarray(mu, dtype=float), MU_MIN)
    return np.maximum(phi_from_table(mu, table), phi_floor) * mu
