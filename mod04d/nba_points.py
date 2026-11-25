"""NBA Points shadow scoring candidates (MOD-04 Cluster d).

Structural decomposition:
    mu_struct = expected_minutes * pace_factor * (fga_per_min + 0.44 * fta_per_min) * expected_efficiency

Provides:
- PointsModel: Two-parameter Distributional NegBin GBDT (mean + OOF dispersion)
- SkewNormalPointsModel: Discretized Skew-Normal around (mu, var, OOF shape)
- ComponentConvolvedPointsModel: Discrete convolution of FT + 2*2PM + 3*3PM
- compare_points_distributions: OOF full + tail LL comparison with +0.0020 decision rule
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from mod04d._xgb import (
    chrono_split,
    fit_mean_and_dispersion,
    predict_mu_alpha_from_art,
)
from mod04d.distributions import (
    ALPHA_MAX,
    ALPHA_MIN,
    MU_MIN,
    convolve_pmfs,
    negbin_pmf,
    pmf_log_likelihood,
    pmf_p_over,
    scale_pmf,
    skewnorm_params_from_moments,
    skewnorm_pmf,
)
from mod04d.features import NBA_FEATURE_COLS, assert_no_leak_columns

DEFAULT_K_MAX = 80
ENGINE_NBA_POINTS = "mod04_nba_points_shadow_v1"
TAIL_LL_GAIN_THRESHOLD = 0.0020

BASE_POINTS_FEATURES: tuple[str, ...] = tuple(
    c for c in NBA_FEATURE_COLS if c not in ("pace_proxy",)
)


def _num_series(df: pd.DataFrame, candidates: Sequence[str], default: float) -> np.ndarray:
    for c in candidates:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            if np.any(np.isfinite(s)):
                med = float(np.nanmedian(s))
                return np.where(np.isfinite(s), s, med if np.isfinite(med) else default)
    return np.full(len(df), default, dtype=float)


def compute_structural_mu(df: pd.DataFrame) -> np.ndarray:
    """Structural points decomposition: E[min] * pace_factor * (fga/min + 0.44*fta/min) * zone_eff."""
    exp_min = _num_series(df, ("min_ewm", "min_l15", "min_l5", "expected_minutes"), 24.0)
    pace_factor = np.clip(_num_series(df, ("pace_factor",), 1.0), 0.75, 1.30)
    fga_pm = _num_series(df, ("fga_per_min_ewm", "fga_per_min_l15", "fga_per_min_l5"), 0.42)
    fta_pm = _num_series(df, ("fta_per_min_ewm", "fta_per_min_l15", "fta_per_min_l5"), 0.11)
    fg2_pct = _num_series(df, ("fg2pct_eb",), 0.53)
    fg3_pct = _num_series(df, ("fg3pct_eb",), 0.36)
    ft_pct = _num_series(df, ("ftpct_eb",), 0.78)
    fg3a_pm = _num_series(df, ("fg3a_per_min_ewm", "fg3a_per_min_l15", "fg3a_per_min_l5"), 0.14)

    # Approximate points per TSA = (2*fg2m + 3*fg3m + ftm) / (fga + 0.44*fta)
    fg2a_pm = np.maximum(fga_pm - fg3a_pm, 0.0)
    tsa_pm = np.maximum(fga_pm + 0.44 * fta_pm, 1e-4)
    pts_pm = 2.0 * fg2a_pm * fg2_pct + 3.0 * fg3a_pm * fg3_pct + fta_pm * ft_pct
    zone_eff = np.where(
        "zone_efg_eb" in df.columns,
        np.clip(0.5 * (pts_pm / tsa_pm) + 0.5 * (2.0 * _num_series(df, ("zone_efg_eb",), 0.54)), 0.7, 1.5),
        np.clip(pts_pm / tsa_pm, 0.7, 1.5),
    )
    mu_struct = np.maximum(exp_min * pace_factor * (fga_pm + 0.44 * fta_pm) * zone_eff, 0.1)
    return mu_struct


def _prepare_points_frame(df: pd.DataFrame, extra_features: Sequence[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    assert_no_leak_columns(df)
    work = df.copy()
    work["mu_struct"] = compute_structural_mu(work)
    if extra_features is not None:
        feats = list(extra_features)
    else:
        feats = [c for c in BASE_POINTS_FEATURES if c in work.columns]
        if "mu_struct" not in feats:
            feats.append("mu_struct")
    return work, feats


@dataclass
class PointsModel:
    """Two-parameter Distributional NegBin GBDT for NBA points."""

    art: dict
    k_max: int = DEFAULT_K_MAX
    engine: str = ENGINE_NBA_POINTS
    meta: dict = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        return list(self.art["features"])

    def predict_mu_alpha(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        work, _ = _prepare_points_frame(X, extra_features=self.art["features"])
        return predict_mu_alpha_from_art(self.art, work)

    def pmf(self, X: pd.DataFrame, k_max: int | None = None) -> np.ndarray:
        km = int(k_max or self.k_max)
        mu, alpha = self.predict_mu_alpha(X)
        return negbin_pmf(mu, alpha, k_max=km)

    def p_over(self, X: pd.DataFrame, lines, k_max: int | None = None) -> np.ndarray:
        return pmf_p_over(self.pmf(X, k_max=k_max), lines)


def fit(
    points_frame: pd.DataFrame,
    target_col: str = "points",
    features: Sequence[str] | None = None,
    *,
    k_max: int = DEFAULT_K_MAX,
    max_rounds: int = 200,
    disp_rounds: int = 60,
    params: dict | None = None,
    disp_params: dict | None = None,
) -> PointsModel:
    """Fit a distributional NegBin PointsModel on `points_frame`."""
    work, feats = _prepare_points_frame(points_frame, extra_features=features)
    art = fit_mean_and_dispersion(
        work,
        target_col,
        feats,
        max_rounds=max_rounds,
        disp_rounds=disp_rounds,
        params=params,
        disp_params=disp_params,
    )
    meta = {
        "engine": ENGINE_NBA_POINTS,
        "family": "negbin",
        "global_alpha": art["global_alpha"],
        "val_ll_disp": art["val_ll_disp"],
        "val_ll_fixed": art["val_ll_fixed"],
        "n_train": art["n_train"],
        "n_val": art["n_val"],
    }
    return PointsModel(art=art, k_max=k_max, meta=meta)


@dataclass
class SkewNormalPointsModel:
    """Discretized Skew-Normal points model with OOF-tuned shape parameter."""

    base_model: PointsModel
    shape: float
    k_max: int = DEFAULT_K_MAX
    engine: str = f"{ENGINE_NBA_POINTS}_skewnorm"
    meta: dict = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        return self.base_model.features

    def predict_mu_alpha(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        return self.base_model.predict_mu_alpha(X)

    def predict_params(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mu, alpha = self.base_model.predict_mu_alpha(X)
        var = np.maximum(mu + alpha * (mu ** 2), 1.0)
        loc, scale = skewnorm_params_from_moments(mu, var, self.shape)
        return loc, scale, np.full(len(mu), self.shape, dtype=float)

    def pmf(self, X: pd.DataFrame, k_max: int | None = None) -> np.ndarray:
        km = int(k_max or self.k_max)
        loc, scale, shape = self.predict_params(X)
        return skewnorm_pmf(loc, scale, shape, k_max=km)

    def p_over(self, X: pd.DataFrame, lines, k_max: int | None = None) -> np.ndarray:
        return pmf_p_over(self.pmf(X, k_max=k_max), lines)

    @classmethod
    def fit(
        cls,
        points_frame: pd.DataFrame,
        target_col: str = "points",
        features: Sequence[str] | None = None,
        *,
        k_max: int = DEFAULT_K_MAX,
        max_rounds: int = 200,
        disp_rounds: int = 60,
    ) -> "SkewNormalPointsModel":
        work, feats = _prepare_points_frame(points_frame, extra_features=features)
        tr, va = chrono_split(work, val_frac=0.25)
        oof_base = fit(
            tr,
            target_col=target_col,
            features=feats,
            k_max=k_max,
            max_rounds=max_rounds,
            disp_rounds=disp_rounds,
        )
        mu_va, alpha_va = oof_base.predict_mu_alpha(va)
        var_va = np.maximum(mu_va + alpha_va * (mu_va ** 2), 1.0)
        y_va = pd.to_numeric(va[target_col], errors="coerce").to_numpy(dtype=int)

        shape_grid = (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 4.0)
        best_shape = 0.0
        best_ll = -np.inf
        for sh in shape_grid:
            loc, scale = skewnorm_params_from_moments(mu_va, var_va, sh)
            pmf_va = skewnorm_pmf(loc, scale, sh, k_max=k_max)
            ll = pmf_log_likelihood(y_va, pmf_va)
            if ll > best_ll:
                best_ll = ll
                best_shape = float(sh)

        full_base = fit(
            work,
            target_col=target_col,
            features=feats,
            k_max=k_max,
            max_rounds=max_rounds,
            disp_rounds=disp_rounds,
        )
        return cls(
            base_model=full_base,
            shape=best_shape,
            k_max=k_max,
            meta={"shape": best_shape, "val_skewnorm_ll": best_ll},
        )


@dataclass
class ComponentConvolvedPointsModel:
    """Points PMF via exact discrete convolution of FT + 2*(2PM) + 3*(3PM)."""

    base_model: PointsModel
    alpha_ft: float = 0.05
    alpha_2pm: float = 0.05
    alpha_3pm: float = 0.05
    k_max: int = DEFAULT_K_MAX
    engine: str = f"{ENGINE_NBA_POINTS}_conv"

    @property
    def features(self) -> list[str]:
        return self.base_model.features

    def predict_mu_alpha(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        return self.base_model.predict_mu_alpha(X)

    def _component_means(self, X: pd.DataFrame, mu_total: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        exp_min = _num_series(X, ("min_ewm", "min_l15", "min_l5"), 24.0)
        fga_pm = _num_series(X, ("fga_per_min_ewm", "fga_per_min_l15"), 0.42)
        fta_pm = _num_series(X, ("fta_per_min_ewm", "fta_per_min_l15"), 0.11)
        fg3a_pm = _num_series(X, ("fg3a_per_min_ewm", "fg3a_per_min_l15"), 0.14)
        fg2a_pm = np.maximum(fga_pm - fg3a_pm, 0.01)

        ft_rate = exp_min * fta_pm * _num_series(X, ("ftpct_eb",), 0.78)
        m2_rate = exp_min * fg2a_pm * _num_series(X, ("fg2pct_eb",), 0.53)
        m3_rate = exp_min * fg3a_pm * _num_series(X, ("fg3pct_eb",), 0.36)

        pts_raw = np.maximum(ft_rate + 2.0 * m2_rate + 3.0 * m3_rate, 1e-3)
        scale = mu_total / pts_raw
        return np.maximum(ft_rate * scale, MU_MIN), np.maximum(m2_rate * scale, MU_MIN), np.maximum(m3_rate * scale, MU_MIN)

    def pmf(self, X: pd.DataFrame, k_max: int | None = None) -> np.ndarray:
        km = int(k_max or self.k_max)
        mu_total, alpha_total = self.base_model.predict_mu_alpha(X)
        mu_ft, mu_2, mu_3 = self._component_means(X, mu_total)
        # Scale component dispersions gently with predicted total dispersion
        a_ft = np.clip(0.5 * alpha_total + 0.5 * self.alpha_ft, ALPHA_MIN, ALPHA_MAX)
        a_2 = np.clip(0.5 * alpha_total + 0.5 * self.alpha_2pm, ALPHA_MIN, ALPHA_MAX)
        a_3 = np.clip(0.5 * alpha_total + 0.5 * self.alpha_3pm, ALPHA_MIN, ALPHA_MAX)

        pmf_ft = negbin_pmf(mu_ft, a_ft, k_max=km)
        pmf_2 = scale_pmf(negbin_pmf(mu_2, a_2, k_max=km // 2 + 1), 2, k_max=km)
        pmf_3 = scale_pmf(negbin_pmf(mu_3, a_3, k_max=km // 3 + 1), 3, k_max=km)
        return convolve_pmfs(convolve_pmfs(pmf_ft, pmf_2, k_max=km), pmf_3, k_max=km)

    def p_over(self, X: pd.DataFrame, lines, k_max: int | None = None) -> np.ndarray:
        return pmf_p_over(self.pmf(X, k_max=k_max), lines)


def compare_points_distributions(
    frame_train: pd.DataFrame,
    frame_oof: pd.DataFrame,
    *,
    target_col: str = "points",
    k_max: int = DEFAULT_K_MAX,
    max_rounds: int = 150,
    disp_rounds: int = 50,
) -> dict:
    """Compare NegBin vs Skew-Normal (and component convolution) on OOF full + tail log-likelihood.

    Tail rows = rows with line >= 75th percentile (if `line` col supplied in frame_oof) OR y in top decile.
    Decision rule: `skewnormal` wins iff `skewnormal_tail_ll - negbin_tail_ll >= +0.0020`.
    """
    nb_model = fit(
        frame_train, target_col=target_col, k_max=k_max, max_rounds=max_rounds, disp_rounds=disp_rounds
    )
    sn_model = SkewNormalPointsModel.fit(
        frame_train, target_col=target_col, k_max=k_max, max_rounds=max_rounds, disp_rounds=disp_rounds
    )
    conv_model = ComponentConvolvedPointsModel(base_model=nb_model, k_max=k_max)

    # Separate optional `line` column from feature frame passed to model (U1 gate)
    oof_lines = frame_oof["line"].to_numpy(dtype=float) if "line" in frame_oof.columns else None
    oof_X = frame_oof.drop(columns=["line"], errors="ignore")
    y_oof = pd.to_numeric(frame_oof[target_col], errors="coerce").to_numpy(dtype=int)

    pmf_nb = nb_model.pmf(oof_X, k_max=k_max)
    pmf_sn = sn_model.pmf(oof_X, k_max=k_max)
    pmf_conv = conv_model.pmf(oof_X, k_max=k_max)

    negbin_ll = pmf_log_likelihood(y_oof, pmf_nb)
    skewnormal_ll = pmf_log_likelihood(y_oof, pmf_sn)
    component_conv_ll = pmf_log_likelihood(y_oof, pmf_conv)

    y_q90 = float(np.quantile(y_oof, 0.90))
    tail_mask = y_oof >= y_q90
    if oof_lines is not None and np.any(np.isfinite(oof_lines)):
        line_q75 = float(np.nanquantile(oof_lines, 0.75))
        tail_mask = tail_mask | (oof_lines >= line_q75)
    if not np.any(tail_mask):
        tail_mask = np.ones(len(y_oof), dtype=bool)

    negbin_tail_ll = pmf_log_likelihood(y_oof[tail_mask], pmf_nb[tail_mask])
    skewnormal_tail_ll = pmf_log_likelihood(y_oof[tail_mask], pmf_sn[tail_mask])
    tail_ll_gain = float(skewnormal_tail_ll - negbin_tail_ll)
    decision = "skewnormal" if tail_ll_gain >= TAIL_LL_GAIN_THRESHOLD else "negbin"

    return {
        "negbin_ll": float(negbin_ll),
        "skewnormal_ll": float(skewnormal_ll),
        "negbin_tail_ll": float(negbin_tail_ll),
        "skewnormal_tail_ll": float(skewnormal_tail_ll),
        "tail_ll_gain": tail_ll_gain,
        "component_conv_ll": float(component_conv_ll),
        "decision": decision,
        "n_oof": int(len(y_oof)),
        "n_tail": int(np.sum(tail_mask)),
    }
