"""xG-aware rolling features for the NHL goalie-saves and skater goals/assists/points models.

LEAKAGE CONTRACT (pinned by tests/test_nhl_xg_features.py): every feature of a row (entity E, game G) is a function of E's
(or its team's / opponent's) games STRICTLY BEFORE G. Every rolling window is `shift(1)` first. The one deliberate piece of
same-game information is WHO STARTED in goal for the opposing team (`opp_g_*`): the starter is public before the puck
drops (and the saves market only exists for the expected starter), but it is a different input from "the outcome". The
opposing starter's own features are, as always, prior-game only.

NO market-derived feature (line, price, implied total, consensus) is used anywhere, so comparing the model with the market
is meaningful.

Frames are built from the masters (nhl/data/flatten.py schema) + the walk-forward xG tables (nhl.xg.build). xG columns are
the OUT-OF-SEASON values, so a row's xG never came from a model that saw its own season or later.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LG_SV = 0.905                  # league save% prior (on-target, non-empty-net); shrinkage target only
SV_PRIOR_SHOTS = 400.0         # prior strength in shots for save% / GSAx shrinkage
GAME_TYPES = (2, 3)


# ---------------------------------------------------------------------------------------------------------------------
# rolling primitives (all PRIOR-only)
# ---------------------------------------------------------------------------------------------------------------------
def prior_roll(df: pd.DataFrame, by: str, col: str, w: int, how: str = "mean", min_periods: int = 1) -> pd.Series:
    """Rolling `how` over the previous `w` rows of each `by` group. `df` must already be in chronological order within
    each group. The current row is never included."""
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda x: getattr(x.rolling(w, min_periods=min_periods), how)())


def prior_ewm(df: pd.DataFrame, by: str, col: str, halflife: float, min_periods: int = 1) -> pd.Series:
    sh = df.groupby(by, sort=False)[col].shift(1)
    return sh.groupby(df[by], sort=False).transform(lambda x: x.ewm(halflife=halflife, min_periods=min_periods).mean())


def prior_ratio(df: pd.DataFrame, by: str, num: str, den: str, w: int, prior_num: float = 0.0, prior_den: float = 0.0,
                min_periods: int = 1) -> pd.Series:
    """(sum num + prior_num) / (sum den + prior_den) over the previous w rows -- a shrunk rate. NaN when no prior rows."""
    n = prior_roll(df, by, num, w, "sum", min_periods)
    d = prior_roll(df, by, den, w, "sum", min_periods)
    out = (n + prior_num) / (d + prior_den).where((d + prior_den) > 0)
    return out.where(n.notna())


def league_prior_mean(dates: pd.Series, values: pd.Series, days: int = 30) -> pd.Series:
    """For each row: the mean of `values` over the previous `days` DISTINCT game dates strictly before the row's date."""
    d = pd.DataFrame({"dt": pd.to_datetime(dates), "v": values.to_numpy()}).dropna()
    daily = d.groupby("dt")["v"].mean().sort_index()
    roll = daily.rolling(days, min_periods=1).mean()
    pos = daily.index.searchsorted(pd.to_datetime(dates).to_numpy(), side="left") - 1
    arr = roll.to_numpy()
    return pd.Series(np.where(pos >= 0, arr[np.maximum(pos, 0)], np.nan), index=dates.index)


# ---------------------------------------------------------------------------------------------------------------------
# base tables
# ---------------------------------------------------------------------------------------------------------------------
def game_table(games: pd.DataFrame) -> pd.DataFrame:
    g = games[games["game_type"].isin(GAME_TYPES)][["game_id", "season_year", "game_type", "date", "home_id", "away_id",
                                                     "home_abbr", "away_abbr"]].copy()
    g["date"] = g["date"].astype(str)
    return g


def team_table(games: pd.DataFrame, xg_team: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team_id): schedule + xG for/against + prior-only rolling form. Sorted by team, date."""
    g = game_table(games)
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        rows.append(pd.DataFrame({
            "game_id": g["game_id"], "date": g["date"], "season_year": g["season_year"],
            "team_id": g[f"{side}_id"], "team": g[f"{side}_abbr"], "opp_id": g[f"{other}_id"], "opp": g[f"{other}_abbr"],
            "is_home": 1 if side == "home" else 0}))
    t = pd.concat(rows, ignore_index=True)
    keep = ["game_id", "team_id", "att_for", "xgf", "sog_for", "xgf_sog", "goals_for_pop", "hd_for", "att_pp_for",
            "att_against", "xga", "sog_against", "xga_sog", "goals_against_pop", "hd_against", "att_pp_against"]
    t = t.merge(xg_team[[c for c in keep if c in xg_team.columns]], on=["game_id", "team_id"], how="left")
    t = t.dropna(subset=["xgf"]).sort_values(["team_id", "date", "game_id"]).reset_index(drop=True)
    dt = pd.to_datetime(t["date"])
    t["rest_days"] = (dt - dt.groupby(t["team_id"]).shift(1)).dt.days.clip(upper=8).fillna(8)
    t["b2b"] = (t["rest_days"] <= 1).astype(int)
    cols = {"sog_for": "sf", "sog_against": "sa", "xgf": "xgf", "xga": "xga", "att_for": "attf", "att_against": "atta",
            "hd_for": "hdf", "hd_against": "hda", "att_pp_for": "ppf", "att_pp_against": "ppa"}
    for src, nm in cols.items():
        if src not in t.columns:
            continue
        for w in (10, 30):
            t[f"{nm}_l{w}"] = prior_roll(t, "team_id", src, w)
        t[f"{nm}_ewm"] = prior_ewm(t, "team_id", src, 12)
    t["lg_sf_l30"] = league_prior_mean(t["date"], t["sog_for"], 30)
    t["lg_xgf_l30"] = league_prior_mean(t["date"], t["xgf"], 30)
    t["lg_goal_l30"] = league_prior_mean(t["date"], t["goals_for_pop"], 30)
    return t


def goalie_table(games: pd.DataFrame, goalies: pd.DataFrame, xg_goalie: pd.DataFrame) -> pd.DataFrame:
    """One row per goalie appearance (TOI > 0) with outcome columns and prior-only rolling form. `is_start` marks the
    goalie who started the game for his team (the box `starter` flag)."""
    g = game_table(games)
    b = goalies[goalies["game_type"].isin(GAME_TYPES) & (goalies["toi_sec"] > 0)].copy()
    b = b.merge(g[["game_id", "home_id", "away_id", "home_abbr", "away_abbr"]], on="game_id", how="inner")
    b["opp_id"] = np.where(b["is_home"].astype(bool), b["away_id"], b["home_id"])
    b["opp"] = np.where(b["is_home"].astype(bool), b["away_abbr"], b["home_abbr"])
    b["team"] = b["team_abbr"]
    b["is_home"] = b["is_home"].astype(int)
    b["date"] = b["date"].astype(str)
    b["is_start"] = (b["starter"].astype(str).str.lower() == "true").astype(int)
    b = b.merge(xg_goalie, on=["game_id", "player_id"], how="left")
    for c in ("shots_faced", "xga", "ga_pop", "gsax", "sa_pk", "hd_faced"):
        b[c] = b[c].fillna(0.0)
    b["short"] = (b["toi_sec"] < 3300).astype(int)                 # pulled / injured / split game
    b = b.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    dt = pd.to_datetime(b["date"])
    b["rest_days"] = (dt - dt.groupby(b["player_id"]).shift(1)).dt.days.clip(upper=14).fillna(14)
    # appearances in the 7 days BEFORE this one (strictly earlier dates)
    cnt7 = pd.Series(0.0, index=b.index)
    for pid, ix in b.groupby("player_id", sort=False).groups.items():
        d = dt.loc[ix].to_numpy().astype("datetime64[D]").astype("int64")
        lo = np.searchsorted(d, d - 7, side="left")
        hi = np.searchsorted(d, d, side="left")
        cnt7.loc[ix] = hi - lo
    b["starts_7d"] = cnt7
    b["n_prior"] = b.groupby("player_id").cumcount().astype(float)
    b["toi_min"] = b["toi_sec"] / 60.0
    for w in (5, 10, 30):
        b[f"sa_l{w}"] = prior_roll(b, "player_id", "shots_against", w)
    b["saves_l10"] = prior_roll(b, "player_id", "saves", 10)
    b["toi_l10"] = prior_roll(b, "player_id", "toi_min", 10)
    b["short_l20"] = prior_roll(b, "player_id", "short", 20)
    b["sv_l60"] = prior_ratio(b, "player_id", "saves", "shots_against", 60, LG_SV * SV_PRIOR_SHOTS, SV_PRIOR_SHOTS)
    b["gsax100_l20"] = 100.0 * prior_ratio(b, "player_id", "gsax", "shots_faced", 20, 0.0, SV_PRIOR_SHOTS)
    b["gsax100_l60"] = 100.0 * prior_ratio(b, "player_id", "gsax", "shots_faced", 60, 0.0, SV_PRIOR_SHOTS)
    b["xga_shot_l30"] = prior_ratio(b, "player_id", "xga", "shots_faced", 30)
    b["hd_share_l30"] = prior_ratio(b, "player_id", "hd_faced", "shots_faced", 30)
    b["lg_sv_l30"] = league_prior_mean(b["date"], b["saves"] / b["shots_against"].where(b["shots_against"] > 0), 30)
    return b


# ---------------------------------------------------------------------------------------------------------------------
# saves model features
# ---------------------------------------------------------------------------------------------------------------------
SAVES_FEATURES = [
    "g_sa_l5", "g_sa_l10", "g_sa_l30", "g_saves_l10", "g_sv_l60", "g_gsax100_l20", "g_gsax100_l60", "g_xga_shot_l30",
    "g_hd_share_l30", "g_toi_l10", "g_short_l20", "g_rest", "g_starts_7d", "g_n_prior",
    "t_sa_l10", "t_sa_l30", "t_sa_ewm", "t_xga_l30", "t_atta_l30", "t_hda_l30", "t_ppa_l30", "t_rest",
    "o_sf_l10", "o_sf_l30", "o_sf_ewm", "o_xgf_l30", "o_attf_l30", "o_hdf_l30", "o_ppf_l30", "o_rest",
    "lg_sf_l30", "lg_sv_l30", "is_home", "analytic_exp_sa",
]
_SAVES_XG_ONLY = {"g_gsax100_l20", "g_gsax100_l60", "g_xga_shot_l30", "g_hd_share_l30", "t_xga_l30", "t_hda_l30",
                  "o_xgf_l30", "o_hdf_l30"}
SAVES_FEATURES_NOXG = [f for f in SAVES_FEATURES if f not in _SAVES_XG_ONLY]     # ablation: same model, no xG-derived input
SAVES_ANALYTIC_A, SAVES_ANALYTIC_B = 0.85, 0.65       # the qrob engine's fixed weights (defence vs opposing offence)


def saves_features(gt: pd.DataFrame, tt: pd.DataFrame, starters_only: bool = True) -> pd.DataFrame:
    """One feature row per goalie appearance (default: starters only -- the saves market only exists for the expected
    starter). Columns: keys + SAVES_FEATURES + targets (saves, shots_against, toi_sec)."""
    g = gt[gt["is_start"] == 1] if starters_only else gt
    team_cols = ["sa_l10", "sa_l30", "sa_ewm", "xga_l30", "atta_l30", "hda_l30", "ppa_l30", "rest_days"]
    own = tt[["game_id", "team_id"] + team_cols].rename(
        columns={"sa_l10": "t_sa_l10", "sa_l30": "t_sa_l30", "sa_ewm": "t_sa_ewm", "xga_l30": "t_xga_l30",
                 "atta_l30": "t_atta_l30", "hda_l30": "t_hda_l30", "ppa_l30": "t_ppa_l30", "rest_days": "t_rest"})
    opp_cols = ["sf_l10", "sf_l30", "sf_ewm", "xgf_l30", "attf_l30", "hdf_l30", "ppf_l30", "rest_days", "lg_sf_l30"]
    opp = tt[["game_id", "team_id"] + opp_cols].rename(
        columns={"team_id": "opp_id", "sf_l10": "o_sf_l10", "sf_l30": "o_sf_l30", "sf_ewm": "o_sf_ewm",
                 "xgf_l30": "o_xgf_l30", "attf_l30": "o_attf_l30", "hdf_l30": "o_hdf_l30", "ppf_l30": "o_ppf_l30",
                 "rest_days": "o_rest"})
    f = g.merge(own, on=["game_id", "team_id"], how="left").merge(opp, on=["game_id", "opp_id"], how="left")
    ren = {"sa_l5": "g_sa_l5", "sa_l10": "g_sa_l10", "sa_l30": "g_sa_l30", "saves_l10": "g_saves_l10", "sv_l60": "g_sv_l60",
           "gsax100_l20": "g_gsax100_l20", "gsax100_l60": "g_gsax100_l60", "xga_shot_l30": "g_xga_shot_l30",
           "hd_share_l30": "g_hd_share_l30", "toi_l10": "g_toi_l10", "short_l20": "g_short_l20", "rest_days": "g_rest",
           "starts_7d": "g_starts_7d", "n_prior": "g_n_prior"}
    f = f.rename(columns=ren)
    lg = f["lg_sf_l30"]
    f["analytic_exp_sa"] = lg * (f["t_sa_l30"] / lg) ** SAVES_ANALYTIC_A * (f["o_sf_l30"] / lg) ** SAVES_ANALYTIC_B
    keys = ["game_id", "player_id", "date", "team", "opp", "team_id", "opp_id", "season_year", "game_type"]
    return f[keys + SAVES_FEATURES + ["saves", "shots_against", "toi_sec", "name"]].reset_index(drop=True)


def analytic_saves_mu(f: pd.DataFrame, k_share: float = 0.0) -> pd.Series:
    """The qrob-style structural projection: exp_SA * (time-in-net share) * save%. A NON-ML benchmark: expected shots from
    the multiplicative team-defence x opposing-offence form, times the goalie's shrunk save%, times his recent TOI share.
    `g_sv_l60` already carries the shrinkage; share = g_toi_l10 / 60 capped at 1."""
    share = (f["g_toi_l10"] / 60.0).clip(upper=1.0).fillna(0.975)
    return f["analytic_exp_sa"] * share * f["g_sv_l60"].fillna(LG_SV)


# ---------------------------------------------------------------------------------------------------------------------
# skater features (goals / assists / points)
# ---------------------------------------------------------------------------------------------------------------------
SKATER_FEATURES = [
    "toi_l10", "toi_ewm", "ixg_p60_l20", "ixg_p60_l60", "ixg_ev_p60_l30", "ixg_pp_g_l30", "att_p60_l20", "att_p60_l60",
    "sog_p60_l30", "hd_p60_l30", "goals_p60_l60", "goals_p60_l120", "assists_p60_l60", "assists_p60_l120", "points_p60_l60",
    "points_p60_l120", "fin_ratio_l120", "pp_att_g_l30", "n_prior", "is_d", "is_c", "is_home", "rest_days",
    "t_xgf_l10", "t_xgf_l30", "t_goal_l30", "t_ppf_l30", "o_xga_l10", "o_xga_l30", "o_goala_l30", "o_ppa_l30",
    "opp_g_gsax100_l60", "opp_g_sv_l60", "opp_g_xga_shot_l30", "lg_goal_l30", "lg_xgf_l30",
]


_SKATER_XG_ONLY = {"ixg_p60_l20", "ixg_p60_l60", "ixg_ev_p60_l30", "ixg_pp_g_l30", "hd_p60_l30", "fin_ratio_l120",
                   "t_xgf_l10", "t_xgf_l30", "o_xga_l10", "o_xga_l30", "opp_g_gsax100_l60", "opp_g_xga_shot_l30", "lg_xgf_l30"}
SKATER_FEATURES_NOXG = [f for f in SKATER_FEATURES if f not in _SKATER_XG_ONLY]  # ablation: shots/goals/TOI only


def skater_table(games: pd.DataFrame, skaters: pd.DataFrame, xg_skater: pd.DataFrame) -> pd.DataFrame:
    g = game_table(games)
    s = skaters[skaters["game_type"].isin(GAME_TYPES) & (skaters["toi_sec"] > 0)].copy()
    s = s.merge(g[["game_id", "home_abbr", "away_abbr", "home_id", "away_id"]], on="game_id", how="inner")
    s["team"] = s["team_abbr"]
    s["opp"] = np.where(s["is_home"].astype(bool), s["away_abbr"], s["home_abbr"])
    s["opp_id"] = np.where(s["is_home"].astype(bool), s["away_id"], s["home_id"])
    s["is_home"] = s["is_home"].astype(int)
    s["date"] = s["date"].astype(str)
    s = s[s["game_id"].isin(set(xg_skater["game_id"]))]                       # games without any shots rows are unusable
    s = s.merge(xg_skater, on=["game_id", "player_id"], how="left")
    for c in ("attempts", "ixg", "ixg_ev", "ixg_pp", "att_ev", "att_pp", "hd_att", "sog_pop", "ixg_sog", "goals_pop"):
        s[c] = s[c].fillna(0.0)
    s = s.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    s["toi_min"] = s["toi_sec"] / 60.0
    s["n_prior"] = s.groupby("player_id").cumcount().astype(float)
    dt = pd.to_datetime(s["date"])
    s["rest_days"] = (dt - dt.groupby(s["player_id"]).shift(1)).dt.days.clip(upper=8).fillna(8)
    s["is_d"] = (s["position"] == "D").astype(float)
    s["is_c"] = (s["position"] == "C").astype(float)
    s["toi_l10"] = prior_roll(s, "player_id", "toi_min", 10)
    s["toi_ewm"] = prior_ewm(s, "player_id", "toi_min", 8)

    def p60(num, w):
        a = prior_roll(s, "player_id", num, w, "sum", min_periods=3)
        t = prior_roll(s, "player_id", "toi_sec", w, "sum", min_periods=3)
        return a / t.where(t > 0) * 3600.0

    s["ixg_p60_l20"], s["ixg_p60_l60"] = p60("ixg", 20), p60("ixg", 60)
    s["ixg_ev_p60_l30"] = p60("ixg_ev", 30)
    s["att_p60_l20"], s["att_p60_l60"] = p60("attempts", 20), p60("attempts", 60)
    s["sog_p60_l30"] = p60("sog", 30)
    s["hd_p60_l30"] = p60("hd_att", 30)
    s["goals_p60_l60"], s["goals_p60_l120"] = p60("goals", 60), p60("goals", 120)
    s["assists_p60_l60"], s["assists_p60_l120"] = p60("assists", 60), p60("assists", 120)
    s["points_p60_l60"], s["points_p60_l120"] = p60("points", 60), p60("points", 120)
    s["ixg_pp_g_l30"] = prior_roll(s, "player_id", "ixg_pp", 30)
    s["pp_att_g_l30"] = prior_roll(s, "player_id", "att_pp", 30)
    # finishing talent: goals over (shrunk) expected, on the SAME population (non-empty-net, non-shootout goals)
    gp = prior_roll(s, "player_id", "goals_pop", 120, "sum", min_periods=5)
    xp = prior_roll(s, "player_id", "ixg", 120, "sum", min_periods=5)
    s["fin_ratio_l120"] = (gp + 3.0) / (xp + 3.0)
    return s


def skater_features(st: pd.DataFrame, tt: pd.DataFrame, gt: pd.DataFrame) -> pd.DataFrame:
    """One feature row per skater appearance. `gt` = goalie_table (supplies the opposing starter's prior-only form)."""
    own = tt[["game_id", "team_id", "xgf_l10", "xgf_l30", "ppf_l30"]].rename(
        columns={"team_id": "tid", "xgf_l10": "t_xgf_l10", "xgf_l30": "t_xgf_l30", "ppf_l30": "t_ppf_l30"})
    tt2 = tt.assign(goala_l30=prior_roll(tt, "team_id", "goals_against_pop", 30),
                    goalf_l30=prior_roll(tt, "team_id", "goals_for_pop", 30))
    own = own.merge(tt2[["game_id", "team_id", "goalf_l30"]].rename(columns={"team_id": "tid", "goalf_l30": "t_goal_l30"}),
                    on=["game_id", "tid"], how="left")
    opp = tt2[["game_id", "team_id", "xga_l10", "xga_l30", "goala_l30", "ppa_l30", "lg_goal_l30", "lg_xgf_l30"]].rename(
        columns={"team_id": "opp_id", "xga_l10": "o_xga_l10", "xga_l30": "o_xga_l30", "goala_l30": "o_goala_l30",
                 "ppa_l30": "o_ppa_l30"})
    f = st.merge(own, left_on=["game_id", "team_id"], right_on=["game_id", "tid"], how="left").drop(columns=["tid"])
    f = f.merge(opp, on=["game_id", "opp_id"], how="left")
    starters = gt[gt["is_start"] == 1][["game_id", "team_id", "gsax100_l60", "sv_l60", "xga_shot_l30"]].rename(
        columns={"team_id": "opp_id", "gsax100_l60": "opp_g_gsax100_l60", "sv_l60": "opp_g_sv_l60",
                 "xga_shot_l30": "opp_g_xga_shot_l30"})
    starters = starters.drop_duplicates(["game_id", "opp_id"], keep="first")
    f = f.merge(starters, on=["game_id", "opp_id"], how="left")
    keys = ["game_id", "player_id", "date", "team", "opp", "team_id", "opp_id", "season_year", "game_type", "name", "position"]
    return f[keys + SKATER_FEATURES + ["goals", "assists", "points", "toi_sec"]].reset_index(drop=True)
