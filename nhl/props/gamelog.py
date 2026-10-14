"""Skater game-log loaders: every data source is normalised to ONE canonical frame so the feature/training code is
identical whether it runs on our own NHL masters (2019+) or on the 2025-26-only `nhl_data.db` used for the validation
study.

Canonical columns (one row per skater appearance):
    player_id (int)  game_id (int)  date ('YYYY-MM-DD', the ET game date)  team  opp  is_home (0/1)  position
    toi_sec (float)  sog (float)  attempts (float, NaN if unknown)  pp_attempts (float, NaN if unknown)
    goals  assists
Only regular-season / playoff games (game_type 2, 3): preseason lineups are not representative.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

GAMELOG_COLS = ["player_id", "game_id", "date", "team", "opp", "is_home", "position", "toi_sec", "sog", "attempts",
                "pp_attempts", "goals", "assists"]


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
        # a skater who played but never shot has no shot rows: 0, not unknown -- but only where the game HAS pbp shots
        have = set(sh["game_id"].unique())
        in_have = s["game_id"].isin(have)
        s.loc[in_have, ["attempts", "pp_attempts"]] = s.loc[in_have, ["attempts", "pp_attempts"]].fillna(0)
    return _finish(s)


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
