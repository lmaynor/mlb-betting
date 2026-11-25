"""B2 saves shadow engine core (`nhl_saves_sota_v2`).

PARALLEL SHADOW ONLY: this module never writes to the live paper track, never touches serving
bundles or boards, and never invokes `promotion_check`. It evaluates covariate-dependent NegBin
dispersion (`AlphaXModel`), full discrete NegBin PMF pricing, a damped residual regime monitor
(`DampedRegimeMonitor`), trailing calibration bias alerts (`bias_alert`), and walk-forward
ablation comparison (`walk_forward_compare`) between `SAVES_FEATURES_NOXG` and the compact
`XG_ABLATION_FEATURES` extension.

Tail Brier note: because market lines are banned from training frames, `walk_forward_compare`
defines the high-workload tail event at the fixed threshold `TAIL_SAVES_THRESHOLD = 35` saves
(`Y >= 35`, i.e., `line = 34.5`), completely free of market lines.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from nhl.props import countmodel as CM
from nhl.props import xgfeat as X

ENGINE_LABEL = "nhl_saves_sota_v2"

# Compact shot-quality / rebound xG subset of SAVES_FEATURES (strictly the _SAVES_XG_ONLY members).
XG_ABLATION_FEATURES: list[str] = [f for f in X.SAVES_FEATURES if f in X._SAVES_XG_ONLY]

ALPHA_MIN = 0.01
ALPHA_MAX = 1.0
DISPERSION_FLOOR = 0.005
DISPERSION_CLIP = (-0.5, 3.0)
MONITOR_OFFSET_MAX = 0.05
MONITOR_DAMP = 0.5
BIAS_ALERT_THRESHOLD = 0.035
TAIL_SAVES_THRESHOLD = 35
TAIL_LINE_PROXY = TAIL_SAVES_THRESHOLD - 0.5


def pmf(mu: float, alpha: float, kmax: int) -> np.ndarray:
    """Full discrete NegBin PMF over `0..kmax` (`alpha <= 0` -> Poisson), normalized to sum 1."""
    if kmax < 0:
        raise ValueError(f"kmax must be >= 0, got {kmax}")
    k = np.arange(0, int(kmax) + 1, dtype=int)
    mu_val = float(mu)
    alpha_val = float(alpha) if alpha is not None else 0.0
    if not math.isfinite(mu_val) or mu_val <= 0.0:
        probs = np.zeros(len(k), dtype=float)
        probs[0] = 1.0
        return probs
    if alpha_val <= 0.0:
        probs = poisson.pmf(k, mu_val)
    else:
        n = 1.0 / alpha_val
        p = n / (n + mu_val)
        probs = nbinom.pmf(k, n, p)
    probs = np.where(np.isfinite(probs) & (probs >= 0.0), probs, 0.0)
    total = float(probs.sum())
    if total <= 0.0:
        probs = np.zeros(len(k), dtype=float)
        probs[-1] = 1.0
        return probs
    return probs / total


def p_over(line: float, mu: float, alpha: float, kmax: int | None = None) -> float:
    """P(Y > line) via the discrete NegBin / Poisson PMF over `0..kmax`."""
    if mu is None or (isinstance(mu, float) and math.isnan(mu)) or float(mu) <= 0.0:
        return 0.5
    mu_val = float(mu)
    alpha_val = float(alpha) if alpha is not None else 0.0
    k_floor = int(math.floor(float(line)))
    if k_floor < 0:
        return 1.0
    if kmax is None:
        sd = math.sqrt(mu_val + max(alpha_val, 0.0) * (mu_val ** 2))
        kmax = max(250, k_floor + 100, int(math.ceil(mu_val + 15.0 * sd + 20.0)))
    if k_floor >= kmax:
        return 0.0
    probs = pmf(mu_val, alpha_val, kmax)
    return float(probs[k_floor + 1:].sum())


class AlphaXModel:
    """Covariate-dependent NegBin dispersion `alpha(x)` fit on Pearson-residual method-of-moments."""

    def __init__(
        self,
        features: Iterable[str] | None = None,
        params: dict[str, Any] | None = None,
        seed: int = 7,
    ) -> None:
        self.features: list[str] = list(features) if features is not None else []
        self.seed = int(seed)
        self.params: dict[str, Any] = {
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "max_depth": 2,
            "eta": 0.05,
            "min_child_weight": 25,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "lambda": 10.0,
            "seed": self.seed,
            "nthread": 4,
            **(params or {}),
        }
        self.num_boost_round = int(self.params.pop("num_boost_round", 80))
        self.booster: Any = None
        self.feature_means: dict[str, float] = {}
        self.fallback_alpha: float = 0.05

    def _prepare_X(self, df: pd.DataFrame) -> pd.DataFrame:
        X_df = pd.DataFrame(index=df.index)
        for f in self.features:
            if f in df.columns:
                col = pd.to_numeric(df[f], errors="coerce")
            else:
                col = pd.Series(np.nan, index=df.index, dtype=float)
            mean_val = self.feature_means.get(f, 0.0)
            if not math.isfinite(mean_val):
                mean_val = 0.0
            X_df[f] = col.fillna(mean_val).astype(float)
        return X_df

    def fit(self, mu_artifact: dict, train_df: pd.DataFrame, target: str = "saves") -> "AlphaXModel":
        """Fit log(alpha_i) regressor from a fitted countmodel artifact and training rows."""
        import xgboost as xgb

        if not self.features:
            self.features = list(mu_artifact["meta"]["features"])
        self.fallback_alpha = float(
            np.clip(mu_artifact["meta"].get("nb_alpha", 0.05), ALPHA_MIN, ALPHA_MAX)
        )

        df = train_df[train_df[target].notna()].copy()
        if df.empty:
            raise ValueError(f"AlphaXModel.fit received 0 non-null {target!r} rows")

        self.feature_means = {}
        for f in self.features:
            if f in df.columns:
                m = float(pd.to_numeric(df[f], errors="coerce").mean())
                self.feature_means[f] = m if math.isfinite(m) else 0.0
            else:
                self.feature_means[f] = 0.0

        mu = np.maximum(CM.predict_mu(mu_artifact, df), 1e-3)
        y = df[target].to_numpy(dtype=float)

        d_i = ((y - mu) ** 2 - mu) / (mu ** 2)
        d_i = np.clip(d_i, DISPERSION_CLIP[0], DISPERSION_CLIP[1])
        log_d = np.log(np.maximum(d_i, DISPERSION_FLOOR))

        weights = np.maximum(mu, 1.0)
        weights = weights / float(weights.mean())

        X_df = self._prepare_X(df)
        fit_params = dict(self.params)
        fit_params["min_child_weight"] = min(
            int(fit_params.get("min_child_weight", 25)),
            max(1, len(df) // 10),
        )
        dmat = xgb.DMatrix(X_df, label=log_d, weight=weights, feature_names=self.features)
        self.booster = xgb.train(fit_params, dmat, num_boost_round=self.num_boost_round)
        return self

    def predict(self, feat_df: pd.DataFrame) -> np.ndarray:
        """Predict per-row dispersion alpha(x) clipped to [0.01, 1.0]."""
        import xgboost as xgb

        if self.booster is None:
            raise RuntimeError("AlphaXModel must be fit before calling predict()")
        if feat_df.empty:
            return np.empty(0, dtype=float)
        X_df = self._prepare_X(feat_df)
        dmat = xgb.DMatrix(X_df, feature_names=self.features)
        pred_log = self.booster.predict(dmat)
        return np.clip(np.exp(pred_log), ALPHA_MIN, ALPHA_MAX).astype(float)


class DampedRegimeMonitor:
    """Damped log-ratio residual monitor over PRIOR settled games only (shadow-tracked by default)."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled: bool = bool(enabled)
        self.offset: float = 0.0
        self.shadow_offset: float = 0.0

    def update(self, settled_rows: pd.DataFrame | Iterable[dict[str, Any]]) -> float:
        """Update damped offset from PRIOR settled games (`y` and `mu`).

        When `enabled` is False, `offset` remains 0.0 while `shadow_offset` tracks the damped update.
        """
        df = settled_rows.copy() if isinstance(settled_rows, pd.DataFrame) else pd.DataFrame(list(settled_rows))
        if df.empty:
            return self.offset
        y_col = "y" if "y" in df.columns else ("saves" if "saves" in df.columns else None)
        if y_col is None or "mu" not in df.columns:
            raise ValueError("settled_rows must contain 'y' (or 'saves') and 'mu'")

        y = pd.to_numeric(df[y_col], errors="coerce").to_numpy(dtype=float)
        mu = pd.to_numeric(df["mu"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(y) & np.isfinite(mu) & (y >= 0.0) & (mu >= 0.0)
        if not ok.any():
            return self.offset

        batch_mean = float(np.mean(np.log((y[ok] + 0.5) / (mu[ok] + 0.5))))
        self.shadow_offset = float(
            np.clip(
                (1.0 - MONITOR_DAMP) * self.shadow_offset + MONITOR_DAMP * batch_mean,
                -MONITOR_OFFSET_MAX,
                MONITOR_OFFSET_MAX,
            )
        )
        if self.enabled:
            self.offset = float(
                np.clip(
                    (1.0 - MONITOR_DAMP) * self.offset + MONITOR_DAMP * batch_mean,
                    -MONITOR_OFFSET_MAX,
                    MONITOR_OFFSET_MAX,
                )
            )
        else:
            self.offset = 0.0
        return self.offset

    def apply(self, mu: float | np.ndarray | pd.Series) -> float | np.ndarray | pd.Series:
        """Scale `mu` by `exp(offset)` when enabled, else unchanged (`exp(0)`)."""
        factor = math.exp(self.offset if self.enabled else 0.0)
        if isinstance(mu, pd.Series):
            return mu.astype(float) * factor
        if isinstance(mu, np.ndarray):
            return mu.astype(float) * factor
        return float(mu) * factor


def bias_alert(
    rows: pd.DataFrame | Iterable[dict[str, Any]],
    window_days: int = 14,
) -> bool:
    """Return True if `|mean(p_model) - mean(outcome)| > 0.035` over the trailing `window_days` days."""
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty or "p_model" not in df.columns:
        return False

    outcome_col = next(
        (c for c in ("outcome", "won_over", "actual_over", "over") if c in df.columns),
        None,
    )
    if outcome_col is None:
        raise ValueError("rows must contain an outcome indicator column ('outcome', 'won_over', etc.)")

    date_col = next((c for c in ("date", "game_date") if c in df.columns), None)
    if date_col is not None:
        dt = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
        valid_dt = dt.dropna()
        if valid_dt.empty:
            return False
        max_dt = valid_dt.max()
        cutoff_dt = max_dt - pd.Timedelta(days=int(window_days))
        df = df[dt > cutoff_dt].copy()

    p = pd.to_numeric(df["p_model"], errors="coerce")
    y = pd.to_numeric(df[outcome_col], errors="coerce")
    ok = p.notna() & y.notna()
    if not ok.any():
        return False
    return bool(abs(float(p[ok].mean()) - float(y[ok].mean())) > BIAS_ALERT_THRESHOLD)


def _row_nb_loglik(y: np.ndarray, mu: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    y_f = np.asarray(y, dtype=float)
    mu_f = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    a_f = np.asarray(alpha, dtype=float)
    pois = poisson.logpmf(y_f, mu_f)
    a_pos = np.where(a_f > 0.0, a_f, 1.0)
    n = 1.0 / a_pos
    nb = nbinom.logpmf(y_f, n, n / (n + mu_f))
    return np.where(a_f > 0.0, nb, pois)


def _score_variant(
    feat_sorted: pd.DataFrame,
    features: list[str],
    variant: str,
    target: str,
    test_from: str,
    step_months: int,
    prior_col: str | None,
    min_prior: int,
    seed: int,
) -> pd.DataFrame:
    wf = CM.walk_forward(
        feat_sorted,
        target,
        features,
        test_from,
        step_months=step_months,
        params={"seed": seed},
        min_prior=min_prior,
        prior_col=prior_col,
    )
    if wf.empty:
        return pd.DataFrame(
            columns=[
                "variant", "game_id", "player_id", "date", "y", "mu",
                "alpha_fixed", "alpha_x", "ll_fixed", "ll_x",
                "brier_tail_fixed", "brier_tail_x",
            ]
        )

    parts = []
    for period_start, grp in wf.groupby("period_start", sort=True):
        art = CM.fit(
            feat_sorted,
            target,
            features,
            cutoff=str(period_start),
            params={"seed": seed},
            min_prior=min_prior,
            prior_col=prior_col,
        )
        tr_slice = feat_sorted[feat_sorted[target].notna() & (feat_sorted["date"] < str(period_start))]
        if prior_col and prior_col in tr_slice.columns:
            tr_slice = tr_slice[tr_slice[prior_col] >= min_prior]
        ax_model = AlphaXModel(features=features, seed=seed).fit(art, tr_slice, target=target)

        merged = grp.merge(
            feat_sorted[["game_id", "player_id", "date"] + features + [target]].drop_duplicates(
                ["game_id", "player_id", "date"]
            ),
            on=["game_id", "player_id", "date"],
            how="left",
        )
        merged = merged[merged[target].notna()].copy()
        if merged.empty:
            continue

        y_arr = merged[target].to_numpy(dtype=float)
        mu_arr = merged["mu"].to_numpy(dtype=float)
        alpha_fixed = merged["alpha"].to_numpy(dtype=float)
        alpha_x = ax_model.predict(merged)

        ll_fixed = _row_nb_loglik(y_arr, mu_arr, alpha_fixed)
        ll_x = _row_nb_loglik(y_arr, mu_arr, alpha_x)

        tail_y = (y_arr >= TAIL_SAVES_THRESHOLD).astype(float)
        p_tail_fixed = np.array([p_over(TAIL_LINE_PROXY, m, a) for m, a in zip(mu_arr, alpha_fixed)], dtype=float)
        p_tail_x = np.array([p_over(TAIL_LINE_PROXY, m, a) for m, a in zip(mu_arr, alpha_x)], dtype=float)

        part = pd.DataFrame({
            "variant": variant,
            "game_id": merged["game_id"].to_numpy(),
            "player_id": merged["player_id"].to_numpy(),
            "date": merged["date"].to_numpy(),
            "y": y_arr,
            "mu": mu_arr,
            "alpha_fixed": alpha_fixed,
            "alpha_x": alpha_x,
            "ll_fixed": ll_fixed,
            "ll_x": ll_x,
            "brier_tail_fixed": (p_tail_fixed - tail_y) ** 2,
            "brier_tail_x": (p_tail_x - tail_y) ** 2,
        })
        parts.append(part)

    if not parts:
        return pd.DataFrame(
            columns=[
                "variant", "game_id", "player_id", "date", "y", "mu",
                "alpha_fixed", "alpha_x", "ll_fixed", "ll_x",
                "brier_tail_fixed", "brier_tail_x",
            ]
        )
    return pd.concat(parts, ignore_index=True)


def walk_forward_compare(
    feat: pd.DataFrame,
    features_fixed: Iterable[str],
    features_xg: Iterable[str],
    test_from: str,
    step_months: int = 3,
    target: str = "saves",
    prior_col: str | None = "g_n_prior",
    min_prior: int = 5,
    seed: int = 7,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run walk-forward comparison of fixed vs xG-ablation features with global alpha vs AlphaXModel.

    Tail Brier evaluates `P(Y >= 35)` (`TAIL_SAVES_THRESHOLD = 35`, i.e., `line = 34.5`) so no
    market lines enter training or evaluation. Deterministic (`seed = 7`).
    """
    feat_sorted = feat.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True).copy()
    pcol = prior_col if (prior_col and prior_col in feat_sorted.columns) else None

    df_fixed = _score_variant(
        feat_sorted, list(features_fixed), "fixed", target, test_from, step_months, pcol, min_prior, seed
    )
    df_xg = _score_variant(
        feat_sorted, list(features_xg), "xg", target, test_from, step_months, pcol, min_prior, seed
    )

    out = pd.concat([df_fixed, df_xg], ignore_index=True)
    summary: dict[str, Any] = {
        "engine": ENGINE_LABEL,
        "tail_threshold": TAIL_SAVES_THRESHOLD,
        "test_from": test_from,
        "step_months": step_months,
        "seed": seed,
    }
    for name, sub in (("fixed", df_fixed), ("xg", df_xg)):
        if sub.empty:
            summary[name] = {"n": 0}
        else:
            summary[name] = {
                "n": int(len(sub)),
                "mean_ll_fixed": float(sub["ll_fixed"].mean()),
                "mean_ll_x": float(sub["ll_x"].mean()),
                "mean_brier_tail_fixed": float(sub["brier_tail_fixed"].mean()),
                "mean_brier_tail_x": float(sub["brier_tail_x"].mean()),
                "mean_alpha_fixed": float(sub["alpha_fixed"].mean()),
                "mean_alpha_x": float(sub["alpha_x"].mean()),
                "mae": float(np.abs(sub["y"] - sub["mu"]).mean()),
            }
    if not df_fixed.empty and not df_xg.empty:
        summary["delta_ll_xg_vs_fixed"] = summary["xg"]["mean_ll_fixed"] - summary["fixed"]["mean_ll_fixed"]
        summary["delta_ll_alphax_vs_fixed_on_fixed"] = (
            summary["fixed"]["mean_ll_x"] - summary["fixed"]["mean_ll_fixed"]
        )
        summary["delta_ll_alphax_vs_fixed_on_xg"] = (
            summary["xg"]["mean_ll_x"] - summary["xg"]["mean_ll_fixed"]
        )
    return out, summary
