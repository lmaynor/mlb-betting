"""nflverse play-by-play -> the clean tables the drive simulator is fitted on.

Definitions (nflfastR conventions, all from the mirrored `pbp` dataset):
  * a SCRIMMAGE play is `play_type in (pass, run)` and not a two-point try; a pass play INCLUDES sacks (nflfastR marks a
    sack `play_type == pass`), so a team's pass attempts = pass plays - sacks (verified against `player_stats`);
    QB kneels / spikes are separate play types and are not fitted.
  * yardline_100 = yards to the opponent goal line; score_differential is the OFFENCE's lead at the snap.
  * elapsed seconds of a play = game clock at its snap minus the game clock at the next real snap of the same drive
    (so replays after a penalty are folded in); the LAST play of a drive instead gets the gap to the next drive's first
    snap (kickoff / punt / change of possession time included). Gaps that cross a half are dropped.
  * regular season only; overtime (qtr 5) is excluded from every fit (the simulator plays 60 minutes).

Everything here is pure DataFrame-in / DataFrame-out so the tests run offline on a synthetic frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

PBP_COLS = [
    "game_id", "season", "week", "season_type", "home_team", "away_team", "posteam", "defteam", "qtr", "fixed_drive",
    "fixed_drive_result", "game_seconds_remaining", "half_seconds_remaining", "down", "ydstogo", "yardline_100",
    "score_differential", "play_type", "yards_gained", "air_yards", "pass_touchdown", "rush_touchdown",
    "return_touchdown", "interception", "fumble_lost", "sack", "complete_pass", "qb_scramble", "two_point_attempt",
    "extra_point_result", "two_point_conv_result", "field_goal_result", "kick_distance", "touchdown",
    "receiver_player_id", "rusher_player_id", "passer_player_id", "total_home_score", "total_away_score",
]

REAL_TYPES = ("pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike")
N_TB, N_SB = 9, 9
# drive end types (fixed_drive_result -> int): 0 TD, 1 FG, 2 punt, 3 turnover (incl. defensive TD), 4 downs, 5 missed FG, 6 end of half
ETYPE = {"Touchdown": 0, "Field goal": 1, "Punt": 2, "Turnover": 3, "Opp touchdown": 3, "Turnover on downs": 4,
         "Missed field goal": 5, "End of half": 6}


# -- state buckets (vectorised) --------------------------------------------------------------------------------------
def time_bucket(qtr, gsr, hsr) -> np.ndarray:
    """9 game-clock states: Q1; Q2 > 5 min left in the half; Q2 5-2 min; Q2 two-minute drill; Q3; Q4 > 10 min;
    Q4 10-5 min; Q4 5-2 min; Q4 last two minutes."""
    qtr = np.asarray(qtr, dtype=float)
    gsr = np.asarray(gsr, dtype=float)
    hsr = np.asarray(hsr, dtype=float)
    tb = np.zeros(len(qtr), dtype=int)
    tb = np.where(qtr == 2, np.where(hsr > 300, 1, np.where(hsr > 120, 2, 3)), tb)
    tb = np.where(qtr == 3, 4, tb)
    tb = np.where(qtr >= 4, np.where(gsr > 600, 5, np.where(gsr > 300, 6, np.where(gsr > 120, 7, 8))), tb)
    return tb


def score_bucket(sd) -> np.ndarray:
    """9 score states by the offence's lead: <=-17, -16..-9, -8..-4, -3..-1, 0, 1..3, 4..8, 9..16, >=17."""
    sd = np.asarray(sd, dtype=float)
    edges = np.array([-16.5, -8.5, -3.5, -0.5, 0.5, 3.5, 8.5, 16.5])
    return np.searchsorted(edges, sd).astype(int)


TB_NAMES = ["Q1", "Q2 >5m", "Q2 5-2m", "Q2 2-min", "Q3", "Q4 >10m", "Q4 10-5m", "Q4 5-2m", "Q4 last 2m"]
SB_NAMES = ["<=-17", "-16..-9", "-8..-4", "-3..-1", "tied", "+1..+3", "+4..+8", "+9..+16", ">=+17"]


def script_class(tb, sb) -> np.ndarray:
    """0 normal, 1 chasing (down 9+ in the second half), 2 milking (up 9+ in the second half)."""
    tb, sb = np.asarray(tb), np.asarray(sb)
    second_half = tb >= 4
    return np.where(second_half & (sb <= 1), 1, np.where(second_half & (sb >= 7), 2, 0)).astype(int)


def hurry_class(tb, sb) -> np.ndarray:
    """clock-behaviour class: 1 hurry (two-minute drills, or trailing in the last 10 minutes), 2 milk (leading late), else 0."""
    tb, sb = np.asarray(tb), np.asarray(sb)
    two_min = (tb == 3) | (tb == 8)
    late = tb >= 5
    return np.where(two_min | (late & (sb <= 3)), 1, np.where(late & (sb >= 5), 2, 0)).astype(int)


ZONE_EDGES = np.array([1.5, 2.5, 3.5, 5.5, 8.5, 12.5, 20.5, 40.5, 70.5])   # yardline_100 -> 10 zones: 1, 2, 3, 4-5, 6-8, 9-12, 13-20, 21-40, 41-70, 71-99
N_ZONE = 10


def zone_of(yl) -> np.ndarray:
    return np.searchsorted(ZONE_EDGES, np.asarray(yl, dtype=float)).astype(int)


def ytg_bucket(ytg) -> np.ndarray:
    ytg = np.asarray(ytg, dtype=float)
    return np.where(ytg <= 3, 0, np.where(ytg <= 7, 1, 2)).astype(int)


def yl_bucket10(yl) -> np.ndarray:
    return np.clip((np.asarray(yl, dtype=float) - 1) // 10, 0, 9).astype(int)


# -- loading ---------------------------------------------------------------------------------------------------------
def load_pbp(seasons: Iterable[int]) -> pd.DataFrame:
    from nfl.data.read import load
    cols = list(PBP_COLS)
    try:
        df = load("pbp", seasons=sorted(set(seasons)), columns=cols)
    except Exception:                                                                    # noqa: BLE001 - older mirrors
        df = load("pbp", seasons=sorted(set(seasons)))
        df = df[[c for c in cols if c in df.columns]]
    return df


@dataclass
class PbpTables:
    plays: pd.DataFrame            # scrimmage pass/run plays with state buckets + elapsed
    drives: pd.DataFrame           # one row per drive
    fg: pd.DataFrame               # field goal attempts
    fourth: pd.DataFrame           # 4th-down decisions (go / fg / punt)
    td_extra: dict                 # P(2pt attempt | TD), P(2pt good), P(XP good)
    games: pd.DataFrame            # one row per game: final regulation scores
    gaps: pd.DataFrame = None      # last snap of a drive -> next drive's first snap: clock gap + next start field position


def _num(df: pd.DataFrame, c: str, default=0.0) -> pd.Series:
    if c not in df.columns:
        return pd.Series(default, index=df.index)
    return pd.to_numeric(df[c], errors="coerce")


def prep(pbp: pd.DataFrame) -> PbpTables:
    """Regular-season, regulation-only tables. See the module docstring for definitions."""
    p = pbp[pbp["season_type"] == "REG"].copy()
    p = p[_num(p, "qtr") <= 4]
    p = p.reset_index(drop=True)
    p["_row"] = np.arange(len(p))
    real = p[p["play_type"].isin(REAL_TYPES) & p["posteam"].notna()].copy()
    real = real.sort_values(["game_id", "_row"], kind="stable")
    g = real.groupby(["game_id", "fixed_drive"], sort=False)
    # elapsed to the next real snap of the same drive (same half), NaN for the last play of a drive
    nxt_gsr = g["game_seconds_remaining"].shift(-1)
    elapsed = real["game_seconds_remaining"] - nxt_gsr
    same_half = (real["game_seconds_remaining"] > 1800) == (nxt_gsr > 1800)
    elapsed = elapsed.where(same_half | nxt_gsr.isna())
    real["elapsed"] = elapsed
    real["last_in_drive"] = g["_row"].transform("max") == real["_row"]
    # gap of the last play of a drive to the first real snap of the NEXT drive of the same game
    first = real.groupby(["game_id", "fixed_drive"], sort=False).agg(
        first_gsr=("game_seconds_remaining", "first"), first_yl=("yardline_100", "first")).reset_index()
    nxt = first.rename(columns={"fixed_drive": "_d", "first_gsr": "next_gsr", "first_yl": "next_yl"})
    nxt["fixed_drive"] = nxt["_d"] - 1
    real = real.merge(nxt[["game_id", "fixed_drive", "next_gsr", "next_yl"]], on=["game_id", "fixed_drive"], how="left")
    gap = real["game_seconds_remaining"] - real["next_gsr"]
    crosses = (real["game_seconds_remaining"] > 1800) != (real["next_gsr"] > 1800)
    real["elapsed"] = np.where(real["last_in_drive"], gap.where(~crosses), real["elapsed"])
    real.loc[real["elapsed"] < 0, "elapsed"] = np.nan
    real.loc[real["elapsed"] > 120, "elapsed"] = np.nan                                  # clock stoppages the engine does not model
    real["tb"] = time_bucket(real["qtr"], real["game_seconds_remaining"], real["half_seconds_remaining"])
    real["sb"] = score_bucket(real["score_differential"].fillna(0))

    two = _num(real, "two_point_attempt") == 1
    plays = real[real["play_type"].isin(["pass", "run"]) & ~two & real["down"].notna()].copy()
    plays["is_pass"] = (plays["play_type"] == "pass").astype(int)
    plays["sack"] = _num(plays, "sack").fillna(0).astype(int)
    plays["complete"] = _num(plays, "complete_pass").fillna(0).astype(int)
    plays["intc"] = _num(plays, "interception").fillna(0).astype(int)
    plays["fumb"] = _num(plays, "fumble_lost").fillna(0).astype(int)
    plays["ret_td"] = _num(plays, "return_touchdown").fillna(0).astype(int)
    plays["yards"] = _num(plays, "yards_gained").fillna(0.0)
    plays["pass_td"] = _num(plays, "pass_touchdown").fillna(0).astype(int)
    plays["rush_td"] = _num(plays, "rush_touchdown").fillna(0).astype(int)
    plays["target"] = plays["receiver_player_id"].notna().astype(int)
    plays["scramble"] = _num(plays, "qb_scramble").fillna(0).astype(int)
    plays["ytg"] = plays["ydstogo"].astype(float)
    plays["yl"] = plays["yardline_100"].astype(float)
    plays["sd"] = plays["score_differential"].astype(float)
    plays["script"] = script_class(plays["tb"], plays["sb"])
    plays["hurry"] = hurry_class(plays["tb"], plays["sb"])
    plays = plays.dropna(subset=["yl", "sd", "ytg"])
    keep = ["game_id", "season", "week", "posteam", "defteam", "home_team", "fixed_drive", "qtr", "game_seconds_remaining",
            "half_seconds_remaining", "down", "ytg", "yl", "sd", "tb", "sb", "script", "hurry", "is_pass", "sack", "complete",
            "intc", "fumb", "ret_td", "yards", "pass_td", "rush_td", "target", "scramble", "elapsed", "last_in_drive",
            "air_yards", "passer_player_id", "receiver_player_id", "rusher_player_id", "fixed_drive_result"]
    plays = plays[[c for c in keep if c in plays.columns]].reset_index(drop=True)

    # -- drives -------------------------------------------------------------------------------------------------
    r2 = real.copy()
    r2["is_fg"] = (r2["play_type"] == "field_goal").astype(int)
    d = r2.groupby(["game_id", "fixed_drive"], sort=False).agg(
        season=("season", "first"), week=("week", "first"), posteam=("posteam", "first"), defteam=("defteam", "first"),
        result=("fixed_drive_result", "last"), start_yl=("yardline_100", "first"), end_yl=("yardline_100", "last"),
        n_plays=("play_type", "size"), t0=("game_seconds_remaining", "first"), qtr0=("qtr", "first"),
        next_yl=("next_yl", "last")).reset_index()
    d = d.sort_values(["game_id", "fixed_drive"]).reset_index(drop=True)

    # -- end-of-drive gaps (clock between the last snap of a drive and the next drive's first snap) ----------------
    lastrows = real[real["last_in_drive"]].copy()
    lastrows["etype"] = lastrows["fixed_drive_result"].map(ETYPE).fillna(-1).astype(int)
    lastrows["hurry"] = hurry_class(lastrows["tb"], lastrows["sb"])
    gaps = lastrows[(lastrows["etype"] >= 0)][["etype", "hurry", "elapsed", "yardline_100", "next_yl"]].copy()
    gaps = gaps.rename(columns={"yardline_100": "end_yl"}).dropna(subset=["elapsed"]).reset_index(drop=True)

    # -- field goals -----------------------------------------------------------------------------------------------
    fg = real[real["play_type"] == "field_goal"].copy()
    fg["made"] = (fg["field_goal_result"] == "made").astype(int)
    fg = fg.dropna(subset=["kick_distance"])[["season", "kick_distance", "made", "yardline_100"]].reset_index(drop=True)

    # -- 4th-down decisions ----------------------------------------------------------------------------------------
    f4 = real[(real["down"] == 4) & real["play_type"].isin(["pass", "run", "punt", "field_goal"]) &
              ~(_num(real, "two_point_attempt") == 1)].copy()
    f4["choice"] = f4["play_type"].map({"pass": 0, "run": 0, "field_goal": 1, "punt": 2})
    f4 = f4.dropna(subset=["yardline_100", "ydstogo", "score_differential"])[
        ["season", "yardline_100", "ydstogo", "score_differential", "qtr", "game_seconds_remaining", "half_seconds_remaining",
         "tb", "sb", "choice"]].reset_index(drop=True)

    # -- touchdown conversions -------------------------------------------------------------------------------------
    xp = p[p["play_type"] == "extra_point"]
    tp = p[(_num(p, "two_point_attempt") == 1)]
    n_xp, n_tp = max(len(xp), 1), len(tp)
    td_extra = {"p_two_attempt": n_tp / max(n_tp + n_xp, 1),
                "p_xp_good": float((xp["extra_point_result"] == "good").mean()) if len(xp) else 0.94,
                "p_two_good": float((tp["two_point_conv_result"] == "success").mean()) if n_tp else 0.48}

    # -- regulation scoreboard (score at the last snap of regulation, plus nothing after) --------------------------
    last_play = p.dropna(subset=["total_home_score"]).groupby("game_id").tail(1)
    games = last_play[["game_id", "season", "week", "home_team", "away_team", "total_home_score", "total_away_score"]].rename(
        columns={"total_home_score": "home_score_reg", "total_away_score": "away_score_reg"})
    return PbpTables(plays=plays, drives=d, fg=fg, fourth=f4, td_extra=td_extra, games=games.reset_index(drop=True),
                     gaps=gaps)
