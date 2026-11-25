"""C3 NHL assists heads: primary (GBDT), secondary (heavily shrunk EB), convolved split, and unified baseline.

Engine labels:
  - `mod04_nhl_assists_primary_shadow_v1`
  - `mod04_nhl_assists_secondary_shadow_v1`
  - `mod04_nhl_assists_split_shadow_v1`
  - `mod04_nhl_assists_unified_shadow_v1`

Shot-quality proxy (`linemate_xg_rate`):
  Uses a simple logistic-free distance/angle proxy on strictly-prior teammate
  shots: `xG_proxy = exp(-0.045 * dist_ft) * cos(radians(clip(angle_deg, 0, 89)))`.
  Documented explicitly as a heuristic proxy; NaN when shots data is unavailable.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from mod04c.dist_negbin import convolve_pmf, fit_alpha_grid, nb_pmf
from mod04c.feature_store import build_pregame_features, filter_history, validate_feature_frame
from nhl.props.negbin import nb_loglik

PRIMARY_ENGINE_LABEL = "mod04_nhl_assists_primary_shadow_v1"
SECONDARY_ENGINE_LABEL = "mod04_nhl_assists_secondary_shadow_v1"
SPLIT_ENGINE_LABEL = "mod04_nhl_assists_split_shadow_v1"
UNIFIED_ENGINE_LABEL = "mod04_nhl_assists_unified_shadow_v1"

SECONDARY_PRIOR_GAMES = 40.0  # heavy shrinkage prior strength (40 games equivalent)
DEFAULT_TOI_MIN = 15.0
MIN_HOURS = 0.05

NHL_FEATURES = [
    "games_played",
    "toi_min_l5",
    "toi_min_l15",
    "prim_per60_l5",
    "prim_per60_l15",
    "sec_per60_l5",
    "sec_per60_l15",
    "ast_per60_l5",
    "ast_per60_l15",
    "sog_per60_l15",
    "linemate_xg_rate",
    "pp1_flag",
    "opp_goalie_sv_above_exp",
    "exp_toi_min",
]

XGB_PARAMS = {
    "objective": "count:poisson",
    "eval_metric": "poisson-nloglik",
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.04,
    "min_child_weight": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 8.0,
    "seed": 7,
    "nthread": 4,
}


def shot_xg_proxy(dist_ft: pd.Series, angle_deg: Optional[pd.Series] = None) -> pd.Series:
    """Simple logistic-free xG proxy: exp(-0.045 * dist_ft) * cos(angle)."""
    d = pd.to_numeric(dist_ft, errors="coerce")
    if angle_deg is None:
        ang_factor = 1.0
    else:
        ang = pd.to_numeric(angle_deg, errors="coerce").fillna(0.0).clip(0.0, 89.0)
        ang_factor = np.cos(np.radians(ang))
    return np.exp(-0.045 * d.clip(lower=0.0)) * ang_factor


def _team_col(df: pd.DataFrame) -> Optional[str]:
    for c in ("team_id", "team_abbr", "team"):
        if c in df.columns:
            return c
    return None


def _opp_col(df: pd.DataFrame) -> Optional[str]:
    for c in ("opp_id", "opp_abbr", "opponent"):
        if c in df.columns:
            return c
    return None


def _compute_linemate_xg_map(
    prior_shots: Optional[pd.DataFrame],
    prior_history: pd.DataFrame,
) -> tuple[dict, float]:
    """Mean xG proxy of team shots in strictly-prior games."""
    if prior_shots is None or prior_shots.empty or "dist_ft" not in prior_shots.columns:
        return {}, np.nan
    sh = prior_shots.copy()
    if "date" not in sh.columns and not prior_history.empty and "game_id" in prior_history.columns:
        g_dates = prior_history[["game_id", "date"]].drop_duplicates("game_id")
        sh = sh.merge(g_dates, on="game_id", how="inner")
    sh["_xg"] = shot_xg_proxy(sh["dist_ft"], sh["angle_deg"] if "angle_deg" in sh.columns else None)
    sh = sh.dropna(subset=["_xg"])
    if sh.empty:
        return {}, np.nan
    lg_mean = float(sh["_xg"].mean())
    tcol = "team_id" if "team_id" in sh.columns else _team_col(sh)
    if tcol is None:
        return {}, lg_mean
    team_means = sh.groupby(tcol)["_xg"].mean().to_dict()
    return team_means, lg_mean


def _compute_opp_goalie_sv_above_exp(
    slate: pd.DataFrame,
    prior_goalies: Optional[pd.DataFrame],
    target_date,
) -> np.ndarray:
    n = len(slate)
    if prior_goalies is None or prior_goalies.empty:
        return np.full(n, np.nan)
    g = filter_history(prior_goalies, target_date)
    if g.empty or not {"saves", "shots_against"}.issubset(g.columns):
        return np.full(n, np.nan)
    g = g.copy()
    g["saves"] = pd.to_numeric(g["saves"], errors="coerce")
    g["shots_against"] = pd.to_numeric(g["shots_against"], errors="coerce")
    g = g.dropna(subset=["saves", "shots_against"])
    g = g[g["shots_against"] > 0]
    if g.empty:
        return np.full(n, np.nan)
    lg_sv = float(g["saves"].sum() / g["shots_against"].sum())

    tcol = _team_col(g)
    opp_col = _opp_col(slate)
    if tcol is None or opp_col is None:
        return np.full(n, np.nan)

    g = g.sort_values(["date", "game_id"] if "game_id" in g.columns else ["date"], kind="mergesort")
    recent = g.groupby(tcol, sort=False).tail(15)
    tm_agg = recent.groupby(tcol).agg(sv=("saves", "sum"), sa=("shots_against", "sum"))
    tm_sv = {
        t: (row["sv"] + 200.0 * lg_sv) / (row["sa"] + 200.0) - lg_sv
        for t, row in tm_agg.iterrows()
    }
    return slate[opp_col].map(tm_sv).to_numpy(float)


def build_nhl_assist_features(
    target_date,
    cutoff_utc,
    *,
    slate: pd.DataFrame,
    history_with_split: pd.DataFrame,
    shots: Optional[pd.DataFrame] = None,
    lineups: Optional[pd.DataFrame] = None,
    goalies: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build strictly-prior NHL assist features via `feature_store.build_pregame_features`."""
    hist = history_with_split.copy()
    if "toi_sec" in hist.columns:
        hist["toi_min"] = pd.to_numeric(hist["toi_sec"], errors="coerce") / 60.0
    elif "toi_min" not in hist.columns:
        hist["toi_min"] = DEFAULT_TOI_MIN

    base = build_pregame_features(target_date, cutoff_utc, slate=slate, history=hist)
    prior = filter_history(hist, target_date)

    for col in ("toi_min_l5", "toi_min_l15", "primary_assists_l5", "primary_assists_l15",
                "secondary_assists_l5", "secondary_assists_l15", "assists_l5", "assists_l15", "sog_l15"):
        if col not in base.columns:
            base[col] = np.nan

    for w in (5, 15):
        toi = pd.to_numeric(base[f"toi_min_l{w}"], errors="coerce")
        prim = pd.to_numeric(base[f"primary_assists_l{w}"], errors="coerce")
        sec = pd.to_numeric(base[f"secondary_assists_l{w}"], errors="coerce")
        ast = pd.to_numeric(base[f"assists_l{w}"], errors="coerce")
        base[f"prim_per60_l{w}"] = np.where(toi > 0, 60.0 * prim / toi, np.nan)
        base[f"sec_per60_l{w}"] = np.where(toi > 0, 60.0 * sec / toi, np.nan)
        base[f"ast_per60_l{w}"] = np.where(toi > 0, 60.0 * ast / toi, np.nan)

    toi15 = pd.to_numeric(base["toi_min_l15"], errors="coerce")
    sog15 = pd.to_numeric(base["sog_l15"], errors="coerce")
    base["sog_per60_l15"] = np.where(toi15 > 0, 60.0 * sog15 / toi15, np.nan)

    t5 = pd.to_numeric(base["toi_min_l5"], errors="coerce")
    t15 = pd.to_numeric(base["toi_min_l15"], errors="coerce")
    exp_toi = np.where(np.isfinite(t5) & np.isfinite(t15), 0.5 * t5 + 0.5 * t15, np.where(np.isfinite(t15), t15, t5))
    base["exp_toi_min"] = np.where(np.isfinite(exp_toi) & (exp_toi > 0), exp_toi, DEFAULT_TOI_MIN)

    # Linemate xG rate from strictly-prior shots
    prior_shots = None
    if shots is not None and not shots.empty:
        if "date" in shots.columns:
            prior_shots = filter_history(shots, target_date)
        elif not prior.empty and "game_id" in prior.columns:
            prior_gids = set(prior["game_id"].dropna().tolist())
            prior_shots = shots[shots["game_id"].isin(prior_gids)]
    tm_xg_map, lg_xg = _compute_linemate_xg_map(prior_shots, prior)
    tcol = _team_col(base)
    if tcol and tm_xg_map:
        base["linemate_xg_rate"] = base[tcol].map(tm_xg_map).fillna(lg_xg).astype(float)
    else:
        base["linemate_xg_rate"] = float(lg_xg) if np.isfinite(lg_xg) else np.nan

    # PP1 flag from optional lineup frame (else NaN)
    if lineups is not None and not lineups.empty and "player_id" in lineups.columns and "pp_unit" in lineups.columns:
        lu = lineups[["player_id", "pp_unit"]].drop_duplicates("player_id", keep="last")
        pp_map = {pid: (1.0 if unit == 1 else 0.0) for pid, unit in zip(lu["player_id"], lu["pp_unit"]) if pd.notna(unit)}
        base["pp1_flag"] = base["player_id"].map(pp_map).astype(float)
    else:
        base["pp1_flag"] = np.nan

    base["opp_goalie_sv_above_exp"] = _compute_opp_goalie_sv_above_exp(base, goalies, target_date)

    # Secondary shrinkage inputs (cumulative prior secondary assists and TOI hours)
    if not prior.empty and "player_id" in prior.columns and "secondary_assists" in prior.columns:
        p_sec = prior.groupby("player_id").agg(
            prior_sec_sum=("secondary_assists", "sum"),
            prior_toi_hours=("toi_min", lambda s: float(np.nansum(s)) / 60.0),
        )
        base["prior_sec_sum"] = base["player_id"].map(p_sec["prior_sec_sum"]).fillna(0.0)
        base["prior_toi_hours"] = base["player_id"].map(p_sec["prior_toi_hours"]).fillna(0.0)
    else:
        base["prior_sec_sum"] = 0.0
        base["prior_toi_hours"] = 0.0

    return validate_feature_frame(base)


class _BasePoissonAssistsModel:
    """Shared XGBoost count:poisson + TOI exposure + OOF NegBin alpha fit."""

    engine_label: str = ""
    target_col: str = ""

    def __init__(self, params: Optional[dict] = None) -> None:
        self.params = {**XGB_PARAMS, **(params or {})}
        self.features = list(NHL_FEATURES)
        self.booster_: Optional[xgb.Booster] = None
        self.best_iteration_: int = 50
        self.alpha_: float = 0.05

    def _dmatrix(self, df: pd.DataFrame, y: Optional[np.ndarray] = None, w: Optional[np.ndarray] = None) -> xgb.DMatrix:
        validate_feature_frame(df)
        X = df.reindex(columns=self.features).apply(pd.to_numeric, errors="coerce").astype("float32")
        return xgb.DMatrix(X, label=y, weight=w, feature_names=self.features)

    def fit(self, train_df: pd.DataFrame, val_frac: float = 0.20):
        df = train_df[train_df[self.target_col].notna()].copy()
        df = df.sort_values(["date", "player_id"], kind="mergesort").reset_index(drop=True)
        if len(df) < 10:
            raise ValueError(f"Need at least 10 training rows for {self.target_col}, got {len(df)}")

        dates = np.sort(df["date"].astype(str).unique())
        split_idx = max(1, min(len(dates) - 1, int(len(dates) * (1.0 - val_frac))))
        split_date = dates[split_idx]
        tr_mask = df["date"].astype(str) < split_date
        va_mask = ~tr_mask
        if not tr_mask.any() or not va_mask.any():
            cut = max(1, int(len(df) * (1.0 - val_frac)))
            tr_df, va_df = df.iloc[:cut].copy(), df.iloc[cut:].copy()
        else:
            tr_df, va_df = df.loc[tr_mask].copy(), df.loc[va_mask].copy()

        def _prep(part: pd.DataFrame):
            y_raw = pd.to_numeric(part[self.target_col], errors="coerce").fillna(0.0).to_numpy(float)
            toi_min = np.maximum(pd.to_numeric(part["exp_toi_min"], errors="coerce").fillna(DEFAULT_TOI_MIN).to_numpy(float), 1.0)
            toi_hrs = np.maximum(toi_min / 60.0, MIN_HOURS)
            return y_raw / toi_hrs, toi_hrs, y_raw

        y_tr, w_tr, _ = _prep(tr_df)
        y_va, w_va, y_va_raw = _prep(va_df)

        dtr = self._dmatrix(tr_df, y=y_tr, w=w_tr)
        dva = self._dmatrix(va_df, y=y_va, w=w_va)
        bst = xgb.train(
            self.params,
            dtr,
            num_boost_round=400,
            evals=[(dva, "val")],
            early_stopping_rounds=25,
            verbose_eval=False,
        )
        best = max(1, int(getattr(bst, "best_iteration", 49)) + 1)
        self.best_iteration_ = best
        rate_va = np.maximum(bst.predict(dva, iteration_range=(0, best)), 1e-5)
        self.alpha_ = fit_alpha_grid(y_va_raw, rate_va * w_va)

        y_all, w_all, _ = _prep(df)
        self.booster_ = xgb.train(self.params, self._dmatrix(df, y=y_all, w=w_all), num_boost_round=best)
        return self

    def predict_mu(self, features_df: pd.DataFrame) -> np.ndarray:
        if self.booster_ is None:
            raise RuntimeError("Model must be fit before calling predict_mu")
        rate60 = np.maximum(
            self.booster_.predict(self._dmatrix(features_df), iteration_range=(0, self.best_iteration_)),
            1e-5,
        )
        toi_min = np.maximum(
            pd.to_numeric(features_df["exp_toi_min"], errors="coerce").fillna(DEFAULT_TOI_MIN).to_numpy(float),
            1.0,
        )
        return np.maximum(rate60 * (toi_min / 60.0), 1e-6)

    def predict_pmf(self, features_df: pd.DataFrame, kmax: int = 20) -> tuple[np.ndarray, np.ndarray, float]:
        mu = self.predict_mu(features_df)
        return nb_pmf(mu, self.alpha_, kmax=kmax), mu, float(self.alpha_)


class PrimaryAssistsModel(_BasePoissonAssistsModel):
    engine_label = PRIMARY_ENGINE_LABEL
    target_col = "primary_assists"


class UnifiedAssistsModel(_BasePoissonAssistsModel):
    engine_label = UNIFIED_ENGINE_LABEL
    target_col = "assists"


class SecondaryAssistsModel:
    """Heavily shrunk Empirical-Bayes per-60 rate model for secondary assists (no GBDT)."""

    engine_label = SECONDARY_ENGINE_LABEL

    def __init__(self, prior_games: float = SECONDARY_PRIOR_GAMES) -> None:
        self.prior_games = float(prior_games)
        self.prior_rate60_: float = 0.30
        self.k_hours_: float = 10.0
        self.alpha_: float = 0.05

    def fit(self, train_df: pd.DataFrame) -> "SecondaryAssistsModel":
        df = train_df[train_df["secondary_assists"].notna()].copy()
        if df.empty:
            raise ValueError("Need non-empty training rows for SecondaryAssistsModel")
        y = pd.to_numeric(df["secondary_assists"], errors="coerce").fillna(0.0).to_numpy(float)
        toi_min = np.maximum(pd.to_numeric(df["exp_toi_min"], errors="coerce").fillna(DEFAULT_TOI_MIN).to_numpy(float), 1.0)
        toi_hrs = np.maximum(toi_min / 60.0, MIN_HOURS)
        tot_hrs = float(toi_hrs.sum())
        self.prior_rate60_ = float(y.sum() / tot_hrs) if tot_hrs > 0 else 0.30
        mean_hrs_per_game = float(toi_hrs.mean()) if len(toi_hrs) else (DEFAULT_TOI_MIN / 60.0)
        self.k_hours_ = max(self.prior_games * mean_hrs_per_game, 1.0)

        mu_tr = self.predict_mu(df)
        self.alpha_ = fit_alpha_grid(y, mu_tr)
        return self

    def predict_mu(self, features_df: pd.DataFrame) -> np.ndarray:
        prior_sec = pd.to_numeric(features_df.get("prior_sec_sum", 0.0), errors="coerce").fillna(0.0).to_numpy(float)
        prior_hrs = pd.to_numeric(features_df.get("prior_toi_hours", 0.0), errors="coerce").fillna(0.0).to_numpy(float)
        shrunk_rate60 = (prior_sec + self.prior_rate60_ * self.k_hours_) / (prior_hrs + self.k_hours_)
        toi_min = np.maximum(
            pd.to_numeric(features_df["exp_toi_min"], errors="coerce").fillna(DEFAULT_TOI_MIN).to_numpy(float),
            1.0,
        )
        return np.maximum(shrunk_rate60 * (toi_min / 60.0), 1e-6)

    def predict_pmf(self, features_df: pd.DataFrame, kmax: int = 20) -> tuple[np.ndarray, np.ndarray, float]:
        mu = self.predict_mu(features_df)
        return nb_pmf(mu, self.alpha_, kmax=kmax), mu, float(self.alpha_)


def _prior_nhl_panel(
    history_with_split: pd.DataFrame,
    shots: Optional[pd.DataFrame],
    goalies: Optional[pd.DataFrame],
) -> pd.DataFrame:
    sort_cols = ["player_id", "date", "game_id"] if "game_id" in history_with_split.columns else ["player_id", "date"]
    df = history_with_split.sort_values(sort_cols, kind="mergesort").copy()
    if "toi_sec" in df.columns:
        df["toi_min"] = pd.to_numeric(df["toi_sec"], errors="coerce") / 60.0
    elif "toi_min" not in df.columns:
        df["toi_min"] = DEFAULT_TOI_MIN

    for c in ("primary_assists", "secondary_assists", "assists", "sog"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = np.nan

    grp = df.groupby("player_id", sort=False)
    df["games_played"] = grp.cumcount()

    for col in ("toi_min", "primary_assists", "secondary_assists", "assists", "sog"):
        sh = grp[col].shift(1)
        df[f"{col}_l5"] = sh.groupby(df["player_id"], sort=False).transform(lambda s: s.rolling(5, min_periods=1).mean())
        df[f"{col}_l15"] = sh.groupby(df["player_id"], sort=False).transform(lambda s: s.rolling(15, min_periods=1).mean())

    for w in (5, 15):
        toi = df[f"toi_min_l{w}"]
        df[f"prim_per60_l{w}"] = np.where(toi > 0, 60.0 * df[f"primary_assists_l{w}"] / toi, np.nan)
        df[f"sec_per60_l{w}"] = np.where(toi > 0, 60.0 * df[f"secondary_assists_l{w}"] / toi, np.nan)
        df[f"ast_per60_l{w}"] = np.where(toi > 0, 60.0 * df[f"assists_l{w}"] / toi, np.nan)

    df["sog_per60_l15"] = np.where(df["toi_min_l15"] > 0, 60.0 * df["sog_l15"] / df["toi_min_l15"], np.nan)
    t5, t15 = df["toi_min_l5"], df["toi_min_l15"]
    exp_toi = np.where(np.isfinite(t5) & np.isfinite(t15), 0.5 * t5 + 0.5 * t15, np.where(np.isfinite(t15), t15, t5))
    df["exp_toi_min"] = np.where(np.isfinite(exp_toi) & (exp_toi > 0), exp_toi, DEFAULT_TOI_MIN)

    cum_sec = grp["secondary_assists"].cumsum() - df["secondary_assists"].fillna(0.0)
    hrs = df["toi_min"].fillna(0.0) / 60.0
    cum_hrs = hrs.groupby(df["player_id"], sort=False).cumsum() - hrs
    df["prior_sec_sum"] = cum_sec
    df["prior_toi_hours"] = cum_hrs

    df["linemate_xg_rate"] = np.nan
    df["pp1_flag"] = np.nan
    df["opp_goalie_sv_above_exp"] = np.nan
    return df


def walk_forward_compare(
    history_with_split: pd.DataFrame,
    fold_dates: list[str],
    *,
    shots: Optional[pd.DataFrame] = None,
    lineups: Optional[pd.DataFrame] = None,
    goalies: Optional[pd.DataFrame] = None,
    min_prior_games: int = 1,
) -> dict:
    """Compare split-convolved (primary GBDT + secondary EB) vs unified GBDT on identical fold rows.

    Returns mean NegBin negative log-likelihood (log-loss, lower is better) and
    `recommendation`: `"KEEP UNIFIED"` if `split_ll >= unified_ll`, else `"ADOPT SPLIT"`.
    """
    hist = history_with_split.copy()
    hist["date"] = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d")
    panel = _prior_nhl_panel(hist, shots, goalies)

    fold_rows = []
    used_folds = []
    split_nlls = []
    unified_nlls = []
    primary_nlls = []
    secondary_nlls = []

    for fdate in sorted(str(d)[:10] for d in fold_dates):
        tr = panel[(panel["date"] < fdate) & (panel["games_played"] >= min_prior_games) & panel["assists"].notna()]
        te_mask = hist["date"] == fdate
        if len(tr) < 20 or not te_mask.any():
            continue

        te_raw = hist.loc[te_mask].copy()
        slate_cols = [c for c in ("player_id", "team_id", "team_abbr", "team", "opp_id", "opp_abbr", "opponent", "game_id") if c in te_raw.columns]
        te_feat = build_nhl_assist_features(
            fdate,
            f"{fdate}T23:59:59Z",
            slate=te_raw[slate_cols].copy(),
            history_with_split=hist,
            shots=shots,
            lineups=lineups,
            goalies=goalies,
        )
        te_feat["y_total"] = pd.to_numeric(te_raw["assists"], errors="coerce").to_numpy(float)
        te_feat["y_prim"] = pd.to_numeric(te_raw["primary_assists"], errors="coerce").to_numpy(float)
        te_feat["y_sec"] = pd.to_numeric(te_raw["secondary_assists"], errors="coerce").to_numpy(float)
        te_feat = te_feat[te_feat["y_total"].notna() & (te_feat["games_played"] >= min_prior_games)].copy()
        if te_feat.empty:
            continue

        prim_m = PrimaryAssistsModel().fit(tr)
        sec_m = SecondaryAssistsModel().fit(tr)
        uni_m = UnifiedAssistsModel().fit(tr)

        pmf_p, mu_p, alpha_p = prim_m.predict_pmf(te_feat, kmax=15)
        pmf_s, mu_s, alpha_s = sec_m.predict_pmf(te_feat, kmax=15)
        pmf_conv = convolve_pmf(pmf_p, pmf_s)
        mu_u = uni_m.predict_mu(te_feat)
        alpha_u = float(uni_m.alpha_)

        y_tot = te_feat["y_total"].to_numpy(int)
        y_prim = te_feat["y_prim"].to_numpy(float)
        y_sec = te_feat["y_sec"].to_numpy(float)

        k_idx = np.clip(y_tot, 0, pmf_conv.shape[1] - 1)
        p_conv_y = np.clip(pmf_conv[np.arange(len(y_tot)), k_idx], 1e-12, 1.0)
        fold_split_nll = -np.log(p_conv_y)

        split_nlls.extend(fold_split_nll.tolist())
        # Unified NLL per row via nb_loglik on singletons or batch:
        uni_fold_ll = -nb_loglik(y_tot, mu_u, alpha_u)
        prim_fold_ll = -nb_loglik(y_prim, mu_p, alpha_p)
        sec_fold_ll = -nb_loglik(y_sec, mu_s, alpha_s)
        unified_nlls.extend([uni_fold_ll] * len(y_tot))
        primary_nlls.extend([prim_fold_ll] * len(y_tot))
        secondary_nlls.extend([sec_fold_ll] * len(y_tot))

        fold_rows.append(pd.DataFrame({
            "date": fdate,
            "player_id": te_feat["player_id"].to_numpy(),
            "y": y_tot,
            "mu_split": mu_p + mu_s,
            "mu_unified": mu_u,
            "alpha_unified": alpha_u,
            "split_nll": fold_split_nll,
        }))
        used_folds.append(fdate)

    if not fold_rows:
        return {
            "split_ll": float("nan"),
            "unified_ll": float("nan"),
            "primary_ll": float("nan"),
            "secondary_ll": float("nan"),
            "n_rows": 0,
            "folds": [],
            "recommendation": "KEEP UNIFIED",
            "rows": pd.DataFrame(),
        }

    split_ll = float(np.mean(split_nlls))
    unified_ll = float(np.mean(unified_nlls))
    recommendation = "KEEP UNIFIED" if split_ll >= unified_ll else "ADOPT SPLIT"

    return {
        "split_ll": split_ll,
        "unified_ll": unified_ll,
        "primary_ll": float(np.mean(primary_nlls)),
        "secondary_ll": float(np.mean(secondary_nlls)),
        "n_rows": int(len(split_nlls)),
        "folds": used_folds,
        "recommendation": recommendation,
        "rows": pd.concat(fold_rows, ignore_index=True),
    }
