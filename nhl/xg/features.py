"""Shot rows -> xG model columns.

Input is the shots master (one row per shot attempt: sog / miss / block / goal; nhl/data/flatten.py). Everything here is a
pure function of the shot sequence of ONE game: no shooter or goalie identity, no future games, no outcome of the shot
itself. (The nhl-xG-model archive put the shooter's cumulative shooting % and the goalie's save % INSIDE the xG model.
We keep xG context-only and model finishing talent / goalie quality as separate rolling features downstream, so the two
effects are not double counted and xG stays a clean "what was the chance worth" number.)

Populations
    attempt  unblocked attempts (sog, miss, goal): the usual "Fenwick" xG, used for skater / team threat.
    sog      shots on target (sog, goal): P(goal | on target), used for goalies (shots a goalie actually faces).
Both exclude shootouts and empty-net shots (an empty-net shot is not a chance against a goalie).

Previous-attempt context is computed over ALL four event types in the same game and period (blocks included -- a block is
still a prior attempt), ordered by the API's own event id. LIMITATION: the shots table has no faceoffs, hits, takeaways
or giveaways, so there is no "rush shot" or "off a turnover" flag; that needs the raw play-by-play.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SHOT_TYPES = ["wrist", "snap", "slap", "tip-in", "backhand", "deflected", "wrap-around", "poke", "bat", "between-legs",
              "cradle"]
ST_COLS = [f"st_{t}" for t in SHOT_TYPES] + ["st_unknown"]

FEATURES = (["dist_ft", "angle_deg", "lateral_ft", "skaters_for", "skaters_against", "sk_diff", "pp", "sh", "period",
             "game_sec", "dt_prev", "prev_same_team", "prev_is_sog", "prev_is_miss", "prev_is_block", "prev_is_goal",
             "prev_move_ft", "rebound", "is_ot"] + ST_COLS)

ATTEMPT_EVENTS = ("sog", "miss", "goal")
SOG_EVENTS = ("sog", "goal")
DT_CAP = 120.0
REBOUND_SECONDS = 3.0


def _as_bool(s: pd.Series) -> pd.Series:
    """CSV round-trips can leave a bool column as the strings 'True'/'False' (or mixed): never bool('False')."""
    if s.dtype == bool:
        return s
    return s.map(lambda v: str(v).strip().lower() == "true").astype(bool)


def game_type(game_id) -> pd.Series:
    """NHL game ids are YYYY TT NNNN: 2025020780 -> 2 (1 preseason, 2 regular, 3 playoffs)."""
    return (pd.to_numeric(game_id, errors="coerce") // 10000 % 100).astype("Int64")


def season_of(game_id) -> pd.Series:
    """2025020780 -> 2025 (season START year)."""
    return (pd.to_numeric(game_id, errors="coerce") // 1000000).astype("Int64")


def prepare_shots(shots: pd.DataFrame) -> pd.DataFrame:
    """Add model columns + population flags to a shots frame. Keeps EVERY input row (blocks included, they feed the
    previous-attempt context) and returns it sorted by (game_id, event_id).

    Added: season, gtype, goal (0/1), on_target (0/1), in_attempt / in_sog (population masks), and FEATURES."""
    s = shots.copy()
    s["game_id"] = pd.to_numeric(s["game_id"], errors="coerce").astype("int64")
    s["event_id"] = pd.to_numeric(s["event_id"], errors="coerce").astype("int64")
    s = s.sort_values(["game_id", "event_id"], kind="mergesort").reset_index(drop=True)
    s["season"] = season_of(s["game_id"]).astype("int64")
    s["gtype"] = game_type(s["game_id"]).astype("int64")
    ev = s["event"].astype(str)
    s["goal"] = (ev == "goal").astype(int)
    s["on_target"] = ev.isin(SOG_EVENTS).astype(int)

    en = _as_bool(s["empty_net"]) if "empty_net" in s.columns else pd.Series(False, index=s.index)
    so = s["period_type"].astype(str).eq("SO")
    geo_ok = s["dist_ft"].notna() & s["angle_deg"].notna()
    base = (~en) & (~so) & geo_ok & s["gtype"].isin([2, 3])
    s["in_attempt"] = base & ev.isin(ATTEMPT_EVENTS)
    s["in_sog"] = base & ev.isin(SOG_EVENTS)
    s["empty_net"] = en

    s["lateral_ft"] = pd.to_numeric(s["y"], errors="coerce").abs()
    sf = pd.to_numeric(s["skaters_for"], errors="coerce")
    sa = pd.to_numeric(s["skaters_against"], errors="coerce")
    s["skaters_for"], s["skaters_against"] = sf, sa
    s["sk_diff"] = sf - sa
    s["pp"] = (sf > sa).astype(int)
    s["sh"] = (sf < sa).astype(int)
    per = pd.to_numeric(s["period"], errors="coerce")
    s["period"] = per
    s["is_ot"] = (per > 3).astype(int)
    s["game_sec"] = (per - 1) * 1200.0 + pd.to_numeric(s["time_sec"], errors="coerce")

    # previous attempt (any team) in the same game AND period
    g = s["game_id"]
    same = (g == g.shift(1)) & (per == per.shift(1))
    dt = s["game_sec"] - s["game_sec"].shift(1)
    s["dt_prev"] = np.where(same, np.minimum(dt, DT_CAP), np.nan)
    prev_team = s["team_id"].shift(1)
    s["prev_same_team"] = np.where(same, (s["team_id"] == prev_team).astype(float), np.nan)
    pev = ev.shift(1)
    for name in ("sog", "miss", "block", "goal"):
        s[f"prev_is_{name}"] = np.where(same, (pev == name).astype(float), np.nan)
    px, py = pd.to_numeric(s["x"], errors="coerce").shift(1), pd.to_numeric(s["y"], errors="coerce").shift(1)
    mv = np.hypot(pd.to_numeric(s["x"], errors="coerce") - px, pd.to_numeric(s["y"], errors="coerce") - py)
    s["prev_move_ft"] = np.where(same, mv, np.nan)
    s["rebound"] = np.where(same & (s["prev_same_team"] == 1.0) & (s["prev_is_sog"] == 1.0) & (s["dt_prev"] <= REBOUND_SECONDS),
                            1.0, 0.0)

    st = s["shot_type"].astype(str).str.strip().str.lower()
    for t in SHOT_TYPES:
        s[f"st_{t}"] = (st == t).astype(int)
    s["st_unknown"] = (~st.isin(SHOT_TYPES)).astype(int)
    return s


def population_mask(prep: pd.DataFrame, population: str) -> pd.Series:
    if population == "attempt":
        return prep["in_attempt"]
    if population == "sog":
        return prep["in_sog"]
    raise ValueError(f"population must be 'attempt' or 'sog', got {population!r}")
