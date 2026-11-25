"""B3 NHL skater SOG shadow engine (`nhl_sog_trained_v1`).

SOG's first trained model (SHADOW-ONLY): structural decomposition
    Expected TOI (EV + PP) * unblocked shot-attempt rate per minute * on-target rate,
integrated over TOI uncertainty via 5-point Gauss-Hermite quadrature into a discrete
NegBin mixture PMF over 0..kmax.

Serving-layer policy note: the Novig x NHL SOG refusal stays in force regardless
(serving-layer concern; this module is strictly shadow-only and never touches serving
bundles, daily_projections, trained.py, paper_track.py, or serve_trained.py).
"""
from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from nhl.props import mod04_features as F
from nhl.props import mod04_nhl_features as NF
from nhl.props import negbin as NB
from nhl.props import xgfeat as X

ENGINE_LABEL = "nhl_sog_trained_v1"

REQUIRED_FEAT_COLS: tuple[str, ...] = (
    "exp_toi_min",
    "exp_toi_sd",
    "att_rate_per_min",
    "ontarget_rate",
)

DEFAULT_TOI_MIN = 15.0
DEFAULT_TOI_SD = 2.5
PRIOR_ATT_RATE_F = 0.16   # ~9.6 unblocked attempts / 60 min for forwards
PRIOR_ATT_RATE_D = 0.10   # ~6.0 unblocked attempts / 60 min for defensemen
ATT_PRIOR_WEIGHT = 6.0    # shrinkage weight in prior games

# 5-point Gauss-Hermite quadrature for Z ~ Normal(0, 1):
# E[g(Z)] ~= sum_j weights[j] * g(z_nodes[j]), where sum(weights) == 1.
_GH_X, _GH_W_RAW = np.polynomial.hermite.hermgauss(5)
_GH_Z = _GH_X * math.sqrt(2.0)
_GH_W = _GH_W_RAW / math.sqrt(math.pi)

_BANNED_OUTCOME_OR_MARKET_COLS = frozenset({
    "sog", "attempts", "goals", "assists", "points", "toi_sec", "toi_min", "played",
    "line", "odds", "over_odds", "under_odds", "p_market", "p_mkt", "market_line",
})


def _nb_pmf_grid(mu: np.ndarray, alpha: float, kmax: int) -> np.ndarray:
    """Evaluate normalized NegBin(mu, alpha) PMF over 0..kmax for arbitrary array shape of `mu`."""
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
        bad = (sums[:, 0] <= 0.0)
        if bad.any():
            probs[bad, :] = 0.0
            probs[bad, -1] = 1.0
            sums[bad, 0] = 1.0
        out[pos] = probs / sums

    return out.reshape(*shape, len(k))


class SogStructuralModel:
    """Structural SOG count model with league-level MoM calibration and GH TOI-uncertainty integration."""

    def __init__(self, kmax: int = 15) -> None:
        self.kmax: int = int(kmax)
        self.att_mult: float = 1.0
        self.ontarget_mult: float = 1.0
        self.alpha: float = 0.05
        self.fitted: bool = False

    def _validate_features(self, feat: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_FEAT_COLS if c not in feat.columns]
        if missing:
            raise ValueError(f"SogStructuralModel missing required feature column(s): {missing}")

    def fit(self, feat: pd.DataFrame, target: str = "sog") -> "SogStructuralModel":
        """Estimate global attempt-rate and on-target multipliers (MoM) + NegBin dispersion alpha."""
        self._validate_features(feat)
        if target not in feat.columns:
            raise ValueError(f"SogStructuralModel.fit missing target column {target!r}")

        toi = pd.to_numeric(feat["exp_toi_min"], errors="coerce").to_numpy(dtype=float)
        att_r = pd.to_numeric(feat["att_rate_per_min"], errors="coerce").to_numpy(dtype=float)
        on_r = pd.to_numeric(feat["ontarget_rate"], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(feat[target], errors="coerce").to_numpy(dtype=float)

        ok = (
            np.isfinite(toi) & (toi >= 0.0)
            & np.isfinite(att_r) & (att_r >= 0.0)
            & np.isfinite(on_r) & (on_r >= 0.0)
            & np.isfinite(y) & (y >= 0.0)
        )
        if not ok.any():
            raise ValueError("SogStructuralModel.fit received 0 valid training rows")

        toi_ok, att_ok, on_ok, y_ok = toi[ok], att_r[ok], on_r[ok], y[ok]
        raw_att = toi_ok * att_ok
        raw_mu = raw_att * on_ok

        has_attempts = False
        if "attempts" in feat.columns:
            att_actual = pd.to_numeric(feat["attempts"], errors="coerce").to_numpy(dtype=float)[ok]
            if np.isfinite(att_actual).all() and (att_actual >= 0.0).all() and float(att_actual.mean()) > 0.0:
                has_attempts = True
                raw_att_mean = float(raw_att.mean())
                self.att_mult = float(np.clip(float(att_actual.mean()) / max(raw_att_mean, 1e-6), 0.2, 5.0))
                after_att_mean = float((raw_att * self.att_mult * on_ok).mean())
                self.ontarget_mult = float(np.clip(float(y_ok.mean()) / max(after_att_mean, 1e-6), 0.2, 5.0))

        if not has_attempts:
            raw_mu_mean = float(raw_mu.mean())
            total_mult = float(np.clip(float(y_ok.mean()) / max(raw_mu_mean, 1e-6), 0.04, 25.0))
            root = math.sqrt(total_mult)
            self.att_mult = root
            self.ontarget_mult = root

        mu_hat = np.maximum(raw_mu * self.att_mult * self.ontarget_mult, 1e-6)
        self.alpha = float(NB.fit_alpha(y_ok, mu_hat))
        self.fitted = True
        return self

    def _gh_mu_nodes(self, feat: pd.DataFrame) -> np.ndarray:
        """Compute `(N, 5)` mean matrix across 5-point Gauss-Hermite TOI quadrature nodes."""
        self._validate_features(feat)
        toi_m = np.maximum(pd.to_numeric(feat["exp_toi_min"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)
        toi_sd = np.maximum(pd.to_numeric(feat["exp_toi_sd"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)
        att_r = np.maximum(pd.to_numeric(feat["att_rate_per_min"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)
        on_r = np.maximum(pd.to_numeric(feat["ontarget_rate"], errors="coerce").fillna(0.0).to_numpy(dtype=float), 0.0)

        toi_nodes = np.maximum(toi_m[:, None] + toi_sd[:, None] * _GH_Z[None, :], 0.0)
        rate_eff = (att_r * self.att_mult) * (on_r * self.ontarget_mult)
        return toi_nodes * rate_eff[:, None]

    def predict_mu(self, feat: pd.DataFrame) -> np.ndarray:
        """Expected SOG integrated over the 5-point Gauss-Hermite TOI quadrature."""
        if feat.empty:
            self._validate_features(feat)
            return np.empty(0, dtype=float)
        mu_nodes = self._gh_mu_nodes(feat)
        return (mu_nodes * _GH_W[None, :]).sum(axis=1)

    def predict_pmf(self, feat: pd.DataFrame, kmax: int | None = None) -> np.ndarray:
        """Full discrete PMF over `0..kmax` integrating NegBin(mu_node, alpha) over TOI uncertainty."""
        km = self.kmax if kmax is None else int(kmax)
        if km < 0:
            raise ValueError(f"kmax must be >= 0, got {km}")
        self._validate_features(feat)
        if feat.empty:
            return np.empty((0, km + 1), dtype=float)

        mu_nodes = self._gh_mu_nodes(feat)
        node_pmfs = _nb_pmf_grid(mu_nodes, self.alpha, km)  # (N, 5, km + 1)
        mix = np.tensordot(node_pmfs, _GH_W, axes=([1], [0]))  # (N, km + 1)
        mix = np.where(np.isfinite(mix) & (mix >= 0.0), mix, 0.0)
        sums = mix.sum(axis=1, keepdims=True)
        bad = sums[:, 0] <= 0.0
        if bad.any():
            mix[bad, :] = 0.0
            mix[bad, 0] = 1.0
            sums[bad, 0] = 1.0
        return mix / sums

    def p_over(
        self,
        feat: pd.DataFrame,
        line: float | np.ndarray,
        kmax: int | None = None,
    ) -> np.ndarray:
        """Compute `P(SOG > line)` directly from the discrete PMF (never a per-line classifier)."""
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


def _build_direct_skater_base(
    frames: Mapping[str, pd.DataFrame],
    toi_splits: pd.DataFrame | None,
    rotowire: pd.DataFrame | None,
) -> pd.DataFrame:
    """Fallback prior-only rolling builder directly from skater box scores via `xgfeat` primitives."""
    sk_raw = frames["skater_box"].copy() if "skater_box" in frames else frames["skaters"].copy()
    if sk_raw.empty:
        return pd.DataFrame(columns=[
            "game_id", "player_id", "date", "team", "opp", "team_id", "opp_id",
            "season_year", "game_type", "name", "position", "toi_l10", "toi_ewm",
            "toi_sd_l10", "att_p60_l20", "att_p60_l60", "on_target_rate_l30",
            "pp_toi_share_prior", "is_pp1_proxy", "deployment_source", "n_prior",
            "is_d", "is_c", "is_home", "rest_days", "rotowire_pp_unit",
        ])

    s = sk_raw.copy()
    s["date"] = s["date"].astype(str).str[:10]
    if "game_type" in s.columns:
        s = s[s["game_type"].isin(X.GAME_TYPES)].copy()
    if "toi_sec" in s.columns:
        s = s[(s["toi_sec"] > 0) | (s["game_id"] >= X.PLACEHOLDER_BASE)].copy()
        s["toi_min"] = pd.to_numeric(s["toi_sec"], errors="coerce").fillna(0.0) / 60.0
    elif "toi_min" not in s.columns:
        s["toi_min"] = DEFAULT_TOI_MIN

    if "attempts" not in s.columns:
        s["attempts"] = pd.to_numeric(s["sog"], errors="coerce") if "sog" in s.columns else np.nan
    if "sog" not in s.columns:
        s["sog"] = np.nan

    if "team" not in s.columns:
        s["team"] = s["team_abbr"] if "team_abbr" in s.columns else ""
    if "opp" not in s.columns:
        s["opp"] = ""
    if "opp_id" not in s.columns:
        s["opp_id"] = np.nan
    if "position" not in s.columns:
        s["position"] = "C"

    s = s.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    s["n_prior"] = s.groupby("player_id").cumcount().astype(float)
    dt = pd.to_datetime(s["date"])
    s["rest_days"] = (dt - dt.groupby(s["player_id"]).shift(1)).dt.days.clip(upper=8).fillna(8)
    s["is_d"] = (s["position"] == "D").astype(float)
    s["is_c"] = (s["position"] == "C").astype(float)
    s["is_home"] = s["is_home"].astype(int) if "is_home" in s.columns else 0

    s["toi_l10"] = X.prior_roll(s, "player_id", "toi_min", 10)
    s["toi_ewm"] = X.prior_ewm(s, "player_id", "toi_min", 8)
    s["toi_sd_l10"] = X.prior_roll(s, "player_id", "toi_min", 10, how="std", min_periods=2)

    for w in (20, 60):
        att_sum = X.prior_roll(s, "player_id", "attempts", w, "sum", min_periods=3)
        toi_sum = X.prior_roll(s, "player_id", "toi_min", w, "sum", min_periods=3)
        s[f"att_p60_l{w}"] = att_sum / toi_sum.where(toi_sum > 0) * 60.0

    s["on_target_rate_l30"] = X.prior_ratio(
        s,
        "player_id",
        "sog",
        "attempts",
        30,
        prior_num=NF.ON_TARGET_PRIOR_RATE * NF.ON_TARGET_PRIOR_ATT,
        prior_den=NF.ON_TARGET_PRIOR_ATT,
    )

    if toi_splits is not None and len(toi_splits):
        sp = toi_splits.copy()
        if "pp_share" not in sp.columns and "toi_pp" in sp.columns and "team_pp_s" in sp.columns:
            sp["pp_share"] = sp["toi_pp"] / sp["team_pp_s"].where(sp["team_pp_s"] > 0)
        sp = sp[["game_id", "player_id", "pp_share"]].drop_duplicates(["game_id", "player_id"])
        s = s.merge(sp, on=["game_id", "player_id"], how="left")
        s = s.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
        s["pp_toi_share_prior"] = X.prior_roll(s, "player_id", "pp_share", 10)
        s = s.drop(columns=["pp_share"])
        s["deployment_source"] = np.where(s["pp_toi_share_prior"].notna(), "toi_splits", "total_toi_proxy")
        s["is_pp1_proxy"] = 0
    else:
        s["pp_toi_share_prior"] = np.nan
        s["is_pp1_proxy"] = 0
        s["deployment_source"] = "total_toi_proxy"

    s = NF._apply_rotowire_skaters(s, rotowire)
    drop_cols = [c for c in s.columns if c in _BANNED_OUTCOME_OR_MARKET_COLS]
    if drop_cols:
        s = s.drop(columns=drop_cols)
    return s


def build_sog_features(
    frames: Mapping[str, pd.DataFrame],
    toi_splits: pd.DataFrame | None = None,
    rotowire: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build leak-free pre-game SOG structural features passing `assert_feature_schema`.

    Uses `mod04_nhl_features.build_nhl_volume_features` assembly output if available (either
    pre-built under `frames["skater"]` or assembled from `games` + `skater_box` + `goalie_box`);
    otherwise falls back to strictly prior-only `xgfeat.prior_roll` / `prior_ratio` over skater
    box scores. Rotowire-derived columns remain strictly `NaN` outside `ROTOWIRE_SPAN`.
    """
    splits = toi_splits if toi_splits is not None else frames.get("toi_splits")
    rw = rotowire if rotowire is not None else frames.get("rotowire")

    if "skater" in frames and isinstance(frames["skater"], pd.DataFrame):
        sk = frames["skater"].copy()
    elif (
        "games" in frames
        and ("skater_box" in frames or "skaters" in frames)
        and ("goalie_box" in frames or "goalies" in frames)
    ):
        sk = NF.build_nhl_volume_features(frames, toi_splits=splits, rotowire=rw)["skater"]
    elif "skater_box" in frames or "skaters" in frames:
        sk = _build_direct_skater_base(frames, splits, rw)
    else:
        raise KeyError(
            "frames must contain 'skater', ('games', 'skater_box', 'goalie_box'), or 'skater_box'"
        )

    if sk.empty:
        for col in ("exp_toi_ev_min", "exp_toi_pp_min") + REQUIRED_FEAT_COLS:
            if col not in sk.columns:
                sk[col] = pd.Series(dtype=float)
        F.assert_feature_schema(sk)
        return sk

    out = sk.copy()

    # Restrict any rotowire column strictly to ROTOWIRE_SPAN
    if "rotowire_pp_unit" in out.columns and "date" in out.columns:
        in_span = NF._in_rotowire_span(out["date"])
        out.loc[~in_span, "rotowire_pp_unit"] = np.nan

    # Expected TOI decomposition (EV + PP) and uncertainty SD
    toi_ewm = pd.to_numeric(out["toi_ewm"], errors="coerce") if "toi_ewm" in out.columns else pd.Series(np.nan, index=out.index)
    toi_l10 = pd.to_numeric(out["toi_l10"], errors="coerce") if "toi_l10" in out.columns else pd.Series(np.nan, index=out.index)
    exp_toi = toi_ewm.fillna(toi_l10).fillna(DEFAULT_TOI_MIN).clip(lower=0.0)

    if "pp_toi_share_prior" in out.columns:
        pp_share = pd.to_numeric(out["pp_toi_share_prior"], errors="coerce").clip(lower=0.0, upper=1.0)
        exp_pp = np.where(pp_share.notna(), exp_toi * pp_share, 0.0)
    else:
        exp_pp = np.zeros(len(out), dtype=float)
    out["exp_toi_min"] = exp_toi.to_numpy(dtype=float)
    out["exp_toi_pp_min"] = exp_pp
    out["exp_toi_ev_min"] = np.maximum(out["exp_toi_min"] - out["exp_toi_pp_min"], 0.0)

    toi_sd = pd.to_numeric(out["toi_sd_l10"], errors="coerce") if "toi_sd_l10" in out.columns else pd.Series(np.nan, index=out.index)
    out["exp_toi_sd"] = toi_sd.fillna(DEFAULT_TOI_SD).clip(lower=0.0).to_numpy(dtype=float)

    # Shrunk unblocked attempt rate per minute
    a20 = pd.to_numeric(out["att_p60_l20"], errors="coerce") if "att_p60_l20" in out.columns else pd.Series(np.nan, index=out.index)
    a60 = pd.to_numeric(out["att_p60_l60"], errors="coerce") if "att_p60_l60" in out.columns else pd.Series(np.nan, index=out.index)
    raw_p60 = np.where(
        a20.notna() & a60.notna(),
        0.5 * a20 + 0.5 * a60,
        a20.fillna(a60),
    )
    raw_rate = raw_p60 / 60.0
    is_d = pd.to_numeric(out["is_d"], errors="coerce").fillna(0.0).to_numpy(dtype=float) if "is_d" in out.columns else np.zeros(len(out))
    prior_rate = np.where(is_d == 1.0, PRIOR_ATT_RATE_D, PRIOR_ATT_RATE_F)
    n_prior = pd.to_numeric(out["n_prior"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=20.0).to_numpy(dtype=float) if "n_prior" in out.columns else np.full(len(out), 10.0)
    out["att_rate_per_min"] = np.where(
        np.isfinite(raw_rate),
        (n_prior * raw_rate + ATT_PRIOR_WEIGHT * prior_rate) / (n_prior + ATT_PRIOR_WEIGHT),
        prior_rate,
    )

    # Shrunk on-target rate (prior SOG / attempts)
    on_t = pd.to_numeric(out["on_target_rate_l30"], errors="coerce") if "on_target_rate_l30" in out.columns else pd.Series(np.nan, index=out.index)
    out["ontarget_rate"] = on_t.fillna(NF.ON_TARGET_PRIOR_RATE).clip(lower=0.05, upper=0.99).to_numpy(dtype=float)

    drop_leak = [c for c in out.columns if c.lower() in _BANNED_OUTCOME_OR_MARKET_COLS]
    if drop_leak:
        out = out.drop(columns=drop_leak)

    F.assert_feature_schema(out)
    sort_cols = [c for c in ("date", "game_id", "player_id") if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols).reset_index(drop=True)
    return out
