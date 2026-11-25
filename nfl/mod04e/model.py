"""MOD-04e E2: Hierarchical Game-Script Opportunity Model.

Decomposes player-game volume and efficiency:
  team_plays (pace prior shrunk to trailing + total_line)
  x pass_share (script: team pass rate, PROE, spread)
  -> team_pass_attempts / team_rush_attempts
  x player snap_share x target_share / carry_share (EB-shrunk toward depth-chart role priors)
  x efficiency (catch_rate, ypt, ypc, comp_pct, int_rate; EB-shrunk toward role priors)

Distributions (discrete PMFs over 0..K, renormalized):
  - Counts (receptions, rush_attempts, pass_attempts, pass_completions): NegBin(mu, alpha)
  - Interceptions & low-target receivers (expected targets < 3): Hurdle-NB
  - Yardage (rec_yds, rush_yds): compound Poisson/NB-Gamma via simulation-free FFT convolution on 0..400
  - anytime_td: TD-count decomposition (RZ carries * rate + RZ targets * rate + explosive non-RZ rate)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd
from scipy import optimize, special, stats as sps

from nfl.mod04e.features import assert_feature_schema

ENGINE_ID = "mod04_nfl_gamescript_shadow_v1"

COUNT_FAMILIES = ("receptions", "rush_attempts", "pass_attempts", "pass_completions", "interceptions")
YARD_FAMILIES = ("rec_yds", "rush_yds")
TD_FAMILIES = ("anytime_td",)
ALL_FAMILIES = COUNT_FAMILIES + YARD_FAMILIES + TD_FAMILIES

_FAMILY_ALIASES = {
    "completions": "pass_completions",
    "pass_att": "pass_attempts",
    "pass_cmp": "pass_completions",
    "rush_att": "rush_attempts",
}

K_COUNT = 100
K_YARDS = 400
K_TD = 10
FFT_LEN = 2048
MU_FLOOR = 1e-4


def canonical_family(family: str) -> str:
    f = str(family).strip().lower()
    f = _FAMILY_ALIASES.get(f, f)
    if f not in ALL_FAMILIES:
        raise ValueError(f"Unknown MOD-04e family {family!r}; expected one of {ALL_FAMILIES}")
    return f


def _to_row_dict(feature_row: Any) -> dict[str, Any]:
    if isinstance(feature_row, pd.DataFrame):
        if len(feature_row) != 1:
            raise ValueError(f"Expected 1-row DataFrame, got {len(feature_row)} rows")
        assert_feature_schema(feature_row)
        return feature_row.iloc[0].to_dict()
    if isinstance(feature_row, pd.Series):
        assert_feature_schema(feature_row.to_frame().T)
        return feature_row.to_dict()
    if isinstance(feature_row, Mapping):
        assert_feature_schema(pd.DataFrame(columns=list(feature_row.keys())))
        return dict(feature_row)
    raise TypeError(f"Unsupported feature_row type: {type(feature_row).__name__}")


def _finite(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _role_key(pos: Any, depth: Any) -> tuple[str, int]:
    p = str(pos or "UNK").strip().upper()
    if p in ("FB",):
        p = "RB"
    if p not in ("QB", "RB", "WR", "TE"):
        p = "UNK"
    d = _finite(depth)
    db = int(min(max(round(d), 1), 3)) if d is not None else 0
    return (p, db)


def _fit_ridge(X: np.ndarray, y: np.ndarray, l2: float = 5.0) -> np.ndarray:
    """Closed-form Ridge with unpenalized intercept (first column = 1)."""
    if len(y) == 0:
        return np.zeros(X.shape[1], dtype=float)
    pen = np.eye(X.shape[1], dtype=float) * l2
    pen[0, 0] = 0.0
    xtx = X.T @ X + pen
    xty = X.T @ y
    return np.linalg.solve(xtx, xty)


def _nb_pmf(mu: float, alpha: float, k_max: int) -> np.ndarray:
    k = np.arange(k_max + 1, dtype=float)
    mu = max(float(mu), 0.0)
    if mu <= 1e-9:
        out = np.zeros(k_max + 1, dtype=float)
        out[0] = 1.0
        return out
    alpha = max(float(alpha), 1e-4)
    r = 1.0 / alpha
    p = r / (r + mu)
    pmf = sps.nbinom.pmf(k, r, p)
    pmf = np.where(np.isfinite(pmf) & (pmf >= 0.0), pmf, 0.0)
    tot = float(pmf.sum())
    if tot <= 0.0:
        out = np.zeros(k_max + 1, dtype=float)
        out[min(int(round(mu)), k_max)] = 1.0
        return out
    return pmf / tot


def _hurdle_nb_pmf(mu_pos: float, alpha: float, p_zero: float, k_max: int) -> np.ndarray:
    pz = float(np.clip(p_zero, 0.0, 0.995))
    if pz >= 0.995:
        out = np.zeros(k_max + 1, dtype=float)
        out[0] = 1.0
        return out
    nb = _nb_pmf(max(mu_pos, MU_FLOOR), alpha, k_max)
    pos_mass = float(nb[1:].sum())
    if pos_mass <= 1e-12:
        out = np.zeros(k_max + 1, dtype=float)
        out[0] = pz
        out[1] = 1.0 - pz
        return out
    out = np.zeros(k_max + 1, dtype=float)
    out[0] = pz
    out[1:] = (1.0 - pz) * (nb[1:] / pos_mass)
    return out / out.sum()


def _compound_gamma_pmf(exp_n: float, mean_gain: float, shape_gain: float, alpha_n: float = 0.0, k_max: int = K_YARDS) -> np.ndarray:
    """Simulation-free discrete compound Poisson/NB-Gamma PMF on integer yards 0..k_max via FFT PGF."""
    exp_n = max(float(exp_n), 0.0)
    mean_gain = max(float(mean_gain), 0.1)
    shape_gain = float(np.clip(shape_gain, 0.4, 30.0))
    if exp_n <= 1e-8:
        out = np.zeros(k_max + 1, dtype=float)
        out[0] = 1.0
        return out

    # Discretize single-event gain X ~ Gamma(shape_gain, scale=mean_gain/shape_gain) on 0..k_max
    scale = mean_gain / shape_gain
    y_grid = np.arange(k_max + 1, dtype=float)
    hi = sps.gamma.cdf(y_grid + 0.5, shape_gain, scale=scale)
    lo = np.where(y_grid <= 0, 0.0, sps.gamma.cdf(np.maximum(y_grid - 0.5, 0.0), shape_gain, scale=scale))
    g_pmf = np.maximum(hi - lo, 0.0)
    g_tot = float(g_pmf.sum())
    if g_tot <= 0.0:
        g_pmf = np.zeros(k_max + 1, dtype=float)
        g_pmf[min(int(round(mean_gain)), k_max)] = 1.0
    else:
        g_pmf /= g_tot

    padded = np.zeros(FFT_LEN, dtype=float)
    padded[: k_max + 1] = g_pmf
    G = np.fft.rfft(padded)

    if alpha_n <= 1e-5:
        # Compound Poisson PGF: exp(lambda * (G - 1))
        phi = np.exp(exp_n * (G - 1.0))
    else:
        # Compound NegBin PGF: (p / (1 - (1 - p) * G))^r
        r = 1.0 / alpha_n
        p = r / (r + exp_n)
        denom = 1.0 - (1.0 - p) * G
        phi = (p / denom) ** r

    raw = np.fft.irfft(phi, n=FFT_LEN)[: k_max + 1]
    pmf = np.maximum(raw, 0.0)
    tot = float(pmf.sum())
    if tot <= 0.0 or not np.isfinite(tot):
        out = np.zeros(k_max + 1, dtype=float)
        out[0] = 1.0
        return out
    return pmf / tot


def pmf_p_over(pmf: np.ndarray, line: float) -> float:
    """P(X > line) from a discrete PMF over 0..K."""
    if line is None or not math.isfinite(float(line)):
        return float("nan")
    k = int(math.floor(float(line)))
    if k < 0:
        return 1.0
    if k + 1 >= len(pmf):
        return 0.0
    return float(np.clip(pmf[k + 1 :].sum(), 0.0, 1.0))


def pmf_p_at_least(pmf: np.ndarray, k: int | float) -> float:
    """P(X >= k) from a discrete PMF over 0..K."""
    idx = int(math.ceil(float(k)))
    if idx <= 0:
        return 1.0
    if idx >= len(pmf):
        return 0.0
    return float(np.clip(pmf[idx:].sum(), 0.0, 1.0))


def _fit_negbin_alpha(mu: np.ndarray, y: np.ndarray) -> float:
    mu_c = np.clip(mu, MU_FLOOR, None)
    y_c = np.clip(np.round(y), 0.0, None)

    def nll(log_a: float) -> float:
        alpha = float(np.exp(log_a))
        r = 1.0 / max(alpha, 1e-6)
        p = r / (r + mu_c)
        return -float(np.sum(sps.nbinom.logpmf(y_c, r, p)))

    res = optimize.minimize_scalar(nll, bounds=(math.log(1e-4), math.log(4.0)), method="bounded")
    return float(np.exp(res.x))


def _fit_hurdle_logistic(mu: np.ndarray, is_zero: np.ndarray) -> tuple[float, float]:
    """Fit P(Y == 0) = sigmoid(h0 + h1 * log(mu + 0.1)) via regularized NLL."""
    x = np.log(np.clip(mu, 0.0, None) + 0.1)
    y0 = is_zero.astype(float)
    if len(y0) < 10 or y0.mean() in (0.0, 1.0):
        base = float(np.clip(y0.mean() if len(y0) else 0.5, 0.02, 0.98))
        return float(special.logit(base)), 0.0

    def nll(theta: np.ndarray) -> float:
        z = theta[0] + theta[1] * x
        loss = np.logaddexp(0.0, z) - y0 * z
        reg = 0.05 * (theta[1] ** 2)
        return float(np.mean(loss) + reg)

    res = optimize.minimize(nll, x0=np.array([float(special.logit(np.clip(y0.mean(), 0.02, 0.98))), -0.5]), method="BFGS")
    return float(res.x[0]), float(res.x[1])


def _fit_yard_params(exp_n: np.ndarray, mean_gain: np.ndarray, y_yards: np.ndarray) -> tuple[float, float]:
    """Fit (shape_gain, alpha_n) for compound NB-Gamma on integer yards 0..K_YARDS (batched FFT)."""
    n_c = np.clip(exp_n, 0.05, 40.0)
    g_c = np.clip(mean_gain, 1.0, 40.0)
    y_i = np.clip(np.round(y_yards), 0, K_YARDS).astype(int)

    if len(y_i) > 250:
        step = max(len(y_i) // 250, 1)
        idx = np.arange(0, len(y_i), step)[:250]
        n_c, g_c, y_i = n_c[idx], g_c[idx], y_i[idx]

    m = len(y_i)
    y_grid = np.arange(K_YARDS + 1, dtype=float)[None, :]
    row_idx = np.arange(m)

    def nll(theta: np.ndarray) -> float:
        shape_g = float(np.exp(theta[0]))
        alpha_n = float(np.exp(theta[1]))
        scale = (g_c / shape_g)[:, None]
        hi = sps.gamma.cdf(y_grid + 0.5, shape_g, scale=scale)
        lo = np.where(y_grid <= 0, 0.0, sps.gamma.cdf(np.maximum(y_grid - 0.5, 0.0), shape_g, scale=scale))
        g_pmf = np.maximum(hi - lo, 0.0)
        g_pmf /= np.maximum(g_pmf.sum(axis=1, keepdims=True), 1e-12)

        padded = np.zeros((m, FFT_LEN), dtype=float)
        padded[:, : K_YARDS + 1] = g_pmf
        G = np.fft.rfft(padded, axis=1)

        if alpha_n <= 1e-5:
            phi = np.exp(n_c[:, None] * (G - 1.0))
        else:
            r = 1.0 / alpha_n
            p = (r / (r + n_c))[:, None]
            phi = (p / (1.0 - (1.0 - p) * G)) ** r

        pmf = np.maximum(np.fft.irfft(phi, n=FFT_LEN, axis=1)[:, : K_YARDS + 1], 0.0)
        pmf /= np.maximum(pmf.sum(axis=1, keepdims=True), 1e-12)
        probs = np.clip(pmf[row_idx, y_i], 1e-12, None)
        return float(-np.sum(np.log(probs))) + 0.01 * (theta[0] ** 2 + (theta[1] + 2.0) ** 2)

    res = optimize.minimize(
        nll,
        x0=np.array([math.log(2.0), math.log(0.15)]),
        bounds=[(math.log(0.5), math.log(15.0)), (math.log(1e-4), math.log(1.5))],
        method="L-BFGS-B",
        options={"maxiter": 25},
    )
    return float(np.exp(res.x[0])), float(np.exp(res.x[1]))


@dataclass
class RolePriors:
    global_priors: dict[str, float]
    pos_priors: dict[tuple[str, str], float]
    role_priors: dict[tuple[str, int, str], float]

    def get(self, role: tuple[str, int], metric: str, default: float) -> float:
        k3 = (role[0], role[1], metric)
        if k3 in self.role_priors:
            return self.role_priors[k3]
        k2 = (role[0], metric)
        if k2 in self.pos_priors:
            return self.pos_priors[k2]
        return self.global_priors.get(metric, default)


def _fit_role_priors(df: pd.DataFrame, metrics: list[tuple[str, float]]) -> RolePriors:
    roles = [_role_key(p, d) for p, d in zip(df.get("position", pd.Series(["UNK"] * len(df))),
                                              df.get("depth_chart_role", pd.Series([np.nan] * len(df))))]
    pos_list = [r[0] for r in roles]
    glob: dict[str, float] = {}
    pos_p: dict[tuple[str, str], float] = {}
    role_p: dict[tuple[str, int, str], float] = {}
    for col, default in metrics:
        if col not in df.columns:
            glob[col] = default
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        valid = vals.dropna()
        gmean = float(valid.mean()) if len(valid) else default
        glob[col] = gmean
        tmp = pd.DataFrame({"pos": pos_list, "role": roles, "v": vals})
        for p, grp in tmp.groupby("pos"):
            gv = grp["v"].dropna()
            if len(gv) >= 3:
                pos_p[(p, col)] = float((gv.sum() + 5.0 * gmean) / (len(gv) + 5.0))
        for r, grp in tmp.groupby("role"):
            gv = grp["v"].dropna()
            if len(gv) >= 3:
                base = pos_p.get((r[0], col), gmean)
                role_p[(r[0], r[1], col)] = float((gv.sum() + 5.0 * base) / (len(gv) + 5.0))
    return RolePriors(glob, pos_p, role_p)


@dataclass
class Model:
    family: str
    engine_id: str
    plays_beta: np.ndarray
    pass_share_beta: np.ndarray
    role_priors: RolePriors
    alpha: float
    hurdle_params: Optional[tuple[float, float]] = None
    yard_params: Optional[tuple[float, float]] = None
    mean_scale: float = 1.0
    defaults: dict[str, float] = field(default_factory=dict)
    _last_pmf: Optional[np.ndarray] = field(default=None, init=False, repr=False)

    def _decompose(self, row: Mapping[str, Any]) -> dict[str, float]:
        role = _role_key(row.get("position"), row.get("depth_chart_role"))
        n_prior = max(_finite(row.get("n_prior_games"), 1.0) or 1.0, 0.0)

        # 1. Team plays (pace prior shrunk to trailing + game total)
        tm_plays_tr = _finite(row.get("team_plays_ewm"), _finite(row.get("team_plays_l5"), self.defaults["team_plays"]))
        tot_line = _finite(row.get("total_line"), self.defaults["total_line"])
        x_plays = np.array([1.0, tm_plays_tr, tot_line], dtype=float)
        team_plays = float(np.clip(x_plays @ self.plays_beta, 45.0, 85.0))

        # 2. Team pass share (script: pass rate + PROE + spread)
        tm_pr_tr = _finite(row.get("team_pass_rate_ewm"), _finite(row.get("team_pass_rate_l5"), self.defaults["pass_rate"]))
        tm_proe = _finite(row.get("team_proe_ewm"), 0.0)
        tm_spread = _finite(row.get("team_spread"), 0.0)
        x_ps = np.array([1.0, tm_pr_tr, tm_proe, tm_spread], dtype=float)
        pass_share = float(np.clip(x_ps @ self.pass_share_beta, 0.35, 0.80))

        team_pass_att = team_plays * pass_share
        team_rush_att = team_plays * (1.0 - pass_share)

        # 3. Player shares EB-shrunk toward depth-chart role priors
        def shrink(col: str, prior_metric: str, default: float, k_eb: float = 4.0) -> float:
            prior = self.role_priors.get(role, prior_metric, default)
            obs = _finite(row.get(col))
            if obs is None:
                return prior
            w = n_prior / (n_prior + k_eb)
            return float(w * obs + (1.0 - w) * prior)

        snap_prior = self.role_priors.get(role, "snap_share_ewm", 0.5)
        snap_sh = shrink("snap_share_ewm", "snap_share_ewm", 0.5, k_eb=3.0)
        snap_factor = float(np.clip(snap_sh / max(snap_prior, 0.10), 0.35, 1.5)) if snap_prior > 0 else 1.0

        vac_tgt_sh = max(_finite(row.get("vacated_target_share"), 0.0) or 0.0, 0.0)
        vac_car_sh = max(_finite(row.get("vacated_carry_share"), 0.0) or 0.0, 0.0)

        tgt_sh = shrink("target_share_ewm", "target_share_ewm", 0.12, k_eb=4.0)
        # Active skill players absorb a fraction of vacated target/carry shares
        tgt_sh_adj = float(np.clip(tgt_sh + 0.35 * vac_tgt_sh * min(max(tgt_sh / 0.25, 0.1), 1.0), 0.0, 0.55))

        car_sh = shrink("carry_share_ewm", "carry_share_ewm", 0.15, k_eb=4.0)
        car_sh_adj = float(np.clip(car_sh + 0.35 * vac_car_sh * min(max(car_sh / 0.30, 0.1), 1.0), 0.0, 0.85))

        exp_targets = max(team_pass_att * tgt_sh_adj * (0.5 + 0.5 * snap_factor), 0.01)
        exp_carries = max(team_rush_att * car_sh_adj * (0.5 + 0.5 * snap_factor), 0.01)

        # QB volume (anchored to trailing pass attempts relative to team pass attempts)
        qb_pa_obs = _finite(row.get("pass_attempts_ewm"))
        qb_pa_prior = self.role_priors.get(role, "pass_attempts_ewm", team_pass_att)
        if role[0] == "QB" and qb_pa_obs is not None:
            w_qb = n_prior / (n_prior + 3.0)
            exp_pass_att = float(np.clip(w_qb * qb_pa_obs * (team_pass_att / max(qb_pa_prior, 10.0)) + (1.0 - w_qb) * team_pass_att, 1.0, 65.0))
        elif role[0] == "QB":
            exp_pass_att = float(np.clip(team_pass_att, 1.0, 65.0))
        else:
            exp_pass_att = float(np.clip(qb_pa_obs if qb_pa_obs is not None else 0.05, 0.01, 5.0))

        # 4. Efficiency rates EB-shrunk toward role priors
        catch_rate = float(np.clip(shrink("catch_rate_ewm", "catch_rate_ewm", 0.65, k_eb=6.0), 0.25, 0.92))
        ypt = float(np.clip(shrink("ypt_ewm", "ypt_ewm", 7.5, k_eb=6.0), 2.0, 20.0))
        ypr = float(np.clip(ypt / max(catch_rate, 0.25), 3.0, 28.0))
        ypc = float(np.clip(shrink("ypc_ewm", "ypc_ewm", 4.1, k_eb=6.0), 1.5, 10.0))
        comp_pct = float(np.clip(shrink("comp_pct_ewm", "comp_pct_ewm", 0.64, k_eb=6.0), 0.45, 0.78))
        int_rate = float(np.clip(shrink("int_rate_ewm", "int_rate_ewm", 0.023, k_eb=8.0), 0.005, 0.07))

        # Red-zone TD rates
        rz_car = max(shrink("rz_carries_ewm", "rz_carries_ewm", 0.4, k_eb=5.0), 0.0)
        rz_tgt = max(shrink("rz_targets_ewm", "rz_targets_ewm", 0.4, k_eb=5.0), 0.0)
        rz_rush_td_rate = float(np.clip(shrink("rz_rush_td_rate_ewm", "rz_rush_td_rate_ewm", 0.14, k_eb=8.0), 0.01, 0.45))
        rz_rec_td_rate = float(np.clip(shrink("rz_rec_td_rate_ewm", "rz_rec_td_rate_ewm", 0.16, k_eb=8.0), 0.01, 0.45))
        expl_td_rate = float(np.clip(shrink("explosive_td_rate_ewm", "explosive_td_rate_ewm", 0.015, k_eb=8.0), 0.001, 0.12))

        exp_catches = exp_targets * catch_rate
        exp_comp = exp_pass_att * comp_pct
        exp_int = exp_pass_att * int_rate

        # Scale RZ opportunity by implied team total / league average (22.5)
        imp_tot = _finite(row.get("implied_team_total"), 22.5) or 22.5
        td_env = float(np.clip(imp_tot / 22.5, 0.6, 1.5))
        exp_touches = exp_carries + exp_targets
        mean_td = max(
            (rz_car * rz_rush_td_rate + rz_tgt * rz_rec_td_rate + exp_touches * expl_td_rate) * td_env,
            1e-3,
        )

        return {
            "team_plays": team_plays,
            "pass_share": pass_share,
            "exp_targets": exp_targets,
            "exp_catches": exp_catches,
            "exp_carries": exp_carries,
            "exp_pass_att": exp_pass_att,
            "exp_comp": exp_comp,
            "exp_int": exp_int,
            "ypr": ypr,
            "ypc": ypc,
            "mean_td": mean_td,
        }

    def raw_mean(self, feature_row: Any) -> float:
        row = _to_row_dict(feature_row)
        d = self._decompose(row)
        if self.family == "receptions":
            return d["exp_catches"]
        if self.family == "rush_attempts":
            return d["exp_carries"]
        if self.family == "pass_attempts":
            return d["exp_pass_att"]
        if self.family == "pass_completions":
            return d["exp_comp"]
        if self.family == "interceptions":
            return d["exp_int"]
        if self.family == "rec_yds":
            return d["exp_catches"] * d["ypr"]
        if self.family == "rush_yds":
            return d["exp_carries"] * d["ypc"]
        if self.family == "anytime_td":
            return d["mean_td"]
        raise ValueError(self.family)

    def pmf(self, feature_row: Any) -> np.ndarray:
        row = _to_row_dict(feature_row)
        d = self._decompose(row)
        if self.family in ("receptions", "rush_attempts", "pass_attempts", "pass_completions", "interceptions"):
            mu = max(self.raw_mean(row) * self.mean_scale, MU_FLOOR)
            use_hurdle = self.family == "interceptions" or (
                self.family == "receptions" and d["exp_targets"] < 3.0
            )
            if use_hurdle and self.hurdle_params is not None:
                h0, h1 = self.hurdle_params
                p_zero = float(special.expit(h0 + h1 * math.log(mu + 0.1)))
                mu_pos = max(mu / max(1.0 - p_zero, 0.05), MU_FLOOR)
                pmf = _hurdle_nb_pmf(mu_pos, self.alpha, p_zero, K_COUNT)
            else:
                pmf = _nb_pmf(mu, self.alpha, K_COUNT)
        elif self.family == "rec_yds":
            shape_g, alpha_n = self.yard_params or (2.0, 0.15)
            exp_n = max(d["exp_catches"] * math.sqrt(self.mean_scale), 0.01)
            mean_g = max(d["ypr"] * math.sqrt(self.mean_scale), 1.0)
            pmf = _compound_gamma_pmf(exp_n, mean_g, shape_g, alpha_n=alpha_n, k_max=K_YARDS)
        elif self.family == "rush_yds":
            shape_g, alpha_n = self.yard_params or (2.0, 0.15)
            exp_n = max(d["exp_carries"] * math.sqrt(self.mean_scale), 0.01)
            mean_g = max(d["ypc"] * math.sqrt(self.mean_scale), 1.0)
            pmf = _compound_gamma_pmf(exp_n, mean_g, shape_g, alpha_n=alpha_n, k_max=K_YARDS)
        elif self.family == "anytime_td":
            mu = max(d["mean_td"] * self.mean_scale, MU_FLOOR)
            pmf = _nb_pmf(mu, self.alpha, K_TD)
        else:
            raise ValueError(self.family)

        self._last_pmf = pmf
        return pmf

    def p_over(self, first: Any, second: Optional[float] = None) -> float:
        """Compute P(X > line). Supports p_over(feature_row, line) or p_over(line) after pmf(feature_row)."""
        if second is not None:
            pmf = self.pmf(first)
            line = float(second)
        elif isinstance(first, (int, float, np.number)):
            if self._last_pmf is None:
                raise ValueError("Call pmf(feature_row) before p_over(line), or call p_over(feature_row, line)")
            pmf = self._last_pmf
            line = float(first)
        else:
            raise TypeError("p_over expects (feature_row, line) or (line,) after pmf(feature_row)")
        return pmf_p_over(pmf, line)

    def p_at_least(self, first: Any, second: Optional[int] = None) -> float:
        """Compute P(X >= k). Supports p_at_least(feature_row, k) or p_at_least(k) after pmf(feature_row)."""
        if second is not None:
            pmf = self.pmf(first)
            k = int(second)
        elif isinstance(first, (int, float, np.number)):
            if self._last_pmf is None:
                raise ValueError("Call pmf(feature_row) before p_at_least(k), or call p_at_least(feature_row, k)")
            pmf = self._last_pmf
            k = int(first)
        else:
            raise TypeError("p_at_least expects (feature_row, k) or (k,) after pmf(feature_row)")
        return pmf_p_at_least(pmf, k)


def fit(train_features: pd.DataFrame, train_labels: pd.DataFrame | pd.Series | np.ndarray, family: str) -> Model:
    """Fit hierarchical game-script opportunity model for `family` on pregame features and outcomes."""
    assert_feature_schema(train_features)
    fam = canonical_family(family)
    if len(train_features) == 0:
        raise ValueError("Cannot fit model on empty train_features")

    if isinstance(train_labels, pd.DataFrame):
        if fam not in train_labels.columns:
            raise KeyError(f"Label column {fam!r} missing from train_labels")
        y_raw = pd.to_numeric(train_labels[fam], errors="coerce").to_numpy(dtype=float)
        if "gradeable" in train_labels.columns:
            mask = train_labels["gradeable"].astype(bool).to_numpy() & np.isfinite(y_raw)
        else:
            mask = np.isfinite(y_raw)
    else:
        y_raw = pd.to_numeric(pd.Series(np.asarray(train_labels)), errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(y_raw)

    df = train_features.loc[mask].reset_index(drop=True)
    y = np.clip(y_raw[mask], 0.0, None)
    if fam == "anytime_td" and isinstance(train_labels, pd.DataFrame) and "td_count" in train_labels.columns:
        # Use full td_count (0, 1, 2, ...) when available so 2+ TD rungs are fit accurately
        tdc = pd.to_numeric(train_labels.loc[mask, "td_count"], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(tdc).all():
            y = np.clip(tdc, 0.0, None)

    if len(df) == 0:
        raise ValueError(f"No valid training rows for family {fam!r}")

    # 1. Fit team plays & pass_share regressions on training data (deduplicated per team-game when possible)
    default_plays = float(pd.to_numeric(df.get("team_plays_ewm"), errors="coerce").median()) if "team_plays_ewm" in df else 63.0
    if not math.isfinite(default_plays):
        default_plays = 63.0
    default_pr = float(pd.to_numeric(df.get("team_pass_rate_ewm"), errors="coerce").median()) if "team_pass_rate_ewm" in df else 0.57
    if not math.isfinite(default_pr):
        default_pr = 0.57
    default_tot = float(pd.to_numeric(df.get("total_line"), errors="coerce").median()) if "total_line" in df else 45.0
    if not math.isfinite(default_tot):
        default_tot = 45.0

    # Prior coefficients shrunk toward sensible NFL priors (plays ~ team_plays_ewm, pass_share ~ pass_rate - 0.005*spread)
    plays_beta = np.array([15.0, 0.70, 0.08], dtype=float)
    pass_share_beta = np.array([0.15, 0.72, 0.20, -0.004], dtype=float)
    if "team_plays_ewm" in df.columns and "total_line" in df.columns and len(df) >= 20:
        tp = pd.to_numeric(df["team_plays_ewm"], errors="coerce").fillna(default_plays).to_numpy()
        tl = pd.to_numeric(df["total_line"], errors="coerce").fillna(default_tot).to_numpy()
        # Regress trailing plays toward league baseline + total_line
        X_p = np.column_stack([np.ones(len(df)), tp, tl])
        y_p = 0.7 * tp + 0.3 * (default_plays + 0.15 * (tl - default_tot))
        plays_beta = _fit_ridge(X_p, y_p, l2=10.0)

    if "team_pass_rate_ewm" in df.columns and len(df) >= 20:
        pr = pd.to_numeric(df["team_pass_rate_ewm"], errors="coerce").fillna(default_pr).to_numpy()
        proe = pd.to_numeric(df.get("team_proe_ewm", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0).to_numpy()
        spr = pd.to_numeric(df.get("team_spread", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0).to_numpy()
        X_ps = np.column_stack([np.ones(len(df)), pr, proe, spr])
        y_ps = np.clip(pr + 0.25 * proe - 0.004 * spr, 0.35, 0.80)
        pass_share_beta = _fit_ridge(X_ps, y_ps, l2=10.0)

    # 2. Fit depth-chart role priors on training data ONLY
    prior_metrics = [
        ("snap_share_ewm", 0.50),
        ("target_share_ewm", 0.12),
        ("carry_share_ewm", 0.15),
        ("pass_attempts_ewm", 32.0),
        ("catch_rate_ewm", 0.65),
        ("ypt_ewm", 7.5),
        ("ypc_ewm", 4.1),
        ("comp_pct_ewm", 0.64),
        ("int_rate_ewm", 0.023),
        ("rz_carries_ewm", 0.4),
        ("rz_targets_ewm", 0.4),
        ("rz_rush_td_rate_ewm", 0.14),
        ("rz_rec_td_rate_ewm", 0.16),
        ("explosive_td_rate_ewm", 0.015),
    ]
    role_priors = _fit_role_priors(df, prior_metrics)

    model = Model(
        family=fam,
        engine_id=ENGINE_ID,
        plays_beta=plays_beta,
        pass_share_beta=pass_share_beta,
        role_priors=role_priors,
        alpha=0.20,
        defaults={"team_plays": default_plays, "pass_rate": default_pr, "total_line": default_tot},
    )

    # 3. Compute raw decomposed means on training rows -> calibrate mean_scale & fit distribution params
    decomps = [model._decompose(r) for r in df.to_dict("records")]
    raw_mu = np.array([max(model._raw_mean_from_decomp(d), MU_FLOOR) for d in decomps], dtype=float)
    mean_scale = float(np.clip(np.sum(y) / max(np.sum(raw_mu), 1e-6), 0.5, 2.0))
    # Shrink scale strongly toward 1.0 on tiny training sets
    w_scale = len(y) / (len(y) + 30.0)
    model.mean_scale = float(w_scale * mean_scale + (1.0 - w_scale) * 1.0)
    mu_cal = np.clip(raw_mu * model.mean_scale, MU_FLOOR, None)

    if fam in COUNT_FAMILIES or fam == "anytime_td":
        model.alpha = _fit_negbin_alpha(mu_cal, y)
        if fam in ("interceptions", "receptions"):
            model.hurdle_params = _fit_hurdle_logistic(mu_cal, (y == 0))
    elif fam == "rec_yds":
        exp_n = np.array([d["exp_catches"] * math.sqrt(model.mean_scale) for d in decomps], dtype=float)
        mean_g = np.array([d["ypr"] * math.sqrt(model.mean_scale) for d in decomps], dtype=float)
        model.yard_params = _fit_yard_params(exp_n, mean_g, y)
    elif fam == "rush_yds":
        exp_n = np.array([d["exp_carries"] * math.sqrt(model.mean_scale) for d in decomps], dtype=float)
        mean_g = np.array([d["ypc"] * math.sqrt(model.mean_scale) for d in decomps], dtype=float)
        model.yard_params = _fit_yard_params(exp_n, mean_g, y)

    return model


def _raw_mean_from_decomp(self: Model, d: dict[str, float]) -> float:
    if self.family == "receptions":
        return d["exp_catches"]
    if self.family == "rush_attempts":
        return d["exp_carries"]
    if self.family == "pass_attempts":
        return d["exp_pass_att"]
    if self.family == "pass_completions":
        return d["exp_comp"]
    if self.family == "interceptions":
        return d["exp_int"]
    if self.family == "rec_yds":
        return d["exp_catches"] * d["ypr"]
    if self.family == "rush_yds":
        return d["exp_carries"] * d["ypc"]
    if self.family == "anytime_td":
        return d["mean_td"]
    raise ValueError(self.family)


Model._raw_mean_from_decomp = _raw_mean_from_decomp
