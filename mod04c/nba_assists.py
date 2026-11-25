"""C2 NBA assists creation x conversion head (`mod04_nba_assists_shadow_v1`).

Structural decomposition:
    mu = exp_possessions * creation_rate_pred * teammate_conv_rate

Where:
  - `creation_rate_pred` is predicted by an XGBoost (`count:poisson`) GBDT trained
    on single-pass pseudo-potential assists per possession:
        pseudo_potential = assists / max(teammate_conv_rate, conv_floor)
        target_rate      = pseudo_potential / max(exp_possessions, 1.0)
    weighted by `exp_possessions`. (Single-iteration approximation: `teammate_conv_rate`
    is held fixed from prior team eFG/zone efficiency rather than iterated EM.)
  - `teammate_conv_rate` is the shrunk team conversion probability derived from
    prior shots (expected eFG% by zone) or prior team boxscore eFG% relative to
    the league prior.
  - Dispersion `alpha` is fit on time-ordered out-of-fold validation predictions
    using `dist_negbin.fit_alpha_grid` (no random shuffling across dates).
  - Prop lines and odds are strictly banned as features.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from mod04c.dist_negbin import fit_alpha_grid, nb_pmf
from mod04c.feature_store import build_pregame_features, filter_history, validate_feature_frame
from nba.props.config import POS_GROUP
from nhl.props.negbin import nb_loglik

ENGINE_LABEL = "mod04_nba_assists_shadow_v1"

DEFAULT_LEAGUE_EFG = 0.535
DEFAULT_BASE_CONV = 0.520
CONV_FLOOR = 0.15
CREATION_PRIOR_K = 250.0  # prior possessions strength for EB creation-rate shrinkage

GAME_ENV_FEATURES = [
    "team_pace_l15",
    "spread",
    "total",
]

CORE_FEATURES = [
    "games_played",
    "minutes_l5",
    "minutes_l15",
    "assists_l5",
    "assists_l15",
    "turnovers_l5",
    "turnovers_l15",
    "ast_per36_l5",
    "ast_per36_l15",
    "tov_per36_l5",
    "tov_per36_l15",
    "ball_dominance_l5",
    "ball_dominance_l15",
    "ast_to_tov_l5",
    "ast_to_tov_l15",
    "creation_rate_eb",
    "teammate_conv_rate",
    "vacated_handler_minutes",
    "finisher_fga_share_available",
    "exp_possessions",
]

BANNED_FEATURE_TOKENS = frozenset({
    "line", "prop_line", "odds", "over_odds", "under_odds", "market_p", "p_market",
})

XGB_PARAMS = {
    "objective": "count:poisson",
    "eval_metric": "poisson-nloglik",
    "tree_method": "hist",
    "max_depth": 3,
    "eta": 0.04,
    "min_child_weight": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 5.0,
    "seed": 7,
    "nthread": 4,
}


def eb_shrink(rate, n, prior, k: float):
    """Empirical-Bayes shrinkage: (rate * n + prior * k) / (n + k)."""
    scalar = np.ndim(rate) == 0 and np.ndim(n) == 0 and np.ndim(prior) == 0
    r = np.asarray(rate, dtype=float)
    cnt = np.asarray(n, dtype=float)
    pr = np.asarray(prior, dtype=float)
    cnt = np.where(np.isfinite(cnt) & (cnt > 0.0), cnt, 0.0)
    r_safe = np.where(np.isfinite(r), r, pr)
    out = (r_safe * cnt + pr * float(k)) / (cnt + float(k))
    return float(out) if scalar else out


def _parse_minutes_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")

    def _one(val):
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return np.nan
        txt = str(val).strip()
        if not txt:
            return np.nan
        if ":" in txt:
            parts = txt.split(":")
            try:
                return float(parts[0]) + float(parts[1]) / 60.0
            except ValueError:
                return np.nan
        try:
            return float(txt)
        except ValueError:
            return np.nan

    return s.map(_one).astype(float)


def _team_col(df: pd.DataFrame) -> Optional[str]:
    for c in ("team", "team_abbr", "team_id"):
        if c in df.columns:
            return c
    return None


def _pos_group_series(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.upper().map(lambda x: POS_GROUP.get(x, "ALL"))


def _compute_team_pace(prior: pd.DataFrame, team_col: str) -> pd.Series:
    """Estimate rolling L15 team possessions per game from strictly-prior boxscores."""
    needed = {"field_goals_attempted", "turnovers"}
    if prior.empty or not needed.issubset(prior.columns) or "game_id" not in prior.columns:
        return pd.Series(dtype=float)

    agg_cols = {"field_goals_attempted": "sum", "turnovers": "sum"}
    if "free_throws_attempted" in prior.columns:
        agg_cols["free_throws_attempted"] = "sum"
    if "rebounds_offensive" in prior.columns:
        agg_cols["rebounds_offensive"] = "sum"

    tg = prior.groupby(["game_id", team_col, "date"], as_index=False).agg(agg_cols)
    fta = tg["free_throws_attempted"] if "free_throws_attempted" in tg.columns else 0.0
    orb = tg["rebounds_offensive"] if "rebounds_offensive" in tg.columns else 0.0
    tg["_poss"] = tg["field_goals_attempted"] + 0.44 * fta - orb + tg["turnovers"]
    tg = tg.sort_values([team_col, "date", "game_id"], kind="mergesort")
    l15 = tg.groupby(team_col, sort=False).tail(15).groupby(team_col)["_poss"].mean()
    return l15


def _compute_team_efg_from_shots(prior_shots: pd.DataFrame) -> tuple[dict, float]:
    """Expected eFG% by zone from strictly-prior shots frame."""
    if prior_shots is None or prior_shots.empty:
        return {}, DEFAULT_LEAGUE_EFG

    sh = prior_shots.copy()
    tcol = _team_col(sh)
    if tcol is None or "made" not in sh.columns:
        return {}, DEFAULT_LEAGUE_EFG

    sh["_made"] = sh["made"].astype(float)
    sh["_is3"] = sh["three"].astype(float) if "three" in sh.columns else 0.0
    sh["_efg_pts"] = sh["_made"] * (1.0 + 0.5 * sh["_is3"])
    league_efg = float(sh["_efg_pts"].mean()) if len(sh) else DEFAULT_LEAGUE_EFG
    if not np.isfinite(league_efg) or league_efg <= 0:
        league_efg = DEFAULT_LEAGUE_EFG

    if "zone" in sh.columns:
        lg_zone = sh.groupby("zone")[" _efg_pts".strip()].mean().to_dict()
        team_zone = sh.groupby([tcol, "zone"]).agg(n=("_efg_pts", "size"), efg=("_efg_pts", "mean")).reset_index()
        out = {}
        for team_val, grp in team_zone.groupby(tcol):
            w = grp["n"].to_numpy(float)
            z_efg = grp["efg"].to_numpy(float)
            z_prior = np.array([lg_zone.get(z, league_efg) for z in grp["zone"]], dtype=float)
            shrunk = eb_shrink(z_efg, w, z_prior, k=80.0)
            out[team_val] = float(np.sum(w * shrunk) / np.maximum(np.sum(w), 1.0))
        return out, league_efg

    team_agg = sh.groupby(tcol).agg(n=("_efg_pts", "size"), efg=("_efg_pts", "mean"))
    out = {
        t: float(eb_shrink(row["efg"], row["n"], league_efg, k=200.0))
        for t, row in team_agg.iterrows()
    }
    return out, league_efg


def _compute_team_efg_from_history(prior: pd.DataFrame, team_col: str) -> tuple[dict, float]:
    req = {"field_goals_made", "field_goals_attempted"}
    if prior.empty or not req.issubset(prior.columns):
        return {}, DEFAULT_LEAGUE_EFG
    fgm = pd.to_numeric(prior["field_goals_made"], errors="coerce").fillna(0.0)
    fga = pd.to_numeric(prior["field_goals_attempted"], errors="coerce").fillna(0.0)
    tpm = pd.to_numeric(prior["three_points_made"], errors="coerce").fillna(0.0) if "three_points_made" in prior.columns else 0.0
    tot_fga = float(fga.sum())
    if tot_fga <= 0:
        return {}, DEFAULT_LEAGUE_EFG
    league_efg = float((fgm + 0.5 * tpm).sum() / tot_fga)
    df_tmp = pd.DataFrame({team_col: prior[team_col], "pts_fg": fgm + 0.5 * tpm, "fga": fga})
    agg = df_tmp.groupby(team_col).sum()
    out = {}
    for t, row in agg.iterrows():
        if row["fga"] > 0:
            out[t] = float(eb_shrink(row["pts_fg"] / row["fga"], row["fga"], league_efg, k=300.0))
    return out, league_efg


def _compute_vacated_usage(
    slate: pd.DataFrame,
    prior: pd.DataFrame,
    injuries: Optional[pd.DataFrame],
    slate_tcol: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute separate `vacated_handler_minutes` and `finisher_fga_share_available`."""
    n = len(slate)
    if injuries is None or injuries.empty or prior.empty:
        return np.full(n, np.nan), np.full(n, np.nan)

    inj = injuries.copy()
    inj_tcol = _team_col(inj) or ("team_abbrev" if "team_abbrev" in inj.columns else None)
    if inj_tcol is None or "status" not in inj.columns:
        return np.full(n, np.nan), np.full(n, np.nan)

    p = prior.copy()
    p_tcol = _team_col(p)
    if p_tcol is None:
        return np.full(n, np.nan), np.full(n, np.nan)
    p["_min"] = _parse_minutes_series(p["minutes"]) if "minutes" in p.columns else np.nan
    p = p.sort_values(["player_id", "date"], kind="mergesort")
    l15 = p.groupby("player_id", sort=False).tail(15)
    p_agg = l15.groupby("player_id").agg(
        team=(p_tcol, "last"),
        min_l15=("_min", "mean"),
        ast_l15=("assists", "mean"),
        fga_l15=("field_goals_attempted", "mean") if "field_goals_attempted" in l15.columns else ("assists", lambda s: 0.0),
    ).reset_index()
    p_agg["ast36"] = np.where(p_agg["min_l15"] > 0, 36.0 * p_agg["ast_l15"] / p_agg["min_l15"], 0.0)
    p_agg["is_handler"] = p_agg["ast36"] >= 4.0

    out_mask = inj["status"].astype(str).str.strip().str.lower().isin({"out", "o", "suspended"})
    out_inj = inj.loc[out_mask]
    out_pids = set(pd.to_numeric(out_inj["player_id"], errors="coerce").dropna().astype(int).tolist()) if "player_id" in out_inj.columns else set()
    out_names = set(out_inj["player"].astype(str).str.strip().str.lower().tolist()) if "player" in out_inj.columns else set()

    vac_handler_mins = np.zeros(n, dtype=float)
    finisher_avail = np.ones(n, dtype=float)
    for i, row in enumerate(slate.itertuples(index=False)):
        tm = getattr(row, slate_tcol)
        tm_players = p_agg[p_agg["team"] == tm]
        if tm_players.empty:
            vac_handler_mins[i] = np.nan
            finisher_avail[i] = np.nan
            continue
        is_out = tm_players["player_id"].isin(out_pids)
        if out_names and "name" in prior.columns:
            # optional name match fallback not required when player_id present
            pass
        vac_handler_mins[i] = float(tm_players.loc[is_out & tm_players["is_handler"], "min_l15"].fillna(0.0).sum())
        tot_fga = float(tm_players["fga_l15"].fillna(0.0).sum())
        avail_fga = float(tm_players.loc[~is_out, "fga_l15"].fillna(0.0).sum())
        finisher_avail[i] = avail_fga / tot_fga if tot_fga > 0 else 1.0
    return vac_handler_mins, finisher_avail


def build_nba_assist_features(
    target_date,
    cutoff_utc,
    *,
    slate: pd.DataFrame,
    history: pd.DataFrame,
    shots: Optional[pd.DataFrame] = None,
    injuries: Optional[pd.DataFrame] = None,
    base_conv: float = DEFAULT_BASE_CONV,
    include_game_env: bool = True,
) -> pd.DataFrame:
    """Build strictly-prior NBA assist features via `feature_store.build_pregame_features`."""
    hist = history.copy()
    if "minutes" in hist.columns:
        hist["minutes"] = _parse_minutes_series(hist["minutes"])
    base = build_pregame_features(target_date, cutoff_utc, slate=slate, history=hist)
    prior = filter_history(hist, target_date)
    prior_shots = filter_history(shots, target_date) if shots is not None else None

    for stat in ("minutes", "assists", "turnovers"):
        for w in (5, 15):
            col = f"{stat}_l{w}"
            if col not in base.columns:
                base[col] = np.nan

    for w in (5, 15):
        mins = pd.to_numeric(base[f"minutes_l{w}"], errors="coerce")
        ast = pd.to_numeric(base[f"assists_l{w}"], errors="coerce")
        tov = pd.to_numeric(base[f"turnovers_l{w}"], errors="coerce")
        ast36 = np.where(mins > 0, 36.0 * ast / mins, np.nan)
        tov36 = np.where(mins > 0, 36.0 * tov / mins, np.nan)
        base[f"ast_per36_l{w}"] = ast36
        base[f"tov_per36_l{w}"] = tov36
        base[f"ball_dominance_l{w}"] = ast36 + tov36
        base[f"ast_to_tov_l{w}"] = np.where(
            np.isfinite(ast) & np.isfinite(tov),
            ast / np.maximum(tov, 0.5),
            np.nan,
        )

    # Team pace (from strictly-prior history)
    hist_tcol = _team_col(prior)
    slate_tcol = _team_col(base)
    team_pace_map = _compute_team_pace(prior, hist_tcol) if hist_tcol else pd.Series(dtype=float)
    if slate_tcol and not team_pace_map.empty:
        base["team_pace_l15"] = base[slate_tcol].map(team_pace_map).astype(float)
    else:
        base["team_pace_l15"] = np.nan

    if "spread" not in base.columns:
        base["spread"] = np.nan
    if "total" not in base.columns:
        base["total"] = np.nan

    # Expected possessions
    m5 = pd.to_numeric(base["minutes_l5"], errors="coerce")
    m15 = pd.to_numeric(base["minutes_l15"], errors="coerce")
    exp_min = np.where(np.isfinite(m5) & np.isfinite(m15), 0.6 * m5 + 0.4 * m15, np.where(np.isfinite(m15), m15, m5))
    exp_min = np.where(np.isfinite(exp_min) & (exp_min > 0), exp_min, 20.0)
    pace = np.where(np.isfinite(base["team_pace_l15"]) & (base["team_pace_l15"] > 0), base["team_pace_l15"], 98.0)
    base["exp_possessions"] = np.maximum(exp_min * (pace / 48.0), 1.0)

    # Hierarchical EB creation rate per possession
    if not prior.empty and "player_id" in prior.columns and "assists" in prior.columns:
        p_df = prior.copy()
        p_df["_min"] = _parse_minutes_series(p_df["minutes"]) if "minutes" in p_df.columns else 24.0
        p_df["_pace"] = p_df[hist_tcol].map(team_pace_map).fillna(98.0) if (hist_tcol and not team_pace_map.empty) else 98.0
        p_df["_poss"] = np.maximum(p_df["_min"].fillna(0.0) * (p_df["_pace"] / 48.0), 0.0)
        p_df["_ast"] = pd.to_numeric(p_df["assists"], errors="coerce").fillna(0.0)
        p_df["_pos_grp"] = _pos_group_series(p_df["position"]) if "position" in p_df.columns else "ALL"

        lg_poss = float(p_df["_poss"].sum())
        lg_rate = float(p_df["_ast"].sum() / lg_poss) if lg_poss > 0 else 0.06
        pos_agg = p_df.groupby("_pos_grp").agg(ast=("_ast", "sum"), poss=("_poss", "sum"))
        pos_prior_map = {
            pg: float(eb_shrink(row["ast"] / row["poss"] if row["poss"] > 0 else lg_rate, row["poss"], lg_rate, k=500.0))
            for pg, row in pos_agg.iterrows()
        }

        pl_agg = p_df.groupby("player_id").agg(
            ast=("_ast", "sum"),
            poss=("_poss", "sum"),
            pos_grp=("_pos_grp", "last"),
        )
        pl_rate = np.where(pl_agg["poss"] > 0, pl_agg["ast"] / pl_agg["poss"], lg_rate)
        pl_prior = pl_agg["pos_grp"].map(lambda g: pos_prior_map.get(g, lg_rate)).to_numpy(float)
        pl_agg["creation_rate_eb"] = eb_shrink(pl_rate, pl_agg["poss"].to_numpy(float), pl_prior, k=CREATION_PRIOR_K)

        base["creation_rate_eb"] = base["player_id"].map(pl_agg["creation_rate_eb"])
        if "position" in base.columns:
            slate_pg = _pos_group_series(base["position"]).map(lambda g: pos_prior_map.get(g, lg_rate))
        else:
            slate_pg = lg_rate
        base["creation_rate_eb"] = base["creation_rate_eb"].fillna(slate_pg).astype(float)
    else:
        base["creation_rate_eb"] = 0.06

    # Teammate conversion rate (shots zone eFG preferred, else prior team boxscore eFG)
    if prior_shots is not None and not prior_shots.empty:
        team_efg_map, league_efg = _compute_team_efg_from_shots(prior_shots)
    elif hist_tcol:
        team_efg_map, league_efg = _compute_team_efg_from_history(prior, hist_tcol)
    else:
        team_efg_map, league_efg = {}, DEFAULT_LEAGUE_EFG

    if slate_tcol and team_efg_map:
        tm_efg = base[slate_tcol].map(team_efg_map).fillna(league_efg).astype(float)
    else:
        tm_efg = pd.Series(league_efg, index=base.index, dtype=float)
    base["teammate_efg_prior"] = tm_efg
    base["league_efg_prior"] = float(league_efg)
    base["teammate_conv_rate"] = np.clip(float(base_conv) * (tm_efg / max(float(league_efg), 1e-6)), 0.25, 0.85)

    # Vacated usage (handler minutes vs finisher FGA share kept separate)
    if slate_tcol:
        vac_h, fin_avail = _compute_vacated_usage(base, prior, injuries, slate_tcol)
    else:
        vac_h, fin_avail = np.full(len(base), np.nan), np.full(len(base), np.nan)
    base["vacated_handler_minutes"] = vac_h
    base["finisher_fga_share_available"] = fin_avail

    if not include_game_env:
        for c in GAME_ENV_FEATURES:
            if c in base.columns:
                base[c] = np.nan

    return validate_feature_frame(base)


def model_feature_columns(include_game_env: bool = True) -> list[str]:
    cols = list(CORE_FEATURES)
    if include_game_env:
        cols.extend(GAME_ENV_FEATURES)
    for c in cols:
        if c.lower() in BANNED_FEATURE_TOKENS:
            raise ValueError(f"Banned market feature column configured: {c}")
    return cols


class CreationConversionAssistsModel:
    """Two-stage NBA assists model: creation rate (XGBoost Poisson) x teammate conversion + OOF NegBin alpha."""

    def __init__(
        self,
        *,
        include_game_env: bool = True,
        params: Optional[dict] = None,
        unified: bool = False,
    ) -> None:
        self.include_game_env = include_game_env
        self.unified = unified
        self.params = {**XGB_PARAMS, **(params or {})}
        self.features = model_feature_columns(include_game_env=include_game_env)
        self.booster_: Optional[xgb.Booster] = None
        self.best_iteration_: int = 50
        self.alpha_: float = 0.05
        self.base_conv_: float = DEFAULT_BASE_CONV

    def _dmatrix(self, df: pd.DataFrame, y: Optional[np.ndarray] = None, w: Optional[np.ndarray] = None) -> xgb.DMatrix:
        validate_feature_frame(df)
        X = df.reindex(columns=self.features).apply(pd.to_numeric, errors="coerce").astype("float32")
        return xgb.DMatrix(X, label=y, weight=w, feature_names=self.features)

    def fit(self, train_df: pd.DataFrame, y_col: str = "assists", val_frac: float = 0.20) -> "CreationConversionAssistsModel":
        """Time-ordered fit: earliest (1-val_frac) dates train, latest val_frac dates fit early stopping + OOF alpha."""
        df = train_df[train_df[y_col].notna()].copy()
        df = df.sort_values(["date", "player_id"], kind="mergesort").reset_index(drop=True)
        if len(df) < 10:
            raise ValueError(f"Need at least 10 training rows, got {len(df)}")

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

        # Calibrate base_conv_ on training split only
        if not self.unified and "potential_assists" in tr_df.columns:
            pot = pd.to_numeric(tr_df["potential_assists"], errors="coerce")
            ast = pd.to_numeric(tr_df[y_col], errors="coerce")
            valid = pot.notna() & (pot > 0) & ast.notna()
            if valid.any() and float(pot[valid].sum()) > 0:
                self.base_conv_ = float(np.clip(ast[valid].sum() / pot[valid].sum(), 0.30, 0.75))

        def _prep_targets(part: pd.DataFrame):
            y_raw = pd.to_numeric(part[y_col], errors="coerce").fillna(0.0).to_numpy(float)
            exp_poss = np.maximum(pd.to_numeric(part["exp_possessions"], errors="coerce").fillna(40.0).to_numpy(float), 1.0)
            conv = np.clip(pd.to_numeric(part["teammate_conv_rate"], errors="coerce").fillna(self.base_conv_).to_numpy(float), CONV_FLOOR, 0.95)
            if self.unified:
                return y_raw / exp_poss, exp_poss, conv, y_raw
            pseudo_pot = y_raw / conv
            return pseudo_pot / exp_poss, exp_poss, conv, y_raw

        y_tr_rate, w_tr, _, _ = _prep_targets(tr_df)
        y_va_rate, w_va, conv_va, y_va_raw = _prep_targets(va_df)

        dtr = self._dmatrix(tr_df, y=y_tr_rate, w=w_tr)
        dva = self._dmatrix(va_df, y=y_va_rate, w=w_va)
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
        mu_va = w_va * rate_va if self.unified else w_va * rate_va * conv_va
        self.alpha_ = fit_alpha_grid(y_va_raw, mu_va)

        y_all_rate, w_all, _, _ = _prep_targets(df)
        self.booster_ = xgb.train(self.params, self._dmatrix(df, y=y_all_rate, w=w_all), num_boost_round=best)
        return self

    def predict_mu(self, features_df: pd.DataFrame) -> np.ndarray:
        if self.booster_ is None:
            raise RuntimeError("Model must be fit before calling predict_mu")
        rate = np.maximum(
            self.booster_.predict(self._dmatrix(features_df), iteration_range=(0, self.best_iteration_)),
            1e-5,
        )
        exp_poss = np.maximum(
            pd.to_numeric(features_df["exp_possessions"], errors="coerce").fillna(40.0).to_numpy(float),
            1.0,
        )
        if self.unified:
            return np.maximum(exp_poss * rate, 1e-6)
        conv = np.clip(
            pd.to_numeric(features_df["teammate_conv_rate"], errors="coerce").fillna(self.base_conv_).to_numpy(float),
            CONV_FLOOR,
            0.95,
        )
        return np.maximum(exp_poss * rate * conv, 1e-6)

    def predict_pmf(self, features_df: pd.DataFrame, kmax: int = 40) -> tuple[np.ndarray, np.ndarray, float]:
        mu = self.predict_mu(features_df)
        pmf = nb_pmf(mu, self.alpha_, kmax=kmax)
        return pmf, mu, float(self.alpha_)


def _prior_history_panel(history: pd.DataFrame, shots: Optional[pd.DataFrame], include_game_env: bool) -> pd.DataFrame:
    """Build strictly-prior training features for historical rows using shift(1) within player."""
    df = history.sort_values(["player_id", "date", "game_id"] if "game_id" in history.columns else ["player_id", "date"], kind="mergesort").copy()
    df["minutes"] = _parse_minutes_series(df["minutes"]) if "minutes" in df.columns else 24.0
    df["assists"] = pd.to_numeric(df["assists"], errors="coerce")
    df["turnovers"] = pd.to_numeric(df["turnovers"], errors="coerce") if "turnovers" in df.columns else np.nan

    grp = df.groupby("player_id", sort=False)
    df["games_played"] = grp.cumcount()
    for stat in ("minutes", "assists", "turnovers"):
        sh = grp[stat].shift(1)
        df[f"{stat}_l5"] = sh.groupby(df["player_id"], sort=False).transform(lambda s: s.rolling(5, min_periods=1).mean())
        df[f"{stat}_l15"] = sh.groupby(df["player_id"], sort=False).transform(lambda s: s.rolling(15, min_periods=1).mean())

    for w in (5, 15):
        m = df[f"minutes_l{w}"]
        a = df[f"assists_l{w}"]
        t = df[f"turnovers_l{w}"]
        a36 = np.where(m > 0, 36.0 * a / m, np.nan)
        t36 = np.where(m > 0, 36.0 * t / m, np.nan)
        df[f"ast_per36_l{w}"] = a36
        df[f"tov_per36_l{w}"] = t36
        df[f"ball_dominance_l{w}"] = a36 + t36
        df[f"ast_to_tov_l{w}"] = np.where(np.isfinite(a) & np.isfinite(t), a / np.maximum(t, 0.5), np.nan)

    tcol = _team_col(df)
    if tcol and {"field_goals_attempted", "turnovers"}.issubset(df.columns) and "game_id" in df.columns:
        fta = df["free_throws_attempted"] if "free_throws_attempted" in df.columns else 0.0
        orb = df["rebounds_offensive"] if "rebounds_offensive" in df.columns else 0.0
        df["_raw_poss"] = df["field_goals_attempted"].fillna(0.0) + 0.44 * pd.Series(fta).fillna(0.0) - pd.Series(orb).fillna(0.0) + df["turnovers"].fillna(0.0)
        tg = df.groupby(["game_id", tcol, "date"], as_index=False)["_raw_poss"].sum().sort_values([tcol, "date", "game_id"])
        tg["team_pace_l15"] = tg.groupby(tcol, sort=False)["_raw_poss"].shift(1).groupby(tg[tcol], sort=False).transform(lambda s: s.rolling(15, min_periods=1).mean())
        df = df.merge(tg[["game_id", tcol, "team_pace_l15"]], on=["game_id", tcol], how="left")
    else:
        df["team_pace_l15"] = np.nan

    if "spread" not in df.columns:
        df["spread"] = np.nan
    if "total" not in df.columns:
        df["total"] = np.nan

    m5, m15 = df["minutes_l5"], df["minutes_l15"]
    exp_min = np.where(np.isfinite(m5) & np.isfinite(m15), 0.6 * m5 + 0.4 * m15, np.where(np.isfinite(m15), m15, m5))
    exp_min = np.where(np.isfinite(exp_min) & (exp_min > 0), exp_min, 20.0)
    pace = np.where(np.isfinite(df["team_pace_l15"]) & (df["team_pace_l15"] > 0), df["team_pace_l15"], 98.0)
    df["exp_possessions"] = np.maximum(exp_min * (pace / 48.0), 1.0)

    # Expanding strictly-prior creation rate per possession
    pl_poss = np.maximum(df["minutes"].fillna(0.0) * (pace / 48.0), 0.0)
    df["_p_poss"] = pl_poss
    cum_ast = df.groupby("player_id", sort=False)["assists"].cumsum() - df["assists"].fillna(0.0)
    cum_poss = df.groupby("player_id", sort=False)["_p_poss"].cumsum() - df["_p_poss"]
    lg_rate = 0.06
    df["creation_rate_eb"] = eb_shrink(
        np.where(cum_poss > 0, cum_ast / cum_poss, lg_rate),
        cum_poss.to_numpy(float),
        lg_rate,
        k=CREATION_PRIOR_K,
    )

    # Strictly-prior team eFG from boxscores (expanding shift(1) per team)
    if tcol and {"field_goals_made", "field_goals_attempted"}.issubset(df.columns) and "game_id" in df.columns:
        tpm = df["three_points_made"].fillna(0.0) if "three_points_made" in df.columns else 0.0
        df["_fg_pts"] = df["field_goals_made"].fillna(0.0) + 0.5 * tpm
        df["_fga"] = df["field_goals_attempted"].fillna(0.0)
        t_efg = df.groupby(["game_id", tcol, "date"], as_index=False)[["_fg_pts", "_fga"]].sum().sort_values([tcol, "date", "game_id"])
        c_pts = t_efg.groupby(tcol)["_fg_pts"].cumsum() - t_efg["_fg_pts"]
        c_fga = t_efg.groupby(tcol)["_fga"].cumsum() - t_efg["_fga"]
        tm_efg = eb_shrink(np.where(c_fga > 0, c_pts / c_fga, DEFAULT_LEAGUE_EFG), c_fga.to_numpy(float), DEFAULT_LEAGUE_EFG, k=300.0)
        t_efg["teammate_conv_rate"] = np.clip(DEFAULT_BASE_CONV * (tm_efg / DEFAULT_LEAGUE_EFG), 0.25, 0.85)
        df = df.merge(t_efg[["game_id", tcol, "teammate_conv_rate"]], on=["game_id", tcol], how="left")
    else:
        df["teammate_conv_rate"] = DEFAULT_BASE_CONV

    df["vacated_handler_minutes"] = np.nan
    df["finisher_fga_share_available"] = np.nan

    if not include_game_env:
        for c in GAME_ENV_FEATURES:
            df[c] = np.nan
    return df


def walk_forward(
    history: pd.DataFrame,
    fold_dates: list[str],
    *,
    shots: Optional[pd.DataFrame] = None,
    injuries: Optional[pd.DataFrame] = None,
    include_game_env: bool = True,
    min_prior_games: int = 1,
) -> dict:
    """Strictly time-ordered walk-forward evaluation + mechanism check vs EB and unified baselines.

    For each fold date `d`, trains models exclusively on `date < d`, scores rows
    on `date == d` using `build_nba_assist_features`, and returns per-fold rows plus
    aggregate NegBin log-loss (negative log-likelihood, lower is better).
    """
    hist = history.copy()
    hist["date"] = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d")
    panel = _prior_history_panel(hist, shots, include_game_env=include_game_env)

    all_preds = []
    used_folds = []
    for fdate in sorted(str(d)[:10] for d in fold_dates):
        tr_rows = panel[(panel["date"] < fdate) & (panel["games_played"] >= min_prior_games) & panel["assists"].notna()]
        te_mask = hist["date"] == fdate
        if len(tr_rows) < 20 or not te_mask.any():
            continue

        te_raw = hist.loc[te_mask].copy()
        slate_cols = [c for c in ("player_id", "team", "team_abbr", "position", "game_id", "spread", "total") if c in te_raw.columns]
        te_slate = te_raw[slate_cols].copy()
        te_feat = build_nba_assist_features(
            fdate,
            f"{fdate}T23:59:59Z",
            slate=te_slate,
            history=hist,
            shots=shots,
            injuries=injuries,
            include_game_env=include_game_env,
        )
        te_feat["y"] = pd.to_numeric(te_raw["assists"], errors="coerce").to_numpy(float)
        te_feat["date"] = fdate
        te_feat = te_feat[te_feat["y"].notna() & (te_feat["games_played"] >= min_prior_games)].copy()
        if te_feat.empty:
            continue

        cc_model = CreationConversionAssistsModel(include_game_env=include_game_env).fit(tr_rows, y_col="assists")
        uni_model = CreationConversionAssistsModel(include_game_env=include_game_env, unified=True).fit(tr_rows, y_col="assists")

        mu_cc = cc_model.predict_mu(te_feat)
        mu_uni = uni_model.predict_mu(te_feat)
        mu_eb = np.maximum(
            te_feat["exp_possessions"].to_numpy(float)
            * te_feat["creation_rate_eb"].to_numpy(float)
            * te_feat["teammate_conv_rate"].to_numpy(float),
            1e-6,
        )
        mu_eb_tr = np.maximum(
            tr_rows["exp_possessions"].to_numpy(float)
            * tr_rows["creation_rate_eb"].to_numpy(float)
            * tr_rows["teammate_conv_rate"].to_numpy(float),
            1e-6,
        )
        alpha_eb = fit_alpha_grid(tr_rows["assists"].to_numpy(float), mu_eb_tr)

        out_fold = pd.DataFrame({
            "date": fdate,
            "player_id": te_feat["player_id"].to_numpy(),
            "game_id": te_feat["game_id"].to_numpy() if "game_id" in te_feat.columns else np.nan,
            "y": te_feat["y"].to_numpy(float),
            "mu": mu_cc,
            "alpha": float(cc_model.alpha_),
            "mu_eb": mu_eb,
            "alpha_eb": float(alpha_eb),
            "mu_unified": mu_uni,
            "alpha_unified": float(uni_model.alpha_),
        })
        all_preds.append(out_fold)
        used_folds.append(fdate)

    if not all_preds:
        empty = pd.DataFrame(columns=["date", "player_id", "game_id", "y", "mu", "alpha", "mu_eb", "alpha_eb", "mu_unified", "alpha_unified"])
        return {
            "rows": empty,
            "model_nb_ll": float("nan"),
            "eb_nb_ll": float("nan"),
            "unified_nb_ll": float("nan"),
            "n_rows": 0,
            "folds": [],
        }

    res = pd.concat(all_preds, ignore_index=True)
    y = res["y"].to_numpy(float)

    def _mean_nll(mu_col: str, alpha_col: str) -> float:
        # Evaluate per-fold alpha cleanly
        lls = []
        for _, grp in res.groupby("date", sort=False):
            a = float(grp[alpha_col].iloc[0])
            lls.append(-nb_loglik(grp["y"].to_numpy(float), grp[mu_col].to_numpy(float), a) * len(grp))
        return float(sum(lls) / len(res))

    return {
        "rows": res,
        "model_nb_ll": _mean_nll("mu", "alpha"),
        "eb_nb_ll": _mean_nll("mu_eb", "alpha_eb"),
        "unified_nb_ll": _mean_nll("mu_unified", "alpha_unified"),
        "n_rows": int(len(res)),
        "folds": used_folds,
    }
