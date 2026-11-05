"""nba.sim.fit -- calibrate the structural constants of the NBA joint simulator from the box-score masters.

    python -m nba.sim.fit --player-master P.csv --team-master T.csv --fit-through 2023 --out nba/sim/params.json

Everything here is fitted on seasons <= --fit-through ONLY (season_year 2023 = the 2023-24 season); the walk-forward in
nba/sim/evaluate.py then scores 2024 / 2025, which no constant has seen. Expectations are EX-ANTE (EWM of each team's earlier games;
no look-ahead): a residual is "what happened minus what the team's own and the opponent's recent form predicted".

What is fitted (every number lands in params.json under the key named here):
  game      : sd of the margin / total residual, the loading of the total on the game's possession count, home-court shift
  team      : team threes / assists / rebounds as linear functions of the team's own points (and pace, and the opponent's), with
              residual standard deviations -- this is where "more team points => more of everything" comes from
  minutes   : mean fraction of a player's usual minutes by |final margin| (garbage time) and by role, plus the empirical
              distribution of what is left over (foul trouble, injury exits, overtime) as a 101-point quantile grid
  rest      : the share of each team-pie (points, threes, assists, rebounds) held by players OUTSIDE the modeled rotation
  The player-level block (params["player"]: shared form amplitude, pie-size elasticity, rest-bucket noise, pie wobble) is NOT fitted
  here; it was tuned on a 2023-24 replay subsample by `nba.sim.evaluate --set ...` and survives a re-fit. The per-player weight
  multiplier and idiosyncratic noise are solved at simulation time so each marginal matches the analytic engine's mean and sd.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

EWM_TEAM_HL = 12.0
EWM_MIN_HL = 8.0
ROTATION_MPG = 12.0
STARTER_MPG = 28.0
MPG_BINS = (12.0, 20.0, 26.0, 31.0, 35.0, 99.0)
ABS_M_EDGES = (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 100.0)
QGRID = np.linspace(0.005, 0.995, 100)


def _minutes(v):
    if isinstance(v, str) and ":" in v:
        try:
            m, s = v.split(":")[:2]
            return int(m) + int(s) / 60.0
        except ValueError:
            return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def team_table(team_master: pd.DataFrame, player_master: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team) with the opponent's numbers, possessions and EX-ANTE expectations. Regular season + playoffs,
    no preseason. `rebounds` is the SUM OF THE PLAYERS' credited rebounds (the team master's own column is NaN before 2025 and
    the player sum is ~8 a game lower: team rebounds are not credited to a player), because that is the pie the player props see."""
    t = team_master[~team_master["season_type"].astype(str).str.lower().eq("preseason")].copy()
    pr = player_master[player_master["played"].astype(str).str.lower().isin(["true", "1", "1.0"])]
    psum = pr.groupby(["game_id", "team_abbr"])[["rebounds", "turnovers"]].sum().add_suffix("_p").reset_index()
    t = t.merge(psum, on=["game_id", "team_abbr"], how="inner")
    t["rebounds"] = t["rebounds_p"]
    t["turnovers"] = t["turnovers_p"]                # the team master's turnovers are NaN before 2025 as well
    t["poss"] = t.field_goals_attempted + 0.44 * t.free_throws_attempted - t.rebounds_offensive + t.turnovers
    cols = ["points", "three_points_made", "assists", "rebounds", "poss"]
    t = t[["game_id", "season_year", "date", "team_abbr", "is_home"] + cols].copy()
    t["is_home"] = t["is_home"].astype(str).str.lower().isin(["true", "1", "1.0"])
    o = t[["game_id", "team_abbr"] + cols].rename(columns={"team_abbr": "opp"})
    o = o.rename(columns={c: "o_" + c for c in cols})
    g = t.merge(o, on="game_id")
    g = g[g.team_abbr != g.opp].copy()
    g = g.sort_values(["date", "game_id", "team_abbr"]).reset_index(drop=True)
    for c in cols:
        g[f"for_{c}"] = g.groupby("team_abbr")[c].transform(lambda s: s.ewm(halflife=EWM_TEAM_HL, min_periods=5).mean().shift(1))
        g[f"ag_{c}"] = g.groupby("team_abbr")["o_" + c].transform(lambda s: s.ewm(halflife=EWM_TEAM_HL, min_periods=5).mean().shift(1))
    key = g[["game_id", "team_abbr"] + [f"for_{c}" for c in cols] + [f"ag_{c}" for c in cols]].rename(
        columns={"team_abbr": "opp", **{f"for_{c}": f"ofor_{c}" for c in cols}, **{f"ag_{c}": f"oag_{c}" for c in cols}})
    g = g.merge(key, on=["game_id", "opp"])
    for c in cols:
        g[f"e_{c}"] = 0.5 * (g[f"for_{c}"] + g[f"oag_{c}"])           # what this team should produce against this opponent
        g[f"r_{c}"] = g[c] - g[f"e_{c}"]
    g["rating"] = g["for_points"] - g["ag_points"]
    g["o_rating"] = g["ofor_points"] - g["oag_points"]
    return g.dropna(subset=[f"e_{c}" for c in cols]).reset_index(drop=True)


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = y - A @ beta
    return beta, float(np.sqrt(res.var(ddof=A.shape[1])))


def fit_team_layer(g: pd.DataFrame) -> dict:
    """Game script + team-pie regressions on ex-ante residuals (all in raw units; the simulator feeds the same residuals)."""
    h = g[g.is_home].copy()
    h["T"] = h.points + h.o_points
    h["M"] = h.points - h.o_points
    # expected total = own expected points + opponent's expected points (opponent row of the same game)
    opp_e = g[~g.is_home][["game_id", "e_points", "e_poss", "r_poss"]].rename(
        columns={"e_points": "oe_points", "e_poss": "oe_poss", "r_poss": "or_poss"})
    h = h.merge(opp_e, on="game_id")
    h["eT"] = h.e_points + h.oe_points
    h["rT"] = h["T"] - h["eT"]
    h["rPace"] = 0.5 * (h.r_poss + h.or_poss)
    bM, sdM = _ols(h["M"].to_numpy(), (h.rating - h.o_rating).to_numpy()[:, None])
    sd_pace = float(h.rPace.std())
    corr_t_pace = float(np.corrcoef(h.rT, h.rPace)[0, 1])
    out = {"game": {"sd_total_resid": float(h.rT.std()), "sd_margin_resid": sdM, "margin_intercept": float(bM[0]),
                    "margin_slope": float(bM[1]), "sd_pace": sd_pace, "corr_total_pace": corr_t_pace,
                    "mean_pace": float(h.e_poss.mean()), "n_games": int(len(h))}}
    # standardised game latents used as regressors: pace z, own points resid, opponent points resid, own / opp threes resid
    sdP = float(g.r_points.std())
    sd3 = float(g.r_three_points_made.std())
    g = g.merge(h[["game_id", "rPace"]], on="game_id", how="left")
    g["zPace"] = g.rPace / sd_pace
    opp = g[["game_id", "team_abbr", "r_points", "r_three_points_made"]].rename(
        columns={"team_abbr": "opp", "r_points": "or_points", "r_three_points_made": "or_three"})
    g = g.merge(opp, on=["game_id", "opp"])
    team = {"sd_points_resid": sdP, "sd_threes_resid": sd3}
    b, s = _ols(g.r_three_points_made.to_numpy(), g.r_points.to_numpy()[:, None])
    team["threes"] = {"b_points": float(b[1]), "resid_sd": s}
    b, s = _ols(g.r_assists.to_numpy(), np.column_stack([g.r_points, g.r_three_points_made]))
    team["assists"] = {"b_points": float(b[1]), "b_threes": float(b[2]), "resid_sd": s}
    b, s = _ols(g.r_rebounds.to_numpy(), np.column_stack([g.zPace, g.r_points, g.or_points, g.r_three_points_made, g.or_three]))
    team["rebounds"] = {"b_pace": float(b[1]), "b_points": float(b[2]), "b_opp_points": float(b[3]),
                        "b_threes": float(b[4]), "b_opp_threes": float(b[5]), "resid_sd": s}
    team["sd_assists_resid"] = float(g.r_assists.std())
    team["sd_rebounds_resid"] = float(g.r_rebounds.std())
    team["mean"] = {"points": float(g.points.mean()), "threes": float(g.three_points_made.mean()),
                    "assists": float(g.assists.mean()), "rebounds": float(g.rebounds.mean())}
    out["team"] = team
    return out


def player_table(player_master: pd.DataFrame, g: pd.DataFrame) -> pd.DataFrame:
    """Played, non-preseason player rows with ex-ante EWM / L10 minutes and the game's final |margin|."""
    p = player_master[~player_master["season_type"].astype(str).str.lower().eq("preseason")].copy()
    p = p[p["played"].astype(str).str.lower().isin(["true", "1", "1.0"])].copy()
    p["min"] = p["minutes"].map(_minutes)
    p = p[p["min"].fillna(0) > 0].copy()
    p = p.sort_values(["date", "game_id"]).reset_index(drop=True)
    grp = p.groupby("player_id")["min"]
    p["ewm_min"] = grp.transform(lambda s: s.ewm(halflife=EWM_MIN_HL, min_periods=4).mean().shift(1))
    p["l10_min"] = grp.transform(lambda s: s.rolling(10, min_periods=3).mean().shift(1))
    marg = g[["game_id", "team_abbr", "points", "o_points", "is_home"]].rename(columns={"team_abbr": "team_abbr_t"})
    marg["M"] = marg.points - marg.o_points
    p = p.merge(marg[["game_id", "team_abbr_t", "M"]], left_on=["game_id", "team_abbr"], right_on=["game_id", "team_abbr_t"],
                how="inner")
    p["absM"] = p["M"].abs()
    p["modeled"] = (p.l10_min >= ROTATION_MPG) & p.ewm_min.notna()
    return p


def fit_minutes(p: pd.DataFrame) -> dict:
    """Mean minutes ratio (minutes / ex-ante EWM minutes) by (mpg bin, |margin| bin), the quantile grid of what is left over, and the
    within-team / cross-team correlation of those leftovers (overtime, shared game flow) as two copula weights."""
    m = p[p.modeled & (p.ewm_min > 0)].copy()
    m["ratio"] = m["min"] / m.ewm_min
    m["mb"] = np.clip(np.searchsorted(MPG_BINS, m.ewm_min.to_numpy(), side="right") - 1, 0, len(MPG_BINS) - 2)
    m["ab"] = np.clip(np.searchsorted(ABS_M_EDGES, m.absM.to_numpy(), side="right") - 1, 0, len(ABS_M_EDGES) - 2)
    tab, grids = [], []
    m["resid"] = np.nan
    m["zres"] = np.nan
    for b in range(len(MPG_BINS) - 1):
        sel = m.mb == b
        sub = m[sel]
        row = [float(sub[sub.ab == a].ratio.mean()) if (sub.ab == a).sum() >= 30 else float(sub.ratio.mean())
               for a in range(len(ABS_M_EDGES) - 1)]
        tab.append(row)
        resid = sub.ratio.to_numpy() - np.array(row)[sub.ab.to_numpy()]
        m.loc[sel, "resid"] = resid
        m.loc[sel, "zres"] = resid / max(float(resid.std()), 1e-9)
        grids.append([float(x) for x in np.quantile(resid, QGRID)])
    # pair correlations of the standardised leftovers: same team (other player) and the opposing team
    grp = m.groupby(["game_id", "team_abbr"])["zres"]
    st = pd.DataFrame({"s1": grp.sum(), "s2": grp.apply(lambda x: float((x ** 2).sum())), "c": grp.size()}).reset_index()
    same_num = float((st.s1 ** 2 - st.s2).sum())
    same_den = float((st.c * (st.c - 1)).sum())
    both = st.merge(st, on="game_id", suffixes=("", "_o"))
    both = both[both.team_abbr != both.team_abbr_o]
    opp_num = float((both.s1 * both.s1_o).sum() / 2.0)
    opp_den = float((both.c * both.c_o).sum() / 2.0)
    rho_same, rho_opp = same_num / same_den, opp_num / opp_den
    rho_game = max(rho_opp, 0.0)
    rho_team = max(rho_same - rho_game, 0.0)
    return {"mpg_bins": list(MPG_BINS), "abs_margin_edges": list(ABS_M_EDGES), "mean_ratio": tab, "resid_quantiles": grids,
            "qgrid": [float(q) for q in QGRID], "n": int(len(m)), "rho_same_team": float(rho_same), "rho_opposing": float(rho_opp),
            "rho_game": float(rho_game), "rho_team": float(rho_team)}


def fit_rest(p: pd.DataFrame) -> dict:
    """Share of each team pie held by players outside the modeled rotation (L10 < 12 mpg or no history)."""
    pp = p.copy()
    pp["three"] = pp["three_points_made"]
    pp["nonthree"] = pp["points"] - 3 * pp["three_points_made"]
    out = {}
    for pie, col in (("points", "nonthree"), ("threes", "three"), ("assists", "assists"), ("rebounds", "rebounds")):
        tot = pp.groupby(["game_id", "team_abbr"])[col].sum()
        mod = pp[pp.modeled].groupby(["game_id", "team_abbr"])[col].sum().reindex(tot.index).fillna(0.0)
        share = 1.0 - mod / tot.replace(0, np.nan)
        out[pie] = float(share.mean())
    return out


def fit(player_master: pd.DataFrame, team_master: pd.DataFrame, fit_through: int) -> dict:
    tm_all = team_master[team_master["season_year"] <= fit_through]
    pl = player_master[player_master["season_year"] <= fit_through]
    g = team_table(tm_all, pl)
    params = fit_team_layer(g)
    p = player_table(pl, g)
    params["minutes"] = fit_minutes(p)
    params["rest"] = fit_rest(p)
    params["fit_through_season_year"] = int(fit_through)
    return params


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--player-master", required=True)
    ap.add_argument("--team-master", required=True)
    ap.add_argument("--fit-through", type=int, default=2023)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    pm = pd.read_csv(a.player_master, low_memory=False)
    tm = pd.read_csv(a.team_master, low_memory=False)
    params = fit(pm, tm, a.fit_through)
    try:                                                        # the player-level block is tuned by replay (evaluate.py --set), not fitted here
        with open(a.out) as fh:
            old = json.load(fh)
        if "player" in old:
            params["player"] = old["player"]
    except (OSError, ValueError):
        pass
    with open(a.out, "w") as fh:
        json.dump(params, fh, indent=1, sort_keys=True)
        fh.write("\n")
    json.dump({k: v for k, v in params.items() if k not in ("minutes",)}, sys.stdout, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
