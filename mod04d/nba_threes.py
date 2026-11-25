"""NBA Threes shadow scoring candidate (MOD-04 Cluster d).

Attempt-driven decomposition:
    mu_att = E[minutes] * fg3a_per_min (structural or GBDT on fg3a)
    p3     = EB-shrunk 3P% toward league/position prior

Compares direct NegBin on makes vs Beta-Binomial over NegBin attempts on OOF log-likelihood,
selects the winning family, and records a `reason` string in model.meta.
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
    betabinom_negbin_pmf,
    negbin_pmf,
    pmf_log_likelihood,
    pmf_p_over,
)
from mod04d.features import LEAGUE_FG3_PCT_DEFAULT, NBA_FEATURE_COLS, assert_no_leak_columns

DEFAULT_K_MAX = 16
ENGINE_NBA_THREES = "mod04_nba_threes_shadow_v1"

THREES_FEATURES: tuple[str, ...] = tuple(
    c
    for c in NBA_FEATURE_COLS
    if c in (
        "min_l5",
        "min_l15",
        "min_ewm",
        "min_std_l10",
        "fg3a_per_min_l5",
        "fg3a_per_min_l15",
        "fg3a_per_min_ewm",
        "fg3pct_eb",
        "pace_factor",
        "rest_days",
        "starter_rate",
        "n_prior_games",
        "env_spread",
        "env_total",
        "env_implied_team_total",
    )
)


def _num(df: pd.DataFrame, candidates: Sequence[str], default: float) -> np.ndarray:
    for c in candidates:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            if np.any(np.isfinite(s)):
                med = float(np.nanmedian(s))
                return np.where(np.isfinite(s), s, med if np.isfinite(med) else default)
    return np.full(len(df), default, dtype=float)


def compute_structural_attempts_mu(df: pd.DataFrame) -> np.ndarray:
    exp_min = _num(df, ("min_ewm", "min_l15", "min_l5"), 24.0)
    fg3a_pm = _num(df, ("fg3a_per_min_ewm", "fg3a_per_min_l15", "fg3a_per_min_l5"), 0.14)
    pace_factor = np.clip(_num(df, ("pace_factor",), 1.0), 0.75, 1.30)
    return np.maximum(exp_min * pace_factor * fg3a_pm, 0.05)


def compute_shrunk_p3(df: pd.DataFrame, prior_p3: float = LEAGUE_FG3_PCT_DEFAULT) -> np.ndarray:
    p3 = _num(df, ("fg3pct_eb",), prior_p3)
    return np.clip(p3, 0.05, 0.65)


@dataclass
class ThreesModel:
    """Attempt-driven NBA threes distributional model."""

    makes_art: dict
    att_art: dict | None
    family: str
    conc: float = 25.0
    k_max: int = DEFAULT_K_MAX
    engine: str = ENGINE_NBA_THREES
    meta: dict = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        return list(self.makes_art["features"])

    def predict_mu_alpha(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        work = X.copy()
        work["mu_att_struct"] = compute_structural_attempts_mu(work)
        work["mu_makes_struct"] = work["mu_att_struct"] * compute_shrunk_p3(work)
        return predict_mu_alpha_from_art(self.makes_art, work)

    def predict_attempts_mu_alpha(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        work = X.copy()
        work["mu_att_struct"] = compute_structural_attempts_mu(work)
        if self.att_art is not None:
            return predict_mu_alpha_from_art(self.att_art, work)
        _, alpha_m = self.predict_mu_alpha(X)
        return work["mu_att_struct"].to_numpy(dtype=float), alpha_m

    def pmf(self, X: pd.DataFrame, k_max: int | None = None) -> np.ndarray:
        km = int(k_max or self.k_max)
        if self.family == "betabinom_negbin":
            mu_att, alpha_att = self.predict_attempts_mu_alpha(X)
            p3 = compute_shrunk_p3(X)
            return betabinom_negbin_pmf(
                mu_att, alpha_att, p3, conc=self.conc, k_max=km, k_att_max=max(40, km * 3)
            )
        mu, alpha = self.predict_mu_alpha(X)
        return negbin_pmf(mu, alpha, k_max=km)

    def p_over(self, X: pd.DataFrame, lines, k_max: int | None = None) -> np.ndarray:
        return pmf_p_over(self.pmf(X, k_max=k_max), lines)


def fit_threes(
    threes_frame: pd.DataFrame,
    target_col: str = "fg3m",
    attempts_col: str = "fg3a",
    features: Sequence[str] | None = None,
    *,
    k_max: int = DEFAULT_K_MAX,
    max_rounds: int = 150,
    disp_rounds: int = 50,
    conc_grid: Sequence[float] = (15.0, 25.0, 45.0, 80.0),
) -> ThreesModel:
    """Fit both direct NegBin makes and Beta-Binomial-over-NegBin-attempts, selecting by OOF LL."""
    assert_no_leak_columns(threes_frame)
    work = threes_frame.copy()
    if target_col not in work.columns and "three_points_made" in work.columns:
        target_col = "three_points_made"
    if attempts_col not in work.columns and "three_points_attempted" in work.columns:
        attempts_col = "three_points_attempted"

    work["mu_att_struct"] = compute_structural_attempts_mu(work)
    work["mu_makes_struct"] = work["mu_att_struct"] * compute_shrunk_p3(work)

    base_feats = [c for c in (features or THREES_FEATURES) if c in work.columns]
    att_feats = [*base_feats, "mu_att_struct"]
    makes_feats = [*base_feats, "mu_att_struct", "mu_makes_struct"]

    tr, va = chrono_split(work, val_frac=0.25)
    y_va = pd.to_numeric(va[target_col], errors="coerce").to_numpy(dtype=int)

    # Fit OOF models on `tr` to compare families cleanly on `va`
    oof_makes_art = fit_mean_and_dispersion(
        tr, target_col, makes_feats, max_rounds=max_rounds, disp_rounds=disp_rounds
    )
    mu_nb_va, al_nb_va = predict_mu_alpha_from_art(oof_makes_art, va)
    pmf_nb_va = negbin_pmf(mu_nb_va, al_nb_va, k_max=k_max)
    ll_negbin = pmf_log_likelihood(y_va, pmf_nb_va)

    has_att = attempts_col in tr.columns and tr[attempts_col].notna().sum() >= 20
    if has_att:
        oof_att_art = fit_mean_and_dispersion(
            tr, attempts_col, att_feats, max_rounds=max_rounds, disp_rounds=disp_rounds
        )
        mu_att_va, al_att_va = predict_mu_alpha_from_art(oof_att_art, va)
    else:
        oof_att_art = None
        mu_att_va = va["mu_att_struct"].to_numpy(dtype=float)
        al_att_va = np.clip(al_nb_va, ALPHA_MIN, ALPHA_MAX)

    p3_va = compute_shrunk_p3(va)
    best_conc = float(conc_grid[0])
    ll_bb = -np.inf
    for conc in conc_grid:
        pmf_bb_va = betabinom_negbin_pmf(
            mu_att_va, al_att_va, p3_va, conc=conc, k_max=k_max, k_att_max=max(40, k_max * 3)
        )
        ll_c = pmf_log_likelihood(y_va, pmf_bb_va)
        if ll_c > ll_bb:
            ll_bb = ll_c
            best_conc = float(conc)

    if ll_bb > ll_negbin:
        chosen = "betabinom_negbin"
        reason = (
            f"betabinom_negbin selected over negbin on OOF LL: "
            f"{ll_bb:.5f} vs {ll_negbin:.5f} (gain {ll_bb - ll_negbin:+.5f}, conc={best_conc:.1f})"
        )
    else:
        chosen = "negbin"
        reason = (
            f"negbin selected over betabinom_negbin on OOF LL: "
            f"{ll_negbin:.5f} vs {ll_bb:.5f} (gain {ll_negbin - ll_bb:+.5f})"
        )

    # Refit final boosters on full training frame
    full_makes_art = fit_mean_and_dispersion(
        work, target_col, makes_feats, max_rounds=max_rounds, disp_rounds=disp_rounds
    )
    full_att_art = (
        fit_mean_and_dispersion(
            work, attempts_col, att_feats, max_rounds=max_rounds, disp_rounds=disp_rounds
        )
        if has_att
        else None
    )

    meta = {
        "engine": ENGINE_NBA_THREES,
        "family": chosen,
        "reason": reason,
        "oof_ll_negbin": float(ll_negbin),
        "oof_ll_betabinom_negbin": float(ll_bb),
        "conc": best_conc,
        "has_attempts_model": bool(has_att),
        "n_train": int(len(work)),
        "n_val": int(len(va)),
    }
    return ThreesModel(
        makes_art=full_makes_art,
        att_art=full_att_art,
        family=chosen,
        conc=best_conc,
        k_max=k_max,
        meta=meta,
    )
