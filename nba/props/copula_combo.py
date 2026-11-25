"""nba.props.copula_combo -- MOD-04 Cluster (g) joint P/R/A copula engine.

SHADOW-ONLY: combo weight c=0.00.
Per RUL-20261124, market-anchored marginals (recovered from served board quantiles)
are a serving-input fallback ONLY -- NOT training features. Prop lines and odds are
banned as training features and must never enter any fit.

This module provides:
- Marginal & MarginalProvider (BoardMarginalProvider market-anchored fallback and
  DictMarginalProvider plug-in interface for validated MOD-04 clusters b/c/d).
- Archetype mapping, exact discrete randomized PIT & Gaussianization helpers.
- Out-of-fold Sigma_role correlation fitter with pooled shrinkage and PSD projection.
- CopulaEngine (Quasi-Monte Carlo Sobol + minutes-factor Gamma mixture + Gaussian /
  Student-t copula over conditional NegBin/Normal marginals).
- Combo PMF, DD/TD, and Var(P+R+A) derivation; G1 ECE gate; and Student-t ablation.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from scipy import optimize, special, stats

from mlb_core.data.player_names import norm_name

ALPHA_MIN = 1e-4
ALPHA_MAX = 8.0
MU_MIN = 1e-6
Z_10_90 = 1.2815515655446004  # scipy.stats.norm.ppf(0.90)

STATS_PRA = ("points", "rebounds", "assists")
COMBO_STATS = ("pr", "pa", "pra", "ra")
ARCHETYPES = ("primary_guard", "wing", "stretch_big", "rim_big")

BOARD_STAT_FAMILY = {
    "points": "normal",
    "rebounds": "negbin",
    "assists": "negbin",
}

_POS_TO_ARCHETYPE = {
    "PG": "primary_guard",
    "SG": "wing",
    "SF": "wing",
    "PF": "stretch_big",
    "C": "rim_big",
}


@dataclass
class Marginal:
    """Single-stat marginal distribution parameterised by (stat, family, mu, var)."""

    stat: str
    family: str
    mu: float
    var: float

    @property
    def sd(self) -> float:
        return math.sqrt(max(float(self.var), 0.0))

    def alpha(self) -> float:
        """NegBin overdispersion alpha = (var - mu) / mu^2, clipped to [1e-4, 8]."""
        mu = max(float(self.mu), MU_MIN)
        a = (float(self.var) - mu) / (mu * mu)
        return float(min(ALPHA_MAX, max(ALPHA_MIN, a)))


@runtime_checkable
class MarginalProvider(Protocol):
    """Protocol supplying per-player (P, R, A) marginals and minutes CV."""

    def marginals(self, player_key: str) -> dict[str, Marginal] | None:
        ...

    def minutes_cv(self, player_key: str) -> float:
        ...


def archetype_for_position(position_str: str | None) -> str:
    """Map position string -> archetype ('primary_guard'|'wing'|'stretch_big'|'rim_big').

    Multi-position strings take the first token before '-' or '/'; unknown -> 'wing'.
    """
    if not position_str or not isinstance(position_str, str):
        return "wing"
    s = position_str.strip().upper()
    if not s:
        return "wing"
    tok = s.split("-", 1)[0].split("/", 1)[0].strip()
    return _POS_TO_ARCHETYPE.get(tok, "wing")


def recover_marginal_from_board_row(row: Mapping) -> Marginal:
    """Recover a Marginal(stat, family, mu, var) from a daily board archive row.

    Market-anchored serving-input fallback per RUL-20261124 (NOT for training fits).
    """
    stat = str(row["stat"]).strip().lower()
    if stat not in BOARD_STAT_FAMILY:
        raise ValueError(f"Unsupported board marginal stat: {stat!r}")
    family = BOARD_STAT_FAMILY[stat]
    mu = float(row["proj_mean"])
    p10 = row.get("proj_p10")
    p90 = row.get("proj_p90")

    has_quantiles = (
        p10 is not None
        and p90 is not None
        and math.isfinite(float(p10))
        and math.isfinite(float(p90))
        and float(p90) > float(p10)
    )
    if has_quantiles:
        spread = float(p90) - float(p10)
        var_norm = (spread / (2.0 * Z_10_90)) ** 2
    else:
        spread = None
        var_norm = max(mu, 1e-3)

    if family == "normal":
        var = max(float(var_norm), 1e-6)
        return Marginal(stat=stat, family=family, mu=mu, var=var)

    # NegBin: solve alpha in [1e-4, 8] so nbinom p90 - p10 matches board spread
    var = float(var_norm)
    if has_quantiles and mu > 0.0 and spread is not None and spread > 0.0:
        def _spread_err(alpha: float) -> float:
            n = 1.0 / alpha
            p = n / (n + mu)
            q90 = float(stats.nbinom.ppf(0.90, n, p))
            q10 = float(stats.nbinom.ppf(0.10, n, p))
            return (q90 - q10) - spread

        try:
            f_lo = _spread_err(ALPHA_MIN)
            f_hi = _spread_err(ALPHA_MAX)
            if f_lo == 0.0:
                alpha_hat = ALPHA_MIN
                var = mu + alpha_hat * mu * mu
            elif f_hi == 0.0:
                alpha_hat = ALPHA_MAX
                var = mu + alpha_hat * mu * mu
            elif f_lo * f_hi < 0.0:
                alpha_hat = float(optimize.brentq(_spread_err, ALPHA_MIN, ALPHA_MAX))
                var = mu + alpha_hat * mu * mu
        except Exception:  # noqa: BLE001
            var = float(var_norm)

    return Marginal(stat=stat, family=family, mu=mu, var=max(float(var), 1e-6))


def _resolve_cv(cv_cfg: float | Mapping[str, float], archetype: str | None) -> float:
    if isinstance(cv_cfg, Mapping):
        if archetype and archetype in cv_cfg:
            return float(cv_cfg[archetype])
        return float(cv_cfg.get("*", 0.18))
    return float(cv_cfg)


class BoardMarginalProvider:
    """Market-anchored serving-input MarginalProvider built from board archive rows.

    Recovered marginals represent the served blend distributions inferred from board
    mean and p10/p90 quantiles (RUL-20261124 fallback; never used as training features).
    """

    def __init__(
        self,
        rows: Iterable[Mapping] | Mapping[str, Mapping],
        cv: float | Mapping[str, float] = 0.18,
    ) -> None:
        self._cv = cv
        self._by_player: dict[str, dict[str, Marginal]] = {}
        self._arch_by_player: dict[str, str] = {}

        row_iter = rows.values() if isinstance(rows, Mapping) else rows
        for row in row_iter:
            stat = str(row.get("stat", "")).strip().lower()
            if stat not in STATS_PRA:
                continue
            if row.get("proj_mean") is None:
                continue
            pn = row.get("player_norm") or norm_name(str(row.get("player", "")))
            if not pn:
                continue
            try:
                marg = recover_marginal_from_board_row(row)
            except (TypeError, ValueError):
                continue
            p_map = self._by_player.setdefault(pn, {})
            if stat not in p_map:
                p_map[stat] = marg
            if pn not in self._arch_by_player and row.get("position"):
                self._arch_by_player[pn] = archetype_for_position(str(row["position"]))

    def marginals(self, player_key: str) -> dict[str, Marginal] | None:
        m = self._by_player.get(player_key)
        if m is None:
            m = self._by_player.get(norm_name(player_key))
        return dict(m) if m else None

    def minutes_cv(self, player_key: str) -> float:
        arch = self._arch_by_player.get(player_key)
        if arch is None:
            arch = self._arch_by_player.get(norm_name(player_key), "*")
        return _resolve_cv(self._cv, arch)


class DictMarginalProvider:
    """Explicit dict-backed MarginalProvider for tests and future MOD-04 clusters b/c/d."""

    def __init__(
        self,
        data: Mapping[str, Mapping[str, Marginal]],
        cv: float | Mapping[str, float] = 0.18,
        archetypes: Mapping[str, str] | None = None,
    ) -> None:
        self._data: dict[str, dict[str, Marginal]] = {
            k: dict(v) for k, v in data.items()
        }
        self._cv = cv
        self._archetypes = dict(archetypes) if archetypes else {}

    def marginals(self, player_key: str) -> dict[str, Marginal] | None:
        m = self._data.get(player_key)
        return dict(m) if m else None

    def minutes_cv(self, player_key: str) -> float:
        arch = self._archetypes.get(player_key, "*")
        return _resolve_cv(self._cv, arch)


def marginal_cdf(k: float | np.ndarray, marginal: Marginal) -> float | np.ndarray:
    """Evaluate exact discrete CDF F(k) = P(Y <= k) for a Marginal."""
    scalar = np.ndim(k) == 0
    k_arr = np.floor(np.asarray(k, dtype=float))
    mu = max(float(marginal.mu), MU_MIN)
    if marginal.family == "negbin":
        a = marginal.alpha()
        n = 1.0 / a
        p = n / (n + mu)
        out = stats.nbinom.cdf(k_arr, n, p)
    elif marginal.family == "normal":
        sd = max(marginal.sd, 1e-6)
        out = stats.norm.cdf((k_arr + 0.5 - mu) / sd)
    else:
        raise ValueError(f"Unsupported marginal family: {marginal.family!r}")
    out = np.where(k_arr < 0, 0.0, out)
    return float(out) if scalar else out


def pit_residual(y: float | int, marginal: Marginal, rng: np.random.Generator | None = None) -> float:
    """Randomized PIT U = F(y-1) + V * (F(y) - F(y-1)), V ~ Uniform(0, 1)."""
    if rng is None:
        rng = np.random.default_rng()
    k = int(round(float(y)))
    lo = 0.0 if k <= 0 else float(marginal_cdf(k - 1, marginal))
    hi = 0.0 if k < 0 else float(marginal_cdf(k, marginal))
    if hi < lo:
        hi = lo
    v = float(rng.random())
    u = lo + v * (hi - lo)
    return float(np.clip(u, 0.0, 1.0))


def gaussianize(u: float | np.ndarray) -> float | np.ndarray:
    """Map uniform PIT values to standard normal via ndtri(clip(u, 1e-6, 1-1e-6))."""
    scalar = np.ndim(u) == 0
    u_clipped = np.clip(np.asarray(u, dtype=float), 1e-6, 1.0 - 1e-6)
    z = special.ndtri(u_clipped)
    return float(z) if scalar else z


def _project_corr_psd(mat: np.ndarray, min_eig: float = 1e-6) -> np.ndarray:
    """Symmetrize, clip eigenvalues at min_eig, and renormalize diagonal to 1."""
    r = 0.5 * (mat + mat.T)
    np.fill_diagonal(r, 1.0)
    eigvals, eigvecs = np.linalg.eigh(r)
    eigvals = np.maximum(eigvals, min_eig)
    r_psd = (eigvecs * eigvals) @ eigvecs.T
    d = np.sqrt(np.maximum(np.diag(r_psd), 1e-12))
    r_corr = r_psd / np.outer(d, d)
    r_corr = 0.5 * (r_corr + r_corr.T)
    np.fill_diagonal(r_corr, 1.0)
    return r_corr


def _safe_corr(z_mat: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    if len(z_mat) < 2:
        return fallback.copy()
    stds = np.std(z_mat, axis=0)
    if np.any(stds < 1e-12):
        return fallback.copy()
    c = np.corrcoef(z_mat, rowvar=False)
    if not np.all(np.isfinite(c)):
        return fallback.copy()
    return _project_corr_psd(c)


def fit_sigma_role(
    records: Iterable[Mapping],
    prior_weight: float = 200.0,
) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    """Fit per-archetype 3x3 correlation matrices from out-of-fold PIT-Gaussianized z triples.

    Shrinks each archetype correlation matrix toward the pooled global correlation matrix
    with weight `prior_weight`, projects to PSD via eigenvalue clipping at 1e-6, and
    renormalizes the diagonal to 1. Archetypes with n < 30 use the global matrix.
    Returns (sigma_by_archetype, counts_by_archetype).
    """
    by_arch: dict[str, list[np.ndarray]] = {a: [] for a in ARCHETYPES}
    all_z: list[np.ndarray] = []

    for rec in records:
        z = np.asarray(rec["z"], dtype=float)
        if z.shape != (3,) or not np.all(np.isfinite(z)):
            continue
        arch = str(rec.get("archetype") or "wing")
        if arch not in by_arch:
            by_arch[arch] = []
        by_arch[arch].append(z)
        all_z.append(z)

    eye3 = np.eye(3, dtype=float)
    z_all = np.vstack(all_z) if all_z else np.empty((0, 3), dtype=float)
    r_global = _safe_corr(z_all, eye3)

    sigma_by_arch: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    for arch, z_list in by_arch.items():
        n = len(z_list)
        counts[arch] = n
        if n < 30:
            sigma_by_arch[arch] = r_global.copy()
            continue
        z_mat = np.vstack(z_list)
        r_arch = _safe_corr(z_mat, r_global)
        r_shrunk = (n * r_arch + prior_weight * r_global) / (n + prior_weight)
        sigma_by_arch[arch] = _project_corr_psd(r_shrunk)

    return sigma_by_arch, counts


def _fast_nbinom_ppf(u: np.ndarray, n: float, p: np.ndarray, kmax: int = 80) -> np.ndarray:
    """Exact vectorised NegBin quantile inversion via PMF recurrence, with scipy fallback for tails."""
    q = 1.0 - p
    pmf = np.power(p, n)
    underflow = pmf <= 0.0
    cdf = pmf.copy()
    out = np.zeros_like(u, dtype=np.int64)
    alive = (~underflow) & (cdf < u)
    out += alive
    for k in range(1, kmax + 1):
        if not np.any(alive):
            break
        pmf *= (n + k - 1.0) / k * q
        cdf += pmf
        alive = (~underflow) & (cdf < u)
        out += alive
    tail = underflow | alive
    if np.any(tail):
        out[tail] = stats.nbinom.ppf(u[tail], n, p[tail]).astype(np.int64)
    return out


class CopulaEngine:
    """Quasi-Monte Carlo Gaussian / Student-t copula with shared Gamma minutes factor."""

    def __init__(
        self,
        sigma_by_archetype: Mapping[str, np.ndarray | list],
        cv_min_by_archetype: Mapping[str, float] | float,
        n_draws: int = 2000,
        seed: int = 20261125,
        copula: str = "gaussian",
        nu: float | None = None,
    ) -> None:
        if copula not in ("gaussian", "t"):
            raise ValueError(f"Unsupported copula type: {copula!r}")
        if copula == "t" and (nu is None or float(nu) <= 0.0):
            raise ValueError(f"Student-t copula requires nu > 0, got {nu!r}")

        self.sigma_by_archetype: dict[str, np.ndarray] = {
            k: _project_corr_psd(np.asarray(v, dtype=float))
            for k, v in sigma_by_archetype.items()
        }
        self.cv_min_by_archetype = cv_min_by_archetype
        self.n_draws = int(n_draws)
        self.seed = int(seed)
        self.copula = copula
        self.nu = float(nu) if nu is not None else None

        dim = 5 if self.copula == "t" else 4
        m = max(11, math.ceil(math.log2(max(self.n_draws, 1))))
        sobol = stats.qmc.Sobol(d=dim, scramble=True, seed=self.seed)
        u_raw = sobol.random_base2(m)[: self.n_draws]
        u_clipped = np.clip(u_raw, 1e-9, 1.0 - 1e-9)

        self._u_min = u_clipped[:, 0]
        z_ind = special.ndtri(u_clipped[:, 1:4])
        chi2_scale = None
        if self.copula == "t":
            assert self.nu is not None
            chi2_draws = stats.chi2.ppf(u_clipped[:, 4], df=self.nu)
            chi2_scale = np.sqrt(self.nu / np.maximum(chi2_draws, 1e-12))[:, None]

        # Precompute correlated copula uniforms per archetype (deterministic & fast)
        self._u_prime_by_arch: dict[str, np.ndarray] = {}
        self._z_norm_by_arch: dict[str, np.ndarray] = {}
        for arch, sigma in self.sigma_by_archetype.items():
            L = np.linalg.cholesky(sigma)
            z = z_ind @ L.T
            if self.copula == "t":
                assert self.nu is not None and chi2_scale is not None
                u_prime = stats.t.cdf(z * chi2_scale, df=self.nu)
            else:
                u_prime = special.ndtr(z)
            u_prime = np.clip(u_prime, 1e-9, 1.0 - 1e-9)
            self._u_prime_by_arch[arch] = u_prime
            self._z_norm_by_arch[arch] = special.ndtri(u_prime)

        self._f_cache: dict[float, np.ndarray] = {}

    def _get_sigma_key(self, archetype: str) -> str:
        if archetype in self._u_prime_by_arch:
            return archetype
        if "wing" in self._u_prime_by_arch:
            return "wing"
        return next(iter(self._u_prime_by_arch))

    def _minutes_factor(self, cv_eff: float) -> np.ndarray:
        if cv_eff <= 1e-6:
            return np.ones(self.n_draws, dtype=float)
        key = round(float(cv_eff), 10)
        cached = self._f_cache.get(key)
        if cached is not None:
            return cached
        shape = 1.0 / (cv_eff * cv_eff)
        scale = cv_eff * cv_eff
        f = stats.gamma.ppf(self._u_min, a=shape, scale=scale)
        self._f_cache[key] = f
        return f

    def sample_player(
        self,
        marginals: Mapping[str, Marginal],
        archetype: str,
        cv: float | None = None,
    ) -> np.ndarray:
        """Sample (n_draws, 3) integer draws (points, rebounds, assists) for one player."""
        m_pts = marginals["points"]
        m_reb = marginals["rebounds"]
        m_ast = marginals["assists"]
        ordered = (m_pts, m_reb, m_ast)

        cv_base = float(cv) if cv is not None else _resolve_cv(self.cv_min_by_archetype, archetype)
        bounds = [max(cv_base, 0.0)]
        for m in ordered:
            mu_k = max(float(m.mu), MU_MIN)
            var_k = float(m.var)
            if m.family == "negbin":
                bounds.append(0.9 * math.sqrt(max(var_k - mu_k, 0.0)) / mu_k)
            else:
                bounds.append(0.9 * math.sqrt(max(0.95 * var_k, 0.0)) / mu_k)
        cv_eff = min(bounds)
        f = self._minutes_factor(cv_eff)

        arch_key = self._get_sigma_key(archetype)
        u_prime = self._u_prime_by_arch[arch_key]
        z_norm = self._z_norm_by_arch[arch_key]

        draws = np.empty((self.n_draws, 3), dtype=np.int64)
        for j, m in enumerate(ordered):
            mu_k = max(float(m.mu), 0.0)
            var_k = max(float(m.var), 1e-6)
            if m.family == "negbin":
                mu_pos = max(mu_k, MU_MIN)
                alpha_star = (
                    var_k - mu_pos - (mu_pos * mu_pos) * (cv_eff * cv_eff)
                ) / ((mu_pos * mu_pos) * (1.0 + cv_eff * cv_eff))
                alpha_star = float(min(ALPHA_MAX, max(ALPHA_MIN, alpha_star)))
                n = 1.0 / alpha_star
                p = n / (n + np.maximum(mu_k * f, MU_MIN))
                draws[:, j] = _fast_nbinom_ppf(u_prime[:, j], n, p)
            elif m.family == "normal":
                v_f = max(var_k - (mu_k * mu_k) * (cv_eff * cv_eff), 0.05 * var_k) * f
                vals = np.round(mu_k * f + np.sqrt(v_f) * z_norm[:, j])
                draws[:, j] = np.maximum(vals, 0.0).astype(np.int64)
            else:
                raise ValueError(f"Unsupported marginal family: {m.family!r}")
        return draws


def _empirical_pmf(arr: np.ndarray) -> dict[int, float]:
    n = float(len(arr))
    bc = np.bincount(arr)
    nz = np.nonzero(bc)[0]
    return {int(k): float(bc[k]) / n for k in nz}


def derive_from_draws(draws: np.ndarray) -> dict:
    """Derive combo PMFs ('pr','pa','pra','ra'), p_dd, p_td, mean_pra, and var_pra."""
    arr = np.asarray(draws, dtype=np.int64)
    p = arr[:, 0]
    r = arr[:, 1]
    a = arr[:, 2]
    pr = p + r
    pa = p + a
    ra = r + a
    pra = pr + a
    ge10 = arr >= 10
    p_dd = float(np.mean(ge10.sum(axis=1) >= 2))
    p_td = float(np.mean(ge10.all(axis=1)))
    return {
        "pmfs": {
            "pr": _empirical_pmf(pr),
            "pa": _empirical_pmf(pa),
            "pra": _empirical_pmf(pra),
            "ra": _empirical_pmf(ra),
        },
        "p_dd": p_dd,
        "p_td": p_td,
        "mean_pra": float(np.mean(pra)),
        "var_pra": float(np.var(pra)),
    }


def p_over_from_pmf(pmf: Mapping[int, float] | Mapping[str, float], line: float) -> float:
    """Book convention P(X > line): half-point -> P(X >= ceil(L)), whole line -> P(X >= L+1)."""
    lf = float(line)
    if math.isclose(lf, round(lf), abs_tol=1e-9):
        k_min = int(round(lf)) + 1
    else:
        k_min = math.ceil(lf)
    return float(sum(float(prob) for k, prob in pmf.items() if int(k) >= k_min))


def _lookup_ece(ece_by_cell: Mapping, stat: str, archetype: str) -> float | None:
    for key in (
        (stat, archetype),
        f"{stat}|{archetype}",
        (stat, "*"),
        f"{stat}|*",
        stat,
    ):
        if key in ece_by_cell and ece_by_cell[key] is not None:
            return float(ece_by_cell[key])
    return None


def marginal_gate(
    ece_by_cell: Mapping,
    archetype: str,
    threshold: float = 0.035,
) -> tuple[bool, list[str]]:
    """G1 gate: check each stat's ECE from the marginal's current validation record.

    Looks up `(stat, archetype)` (or `'stat|archetype'`), falling back to `(stat, '*')`
    then `stat`. Engine refuses any player-game with one or more stats exceeding threshold.
    """
    failing: list[str] = []
    for stat in STATS_PRA:
        val = _lookup_ece(ece_by_cell, stat, archetype)
        if val is not None and val > threshold:
            failing.append(stat)
    return (len(failing) == 0, failing)


def _gauss_copula_log_density(u: np.ndarray, r_inv: np.ndarray, log_det: float) -> np.ndarray:
    """Gaussian copula log-density at uniforms u of shape (G, 3)."""
    z = special.ndtri(np.clip(u, 1e-6, 1.0 - 1e-6))
    quad = np.einsum("gi,ij,gj->g", z, r_inv, z)
    norm_sq = np.sum(z * z, axis=1)
    return -0.5 * log_det - 0.5 * (quad - norm_sq)


def _t_copula_log_density(
    u: np.ndarray,
    r_inv: np.ndarray,
    log_det: float,
    nu: float,
) -> np.ndarray:
    """Student-t copula log-density (d=3) at uniforms u of shape (G, 3)."""
    d = 3.0
    x = stats.t.ppf(np.clip(u, 1e-6, 1.0 - 1e-6), df=nu)
    quad = np.einsum("gi,ij,gj->g", x, r_inv, x)
    const = (
        special.gammaln(0.5 * (nu + d))
        + (d - 1.0) * special.gammaln(0.5 * nu)
        - d * special.gammaln(0.5 * (nu + 1.0))
        - 0.5 * log_det
    )
    marg_term = 0.5 * (nu + 1.0) * np.sum(np.log1p((x * x) / nu), axis=1)
    joint_term = -0.5 * (nu + d) * np.log1p(quad / nu)
    return const + joint_term + marg_term


def _cond_marginal_pmf_and_pit(
    y_val: int,
    m: Marginal,
    f_grid: np.ndarray,
    cv_eff: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Conditional discrete log-pmf and midpoint PIT u(f) on Gauss-Legendre grid f_grid."""
    mu_k = max(float(m.mu), 0.0)
    var_k = max(float(m.var), 1e-6)
    mu_f = np.maximum(mu_k * f_grid, MU_MIN)
    if m.family == "negbin":
        mu_pos = max(mu_k, MU_MIN)
        alpha_star = (
            var_k - mu_pos - (mu_pos * mu_pos) * (cv_eff * cv_eff)
        ) / ((mu_pos * mu_pos) * (1.0 + cv_eff * cv_eff))
        alpha_star = float(min(ALPHA_MAX, max(ALPHA_MIN, alpha_star)))
        n = 1.0 / alpha_star
        p = n / (n + mu_f)
        pmf = stats.nbinom.pmf(y_val, n, p)
        cdf_hi = stats.nbinom.cdf(y_val, n, p)
        cdf_lo = np.zeros_like(cdf_hi) if y_val <= 0 else stats.nbinom.cdf(y_val - 1, n, p)
    elif m.family == "normal":
        sd_f = np.sqrt(np.maximum(var_k - (mu_k * mu_k) * (cv_eff * cv_eff), 0.05 * var_k) * f_grid)
        sd_f = np.maximum(sd_f, 1e-6)
        cdf_hi = stats.norm.cdf((y_val + 0.5 - mu_f) / sd_f)
        cdf_lo = np.zeros_like(cdf_hi) if y_val <= 0 else stats.norm.cdf((y_val - 0.5 - mu_f) / sd_f)
        pmf = np.maximum(cdf_hi - cdf_lo, 1e-12)
    else:
        raise ValueError(f"Unsupported marginal family: {m.family!r}")
    u_mid = np.clip(0.5 * (cdf_lo + cdf_hi), 1e-6, 1.0 - 1e-6)
    return np.log(np.maximum(pmf, 1e-12)), u_mid


def _bernoulli_ll(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    pc = np.clip(p_pred, 1e-6, 1.0 - 1e-6)
    return float(np.mean(y_true * np.log(pc) + (1.0 - y_true) * np.log(1.0 - pc)))


def student_t_ablation(
    records_by_archetype: Mapping[str, Iterable[Mapping]] | Iterable[Mapping],
    sigma_by_archetype: Mapping[str, np.ndarray | list],
    cv: Mapping[str, float] | float,
    nu_grid: tuple[int | float, ...] = (4, 6, 8, 12, 20),
) -> dict:
    """Walk-forward comparison of Gaussian vs Student-t copula on held-out (P, R, A) records.

    Conditional-on-realized-f approximation: for each record, evaluates the conditional
    discrete marginal log-pmfs plus the copula log-density at midpoint PITs u(f),
    integrating f over a 9-point Gauss-Legendre quadrature grid on [0.2, 2.5] weighted
    by the Gamma(shape=1/cv_eff^2, scale=cv_eff^2) minutes prior. Also evaluates
    DD/TD tail Bernoulli log-likelihood from derived draws vs realized DD/TD.

    Adoption trigger: `adopt_student_t` is True iff the best Student-t variant beats
    Gaussian by >= +0.0020 on BOTH mean joint LL and DD/TD tail LL.
    """
    if isinstance(records_by_archetype, Mapping):
        records = [r for grp in records_by_archetype.values() for r in grp]
    else:
        records = list(records_by_archetype)

    if not records:
        return {
            "n_records": 0,
            "variants": {},
            "best_t_variant": None,
            "best_t_nu": None,
            "delta_joint_ll": 0.0,
            "delta_tail_ll": 0.0,
            "adopt_student_t": False,
        }

    # 9-point Gauss-Legendre grid on f in [0.2, 2.5]
    nodes_std, weights_std = np.polynomial.legendre.leggauss(9)
    a_f, b_f = 0.2, 2.5
    f_grid = 0.5 * (b_f - a_f) * nodes_std + 0.5 * (a_f + b_f)
    w_grid = 0.5 * (b_f - a_f) * weights_std

    sigma_clean = {
        k: _project_corr_psd(np.asarray(v, dtype=float))
        for k, v in sigma_by_archetype.items()
    }
    sigma_inv = {}
    sigma_logdet = {}
    for k, mat in sigma_clean.items():
        sign, ld = np.linalg.slogdet(mat)
        sigma_logdet[k] = float(ld) if sign > 0 else 0.0
        sigma_inv[k] = np.linalg.inv(mat)

    def _sigma_key(arch: str) -> str:
        if arch in sigma_inv:
            return arch
        return "wing" if "wing" in sigma_inv else next(iter(sigma_inv))

    # Precompute per-record conditional marginal terms & prior weights on f_grid
    precomp = []
    realized_dd = []
    realized_td = []
    for rec in records:
        arch = str(rec.get("archetype") or "wing")
        marginals = rec["marginals"]
        y_triple = tuple(int(round(float(v))) for v in rec["actual"])
        cv_base = _resolve_cv(cv, arch)
        bounds = [max(cv_base, 0.0)]
        for stat in STATS_PRA:
            m = marginals[stat]
            mu_k = max(float(m.mu), MU_MIN)
            var_k = float(m.var)
            if m.family == "negbin":
                bounds.append(0.9 * math.sqrt(max(var_k - mu_k, 0.0)) / mu_k)
            else:
                bounds.append(0.9 * math.sqrt(max(0.95 * var_k, 0.0)) / mu_k)
        cv_eff = min(bounds)

        if cv_eff <= 1e-6:
            f_use = np.array([1.0], dtype=float)
            log_pf = np.array([0.0], dtype=float)
        else:
            f_use = f_grid
            shape = 1.0 / (cv_eff * cv_eff)
            scale = cv_eff * cv_eff
            pf = stats.gamma.pdf(f_use, a=shape, scale=scale) * w_grid
            s_pf = float(np.sum(pf))
            pf = pf / s_pf if s_pf > 0.0 else np.full_like(f_use, 1.0 / len(f_use))
            log_pf = np.log(np.maximum(pf, 1e-12))

        log_marg_sum = np.zeros_like(f_use)
        u_cols = []
        for y_val, stat in zip(y_triple, STATS_PRA):
            lpmf, u_mid = _cond_marginal_pmf_and_pit(y_val, marginals[stat], f_use, cv_eff)
            log_marg_sum += lpmf
            u_cols.append(u_mid)
        u_mat = np.column_stack(u_cols)
        skey = _sigma_key(arch)
        precomp.append((rec, arch, skey, log_pf, log_marg_sum, u_mat))
        ge10 = sum(1 for yv in y_triple if yv >= 10)
        realized_dd.append(1.0 if ge10 >= 2 else 0.0)
        realized_td.append(1.0 if ge10 == 3 else 0.0)

    y_dd = np.asarray(realized_dd, dtype=float)
    y_td = np.asarray(realized_td, dtype=float)

    variants_spec: list[tuple[str, str, float | None]] = [("gaussian", "gaussian", None)]
    for nu in nu_grid:
        variants_spec.append((f"t_{nu}", "t", float(nu)))

    results: dict[str, dict] = {}
    for vname, cop_type, nu_val in variants_spec:
        eng = CopulaEngine(
            sigma_clean,
            cv,
            n_draws=2000,
            copula=cop_type,
            nu=nu_val,
        )
        joint_lls = []
        p_dds = []
        p_tds = []
        for rec, arch, skey, log_pf, log_marg_sum, u_mat in precomp:
            r_inv = sigma_inv[skey]
            log_det = sigma_logdet[skey]
            if cop_type == "gaussian":
                log_c = _gauss_copula_log_density(u_mat, r_inv, log_det)
            else:
                assert nu_val is not None
                log_c = _t_copula_log_density(u_mat, r_inv, log_det, nu_val)
            log_joint = float(special.logsumexp(log_pf + log_marg_sum + log_c))
            joint_lls.append(log_joint)

            draws = eng.sample_player(rec["marginals"], arch)
            derived = derive_from_draws(draws)
            p_dds.append(derived["p_dd"])
            p_tds.append(derived["p_td"])

        dd_ll = _bernoulli_ll(y_dd, np.asarray(p_dds, dtype=float))
        td_ll = _bernoulli_ll(y_td, np.asarray(p_tds, dtype=float))
        tail_ll = 0.5 * (dd_ll + td_ll)
        results[vname] = {
            "copula": cop_type,
            "nu": nu_val,
            "mean_joint_ll": float(np.mean(joint_lls)),
            "mean_dd_ll": dd_ll,
            "mean_td_ll": td_ll,
            "mean_tail_ll": tail_ll,
            "tail_logloss": -tail_ll,
        }

    gauss = results["gaussian"]
    t_names = [v for v in results if v != "gaussian"]
    # Select best t variant by mean_joint_ll (breaking ties by mean_tail_ll)
    best_t_name = max(
        t_names,
        key=lambda k: (results[k]["mean_joint_ll"], results[k]["mean_tail_ll"]),
    ) if t_names else None

    if best_t_name is not None:
        best_t = results[best_t_name]
        delta_joint = best_t["mean_joint_ll"] - gauss["mean_joint_ll"]
        delta_tail = best_t["mean_tail_ll"] - gauss["mean_tail_ll"]
        best_nu = best_t["nu"]
        adopt = (delta_joint >= 0.0020) and (delta_tail >= 0.0020)
    else:
        delta_joint = 0.0
        delta_tail = 0.0
        best_nu = None
        adopt = False

    return {
        "n_records": len(records),
        "variants": results,
        "best_t_variant": best_t_name,
        "best_t_nu": best_nu,
        "delta_joint_ll": float(delta_joint),
        "delta_tail_ll": float(delta_tail),
        "adopt_student_t": bool(adopt),
    }
