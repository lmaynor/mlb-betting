"""Shot-level xG -> per skater-game, goalie-game and team-game tables (all keyed by NHL ids; regular season + playoffs).

`attach_xg(prep, xg_att, xg_sog)` adds the walk-forward `xg_att` / `xg_sog` columns (NaN outside each population).
Definitions (empty-net and shootout shots are already outside both populations, see nhl.xg.features):
  xg_att   P(goal | unblocked attempt)      skater / team threat ("Fenwick xG")
  xg_sog   P(goal | on target)              what a goalie faces
  ev       skaters_for == skaters_against   (5v5, 4v4, 3v3 -- anything even), pp = skaters_for > skaters_against
`goals` here are goals in the population (non-empty-net, non-shootout), so they can differ slightly from box-score goals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def attach_xg(prep: pd.DataFrame, xg_att: pd.Series | None, xg_sog: pd.Series | None) -> pd.DataFrame:
    s = prep.copy()
    s["xg_att"] = np.nan
    s["xg_sog"] = np.nan
    if xg_att is not None:
        s.loc[xg_att.index, "xg_att"] = xg_att.to_numpy()
    if xg_sog is not None:
        s.loc[xg_sog.index, "xg_sog"] = xg_sog.to_numpy()
    return s


def skater_game(s: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, shooter_id): attempts, ixg (att), sog, ixg_sog, goals, even-strength / power-play splits."""
    a = s[s["in_attempt"] & s["shooter_id"].notna()].copy()
    a["shooter_id"] = a["shooter_id"].astype("int64")
    a["ev"] = (a["skaters_for"] == a["skaters_against"])
    a["xg_ev"] = a["xg_att"].where(a["ev"], 0.0)
    a["xg_pp"] = a["xg_att"].where(a["pp"] == 1, 0.0)
    a["att_pp"] = (a["pp"] == 1).astype(int)
    a["att_ev"] = a["ev"].astype(int)
    a["hd"] = ((a["dist_ft"] <= 25.0) & (a["angle_deg"] <= 45.0)).astype(int)
    a["sog1"] = a["on_target"]
    a["xg_sog_s"] = a["xg_sog"].fillna(0.0)
    g = a.groupby(["game_id", "shooter_id"]).agg(
        attempts=("goal", "size"), ixg=("xg_att", "sum"), ixg_ev=("xg_ev", "sum"), ixg_pp=("xg_pp", "sum"),
        att_ev=("att_ev", "sum"), att_pp=("att_pp", "sum"), hd_att=("hd", "sum"), sog_pop=("sog1", "sum"),
        ixg_sog=("xg_sog_s", "sum"), goals_pop=("goal", "sum")).reset_index()
    return g.rename(columns={"shooter_id": "player_id"})


def goalie_game(s: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, goalie_id): shots faced (on target, non-EN), expected goals against, goals against, plus
    even-strength / power-play-against splits (`pp` here is the SHOOTING team's power play = goalie's penalty-kill)."""
    q = s[s["in_sog"] & s["goalie_id"].notna()].copy()
    q["goalie_id"] = q["goalie_id"].astype("int64")
    q["xg_pk"] = q["xg_sog"].where(q["pp"] == 1, 0.0)
    q["sa_pk"] = (q["pp"] == 1).astype(int)
    q["hd"] = ((q["dist_ft"] <= 25.0) & (q["angle_deg"] <= 45.0)).astype(int)
    g = q.groupby(["game_id", "goalie_id"]).agg(
        shots_faced=("goal", "size"), xga=("xg_sog", "sum"), ga_pop=("goal", "sum"), xga_pk=("xg_pk", "sum"),
        sa_pk=("sa_pk", "sum"), hd_faced=("hd", "sum")).reset_index()
    g["gsax"] = g["xga"] - g["ga_pop"]
    return g.rename(columns={"goalie_id": "player_id"})


def team_game(s: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team_id): for-side from the team's own shots, against-side from the opponent's.
    Columns: att_for, xgf, sog_for, xgf_sog, goals_for_pop, xgf_ev, att_ev_for, xgf_pp, att_pp_for, hd_for and *_against."""
    a = s[s["in_attempt"]].copy()
    a["ev"] = (a["skaters_for"] == a["skaters_against"])
    a["xg_ev"] = a["xg_att"].where(a["ev"], 0.0)
    a["att_ev"] = a["ev"].astype(int)
    a["xg_pp"] = a["xg_att"].where(a["pp"] == 1, 0.0)
    a["att_pp"] = (a["pp"] == 1).astype(int)
    a["hd"] = ((a["dist_ft"] <= 25.0) & (a["angle_deg"] <= 45.0)).astype(int)
    a["xg_sog_s"] = a["xg_sog"].fillna(0.0)
    f = a.groupby(["game_id", "team_id"]).agg(
        att_for=("goal", "size"), xgf=("xg_att", "sum"), sog_for=("on_target", "sum"), xgf_sog=("xg_sog_s", "sum"),
        goals_for_pop=("goal", "sum"), xgf_ev=("xg_ev", "sum"), att_ev_for=("att_ev", "sum"), xgf_pp=("xg_pp", "sum"),
        att_pp_for=("att_pp", "sum"), hd_for=("hd", "sum")).reset_index()
    teams = f.groupby("game_id")["team_id"].apply(list)
    pair = []
    for gid, ts in teams.items():
        if len(ts) == 2:
            pair += [(gid, ts[0], ts[1]), (gid, ts[1], ts[0])]
    opp = pd.DataFrame(pair, columns=["game_id", "team_id", "opp_id"])
    f = f.merge(opp, on=["game_id", "team_id"], how="inner")
    ag = f.drop(columns=["opp_id"]).rename(columns={"team_id": "opp_id"})
    ag = ag.rename(columns={c: c.replace("_for", "_against") if "_for" in c else c.replace("xgf", "xga") for c in ag.columns
                            if c not in ("game_id", "opp_id")})
    return f.merge(ag, on=["game_id", "opp_id"], how="left")
