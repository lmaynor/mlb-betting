"""B4 NBA rebounds structural decomposition shadow model (`nba_rebounds_decomp_shadow_v1`).

Decomposition fix attempt for MOD-02's failed rebounds refit (c=0.13, holdout slope 1.68) --
this is a fresh physical decomposition, NOT a re-weight of the old model.

Structure (strictly pre-game, prior-only rolling, shadow-only):
  1. Expected Missed Shots Available (EMSA):
       team pace proxy (prior possessions per game) * opponent (and own) missed-shot rates
       split by zone: `rim_miss_rate` and `three_miss_rate` (prior FGA rate * (1 - FG%) by zone),
       splitting rim misses vs long misses.
       - When a `shots` DataFrame (nba.shots.court schema) is passed, rim vs non-rim 2PT vs 3PT
         rates are computed directly from shot coordinates/zones (`zone_source = "shots_parquet"`).
       - When `shots` is NOT passed, `team_box` aggregates are used and rim misses are proxied
         by opponent 2PA share (`(2PA - 2PM) / poss`; `zone_source = "box_2pa_proxy"`).
  2. Minutes-Integrated Rebound Share:
       prior-only player OREB and DREB on-floor shares, EB-shrunk toward position archetype priors
       (`G`, `W`, `B`), renormalized over the expected on-floor 5-man unit (keeping unit shares
       summing to <= 1.0), and adjusted for opponent rebounding strength (`opp_oreb_rate`,
       `opp_dreb_rate`). Integrated over `minutes ~ Normal(mu_min, sd_min)` via 5-point
       Gauss-Hermite quadrature where per-minute opportunity scales linearly with minutes.
  3. Total Rebounds Distribution:
       total rebounds = OREB + DREB, each modeled as NegBin with its own mu from EMSA
       zone-weighted shares; full discrete PMF = discrete convolution of OREB and DREB PMFs.

All feature outputs are validated through `nhl.props.mod04_features.assert_feature_schema`
(single shared gate across MOD-04). No prop lines, odds, or outcome columns are ever features.
"""
from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from nba.props import config as C
from nba.props.data import parse_minutes, sb_team
from nhl.props import mod04_features as F

ENGINE_LABEL = "nba_rebounds_decomp_shadow_v1"

# Position archetype on-floor rebound share priors (2G + 2W + 1B sums to 1.00 for both OREB and DREB)
POS_OREB_PRIOR: dict[str, float] = {"G": 0.08, "W": 0.18, "B": 0.48}
POS_DREB_PRIOR: dict[str, float] = {"G": 0.14, "W": 0.20, "B": 0.32}
DEFAULT_OREB_PRIOR = 0.20
DEFAULT_DREB_PRIOR = 0.20

# Empirical-Bayes shrinkage strengths and league baselines
K_SHARE_OREB = 40.0
K_SHARE_DREB = 100.0
K_ZONE_FGA = 400.0    # possessions prior weight for FGA rate per possession
K_ZONE_FGM = 120.0    # attempts prior weight for FG%
K_TEAM_REB = 200.0    # missed-shot opportunities prior weight for team OREB/DREB rate

PRIOR_RIM_FGA_RATE = 0.30
PRIOR_RIM_FGP = 0.62
PRIOR_THREE_FGA_RATE = 0.36
PRIOR_THREE_FGP = 0.36
PRIOR_LONG_FGA_RATE = 0.56
PRIOR_LONG_FGP = 0.38

LG_OREB_RATE = 0.24
LG_DREB_RATE = 0.74
DEFAULT_POSS = 98.0
DEFAULT_MU_MIN = 20.0
DEFAULT_SD_MIN = 4.5

# Zone weights converting expected missed shots into team rebound pools
W_OREB_RIM = 0.28
W_OREB_LONG = 0.20
W_DREB_RIM = 0.72
W_DREB_LONG = 0.80

# 5-point Gauss-Hermite quadrature for Z ~ Normal(0, 1)
_GH_X, _GH_W_RAW = np.polynomial.hermite.hermgauss(5)
_GH_Z = _GH_X * math.sqrt(2.0)
_GH_W = _GH_W_RAW / math.sqrt(math.pi)

REQUIRED_FEAT_COLS: tuple[str, ...] = (
    "mu_min",
    "sd_min",
    "emsa_own_rim",
    "emsa_own_long",
    "emsa_opp_rim",
    "emsa_opp_long",
    "renorm_oreb_share",
    "renorm_dreb_share",
    "opp_oreb_rate",
    "opp_dreb_rate",
)

_BANNED_OUTCOME_OR_MARKET_COLS = frozenset({
    "rebounds", "rebounds_offensive", "rebounds_defensive", "points", "assists",
    "steals", "blocks", "turnovers", "fouls", "minutes", "min", "played", "starter",
    "field_goals_made", "field_goals_attempted", "three_points_made", "three_points_attempted",
    "two_points_made", "two_points_attempted", "free_throws_made", "free_throws_attempted",
    "plus_minus", "line", "odds", "over_odds", "under_odds", "p_market", "p_mkt",
})


def _nb_pmf_grid(mu: np.ndarray, alpha: float, kmax: int) -> np.ndarray:
    """Evaluate normalized NegBin(mu, alpha) PMF over `0..kmax` (`alpha <= 0` -> Poisson)."""
    k = np.arange(0, int(kmax) + 1, dtype=float)
    mu_arr = np.asarray(mu, dtype=float)
    shape = mu_arr.shape
    mu_flat = mu_arr.ravel()
    out = np.zeros((len(mu_flat), len(k)), dtype=float)

    pos = np.isfinite(mu_flat) & (mu_flat > 0.0)
    out[~pos, 0] = 1.0

    if pos.any():
        m = mu_flat[pos][:, None]
        if alpha is None or float(alpha) <= 0.0:
            probs = poisson.pmf(k[None, :], m)
        else:
            n = 1.0 / float(alpha)
            p = n / (n + m)
            probs = nbinom.pmf(k[None, :], n, p)
        probs = np.where(np.isfinite(probs) & (probs >= 0.0), probs, 0.0)
        sums = probs.sum(axis=1, keepdims=True)
        bad = sums[:, 0] <= 0.0
        if bad.any():
            probs[bad, :] = 0.0
            probs[bad, -1] = 1.0
            sums[bad, 0] = 1.0
        out[pos] = probs / sums

    return out.reshape(*shape, len(k))


def convolve_pmfs(
    pmf_a: np.ndarray,
    pmf_b: np.ndarray,
    kmax: int | None = None,
) -> np.ndarray:
    """Discrete convolution of two PMFs `P(A + B = k)` truncated/normalized on `0..kmax`.

    Supports 1D arrays `(L_a,)`, `(L_b,)` or 2D batches `(N, L_a)`, `(N, L_b)`.
    """
    a = np.asarray(pmf_a, dtype=float)
    b = np.asarray(pmf_b, dtype=float)
    is_1d = (a.ndim == 1 and b.ndim == 1)
    if is_1d:
        a = a[None, :]
        b = b[None, :]
    if a.shape[0] != b.shape[0]:
        raise ValueError(f"Batch sizes must match: {a.shape[0]} vs {b.shape[0]}")

    full_len = a.shape[1] + b.shape[1] - 1
    out_len = full_len if kmax is None else int(kmax) + 1
    if out_len <= 0:
        raise ValueError(f"kmax must be >= 0, got {kmax}")

    out = np.zeros((a.shape[0], out_len), dtype=float)
    for i in range(a.shape[0]):
        c = np.convolve(a[i], b[i])[:out_len]
        c = np.where(np.isfinite(c) & (c >= 0.0), c, 0.0)
        tot = float(c.sum())
        if tot <= 0.0:
            c = np.zeros(out_len, dtype=float)
            c[0] = 1.0
        else:
            c = c / tot
        out[i] = c

    return out[0] if is_1d else out


def fit_nb_alpha(
    y: np.ndarray,
    mu: np.ndarray,
    grid: np.ndarray | None = None,
) -> float:
    """Maximum-likelihood NegBin dispersion `alpha` over a grid (`Var = mu + alpha*mu^2`).

    `nba/props/dist.py` parameterizes distributions via `(mu, var)` without a dispersion grid
    fitter, so we implement the grid MLE locally using `scipy.stats.nbinom` / `poisson`.
    """
    y_arr = np.asarray(y, dtype=float)
    mu_arr = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    ok = np.isfinite(y_arr) & np.isfinite(mu_arr) & (y_arr >= 0.0)
    if not ok.any():
        return 0.05
    y_v, m_v = y_arr[ok], mu_arr[ok]

    if grid is None:
        grid = np.concatenate([[0.0], np.linspace(0.005, 1.0, 80)])

    best_ll = -math.inf
    best_a = 0.0
    for a in grid:
        a_f = float(a)
        if a_f <= 0.0:
            ll = float(poisson.logpmf(y_v, m_v).mean())
        else:
            n = 1.0 / a_f
            ll = float(nbinom.logpmf(y_v, n, n / (n + m_v)).mean())
        if math.isfinite(ll) and ll > best_ll:
            best_ll = ll
            best_a = a_f
    return best_a


def renormalize_unit_shares(
    df: pd.DataFrame,
    share_col: str,
    group_cols: tuple[str, str] = ("game_id", "team"),
    weight_col: str = "mu_min",
    unit_size: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Renormalize player shares over the expected on-floor `unit_size` teammates.

    For the top `unit_size` rotation players on `(game_id, team)` ordered by `weight_col`
    descending, each player's share is divided by the sum of shares of those `unit_size`
    players so that the expected on-floor unit shares sum to `<= 1.0` (exact 1.0).
    For bench players outside the top `unit_size`, the denominator is the player's own
    share plus the top `unit_size - 1` teammates' shares.

    Returns `(renorm_share, is_expected_unit)`.
    """
    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=int)

    work = pd.DataFrame({
        "_idx": df.index,
        "_g0": df[group_cols[0]].values,
        "_g1": df[group_cols[1]].values,
        "_share": pd.to_numeric(df[share_col], errors="coerce").fillna(0.0).clip(lower=0.0).values,
        "_w": pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0).values,
        "_pid": df["player_id"].astype(str).values if "player_id" in df.columns else np.arange(len(df)).astype(str),
    })

    renorm = pd.Series(0.0, index=df.index, dtype=float)
    in_unit = pd.Series(0, index=df.index, dtype=int)

    for _, grp in work.groupby(["_g0", "_g1"], sort=False):
        ordered = grp.sort_values(["_w", "_pid"], ascending=[False, True])
        idxs = ordered["_idx"].to_numpy()
        shares = ordered["_share"].to_numpy(dtype=float)
        n = len(shares)
        k = min(int(unit_size), n)

        unit_sum = float(shares[:k].sum())
        core_sum = float(shares[: max(k - 1, 0)].sum())

        for pos_i in range(n):
            idx = idxs[pos_i]
            s_val = float(shares[pos_i])
            if pos_i < k:
                in_unit.at[idx] = 1
                denom = unit_sum
            else:
                denom = s_val + core_sum
            renorm.at[idx] = s_val / max(denom, 1e-6)

    return renorm, in_unit


class ReboundsDecompModel:
    """Structural OREB + DREB decomposition model with NegBin convolution and GH minutes integration."""

    def __init__(self, kmax: int = 25) -> None:
        self.kmax: int = int(kmax)
        self.global_share_mult: float = 1.0
        self.oreb_mult: float = 1.0
        self.dreb_mult: float = 1.0
        self.alpha_oreb: float = 0.05
        self.alpha_dreb: float = 0.05
        self.fitted: bool = False

    def _validate_features(self, feat: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_FEAT_COLS if c not in feat.columns]
        if missing:
            raise ValueError(f"ReboundsDecompModel missing required feature column(s): {missing}")

    @staticmethod
    def _base_pools_per48(feat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Zone-weighted expected team OREB and DREB pools per 48 minutes, opponent-adjusted."""
        own_rim = pd.to_numeric(feat["emsa_own_rim"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        own_long = pd.to_numeric(feat["emsa_own_long"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        opp_rim = pd.to_numeric(feat["emsa_opp_rim"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        opp_long = pd.to_numeric(feat["emsa_opp_long"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)

        opp_oreb = pd.to_numeric(feat["opp_oreb_rate"], errors="coerce").fillna(LG_OREB_RATE).to_numpy(dtype=float)
        opp_dreb = pd.to_numeric(feat["opp_dreb_rate"], errors="coerce").fillna(LG_DREB_RATE).to_numpy(dtype=float)

        # Opponent strength adjustment: stronger opp DREB% suppresses our OREB pool; stronger opp OREB% suppresses our DREB pool
        opp_oreb_allow_adj = np.clip((1.0 - opp_dreb) / max(1.0 - LG_DREB_RATE, 1e-3), 0.5, 1.5)
        opp_dreb_allow_adj = np.clip((1.0 - opp_oreb) / max(1.0 - LG_OREB_RATE, 1e-3), 0.5, 1.5)

        pool_oreb = (W_OREB_RIM * own_rim + W_OREB_LONG * own_long) * opp_oreb_allow_adj
        pool_dreb = (W_DREB_RIM * opp_rim + W_DREB_LONG * opp_long) * opp_dreb_allow_adj
        return np.maximum(pool_oreb, 0.0), np.maximum(pool_dreb, 0.0)

    def _raw_per_min_rates(self, feat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Un-calibrated per-minute expected OREB and DREB for each row."""
        pool_oreb, pool_dreb = self._base_pools_per48(feat)
        sh_o = pd.to_numeric(feat["renorm_oreb_share"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        sh_d = pd.to_numeric(feat["renorm_dreb_share"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        return (sh_o * pool_oreb) / 48.0, (sh_d * pool_dreb) / 48.0

    def fit(self, feat: pd.DataFrame, target: str = "rebounds") -> "ReboundsDecompModel":
        """Estimate league calibration constants (global + OREB/DREB split) via MoM and fit NegBin alphas."""
        self._validate_features(feat)
        if target not in feat.columns:
            raise ValueError(f"ReboundsDecompModel.fit missing target column {target!r}")

        mu_min = pd.to_numeric(feat["mu_min"], errors="coerce").to_numpy(dtype=float)
        y_tot = pd.to_numeric(feat[target], errors="coerce").to_numpy(dtype=float)
        r_o_pm, r_d_pm = self._raw_per_min_rates(feat)

        ok = (
            np.isfinite(mu_min) & (mu_min >= 0.0)
            & np.isfinite(y_tot) & (y_tot >= 0.0)
            & np.isfinite(r_o_pm) & np.isfinite(r_d_pm)
        )
        if not ok.any():
            raise ValueError("ReboundsDecompModel.fit received 0 valid training rows")

        raw_o = mu_min[ok] * r_o_pm[ok]
        raw_d = mu_min[ok] * r_d_pm[ok]
        raw_tot = raw_o + raw_d
        y_ok = y_tot[ok]

        self.global_share_mult = float(np.clip(float(y_ok.mean()) / max(float(raw_tot.mean()), 1e-6), 0.2, 5.0))

        has_split = (
            "rebounds_offensive" in feat.columns
            and "rebounds_defensive" in feat.columns
            and pd.to_numeric(feat.loc[ok, "rebounds_offensive"], errors="coerce").notna().all()
            and pd.to_numeric(feat.loc[ok, "rebounds_defensive"], errors="coerce").notna().all()
        )

        if has_split:
            y_o = pd.to_numeric(feat.loc[ok, "rebounds_offensive"], errors="coerce").to_numpy(dtype=float)
            y_d = pd.to_numeric(feat.loc[ok, "rebounds_defensive"], errors="coerce").to_numpy(dtype=float)
            pred_o_g = raw_o * self.global_share_mult
            pred_d_g = raw_d * self.global_share_mult
            self.oreb_mult = float(np.clip(float(y_o.mean()) / max(float(pred_o_g.mean()), 1e-6), 0.2, 5.0))
            self.dreb_mult = float(np.clip(float(y_d.mean()) / max(float(pred_d_g.mean()), 1e-6), 0.2, 5.0))

            mu_o_hat = np.maximum(pred_o_g * self.oreb_mult, 1e-6)
            mu_d_hat = np.maximum(pred_d_g * self.dreb_mult, 1e-6)
            self.alpha_oreb = fit_nb_alpha(y_o, mu_o_hat)
            self.alpha_dreb = fit_nb_alpha(y_d, mu_d_hat)
        else:
            self.oreb_mult = 1.0
            self.dreb_mult = 1.0
            mu_tot_hat = np.maximum(raw_tot * self.global_share_mult, 1e-6)
            alpha_tot = fit_nb_alpha(y_ok, mu_tot_hat)
            # Variance matching: alpha_o*mu_o^2 + alpha_d*mu_d^2 = alpha_tot*(mu_o+mu_d)^2
            mu_o_hat = np.maximum(raw_o * self.global_share_mult, 1e-6)
            mu_d_hat = np.maximum(raw_d * self.global_share_mult, 1e-6)
            ratio = float(np.mean((mu_o_hat + mu_d_hat) ** 2) / max(np.mean(mu_o_hat ** 2 + mu_d_hat ** 2), 1e-6))
            a_comp = float(np.clip(alpha_tot * ratio, 0.0, 2.0))
            self.alpha_oreb = a_comp
            self.alpha_dreb = a_comp

        self.fitted = True
        return self

    def _gh_component_nodes(self, feat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return `(mu_oreb_nodes, mu_dreb_nodes)` each shape `(N, 5)` across GH minutes nodes."""
        self._validate_features(feat)
        mu_min = np.maximum(pd.to_numeric(feat["mu_min"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)
        sd_min = np.maximum(pd.to_numeric(feat["sd_min"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)
        min_nodes = np.maximum(mu_min[:, None] + sd_min[:, None] * _GH_Z[None, :], 0.0)

        r_o_pm, r_d_pm = self._raw_per_min_rates(feat)
        rate_o = r_o_pm * self.global_share_mult * self.oreb_mult
        rate_d = r_d_pm * self.global_share_mult * self.dreb_mult
        return min_nodes * rate_o[:, None], min_nodes * rate_d[:, None]

    def predict_components(self, feat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return expected `(mu_oreb, mu_dreb)` integrated over GH minutes distribution."""
        if feat.empty:
            self._validate_features(feat)
            return np.empty(0, dtype=float), np.empty(0, dtype=float)
        mu_o_nodes, mu_d_nodes = self._gh_component_nodes(feat)
        return (mu_o_nodes * _GH_W[None, :]).sum(axis=1), (mu_d_nodes * _GH_W[None, :]).sum(axis=1)

    def predict_mu(self, feat: pd.DataFrame) -> np.ndarray:
        """Expected total rebounds (`mu_oreb + mu_dreb`)."""
        mu_o, mu_d = self.predict_components(feat)
        return mu_o + mu_d

    def predict_pmf(self, feat: pd.DataFrame, kmax: int | None = None) -> np.ndarray:
        """Full discrete PMF over `0..kmax` as the GH-minutes-integrated convolution of OREB and DREB PMFs."""
        km = self.kmax if kmax is None else int(kmax)
        if km < 0:
            raise ValueError(f"kmax must be >= 0, got {km}")
        self._validate_features(feat)
        if feat.empty:
            return np.empty((0, km + 1), dtype=float)

        mu_o_nodes, mu_d_nodes = self._gh_component_nodes(feat)
        pmf_o = _nb_pmf_grid(mu_o_nodes, self.alpha_oreb, km)  # (N, 5, km + 1)
        pmf_d = _nb_pmf_grid(mu_d_nodes, self.alpha_dreb, km)  # (N, 5, km + 1)

        n = len(feat)
        out = np.zeros((n, km + 1), dtype=float)
        for j in range(len(_GH_W)):
            conv_j = convolve_pmfs(pmf_o[:, j, :], pmf_d[:, j, :], kmax=km)
            out += _GH_W[j] * conv_j

        out = np.where(np.isfinite(out) & (out >= 0.0), out, 0.0)
        sums = out.sum(axis=1, keepdims=True)
        bad = sums[:, 0] <= 0.0
        if bad.any():
            out[bad, :] = 0.0
            out[bad, 0] = 1.0
            sums[bad, 0] = 1.0
        return out / sums

    def p_over(
        self,
        feat: pd.DataFrame,
        line: float | np.ndarray,
        kmax: int | None = None,
    ) -> np.ndarray:
        """Compute `P(rebounds > line)` directly from the convolved discrete PMF."""
        km = self.kmax if kmax is None else int(kmax)
        probs = self.predict_pmf(feat, kmax=km)
        n = len(feat)
        lines = np.broadcast_to(np.asarray(line, dtype=float), (n,))
        out = np.empty(n, dtype=float)
        for i in range(n):
            k_floor = int(math.floor(float(lines[i])))
            if k_floor < 0:
                out[i] = 1.0
            elif k_floor >= km:
                out[i] = 0.0
            else:
                out[i] = float(probs[i, k_floor + 1:].sum())
        return out


def _norm_team_col(df: pd.DataFrame, src_col: str, fallback_col: str | None = None) -> pd.Series:
    raw = df[src_col] if src_col in df.columns else (df[fallback_col] if fallback_col and fallback_col in df.columns else pd.Series("", index=df.index))
    return raw.map(lambda x: sb_team(x) or str(x).strip().upper())


def _prior_sum(df: pd.DataFrame, by: str, col: str, w: int) -> pd.Series:
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda s: s.rolling(w, min_periods=1).sum())


def _prior_mean(df: pd.DataFrame, by: str, col: str, w: int) -> pd.Series:
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda s: s.rolling(w, min_periods=1).mean())


def _prior_std(df: pd.DataFrame, by: str, col: str, w: int) -> pd.Series:
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda s: s.rolling(w, min_periods=2).std())


def _prior_ewm(df: pd.DataFrame, by: str, col: str, halflife: float) -> pd.Series:
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda s: s.ewm(halflife=halflife, min_periods=1).mean())


def _aggregate_shots_by_team_game(shots: pd.DataFrame) -> pd.DataFrame:
    """Aggregate `nba.shots.court` shot rows to per-(game_id, team) rim / mid / 3PT FGA and FGM."""
    if shots is None or shots.empty:
        return pd.DataFrame(columns=[
            "game_id", "team", "sh_rim_fga", "sh_rim_fgm",
            "sh_mid_fga", "sh_mid_fgm", "sh_three_fga", "sh_three_fgm",
        ])
    sh = shots.copy()
    sh["team"] = _norm_team_col(sh, "team", "team_abbr")
    is_three = pd.to_numeric(sh["three"], errors="coerce").fillna(0).astype(bool) if "three" in sh.columns else pd.Series(False, index=sh.index)
    is_made = pd.to_numeric(sh["made"], errors="coerce").fillna(0).astype(bool) if "made" in sh.columns else pd.Series(False, index=sh.index)

    if "zone" in sh.columns and sh["zone"].notna().any():
        is_rim_zone = sh["zone"].astype(str).str.contains("Restricted Area", case=False, na=False)
    elif "x" in sh.columns and "y" in sh.columns:
        x = pd.to_numeric(sh["x"], errors="coerce")
        y = pd.to_numeric(sh["y"], errors="coerce")
        dist = np.hypot(x - 0.0, y - 5.25)
        is_rim_zone = dist <= 6.0
    else:
        is_rim_zone = ~is_three

    sh["rim_fga"] = (~is_three & is_rim_zone).astype(float)
    sh["rim_fgm"] = (~is_three & is_rim_zone & is_made).astype(float)
    sh["mid_fga"] = (~is_three & ~is_rim_zone).astype(float)
    sh["mid_fgm"] = (~is_three & ~is_rim_zone & is_made).astype(float)
    sh["three_fga"] = is_three.astype(float)
    sh["three_fgm"] = (is_three & is_made).astype(float)

    return sh.groupby(["game_id", "team"], as_index=False).agg(
        sh_rim_fga=("rim_fga", "sum"),
        sh_rim_fgm=("rim_fgm", "sum"),
        sh_mid_fga=("mid_fga", "sum"),
        sh_mid_fgm=("mid_fgm", "sum"),
        sh_three_fga=("three_fga", "sum"),
        sh_three_fgm=("three_fgm", "sum"),
    )


def build_rebound_features(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Build leak-free pre-game NBA rebounds decomposition features.

    Parameters
    ----------
    frames:
        Dict with keys `"player_box"`, `"team_box"`, and optional `"shots"` (`nba.shots.court`
        parquet schema). If `"shots"` is absent or empty, rim miss rates are proxied from
        team box-score 2PA shares (`zone_source = "box_2pa_proxy"`).

    Returns
    -------
    DataFrame validated by `nhl.props.mod04_features.assert_feature_schema`.
    """
    if "player_box" not in frames or "team_box" not in frames:
        raise KeyError("frames must contain 'player_box' and 'team_box'")

    pb = frames["player_box"].copy()
    tb = frames["team_box"].copy()
    shots = frames.get("shots")

    if pb.empty or tb.empty:
        cols = [
            "game_id", "player_id", "date", "team", "opp", "is_home",
            "zone_source", "exp_poss", "rim_miss_rate", "three_miss_rate",
            "is_expected_unit", "raw_oreb_share", "raw_dreb_share",
        ] + list(REQUIRED_FEAT_COLS)
        out_empty = pd.DataFrame(columns=cols)
        F.assert_feature_schema(out_empty)
        return out_empty

    # Normalize player box minutes and team codes
    pb["date"] = pb["date"].astype(str).str[:10]
    pb["team"] = _norm_team_col(pb, "team", "team_abbr")
    if pb["minutes"].dtype == object or pd.api.types.is_string_dtype(pb["minutes"]):
        pb["_min"] = parse_minutes(pb["minutes"])
    else:
        pb["_min"] = pd.to_numeric(pb["minutes"], errors="coerce")
    pb = pb[pb["_min"].notna() & (pb["_min"] > 0.0)].copy()

    for col in ("rebounds", "rebounds_offensive", "rebounds_defensive"):
        if col not in pb.columns:
            pb[col] = np.nan
        pb[col] = pd.to_numeric(pb[col], errors="coerce")
    if pb["rebounds_defensive"].isna().all() and pb["rebounds"].notna().any() and pb["rebounds_offensive"].notna().any():
        pb["rebounds_defensive"] = (pb["rebounds"] - pb["rebounds_offensive"]).clip(lower=0.0)

    # Normalize team box and supplement missing team rebound/minute sums from player rows
    tb["date"] = tb["date"].astype(str).str[:10]
    tb["team"] = _norm_team_col(tb, "team", "team_abbr")
    if "opp" in tb.columns or "opp_abbr" in tb.columns:
        tb["opp"] = _norm_team_col(tb, "opp", "opp_abbr")
    else:
        # Derive opp from paired team rows per game_id
        pairs = tb[["game_id", "team"]].drop_duplicates()
        opp_map = pairs.merge(pairs, on="game_id", suffixes=("", "_opp"))
        opp_map = opp_map[opp_map["team"] != opp_map["team_opp"]].drop_duplicates(["game_id", "team"])
        tb = tb.merge(opp_map.rename(columns={"team_opp": "opp"}), on=["game_id", "team"], how="left")

    pb_team_agg = pb.groupby(["game_id", "team"], as_index=False).agg(
        _pb_oreb=("rebounds_offensive", "sum"),
        _pb_dreb=("rebounds_defensive", "sum"),
        _pb_reb=("rebounds", "sum"),
        _pb_min=("_min", "sum"),
    )
    tb = tb.merge(pb_team_agg, on=["game_id", "team"], how="left")

    for col in (
        "field_goals_attempted", "field_goals_made",
        "three_points_attempted", "three_points_made",
        "free_throws_attempted", "turnovers",
        "rebounds_offensive", "rebounds_defensive", "rebounds",
    ):
        if col not in tb.columns:
            tb[col] = np.nan
        tb[col] = pd.to_numeric(tb[col], errors="coerce")

    tb["t_oreb"] = tb["rebounds_offensive"].fillna(tb["_pb_oreb"]).fillna(0.0)
    tb["t_dreb"] = tb["rebounds_defensive"].fillna(tb["_pb_dreb"]).fillna(
        (tb["rebounds"].fillna(tb["_pb_reb"]) - tb["t_oreb"]).clip(lower=0.0)
    ).fillna(0.0)
    tb["t_min"] = pd.to_numeric(tb["minutes"], errors="coerce") if "minutes" in tb.columns else np.nan
    tb["t_min"] = tb["t_min"].fillna(tb["_pb_min"]).fillna(240.0)
    tb["team_court_min"] = (tb["t_min"] / 5.0).clip(lower=40.0)

    fga = tb["field_goals_attempted"].fillna(0.0)
    fgm = tb["field_goals_made"].fillna(0.0)
    fg3a = tb["three_points_attempted"].fillna(0.0)
    fg3m = tb["three_points_made"].fillna(0.0)
    fta = tb["free_throws_attempted"].fillna(0.0)
    tov = tb["turnovers"].fillna(0.0)
    fg2a = pd.to_numeric(tb["two_points_attempted"], errors="coerce") if "two_points_attempted" in tb.columns else (fga - fg3a)
    fg2m = pd.to_numeric(tb["two_points_made"], errors="coerce") if "two_points_made" in tb.columns else (fgm - fg3m)
    fg2a = fg2a.fillna(fga - fg3a).clip(lower=0.0)
    fg2m = fg2m.fillna(fgm - fg3m).clip(lower=0.0)

    tb["poss"] = (fga + 0.44 * fta - tb["t_oreb"] + tov).clip(lower=1.0)
    tb["own_misses"] = (fga - fgm).clip(lower=1.0)

    # Opponent misses in the same game (for team DREB% calculation)
    opp_m = tb[["game_id", "team", "own_misses"]].rename(
        columns={"team": "opp", "own_misses": "opp_misses"}
    )
    tb = tb.merge(opp_m, on=["game_id", "opp"], how="left")
    tb["opp_misses"] = tb["opp_misses"].fillna(tb["own_misses"]).clip(lower=1.0)

    has_shots = shots is not None and len(shots) > 0
    if has_shots:
        sh_agg = _aggregate_shots_by_team_game(shots)
        tb = tb.merge(sh_agg, on=["game_id", "team"], how="left")
        use_sh = tb["sh_rim_fga"].notna()
        tb["rim_fga"] = np.where(use_sh, tb["sh_rim_fga"], fg2a)
        tb["rim_fgm"] = np.where(use_sh, tb["sh_rim_fgm"], fg2m)
        tb["three_fga"] = np.where(use_sh, tb["sh_three_fga"], fg3a)
        tb["three_fgm"] = np.where(use_sh, tb["sh_three_fgm"], fg3m)
        tb["long_fga"] = np.where(use_sh, tb["sh_three_fga"] + tb["sh_mid_fga"], fg3a)
        tb["long_fgm"] = np.where(use_sh, tb["sh_three_fgm"] + tb["sh_mid_fgm"], fg3m)
        tb["zone_source"] = np.where(use_sh, "shots_parquet", "box_2pa_proxy")
    else:
        # Honest proxy when shots parquet is absent: rim misses proxied by 2PA - 2PM
        tb["rim_fga"] = fg2a
        tb["rim_fgm"] = fg2m
        tb["three_fga"] = fg3a
        tb["three_fgm"] = fg3m
        tb["long_fga"] = fg3a
        tb["long_fgm"] = fg3m
        tb["zone_source"] = "box_2pa_proxy"

    # Strictly prior-only rolling team features (window = 15 games)
    tb = tb.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
    w_tm = 15
    tb["tm_exp_poss"] = _prior_mean(tb, "team", "poss", w_tm).fillna(DEFAULT_POSS)
    poss_sum = _prior_sum(tb, "team", "poss", w_tm).fillna(0.0)

    for prefix, prior_rate, prior_fgp in (
        ("rim", PRIOR_RIM_FGA_RATE, PRIOR_RIM_FGP),
        ("three", PRIOR_THREE_FGA_RATE, PRIOR_THREE_FGP),
        ("long", PRIOR_LONG_FGA_RATE, PRIOR_LONG_FGP),
    ):
        fga_s = _prior_sum(tb, "team", f"{prefix}_fga", w_tm).fillna(0.0)
        fgm_s = _prior_sum(tb, "team", f"{prefix}_fgm", w_tm).fillna(0.0)
        fga_rate = (fga_s + K_ZONE_FGA * prior_rate) / (poss_sum + K_ZONE_FGA)
        fg_pct = (fgm_s + K_ZONE_FGM * prior_fgp) / (fga_s + K_ZONE_FGM)
        tb[f"own_{prefix}_miss_rate"] = (fga_rate * (1.0 - fg_pct)).clip(lower=0.01)

    oreb_s = _prior_sum(tb, "team", "t_oreb", w_tm).fillna(0.0)
    own_miss_s = _prior_sum(tb, "team", "own_misses", w_tm).fillna(0.0)
    tb["tm_oreb_rate"] = ((oreb_s + K_TEAM_REB * LG_OREB_RATE) / (own_miss_s + K_TEAM_REB)).clip(0.05, 0.55)

    dreb_s = _prior_sum(tb, "team", "t_dreb", w_tm).fillna(0.0)
    opp_miss_s = _prior_sum(tb, "team", "opp_misses", w_tm).fillna(0.0)
    tb["tm_dreb_rate"] = ((dreb_s + K_TEAM_REB * LG_DREB_RATE) / (opp_miss_s + K_TEAM_REB)).clip(0.45, 0.92)

    # Merge opponent's prior shooting & rebounding rates
    opp_rates = tb[[
        "game_id", "team", "tm_exp_poss",
        "own_rim_miss_rate", "own_three_miss_rate", "own_long_miss_rate",
        "tm_oreb_rate", "tm_dreb_rate",
    ]].rename(columns={
        "team": "opp",
        "tm_exp_poss": "opp_exp_poss",
        "own_rim_miss_rate": "opp_rim_miss_rate",
        "own_three_miss_rate": "opp_three_miss_rate",
        "own_long_miss_rate": "opp_long_miss_rate",
        "tm_oreb_rate": "opp_oreb_rate",
        "tm_dreb_rate": "opp_dreb_rate",
    })
    tb = tb.merge(opp_rates, on=["game_id", "opp"], how="left")
    tb["opp_exp_poss"] = tb["opp_exp_poss"].fillna(DEFAULT_POSS)
    tb["opp_rim_miss_rate"] = tb["opp_rim_miss_rate"].fillna(PRIOR_RIM_FGA_RATE * (1.0 - PRIOR_RIM_FGP))
    tb["opp_three_miss_rate"] = tb["opp_three_miss_rate"].fillna(PRIOR_THREE_FGA_RATE * (1.0 - PRIOR_THREE_FGP))
    tb["opp_long_miss_rate"] = tb["opp_long_miss_rate"].fillna(PRIOR_LONG_FGA_RATE * (1.0 - PRIOR_LONG_FGP))
    tb["opp_oreb_rate"] = tb["opp_oreb_rate"].fillna(LG_OREB_RATE)
    tb["opp_dreb_rate"] = tb["opp_dreb_rate"].fillna(LG_DREB_RATE)

    tb["exp_poss"] = 0.5 * (tb["tm_exp_poss"] + tb["opp_exp_poss"])
    tb["rim_miss_rate"] = tb["opp_rim_miss_rate"]
    tb["three_miss_rate"] = tb["opp_three_miss_rate"]
    tb["emsa_opp_rim"] = tb["exp_poss"] * tb["opp_rim_miss_rate"]
    tb["emsa_opp_long"] = tb["exp_poss"] * tb["opp_long_miss_rate"]
    tb["emsa_own_rim"] = tb["exp_poss"] * tb["own_rim_miss_rate"]
    tb["emsa_own_long"] = tb["exp_poss"] * tb["own_long_miss_rate"]
    tb["emsa_total"] = tb["emsa_opp_rim"] + tb["emsa_opp_long"] + tb["emsa_own_rim"] + tb["emsa_own_long"]

    # Join team game context onto player rows for strictly prior-only player rolling
    team_join_cols = [
        "game_id", "team", "opp", "is_home", "zone_source", "t_oreb", "t_dreb", "team_court_min",
        "exp_poss", "rim_miss_rate", "three_miss_rate",
        "emsa_opp_rim", "emsa_opp_long", "emsa_own_rim", "emsa_own_long", "emsa_total",
        "opp_oreb_rate", "opp_dreb_rate",
    ]
    tb_sub = tb[team_join_cols].drop_duplicates(["game_id", "team"])
    if "is_home" in pb.columns:
        pb = pb.drop(columns=["is_home"])
    if "opp" in pb.columns:
        pb = pb.drop(columns=["opp"])
    p = pb.merge(tb_sub, on=["game_id", "team"], how="inner")

    p = p.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)

    # Prior position archetype (previous played game's position)
    pos_prev = p.groupby("player_id", sort=False)["position"].shift(1) if "position" in p.columns else pd.Series(np.nan, index=p.index)
    pos_grp = pos_prev.map(C.POS_GROUP)
    prior_oreb_p = pos_grp.map(POS_OREB_PRIOR).fillna(DEFAULT_OREB_PRIOR).to_numpy(dtype=float)
    prior_dreb_p = pos_grp.map(POS_DREB_PRIOR).fillna(DEFAULT_DREB_PRIOR).to_numpy(dtype=float)

    # Expected minutes distribution (prior-only)
    min_ewm = _prior_ewm(p, "player_id", "_min", 8.0)
    min_l10 = _prior_mean(p, "player_id", "_min", 10)
    p["mu_min"] = min_ewm.fillna(min_l10).fillna(DEFAULT_MU_MIN).clip(lower=0.0, upper=48.0)
    p["sd_min"] = _prior_std(p, "player_id", "_min", 10).fillna(DEFAULT_SD_MIN).clip(lower=0.0, upper=15.0)

    # On-floor rebound opportunities per player-game (used strictly via shift(1))
    on_floor_frac = (p["_min"] / p["team_court_min"]).clip(lower=0.0, upper=1.0)
    p["_oreb_opp_floor"] = p["t_oreb"] * on_floor_frac
    p["_dreb_opp_floor"] = p["t_dreb"] * on_floor_frac

    w_pl = 20
    p_oreb_num = _prior_sum(p, "player_id", "rebounds_offensive", w_pl).fillna(0.0).to_numpy(dtype=float)
    p_oreb_den = _prior_sum(p, "player_id", "_oreb_opp_floor", w_pl).fillna(0.0).to_numpy(dtype=float)
    p_dreb_num = _prior_sum(p, "player_id", "rebounds_defensive", w_pl).fillna(0.0).to_numpy(dtype=float)
    p_dreb_den = _prior_sum(p, "player_id", "_dreb_opp_floor", w_pl).fillna(0.0).to_numpy(dtype=float)

    p["raw_oreb_share"] = np.clip(
        (p_oreb_num + K_SHARE_OREB * prior_oreb_p) / (p_oreb_den + K_SHARE_OREB),
        0.01,
        0.95,
    )
    p["raw_dreb_share"] = np.clip(
        (p_dreb_num + K_SHARE_DREB * prior_dreb_p) / (p_dreb_den + K_SHARE_DREB),
        0.01,
        0.95,
    )

    # Renormalize shares over the expected on-floor 5-man unit per (game_id, team)
    p["renorm_oreb_share"], unit_o = renormalize_unit_shares(p, "raw_oreb_share", unit_size=5)
    p["renorm_dreb_share"], _ = renormalize_unit_shares(p, "raw_dreb_share", unit_size=5)
    p["is_expected_unit"] = unit_o

    p["is_home"] = p["is_home"].astype(int)

    keep_cols = [
        "game_id", "player_id", "date", "team", "opp", "is_home",
        "zone_source", "exp_poss", "rim_miss_rate", "three_miss_rate",
        "emsa_total", "is_expected_unit", "raw_oreb_share", "raw_dreb_share",
    ] + list(REQUIRED_FEAT_COLS)
    if "name" in p.columns:
        keep_cols.insert(2, "name")

    out = p[keep_cols].copy()
    drop_leak = [c for c in out.columns if c.lower() in _BANNED_OUTCOME_OR_MARKET_COLS]
    if drop_leak:
        out = out.drop(columns=drop_leak)

    F.assert_feature_schema(out)
    return out.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
