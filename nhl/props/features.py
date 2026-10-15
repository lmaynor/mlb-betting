"""Leakage-safe rolling features for the NHL SOG model.

Contract (pinned by tests/test_nhl_props_features.py): the feature vector of a row (player P, game G, date D) is a
function of P's appearances STRICTLY BEFORE G and of team-level results from games STRICTLY BEFORE G. Implemented with
`shift(1)` on every per-entity series before any rolling/ewm window, so a row can never see its own game or any later
one. Rows whose own outcome is unknown (upcoming-game placeholders: sog / toi_sec NaN) go through the SAME code path --
that is how the live scorer builds today's features, so train and serve cannot drift.

FEATURE SETS (versioned; the default is UNCHANGED so existing artifacts and tests are untouched):
  FEATURES     ("v1", default)  what `xgb_nhl_sog_v1.json` was trained on.
  FEATURES_V2  v1 + V2_EXTRA    opt-in via build_features(gl, extras=True): sog_xg_l10, high_danger_sog_l10 (from the
               play-by-play shots table; see gamelog.xg_proxy -- a distance/angle PROXY, the shots master has no xG
               column) and team_missing_sog_l10 (SOG of absent top-6 forwards / top-4 defensemen). A model trained on
               V2 carries V2 in its meta `features` list; train.py already reads that list from the meta, never from here.
  team_missing_sog_l10 depends on WHO DRESSED in the row's own game (training: rotation players absent from that game's
  lineup). That is pre-game information (lineups/scratches are known before the puck drops), outcome-adjacent for OTHER
  players only: the summed per-player numbers are strictly pre-game, and a player's own row never counts himself. Live:
  availability={team: ids on the injury/scratch list} -> same code path; a team absent from the mapping -> NaN.

NO market-derived feature (line, price, implied total, consensus) is used anywhere: the model must be independent of the
market so that comparing the two is meaningful.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nhl.props.gamelog import team_games

FEATURES = [
    "sog_l5", "sog_l10", "sog_l20", "sog_ewm10", "sog_per60_l20", "sog_per60_l60",
    "toi_l5", "toi_l10", "toi_ewm10",
    "att_l10", "att_per60_l20", "pp_att_l10",
    "team_sf_l10", "opp_sa_l10", "opp_sa_l30", "lg_sf_l30",
    "is_home", "rest_days", "opp_rest_days", "is_d", "is_c", "n_prior",
]
V2_EXTRA = ["sog_xg_l10", "high_danger_sog_l10", "team_missing_sog_l10"]
FEATURES_V2 = FEATURES + V2_EXTRA
KEYS = ["player_id", "game_id", "date", "team", "opp"]

TOP_FORWARDS, TOP_DEFENSE = 6, 4          # "top" = highest trailing-L10 TOI among the rotation, per position group
ROTATION_GAMES = 5                        # rotation = dressed for the team in >= 1 of its previous N games
ROTATION_MAX_GAP_DAYS = 30                # previous team game further back (offseason) -> NaN, not guessed


def feature_list(version: str = "v1") -> list:
    """Versioned feature list. 'v1' (default) is the list existing artifacts were trained on."""
    if version == "v1":
        return list(FEATURES)
    if version == "v2":
        return list(FEATURES_V2)
    raise ValueError(f"unknown feature set {version!r}")


def _roll(s: pd.Series, by: pd.Series, w: int, how: str = "mean", min_periods: int = 1) -> pd.Series:
    """Rolling window over the PRIOR rows of each group (shift(1) first -> never the current row)."""
    sh = s.groupby(by).shift(1)
    return sh.groupby(by).transform(lambda x: getattr(x.rolling(w, min_periods=min_periods), how)())


def _ewm(s: pd.Series, by: pd.Series, halflife: float) -> pd.Series:
    sh = s.groupby(by).shift(1)
    return sh.groupby(by).transform(lambda x: x.ewm(halflife=halflife, min_periods=1).mean())


def _team_missing_sog(g: pd.DataFrame, availability) -> pd.Series:
    """team_missing_sog_l10 per row of `g` (default RangeIndex). See the module docstring for the definition.

    availability = "lineup" (training: who dressed in the row's own game, from `g`) or {team: injured/scratched ids}."""
    n = len(g)
    res = np.full(n, np.nan)
    if not n:
        return pd.Series(res, index=g.index)
    dt = pd.to_datetime(g["date"]).astype("datetime64[ns]")
    played = g["toi_sec"].notna().to_numpy()
    gp = g.loc[played, ["player_id", "game_id", "team", "position", "toi_sec", "sog"]].copy()
    gp["dt"] = dt[played].to_numpy()
    gp = gp.sort_values(["player_id", "dt", "game_id"])
    byp = gp.groupby("player_id", sort=False)
    # POST-game trailing windows (include the game just played); merged back with allow_exact_matches=False so a lookup
    # at game G only ever sees records from strictly earlier dates.
    pp = pd.DataFrame({
        "player_id": gp["player_id"].to_numpy(), "dt": gp["dt"].to_numpy(),
        "toi10": byp["toi_sec"].transform(lambda x: x.rolling(10, min_periods=1).mean()).to_numpy() / 60.0,
        "sog10": byp["sog"].transform(lambda x: x.rolling(10, min_periods=1).mean()).to_numpy(),
        "last_team": gp["team"].to_numpy(), "pos": gp["position"].to_numpy()}).sort_values("dt", kind="stable")
    # team games that were actually played (>= 1 skater with a TOI), numbered per team in date order
    tgp = (gp.groupby(["team", "game_id"], as_index=False).agg(dt=("dt", "first"))
             .sort_values(["team", "dt", "game_id"]))
    tgp["tgn"] = tgp.groupby("team").cumcount() + 1
    days = {t: v["dt"].to_numpy().astype("datetime64[D]").astype("int64") for t, v in tgp.groupby("team")}
    tgn_of = {(t, gid): k for t, gid, k in zip(tgp["team"], tgp["game_id"], tgp["tgn"])}
    roster = {k: v.to_numpy() for k, v in gp.merge(tgp[["team", "game_id", "tgn"]], on=["team", "game_id"])
              .groupby(["team", "tgn"])["player_id"]}
    # per target (team, game): is every skater row an observed appearance? (placeholders have NaN TOI)
    grp = g.assign(_dt=dt, _pl=played).groupby(["team", "game_id"], sort=False).agg(
        dt=("_dt", "first"), all_played=("_pl", "all"))
    cand_rows, valid = [], set()
    for (team, gid), r in zip(grp.index, grp.itertuples(index=False)):
        d = days.get(team)
        qd = int(np.datetime64(r.dt, "D").astype("int64"))
        if d is None:
            continue
        tb = int(np.searchsorted(d, qd, side="left"))
        if tb < 1 or qd - int(d[tb - 1]) > ROTATION_MAX_GAP_DAYS:
            continue
        rot: set = set()
        for k in range(max(1, tb - ROTATION_GAMES + 1), tb + 1):
            rot.update(roster.get((team, k), ()))
        if isinstance(availability, str):
            if availability != "lineup":
                raise ValueError(f"availability must be 'lineup' or a mapping, got {availability!r}")
            if not r.all_played or (team, gid) not in tgn_of:
                continue                                       # lineup of this game not observed -> unknown
            active = set(roster.get((team, tgn_of[(team, gid)]), ()))
            absent = {p for p in rot if p not in active}
        else:
            inj = availability.get(team)
            if inj is None:
                continue
            absent = rot & set(inj)
        valid.add((team, gid))
        for p in rot:
            cand_rows.append((team, gid, qd, p, p in absent))
    if not valid:
        return pd.Series(res, index=g.index)
    cand = pd.DataFrame(cand_rows, columns=["team", "game_id", "qd", "player_id", "absent"])
    cand["dt"] = cand["qd"].to_numpy().astype("datetime64[D]").astype("datetime64[ns]")
    cand = cand.sort_values("dt", kind="stable")
    cand = pd.merge_asof(cand, pp, on="dt", by="player_id", allow_exact_matches=False, direction="backward")
    cand = cand[(cand["last_team"] == cand["team"]) & cand["toi10"].notna()].copy()
    cand["is_d"] = (cand["pos"] == "D")
    # rank by trailing TOI within the rotation, per (game, team, position group); ties broken by player id
    cand = cand.sort_values(["team", "game_id", "is_d", "toi10", "player_id"], ascending=[True, True, True, False, True])
    cand["rk"] = cand.groupby(["team", "game_id", "is_d"]).cumcount() + 1
    cand["top"] = np.where(cand["is_d"], cand["rk"] <= TOP_DEFENSE, cand["rk"] <= TOP_FORWARDS)
    ab = cand[cand["absent"] & cand["top"]]
    sums = {k: 0.0 for k in valid}
    contrib = {}
    for team, gid, pid, sg in zip(ab["team"], ab["game_id"], ab["player_id"], ab["sog10"]):
        v = 0.0 if pd.isna(sg) else float(sg)
        sums[(team, gid)] += v
        contrib[(team, gid, pid)] = v
    for i, (team, gid, pid) in enumerate(zip(g["team"], g["game_id"], g["player_id"])):
        k = (team, gid)
        if k in sums:
            res[i] = sums[k] - contrib.get((team, gid, pid), 0.0)     # own-row exclusion
    return pd.Series(res, index=g.index)


def build_features(gl: pd.DataFrame, extras: bool = False, availability="lineup") -> pd.DataFrame:
    """Canonical game log (see gamelog.py; may include NaN-outcome placeholder rows) -> one feature row per game-log row.

    extras=False (default): the v1 columns, byte-for-byte as before. extras=True: also V2_EXTRA (FEATURES_V2).
    `availability` only matters with extras=True: "lineup" (training; placeholder games -> NaN) or
    {team: injured/scratched player ids} (live)."""
    gl = gl.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True).copy()
    gl["dt"] = pd.to_datetime(gl["date"])
    pid = gl["player_id"]
    toi_min = gl["toi_sec"] / 60.0
    ordered = gl.assign(_p=pid).sort_values(["_p", "dt", "game_id"])      # per-player chronological order for rolling
    inv = ordered.index
    o_pid = ordered["player_id"]
    o_toi_min = ordered["toi_sec"] / 60.0

    out = pd.DataFrame(index=ordered.index)
    out["sog_l5"] = _roll(ordered["sog"], o_pid, 5)
    out["sog_l10"] = _roll(ordered["sog"], o_pid, 10)
    out["sog_l20"] = _roll(ordered["sog"], o_pid, 20)
    out["sog_ewm10"] = _ewm(ordered["sog"], o_pid, 10)
    for w, name in ((20, "sog_per60_l20"), (60, "sog_per60_l60")):
        s_sog = _roll(ordered["sog"], o_pid, w, "sum", min_periods=3)
        s_toi = _roll(ordered["toi_sec"], o_pid, w, "sum", min_periods=3)
        out[name] = s_sog / s_toi.where(s_toi > 0) * 3600.0
    out["toi_l5"] = _roll(o_toi_min, o_pid, 5)
    out["toi_l10"] = _roll(o_toi_min, o_pid, 10)
    out["toi_ewm10"] = _ewm(o_toi_min, o_pid, 10)
    out["att_l10"] = _roll(ordered["attempts"], o_pid, 10)
    a_sum = _roll(ordered["attempts"], o_pid, 20, "sum", min_periods=3)
    t_sum = _roll(ordered["toi_sec"].where(ordered["attempts"].notna()), o_pid, 20, "sum", min_periods=3)
    out["att_per60_l20"] = a_sum / t_sum.where(t_sum > 0) * 3600.0
    out["pp_att_l10"] = _roll(ordered["pp_attempts"], o_pid, 10)
    out["n_prior"] = ordered.groupby("player_id").cumcount().astype(float)
    if extras:
        for src, name in (("sog_xg", "sog_xg_l10"), ("hd_sog", "high_danger_sog_l10")):
            out[name] = _roll(ordered[src], o_pid, 10) if src in ordered.columns else np.nan
    out = out.reindex(gl.index)          # back to the date-sorted order of `gl`

    # ---- team-level: own offence, opposing defence, rest. Built from played rows, shifted per team.
    tg = team_games(gl)
    tg["dt"] = pd.to_datetime(tg["date"])
    tby = tg["team"]
    tg["team_sf_l10"] = _roll(tg["sf"], tby, 10)
    tg["opp_sa_l10_own"] = _roll(tg["sa"], tby, 10)        # this team's shots-allowed form (used when it is the OPPONENT)
    tg["opp_sa_l30_own"] = _roll(tg["sa"], tby, 30)
    prev = tg.groupby("team")["dt"].shift(1)
    tg["rest_days"] = ((tg["dt"] - prev).dt.days).clip(upper=7).fillna(7)
    # league-wide shot environment (era drift: ~31 team SOG/game in 2019-20 vs ~28 in 2025-26): mean team shots-for over the
    # previous 30 distinct game DAYS, strictly before this game's date.
    daily = tg.dropna(subset=["sf"]).groupby("dt")["sf"].mean().sort_index()
    roll = daily.rolling(30, min_periods=1).mean()                    # window ENDING on each data date (inclusive)
    pos = daily.index.searchsorted(tg["dt"].to_numpy(), side="left") - 1       # last data date STRICTLY before this game
    tg["lg_sf_l30"] = np.where(pos >= 0, roll.to_numpy()[np.maximum(pos, 0)], np.nan) if len(daily) else np.nan
    own = tg[["game_id", "team", "team_sf_l10", "rest_days", "lg_sf_l30"]]
    opp = tg[["game_id", "team", "opp_sa_l10_own", "opp_sa_l30_own", "rest_days"]].rename(
        columns={"team": "opp", "opp_sa_l10_own": "opp_sa_l10", "opp_sa_l30_own": "opp_sa_l30",
                 "rest_days": "opp_rest_days"})
    feat = gl[KEYS + ["is_home", "position", "sog", "toi_sec"]].join(out)
    feat = feat.merge(own, on=["game_id", "team"], how="left").merge(opp, on=["game_id", "opp"], how="left")
    feat["is_d"] = (feat["position"] == "D").astype(float)
    feat["is_c"] = (feat["position"] == "C").astype(float)
    feat["is_home"] = feat["is_home"].astype(float)
    if extras:
        feat["team_missing_sog_l10"] = _team_missing_sog(gl.reset_index(drop=True), availability).to_numpy()
        return feat[KEYS + FEATURES_V2 + ["position", "sog", "toi_sec"]].reset_index(drop=True)
    return feat[KEYS + FEATURES + ["position", "sog", "toi_sec"]].reset_index(drop=True)
