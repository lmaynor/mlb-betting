"""nba.props.features -- leakage-safe per-player-game features from the SportsBlaze box scores.

DESIGN (why the leakage guarantee is structural, not a matter of careful shifting):
  1. History is summarised into *post-game* series -- one record per (player, game) holding rolling/EWM statistics
     INCLUDING that game; likewise per team and per (defending team, position group).
  2. A query row (player, ET game day, team, opponent) is joined to those series with
     pd.merge_asof(..., allow_exact_matches=False): it receives the most recent record on a STRICTLY EARLIER ET day.
     A row therefore can never see its own game, a same-day game, or anything later -- and the SAME code path builds
     training features (queries = historical rows) and live features (queries = tonight's quoted players, whose games
     simply are not in history yet).  tests/test_nba_props_features.py proves it by perturbation.
  3. Only information knowable pre-tip is used: no lineup, no injuries, no actual minutes. Minutes and injury
     uncertainty is the dominant NBA prop driver and is handled in the distribution (train.py variance model) and the
     scorer's abstain rules, NOT hidden in a feature the live path cannot reproduce.

Rolling series run over games the player PLAYED (minutes > 0); games missed are encoded separately (days since last
played, team games missed since / in the last 10). Windows span season boundaries on purpose (rollover).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nba.props import config as C

# short stat name -> prepared-row column
ROLL = {"min": "min", "points": "points", "rebounds": "rebounds", "assists": "assists", "fg3m": "three_points_made",
        "pra": "pra", "fga": "field_goals_attempted", "fg3a": "three_points_attempted",
        "fta": "free_throws_attempted", "tov": "turnovers", "stl": "steals", "blk": "blocks", "usg": "usg_poss",
        "oreb": "rebounds_offensive"}
TARGET_SHORT = {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "fg3m", "pra": "pra"}
DIST_STATS = ["min", "points", "rebounds", "assists", "fg3m", "pra"]          # also get median/std/season-to-date
PM_STATS = ["points", "rebounds", "assists", "fg3m", "pra", "fga", "fg3a", "fta", "tov", "usg", "oreb", "stl", "blk"]
TEAM_RATE_COLS = ["pace", "ortg", "reb100", "ast100", "fg3m100", "fg3a100"]
DPOS_STATS = ["points", "rebounds", "assists", "fg3m", "pra"]

ID_COLS = ["player_id", "date", "team", "opp", "is_home", "season_year", "season_type"]


# ----------------------------------------------------------------------------- generic helpers
def _days(s: pd.Series) -> np.ndarray:
    return (pd.to_datetime(s).values.astype("datetime64[D]").astype("int64"))


def window_ratio(days: np.ndarray, vals: np.ndarray, w: np.ndarray, q_days: np.ndarray, window: int) -> np.ndarray:
    """For each query day d: sum(vals over rows with day in [d-window, d-1]) / sum(w over those rows).

    `days` must be sorted ascending. An empty window (offseason) falls back to the last non-empty window ending at the
    most recent earlier row; no earlier row at all -> NaN. Strictly-earlier days only (no leakage of day d itself)."""
    vals = np.asarray(vals, dtype=float)
    if vals.ndim == 1:
        vals = vals[:, None]
    k = vals.shape[1]
    cs = np.vstack([np.zeros((1, k)), np.cumsum(np.nan_to_num(vals), axis=0)])
    cw = np.concatenate([[0.0], np.cumsum(w)])
    hi = np.searchsorted(days, q_days, side="left")
    lo = np.searchsorted(days, q_days - window, side="left")
    num = cs[hi] - cs[lo]
    den = (cw[hi] - cw[lo]).astype(float)
    empty = (den <= 0) & (hi > 0)
    if empty.any():
        last = hi[empty] - 1
        lo2 = np.searchsorted(days, days[last] - window + 1, side="left")
        num[empty] = cs[hi[empty]] - cs[lo2]
        den[empty] = cw[hi[empty]] - cw[lo2]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den[:, None] > 0, num / den[:, None], np.nan)
    return out


def _roll_group(x: pd.Series, key: pd.Series, w: int, fn: str, min_periods: int) -> pd.Series:
    r = x.groupby(key, sort=False).rolling(w, min_periods=min_periods)
    out = getattr(r, fn)()
    return out.reset_index(level=0, drop=True).reindex(x.index)


def _ewm_group(x: pd.Series, key: pd.Series, halflife: float) -> pd.Series:
    return x.groupby(key, sort=False).transform(lambda s: s.ewm(halflife=halflife).mean())


# ----------------------------------------------------------------------------- post-game series
def team_index(tg: pd.DataFrame) -> pd.DataFrame:
    """Adds `tgn`: the team's running game number (continuous across seasons; preseason excluded)."""
    tg = tg.sort_values(["tip_sort", "game_id"]).copy()
    tg["tgn"] = tg.groupby("team").cumcount() + 1
    return tg


def player_series(rows: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """Post-game rolling record per (player, played game). Columns: player_id, date, tip_sort + features + last_*."""
    r = rows.merge(tg[["game_id", "team", "tgn", "t_usg"]], on=["game_id", "team"], how="left")
    r = r.sort_values(["player_id", "tip_sort", "game_id"]).reset_index(drop=True)
    pid = r["player_id"]
    out: dict = {}
    base: dict = {}
    for short, col in ROLL.items():
        x = r[col].astype(float)
        base[short] = x
        for w in C.WINDOWS:
            out[f"{short}_l{w}"] = _roll_group(x, pid, w, "mean", 1)
        for h in C.EWM_HALFLIVES:
            out[f"{short}_ewm{h}"] = _ewm_group(x, pid, h)
    for short in DIST_STATS:
        x = base[short]
        out[f"{short}_med10"] = _roll_group(x, pid, 10, "median", 3)
        out[f"{short}_std10"] = _roll_group(x, pid, 10, "std", 3)
        gs = x.groupby([pid, r["season_year"]], sort=False)
        out[f"{short}_szn"] = gs.cumsum() / (gs.cumcount() + 1)
    out["min_max10"] = _roll_group(base["min"], pid, 10, "max", 1)
    out["min_min10"] = _roll_group(base["min"], pid, 10, "min", 1)
    for h in C.EWM_HALFLIVES:
        for short in PM_STATS:
            out[f"pm_{short}_ewm{h}"] = out[f"{short}_ewm{h}"] / out[f"min_ewm{h}"].clip(lower=1.0)
    out["starter_l10"] = _roll_group(r["starter"].astype(float), pid, 10, "mean", 1)
    share = r["usg_poss"] / r["t_usg"].clip(lower=1.0)
    out["usg_share_ewm8"] = _ewm_group(share, pid, 8)
    out["n_played"] = r.groupby("player_id", sort=False).cumcount() + 1
    out["n_szn"] = r.groupby(["player_id", "season_year"], sort=False).cumcount() + 1
    ps = pd.DataFrame(out)
    ps["player_id"] = r["player_id"].values
    ps["date"] = r["date"].values
    ps["tip_sort"] = r["tip_sort"].values
    ps["last_team"] = r["team"].values
    ps["last_tgn"] = r["tgn"].values
    ps["last_season"] = r["season_year"].values
    ps["last_min"] = r["min"].values
    ps["last_pos_grp"] = r["position"].map(C.POS_GROUP).values
    ps["last_date"] = r["date"].values
    return ps


def team_series(tg: pd.DataFrame) -> pd.DataFrame:
    """Post-game per-team EWM of pace / offense / defense / rebound-assist-three rates (own and allowed)."""
    t = tg.copy()
    p = t["poss"].clip(lower=1.0)
    t["reb100"] = 100.0 * t["t_reb"] / p
    t["ast100"] = 100.0 * t["t_ast"] / p
    t["fg3m100"] = 100.0 * t["t_fg3m"] / p
    t["fg3a100"] = 100.0 * t["t_fg3a"] / p
    opp = t[["game_id", "team"] + TEAM_RATE_COLS].rename(columns={"team": "opp", **{c: f"a_{c}" for c in TEAM_RATE_COLS}})
    t = t.merge(opp, on=["game_id", "opp"], how="left")
    t = t.sort_values(["team", "tip_sort", "game_id"]).reset_index(drop=True)
    cols = TEAM_RATE_COLS + [f"a_{c}" for c in TEAM_RATE_COLS if c not in ("pace",)]
    for c in cols:
        t[f"e_{c}"] = _ewm_group(t[c], t["team"], C.TEAM_EWM_HALFLIFE)
    keep = ["team", "date", "tip_sort"] + [f"e_{c}" for c in cols]
    return t[keep]


def dpos_series(rows: pd.DataFrame) -> pd.DataFrame:
    """Post-game EWM rate (per minute) that (defending team, position group) has ALLOWED, per target stat."""
    r = rows[rows["pos_grp"].notna()]
    num_cols = {"points": "points", "rebounds": "rebounds", "assists": "assists", "fg3m": "three_points_made",
                "pra": "pra"}
    agg = r.groupby(["game_id", "opp", "pos_grp"]).agg(
        date=("date", "first"), tip_sort=("tip_sort", "first"), den=("min", "sum"),
        **{f"n_{k}": (v, "sum") for k, v in num_cols.items()}).reset_index().rename(columns={"opp": "def_team"})
    agg = agg.sort_values(["def_team", "pos_grp", "tip_sort", "game_id"]).reset_index(drop=True)
    key = [agg["def_team"], agg["pos_grp"]]
    den_e = agg["den"].groupby(key, sort=False).transform(lambda s: s.ewm(halflife=C.TEAM_EWM_HALFLIFE).mean())
    out = agg[["def_team", "pos_grp", "date", "tip_sort"]].copy()
    for k in num_cols:
        num_e = agg[f"n_{k}"].groupby(key, sort=False).transform(
            lambda s: s.ewm(halflife=C.TEAM_EWM_HALFLIFE).mean())
        out[f"e_dp_{k}"] = num_e / den_e.clip(lower=1.0)
    league = {}
    for g, a in agg.groupby("pos_grp"):
        a = a.sort_values("tip_sort")
        league[g] = (_days(a["date"]), a[[f"n_{k}" for k in num_cols]].to_numpy(float), a["den"].to_numpy(float))
    return out, league, list(num_cols)


# ----------------------------------------------------------------------------- the context
class FeatureContext:
    """Everything needed to featurise any query row against a fixed history. Build once, call .features(queries)."""

    def __init__(self, rows: pd.DataFrame, tg: pd.DataFrame):
        self.tg = team_index(tg)
        self.ps = player_series(rows, self.tg)
        self._ps_right = (self.ps.drop(columns=["tip_sort"]).rename(columns={"last_date": "_ld"})
                          .sort_values("date", kind="stable").reset_index(drop=True))
        self.ts = team_series(self.tg)
        self.dp, self._dp_league, self._dp_stats = dpos_series(rows)
        t = self.tg
        self._team_days = {k: _days(v["date"]) for k, v in t.sort_values("tip_sort").groupby("team")}
        tgd = t.sort_values("date")
        self._lg_days = _days(tgd["date"])
        p = tgd["poss"].clip(lower=1.0)
        lg = pd.DataFrame({"pace": tgd["pace"], "ortg": tgd["ortg"], "reb100": 100.0 * tgd["t_reb"] / p,
                           "ast100": 100.0 * tgd["t_ast"] / p, "fg3m100": 100.0 * tgd["t_fg3m"] / p,
                           "fg3a100": 100.0 * tgd["t_fg3a"] / p})
        self._lg_vals = lg[TEAM_RATE_COLS].to_numpy(float)
        # (player, team) -> sorted tgn of PLAYED games, for "games missed in the last N team games"
        r = rows.merge(t[["game_id", "team", "tgn"]], on=["game_id", "team"], how="left")
        self._pt_tgn = {k: np.sort(v.to_numpy()) for k, v in r.groupby(["player_id", "team"])["tgn"]}
        self.last_date = rows["date"].max()
        self.last_season = int(rows.loc[rows["date"] == self.last_date, "season_year"].max())

    # -- team schedule helpers ----------------------------------------------------------------------------
    def _schedule(self, teams: np.ndarray, qdays: np.ndarray, prefix: str) -> dict:
        n = len(teams)
        tgn_before = np.zeros(n, dtype=float)
        rest = np.full(n, np.nan)
        g7 = np.zeros(n, dtype=float)
        g14 = np.zeros(n, dtype=float)
        for team in np.unique(teams):
            idx = np.where(teams == team)[0]
            d = self._team_days.get(team)
            if d is None or not len(d):
                tgn_before[idx], g7[idx], g14[idx] = np.nan, np.nan, np.nan
                continue
            hi = np.searchsorted(d, qdays[idx], side="left")
            tgn_before[idx] = hi
            ok = hi > 0
            rest[idx[ok]] = qdays[idx][ok] - d[hi[ok] - 1]
            g7[idx] = hi - np.searchsorted(d, qdays[idx] - C.SCHEDULE_WINDOW_DAYS, side="left")
            g14[idx] = hi - np.searchsorted(d, qdays[idx] - 14, side="left")
        out = {f"{prefix}rest_days": np.minimum(rest, 30.0), f"{prefix}b2b": (rest <= 1).astype(float),
               f"{prefix}g7": g7, f"{prefix}g14": g14}
        out[f"{prefix}b2b"][np.isnan(rest)] = np.nan
        return out, tgn_before

    def features(self, q: pd.DataFrame) -> pd.DataFrame:
        """Feature matrix for query rows (columns ID_COLS; extra columns ignored). Index preserved."""
        q = q[ID_COLS].copy()
        q["date"] = pd.to_datetime(q["date"])
        q["_i"] = np.arange(len(q))
        orig_index = q.index
        q = q.reset_index(drop=True)

        # 1. player's most recent PRIOR record (strictly earlier ET day)
        left = q.sort_values("date")
        m = pd.merge_asof(left, self._ps_right, on="date", by="player_id", allow_exact_matches=False,
                          direction="backward")
        m = m.sort_values("_i").reset_index(drop=True)
        feat: dict = {}                       # built as a dict and assembled once (avoids a fragmented frame)
        for c in m.columns:
            if c in ID_COLS or c in ("_i", "_ld", "last_team", "last_tgn", "last_season", "last_pos_grp", "last_min"):
                continue
            feat[c] = m[c]
        same_season = (m["last_season"] == m["season_year"])
        for short in DIST_STATS:                       # season-to-date only counts games of THIS season
            feat[f"{short}_szn"] = m[f"{short}_szn"].where(same_season)
        feat["n_szn"] = m["n_szn"].where(same_season, 0.0)
        feat["n_played"] = m["n_played"].fillna(0.0)
        qday = _days(m["date"])
        ld = pd.to_datetime(m["_ld"])
        feat["days_since_last"] = (m["date"] - ld).dt.days.astype(float)
        feat["last_min"] = m["last_min"]
        feat["team_changed"] = np.where(m["last_team"].isna(), np.nan, (m["last_team"] != m["team"]).astype(float))
        feat["season_rollover"] = np.where(m["last_season"].isna(), np.nan, (~same_season).astype(float))

        # 2. schedule density (own + opponent) and games missed
        teams = m["team"].to_numpy(object)
        opps = m["opp"].to_numpy(object)
        sch_t, tgn_before = self._schedule(teams, qday, "tm_")
        sch_o, _ = self._schedule(opps, qday, "opp_")
        for k, v in {**sch_t, **sch_o}.items():
            feat[k] = v
        tgn_last = m["last_tgn"].to_numpy(float)
        same_team = (m["last_team"] == m["team"]).to_numpy()
        feat["team_games_since_last"] = np.where(same_team, tgn_before - tgn_last, np.nan)
        pids = m["player_id"].to_numpy(object)
        miss5 = np.full(len(m), np.nan)
        miss10 = np.full(len(m), np.nan)
        miss20 = np.full(len(m), np.nan)
        grp = pd.Series(np.arange(len(m))).groupby([pids, teams]).indices
        for (pid, team), idx in grp.items():
            arr = self._pt_tgn.get((pid, team))
            tb = tgn_before[idx]
            for w, out in ((5, miss5), (10, miss10), (20, miss20)):
                if arr is None or not len(arr):
                    out[idx] = np.minimum(w, tb)               # never played for this team: every window game "missed"
                    continue
                played = np.searchsorted(arr, tb, side="right") - np.searchsorted(arr, tb - w, side="right")
                out[idx] = np.minimum(w, tb) - played
        feat["missed_l5"], feat["missed_l10"], feat["missed_l20"] = miss5, miss10, miss20

        # 3. team + opponent strength (EWM of prior games) relative to the league level
        lg = window_ratio(self._lg_days, self._lg_vals, np.ones(len(self._lg_days)), qday, C.LEAGUE_WINDOW_DAYS)
        lgd = pd.DataFrame(lg, columns=TEAM_RATE_COLS)
        ts = self.ts.sort_values("date")
        for side, key in (("tm_", "team"), ("opp_", "opp")):
            lj = m[["_i", "date", key]].rename(columns={key: "team"}).sort_values("date")
            mt = pd.merge_asof(lj, ts.drop(columns=["tip_sort"]), on="date", by="team",
                               allow_exact_matches=False, direction="backward").sort_values("_i").reset_index(drop=True)
            for c in TEAM_RATE_COLS:
                feat[f"{side}{c}_rel"] = mt[f"e_{c}"] / lgd[c]
                if c != "pace":
                    feat[f"{side}a_{c}_rel"] = mt[f"e_a_{c}"] / lgd[c]
        feat["exp_pace_rel"] = (feat["tm_pace_rel"] + feat["opp_pace_rel"]) / 2.0
        feat["exp_pace_x_min"] = feat["exp_pace_rel"] * feat["min_ewm8"]

        # 4. position-group matchup: what the opponent allows per minute to players of this group
        dpj = pd.DataFrame({"_i": m["_i"], "date": m["date"], "def_team": m["opp"],
                            "pos_grp": m["last_pos_grp"]}).sort_values("date")
        dpm = pd.merge_asof(dpj, self.dp.drop(columns=["tip_sort"]).sort_values("date"), on="date",
                            by=["def_team", "pos_grp"], allow_exact_matches=False,
                            direction="backward").sort_values("_i").reset_index(drop=True)
        for k_i, k in enumerate(self._dp_stats):
            rel = np.full(len(m), np.nan)
            for g in ("G", "W", "B"):
                sel = np.where(m["last_pos_grp"].to_numpy(object) == g)[0]
                if not len(sel) or g not in self._dp_league:
                    continue
                days, nums, den = self._dp_league[g]
                lr = window_ratio(days, nums[:, k_i], den, qday[sel], C.LEAGUE_WINDOW_DAYS)[:, 0]
                rel[sel] = dpm[f"e_dp_{k}"].to_numpy(float)[sel] / lr
            feat[f"opp_pos_{k}_rel"] = rel
        feat["pos_grp_code"] = m["last_pos_grp"].map({"G": 0.0, "W": 1.0, "B": 2.0})

        # 5. context
        feat["is_home"] = m["is_home"].astype(float)
        feat["is_playoff"] = (m["season_type"] == "Playoffs").astype(float)
        feat["min_ratio_l5_l20"] = feat["min_l5"] / feat["min_l20"].clip(lower=1.0)
        feat["min_cv10"] = feat["min_std10"] / feat["min_l10"].clip(lower=1.0)
        out = pd.DataFrame(feat, index=m.index)
        out.index = orig_index
        return out


def build_training_frame(rows: pd.DataFrame, tg: pd.DataFrame, ctx: FeatureContext | None = None):
    """Features + targets for every played row (the row's own game is excluded from its features by construction).

    -> (X, meta) where meta holds game_id, player_id, date, season_year, season_type, team, opp, min and the five
    target columns. Rows with fewer than MIN_HISTORY_GAMES prior played games are dropped."""
    ctx = ctx or FeatureContext(rows, tg)
    X = ctx.features(rows[ID_COLS])
    meta = rows[["game_id", "player_id", "name", "date", "season_year", "season_type", "team", "opp", "min", "starter",
                 "points", "rebounds", "assists", "three_points_made", "pra"]].rename(
        columns={"three_points_made": "threes"}).copy()
    ok = X["n_played"] >= C.MIN_HISTORY_GAMES
    return X[ok], meta[ok]
