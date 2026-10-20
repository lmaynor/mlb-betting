"""
nba.games.features -- leakage-safe pre-game features for NBA moneyline / spread / totals.

Distilled from the two reference zips (Game-Outcome-Prediction: Elo with a 0.75 season carryover + rolling team
averages; Time-Series-Forecasts: rest / last-result context) and rebuilt on our own data so every value for game i is
computed from games strictly BEFORE game i (`shift(1)` on every rolling stat, pre-game Elo recorded before the update).
tests/test_nba_games.py perturbs a game's result and asserts that its own features do not move.

Per team (EWM, game-indexed, carried across seasons, halflife 10): ortg, drtg, pace, efg, tov_rate, orb_pct, ftr,
fg3_rate and the DEFENSIVE mirrors (opp_efg, opp_tov_rate, opp_orb_pct, opp_ftr) + margin; plus a slow (halflife 30)
net rating. Context: rest days, back-to-back, games in the last 7 days, games played this season.
Elo: K=20, home advantage 100, margin-of-victory multiplier, 0.75 carryover toward 1500 each new season.

NOT modelled (and why it matters): lineup / injury / load-management news. The market prices it; these features cannot.
That gap is the reason the 2026-06-15 moneyline backtest failed and the reason a "model vs close" edge should be
presumed absent until a walk-forward says otherwise (handoffs/handoff_nba_backtest_2026-06-15.md).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HALFLIFE = 10
SLOW_HALFLIFE = 30
ELO_K = 20.0
ELO_HOME = 100.0
ELO_CARRY = 0.75
EWM_METRICS = ["ortg", "drtg", "pace", "efg", "tov_rate", "orb_pct", "ftr", "fg3_rate",
               "opp_efg", "opp_tov_rate", "opp_orb_pct", "opp_ftr", "margin"]


def _poss(fga, oreb, tov, fta):
    return fga - oreb + tov + 0.44 * fta


def team_games(g: pd.DataFrame) -> pd.DataFrame:
    """Long frame: one row per team-game with per-game efficiency metrics (these are POST-game values; features shift them)."""
    rows = []
    for me, op, is_home in (("h", "a", True), ("a", "h", False)):
        d = pd.DataFrame({"gi": g.index, "date": g["date"], "season": g["season"], "team": g[home_or_away(is_home)],
                          "is_home": is_home})
        pts, opp_pts = g[f"{'home' if is_home else 'away'}_pts"], g[f"{'away' if is_home else 'home'}_pts"]
        b = {k: g[f"{me}_{k}"] for k in ("fga", "fgm", "fg3m", "fg3a", "fta", "oreb", "dreb", "tov")}
        o = {k: g[f"{op}_{k}"] for k in ("fga", "fgm", "fg3m", "fg3a", "fta", "oreb", "dreb", "tov")}
        poss = 0.5 * (_poss(b["fga"], b["oreb"], b["tov"], b["fta"]) + _poss(o["fga"], o["oreb"], o["tov"], o["fta"]))
        d["pts"], d["opp_pts"] = pts, opp_pts
        d["ortg"], d["drtg"], d["pace"] = 100 * pts / poss, 100 * opp_pts / poss, poss
        d["efg"] = (b["fgm"] + 0.5 * b["fg3m"]) / b["fga"]
        d["tov_rate"] = b["tov"] / (b["fga"] + 0.44 * b["fta"] + b["tov"])
        d["orb_pct"] = b["oreb"] / (b["oreb"] + o["dreb"])
        d["ftr"] = b["fta"] / b["fga"]
        d["fg3_rate"] = b["fg3a"] / b["fga"]
        d["opp_efg"] = (o["fgm"] + 0.5 * o["fg3m"]) / o["fga"]
        d["opp_tov_rate"] = o["tov"] / (o["fga"] + 0.44 * o["fta"] + o["tov"])
        d["opp_orb_pct"] = o["oreb"] / (o["oreb"] + b["dreb"])
        d["opp_ftr"] = o["fta"] / o["fga"]
        d["margin"] = pts - opp_pts
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def home_or_away(is_home: bool) -> str:
    return "home" if is_home else "away"


def _context(tg: pd.DataFrame) -> pd.DataFrame:
    """rest_days (capped 7; 7 also for a season opener), b2b, games_7d, season_game_no -- all from PRIOR games only."""
    tg = tg.sort_values(["team", "date", "gi"]).copy()
    dt = pd.to_datetime(tg["date"])
    prev = dt.groupby(tg["team"]).shift(1)
    new_season = tg["season"].ne(tg.groupby("team")["season"].shift(1))
    rest = (dt - prev).dt.days
    tg["rest_days"] = np.where(new_season | rest.isna(), 7, rest.clip(upper=7)).astype(float)
    tg["b2b"] = (tg["rest_days"] == 1).astype(float)
    # games in the 7 days strictly before this date (count of this team's prior game dates within 7 days)
    n7 = np.zeros(len(tg))
    for _, idx in tg.groupby("team").indices.items():
        d = dt.iloc[idx].values.astype("datetime64[D]").astype(int)
        lo = np.searchsorted(d, d - 7, side="left")
        hi = np.searchsorted(d, d, side="left")      # excludes same-date games (incl. itself)
        n7[idx] = hi - lo
    tg["games_7d"] = n7
    tg["season_game_no"] = tg.groupby(["team", "season"]).cumcount().astype(float)
    return tg


def _ewm_features(tg: pd.DataFrame) -> pd.DataFrame:
    tg = tg.sort_values(["team", "date", "gi"]).copy()
    grp = tg.groupby("team", sort=False)
    for m in EWM_METRICS:
        tg[f"e_{m}"] = grp[m].transform(lambda s: s.shift(1).ewm(halflife=HALFLIFE, min_periods=3).mean())
    tg["e_net_slow"] = grp.apply(lambda d: (d["ortg"] - d["drtg"]).shift(1).ewm(halflife=SLOW_HALFLIFE, min_periods=5)
                                 .mean()).reset_index(level=0, drop=True)
    return tg


def elo_pre(g: pd.DataFrame) -> pd.DataFrame:
    """Pre-game Elo for home and away, chronological over g (g must be sorted). Returns frame indexed like g."""
    elo: dict = {}
    last_season: dict = {}
    eh, ea = np.zeros(len(g)), np.zeros(len(g))
    home, away = g["home"].values, g["away"].values
    hp, ap, season = g["home_pts"].values, g["away_pts"].values, g["season"].values
    for i in range(len(g)):
        for t in (home[i], away[i]):
            if t not in elo:
                elo[t] = 1500.0
            elif last_season.get(t) != season[i]:
                elo[t] = ELO_CARRY * elo[t] + (1 - ELO_CARRY) * 1500.0
            last_season[t] = season[i]
        rh, ra = elo[home[i]], elo[away[i]]
        eh[i], ea[i] = rh, ra
        if np.isnan(hp[i]) or np.isnan(ap[i]):       # an UPCOMING game (scoring path): record the pre-game rating, no update
            continue
        exp_h = 1.0 / (1.0 + 10 ** (-((rh + ELO_HOME) - ra) / 400.0))
        mov = hp[i] - ap[i]
        win_diff = (rh + ELO_HOME - ra) if mov > 0 else (ra - rh - ELO_HOME)
        mult = ((abs(mov) + 3.0) ** 0.8) / (7.5 + 0.006 * win_diff)
        delta = ELO_K * mult * ((1.0 if mov > 0 else 0.0) - exp_h)
        elo[home[i]], elo[away[i]] = rh + delta, ra - delta
    return pd.DataFrame({"elo_h": eh, "elo_a": ea}, index=g.index)


def build(games: pd.DataFrame) -> pd.DataFrame:
    """games (nba.games.data output, chronological) -> games + targets + pre-game features (h_*, a_*).

    Rows with NaN points are UPCOMING games (the live scoring path): they get pre-game features from everything before
    them and never update a rating or an average."""
    g = games.sort_values(["date", "tip", "game_id"], na_position="last").reset_index(drop=True)
    tg = _ewm_features(_context(team_games(g)))
    feat_cols = [f"e_{m}" for m in EWM_METRICS] + ["e_net_slow", "rest_days", "b2b", "games_7d", "season_game_no"]
    key = tg[["gi", "is_home"] + feat_cols]
    h = key[key["is_home"]].drop(columns="is_home").set_index("gi").add_prefix("h_")
    a = key[~key["is_home"]].drop(columns="is_home").set_index("gi").add_prefix("a_")
    out = g.join(h).join(a).join(elo_pre(g))
    out["margin"] = out["home_pts"] - out["away_pts"]
    out["total"] = out["home_pts"] + out["away_pts"]
    out["home_win"] = (out["margin"] > 0).astype(int)
    out["playoff"] = (out["season_type"] == 3).astype(float)
    out["elo_diff"] = out["elo_h"] - out["elo_a"]
    # league scoring environment: mean total of the previous 300 games (strictly before; shift(1))
    out["lg_total"] = out["total"].shift(1).rolling(300, min_periods=60).mean()
    return out


# ---- model matrices -------------------------------------------------------------------------------------------------

_DIFF = ["e_ortg", "e_drtg", "e_efg", "e_tov_rate", "e_orb_pct", "e_ftr", "e_opp_efg", "e_opp_tov_rate",
         "e_opp_orb_pct", "e_margin", "e_net_slow", "rest_days", "b2b", "games_7d"]


def margin_matrix(f: pd.DataFrame, avail: bool = False) -> pd.DataFrame:
    X = pd.DataFrame({f"d_{c}": f[f"h_{c}"] - f[f"a_{c}"] for c in _DIFF}, index=f.index)
    X["elo_diff"] = f["elo_diff"]
    X["playoff"] = f["playoff"]
    if avail:   # nba.games.availability columns (h_av_*, a_av_*): positive = home is missing more
        for c in ("missing_min", "new_missing_min", "missing_pts", "missing_usg", "top_pts"):
            X[f"av_{c}_h"], X[f"av_{c}_a"] = f[f"h_av_{c}"], f[f"a_av_{c}"]
    return X


def total_matrix(f: pd.DataFrame, avail: bool = False) -> pd.DataFrame:
    X = pd.DataFrame(index=f.index)
    X["pace_sum"] = f["h_e_pace"] + f["a_e_pace"]
    X["ortg_sum"] = f["h_e_ortg"] + f["a_e_ortg"]
    X["drtg_sum"] = f["h_e_drtg"] + f["a_e_drtg"]
    X["h_off_a_def"] = f["h_e_ortg"] + f["a_e_drtg"]
    X["a_off_h_def"] = f["a_e_ortg"] + f["h_e_drtg"]
    X["efg_sum"] = f["h_e_efg"] + f["a_e_efg"]
    X["ftr_sum"] = f["h_e_ftr"] + f["a_e_ftr"]
    X["rest_sum"] = f["h_rest_days"] + f["a_rest_days"]
    X["b2b_any"] = np.maximum(f["h_b2b"], f["a_b2b"])
    X["lg_total"] = f["lg_total"]
    X["playoff"] = f["playoff"]
    if avail:
        for c in ("missing_min", "new_missing_min", "missing_pts", "missing_usg"):
            X[f"av_{c}_sum"] = f[f"h_av_{c}"] + f[f"a_av_{c}"]
        X["av_top_pts_max"] = np.maximum(f["h_av_top_pts"], f["a_av_top_pts"])
    return X
