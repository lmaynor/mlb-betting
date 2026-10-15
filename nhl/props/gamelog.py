"""Skater game-log loaders: every data source is normalised to ONE canonical frame so the feature/training code is
identical whether it runs on our own NHL masters (2019+) or on the 2025-26-only `nhl_data.db` used for the validation
study.

Canonical columns (one row per skater appearance):
    player_id (int)  game_id (int)  date ('YYYY-MM-DD', the ET game date)  team  opp  is_home (0/1)  position
    toi_sec (float)  sog (float)  attempts (float, NaN if unknown)  pp_attempts (float, NaN if unknown)
    goals  assists
    sog_xg (float, NaN if no shot geometry)  hd_sog (float, NaN if no shot geometry)   -- from the pbp shots table only
Only regular-season / playoff games (game_type 2, 3): preseason lineups are not representative.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

GAMELOG_COLS = ["player_id", "game_id", "date", "team", "opp", "is_home", "position", "toi_sec", "sog", "attempts",
                "pp_attempts", "goals", "assists", "sog_xg", "hd_sog"]

# ---- shot-quality PROXY. The shots master (nhl/data/flatten.py) carries x/y/dist_ft/angle_deg/shot_type but NO xG column,
# so `sog_xg` is a FIXED, hand-set distance/angle function -- NOT a fitted or calibrated expected-goals model (fitting it
# on the full history would leak the future into early rows, and nothing here claims calibration). It only has to rank
# shots by danger so the SOG model can see "this player takes shots from the slot".
XG_PROXY_SCALE_FT = 22.0
HIGH_DANGER_MAX_DIST_FT = 25.0
HIGH_DANGER_MAX_ANGLE_DEG = 45.0
SOG_EVENTS = ("sog", "goal")           # shots that reached the net (blocks and misses are not SOG)


def xg_proxy(dist_ft, angle_deg):
    """PROXY expected-goal value of a shot: 0.45 * exp(-dist/22) * (1 - 0.55 * angle/90). Monotone in distance and angle,
    ~0.27 from 10 ft in the slot, ~0.03 from 60 ft. NaN geometry -> NaN. Fixed constants; see the block comment above."""
    d = np.clip(np.asarray(dist_ft, dtype=float), 3.0, 100.0)
    a = np.clip(np.asarray(angle_deg, dtype=float), 0.0, 90.0)
    return 0.45 * np.exp(-d / XG_PROXY_SCALE_FT) * (1.0 - 0.55 * a / 90.0)


def game_type_from_id(game_id) -> int:
    """NHL game ids are YYYY TT NNNN (season start year, 2-digit type, 4-digit number): 2025020780 -> 2."""
    return int(str(int(game_id))[4:6])


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    for c in GAMELOG_COLS:
        if c not in df.columns:
            df[c] = np.nan
    df = df[GAMELOG_COLS].copy()
    df["player_id"] = df["player_id"].astype("int64")
    df["game_id"] = df["game_id"].astype("int64")
    df["is_home"] = df["is_home"].astype(int)
    return df.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def from_masters(games: pd.DataFrame, skaters: pd.DataFrame, shots: pd.DataFrame | None = None) -> pd.DataFrame:
    """Our NHL masters (nhl/data/flatten.py schema). `shots` (shots_{season}.csv rows, optional) supplies shot ATTEMPTS
    (sog + miss + block credited to the shooter) and power-play attempts (skaters_for > skaters_against)."""
    g = games[games["game_type"].isin([2, 3])][["game_id", "date", "away_abbr", "home_abbr"]]
    s = skaters.merge(g, on="game_id", how="inner", suffixes=("_s", ""))
    if "date_s" in s.columns:
        s = s.drop(columns=["date_s"])
    s = s[s["played"].astype(bool)] if "played" in s.columns else s
    s["team"] = s["team_abbr"]
    s["opp"] = np.where(s["is_home"].astype(bool), s["away_abbr"], s["home_abbr"])
    s = s.rename(columns={"sog": "sog"})
    if shots is not None and len(shots):
        sh = shots[shots["shooter_id"].notna() & shots["event"].isin(["sog", "miss", "block", "goal"])].copy()
        sh["pp"] = (sh["skaters_for"] > sh["skaters_against"]).astype(int)
        agg = sh.groupby(["game_id", "shooter_id"]).agg(attempts=("event", "size"), pp_attempts=("pp", "sum")).reset_index()
        agg = agg.rename(columns={"shooter_id": "player_id"})
        agg["player_id"] = agg["player_id"].astype("int64")
        s = s.merge(agg, on=["game_id", "player_id"], how="left")
        s = _add_shot_quality(s, sh)
        # a skater who played but never shot has no shot rows: 0, not unknown -- but only where the game HAS pbp shots
        have = set(sh["game_id"].unique())
        in_have = s["game_id"].isin(have)
        s.loc[in_have, ["attempts", "pp_attempts"]] = s.loc[in_have, ["attempts", "pp_attempts"]].fillna(0)
    return _finish(s)


def _add_shot_quality(s: pd.DataFrame, sh: pd.DataFrame) -> pd.DataFrame:
    """Per (game, shooter): sog_xg = sum of xg_proxy over his shots ON GOAL (sog + goal; empty-net shots excluded) and
    hd_sog = count of those inside the high-danger box. NaN where the game has no geometry at all (a missing season is
    unknown, never zero); 0 where the game has geometry but this skater had no qualifying shot."""
    if "dist_ft" not in sh.columns or "angle_deg" not in sh.columns:
        s["sog_xg"], s["hd_sog"] = np.nan, np.nan
        return s
    q = sh[sh["event"].isin(SOG_EVENTS)].copy()
    if "empty_net" in q.columns:
        # CSV round-trips can leave this column as the strings 'True'/'False' (mixed dtype): never bool('False')
        q = q[~q["empty_net"].map(lambda v: str(v).strip().lower() == "true")]
    q["xg"] = xg_proxy(q["dist_ft"], q["angle_deg"])
    q["hd"] = ((q["dist_ft"] <= HIGH_DANGER_MAX_DIST_FT) & (q["angle_deg"] <= HIGH_DANGER_MAX_ANGLE_DEG)).astype(float)
    q.loc[q["dist_ft"].isna() | q["angle_deg"].isna(), "hd"] = np.nan
    agg = q.groupby(["game_id", "shooter_id"]).agg(sog_xg=("xg", lambda x: x.sum(min_count=1)),
                                                     hd_sog=("hd", lambda x: x.sum(min_count=1))).reset_index()
    agg = agg.rename(columns={"shooter_id": "player_id"})
    agg["player_id"] = agg["player_id"].astype("int64")
    s = s.merge(agg, on=["game_id", "player_id"], how="left")
    have_geo = set(sh.loc[sh["dist_ft"].notna() & sh["angle_deg"].notna(), "game_id"].unique())
    m = s["game_id"].isin(have_geo)
    s.loc[m, ["sog_xg", "hd_sog"]] = s.loc[m, ["sog_xg", "hd_sog"]].fillna(0.0)
    return s


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def from_history_db(path: str) -> pd.DataFrame:
    """The 2025-26 `nhl_data.db` (read-only). Attempts / PP attempts come from the MoneyPuck-derived `mp_player_game`
    (situation 'all' / '5on4'), which is the same definition as the shot-table attempts above."""
    con = _ro(path)
    try:
        pg = pd.read_sql("select * from player_game_stats", con)
        g = pd.read_sql("select game_id, date, home_team, away_team from games", con)
        try:
            mp = pd.read_sql("select game_id, player_id, situation, shot_attempts from mp_player_game "
                             "where situation in ('all','5on4')", con)
        except Exception:                                                                # noqa: BLE001
            mp = pd.DataFrame(columns=["game_id", "player_id", "situation", "shot_attempts"])
    finally:
        con.close()
    pg = pg.merge(g, on="game_id", how="inner")
    pg = pg[pg["game_id"].map(game_type_from_id).isin([2, 3])].copy()
    pg["team"] = pg["team"]
    pg["opp"] = np.where(pg["is_home"] == 1, pg["away_team"], pg["home_team"])
    pg["toi_sec"] = pg["toi"].astype(float) * 60.0
    pg["sog"] = pg["shots"].astype(float)
    if len(mp):
        wide = mp.pivot_table(index=["game_id", "player_id"], columns="situation", values="shot_attempts",
                              aggfunc="first").reset_index()
        wide = wide.rename(columns={"all": "attempts", "5on4": "pp_attempts"})
        pg = pg.merge(wide[["game_id", "player_id", "attempts", "pp_attempts"]], on=["game_id", "player_id"], how="left")
    pg = pg[pg["toi_sec"] > 0]
    return _finish(pg)


def team_games(gl: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team): shots for (sum of skater SOG), schedule (date/opp/is_home). Rows whose skater SOG is NaN
    (upcoming placeholder games) keep NaN."""
    tg = (gl.groupby(["game_id", "team"], as_index=False)
            .agg(date=("date", "first"), opp=("opp", "first"), is_home=("is_home", "first"),
                 sf=("sog", lambda s: s.sum(min_count=1))))
    sa = tg[["game_id", "team", "sf"]].rename(columns={"team": "opp", "sf": "sa"})
    tg = tg.merge(sa, on=["game_id", "opp"], how="left")
    return tg.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
