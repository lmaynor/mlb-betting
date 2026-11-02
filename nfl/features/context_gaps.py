"""Context layers for the hierarchical NFL prop engine: coverage shells, receiver / passer profiles, offensive-line health,
trench pressure, and practice-weighted teammate absences (vacated opportunity redistributed by receiver role).

Every public `*_before` / `*_as_of` function takes a (season, week) cutoff and filters its input to games STRICTLY BEFORE
that week BEFORE computing anything, so a value for week w can never see week w or later (the leakage tests mutate those
weeks and assert nothing moves). Injury / practice status is the exception by design: the report for week w itself is the
final pre-kickoff status and is the whole point.

Degrades gracefully: a missing optional table (participation, snap_counts, injuries, players) yields NaN / empty results,
never an exception; a column absent from a mirrored table drops only the metrics that need it.

Inputs are plain pandas frames in the nflverse mirror's schemas (nfl.config.DATASETS):
  pbp            game_id, season, week, posteam, defteam, play_type, qb_dropback, sack, qb_hit, air_yards,
                 yards_after_catch, complete_pass, yards_gained, rushing_yards, receiver_player_id, passer_player_id,
                 cpoe, xpass, down, wp, half_seconds_remaining, season_type, play_id, qb_kneel, qb_spike
  participation  nflverse_game_id, play_id, defense_coverage_type, defense_man_zone_type, was_pressure (optional)
  injuries       season, week, team, gsis_id, position, report_status, practice_status
  snap_counts    season, week, team, position, pfr_player_id, offense_pct
  players        gsis_id, pfr_id (the id crosswalk)

ASCII only.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from nfl.data.derived import neutral_mask, offensive_plays

HALFLIFE_GAMES = 5.0                  # trailing EWM for team context (spec: trailing 5-game EWM)
RECEIVER_HALFLIFE = 6.0

SINGLE_HIGH = frozenset({"COVER_1", "COVER_3", "COVER_0"})                    # middle of the field closed
TWO_HIGH = frozenset({"COVER_2", "COVER_4", "COVER_6", "2_MAN"})              # Cover-2 / Quarters / Cover-6 / 2-man
OL_POSITIONS = frozenset({"T", "OT", "G", "OG", "C", "OL"})

DEF_COLS = ["def_middle_closed_rate", "def_two_high_rate", "def_man_rate", "def_zone_rate", "def_pass_funnel_rate",
            "def_short_target_share_allowed", "def_deep_target_share_allowed", "def_yac_per_rec_allowed",
            "def_rb_target_share_allowed", "def_te_target_share_allowed", "def_pass_rush_pressure_rate",
            "def_stuff_rate"]
OFF_COLS = ["off_sack_hit_rate", "off_stuffed_run_rate"]


def week_key(season, week) -> int:
    return int(season) * 100 + int(week)


def _key(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df["season"], errors="coerce") * 100 + pd.to_numeric(df["week"], errors="coerce")


def before(df: Optional[pd.DataFrame], season: int, week: int) -> Optional[pd.DataFrame]:
    """Rows of a (season, week)-keyed frame strictly before the cutoff. The ONLY place cutoff filtering happens."""
    if df is None or len(df) == 0:
        return df
    return df[_key(df) < week_key(season, week)]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def _canon(s: pd.Series) -> pd.Series:
    return s.astype("object").map(lambda t: {"JAC": "JAX", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC",
                                             "WSH": "WAS"}.get(str(t).upper(), str(t).upper()) if pd.notna(t) else None)


# ------------------------------------------------------------------------------------------------------ EWM helpers
def _weights(n: int, halflife: float) -> np.ndarray:
    return 0.5 ** ((n - 1 - np.arange(n)) / max(halflife, 1e-6))


def ewm_by(df: pd.DataFrame, key: str, cols: Iterable[str], halflife: float) -> pd.DataFrame:
    """NaN-aware recency-weighted mean of each column per `key`, rows ordered by (season, week). -> frame indexed by key."""
    cols = list(cols)
    d = df.sort_values(["season", "week"], kind="mergesort")
    out = {}
    for k, g in d.groupby(key, sort=False):
        w = _weights(len(g), halflife)
        row = {"n_games": float(len(g))}
        for c in cols:
            v = pd.to_numeric(g[c], errors="coerce").to_numpy(dtype=float)
            ok = np.isfinite(v)
            row[c] = float(np.sum(w[ok] * v[ok]) / np.sum(w[ok])) if ok.any() else np.nan
        out[k] = row
    return pd.DataFrame.from_dict(out, orient="index", columns=["n_games"] + cols) if out else pd.DataFrame(
        columns=["n_games"] + cols)


def ewm_ratio_by(df: pd.DataFrame, key: str, num: str, den: str, halflife: float) -> pd.Series:
    """Recency-weighted sum(num) / sum(den) per key (a rate over opportunities, not a mean of per-game rates)."""
    d = df.sort_values(["season", "week"], kind="mergesort")
    out = {}
    for k, g in d.groupby(key, sort=False):
        w = _weights(len(g), halflife)
        n = pd.to_numeric(g[num], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        m = pd.to_numeric(g[den], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        out[k] = float(np.sum(w * n) / np.sum(w * m)) if np.sum(w * m) > 0 else np.nan
    return pd.Series(out, dtype=float)


# ------------------------------------------------------------------------------------ 1.1 coverage shells (defense)
def _plays_with_participation(plays: pd.DataFrame, participation: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Attach coverage columns from pbp_participation (joined on game id + play id) when the table is present."""
    p = plays
    for c in ("defense_coverage_type", "defense_man_zone_type", "was_pressure"):
        p = p.assign(**{c: np.nan}) if c not in p.columns else p
    if participation is None or len(participation) == 0 or "nflverse_game_id" not in participation.columns:
        return p
    keep = [c for c in ("nflverse_game_id", "play_id", "defense_coverage_type", "defense_man_zone_type", "was_pressure")
            if c in participation.columns]
    part = participation[keep].drop_duplicates(["nflverse_game_id", "play_id"]).rename(
        columns={"nflverse_game_id": "game_id", "defense_coverage_type": "_cov", "defense_man_zone_type": "_mz",
                 "was_pressure": "_wp"})
    p = p.drop(columns=["defense_coverage_type", "defense_man_zone_type", "was_pressure"])
    p = p.merge(part, on=["game_id", "play_id"], how="left")
    for src, dst in (("_cov", "defense_coverage_type"), ("_mz", "defense_man_zone_type"), ("_wp", "was_pressure")):
        p[dst] = p[src] if src in p.columns else np.nan
    return p.drop(columns=[c for c in ("_cov", "_mz", "_wp") if c in p.columns])


def _flags(p: pd.DataFrame, positions: Optional[dict]) -> pd.DataFrame:
    """Per-play 0/1 columns the game tables sum."""
    p = p.copy()
    ay = _num(p, "air_yards")
    rcv = p["receiver_player_id"] if "receiver_player_id" in p.columns else pd.Series(None, index=p.index, dtype=object)
    sack = _num(p, "sack").fillna(0.0)
    p["_tgt"] = ((p["_pass"] == 1) & ay.notna() & rcv.notna() & (sack == 0)).astype(float)
    p["_short"] = (p["_tgt"] * (ay < 10)).astype(float)
    p["_deep"] = (p["_tgt"] * (ay >= 20)).astype(float)
    comp = _num(p, "complete_pass").fillna(0.0)
    p["_comp"] = (p["_tgt"] * (comp == 1)).astype(float)
    p["_yac"] = (p["_comp"] * _num(p, "yards_after_catch").fillna(0.0)).astype(float)
    if positions:
        pos = rcv.map(positions)
        p["_pos_known"] = (p["_tgt"] * pos.notna()).astype(float)
        p["_rb_t"] = (p["_tgt"] * (pos == "RB")).astype(float)
        p["_te_t"] = (p["_tgt"] * (pos == "TE")).astype(float)
    else:
        p["_pos_known"] = p["_rb_t"] = p["_te_t"] = 0.0
    hit = ((sack == 1) | (_num(p, "qb_hit").fillna(0.0) == 1))
    p["_press"] = ((p["_pass"] == 1) & hit).astype(float)
    if p["was_pressure"].notna().any():                                  # participation's own flag, where present
        wp = pd.to_numeric(p["was_pressure"].astype("object").map(
            lambda v: 1.0 if v in (True, 1, 1.0, "TRUE", "True", "true") else (0.0 if pd.notna(v) else np.nan)),
            errors="coerce")
        p["_press"] = np.where(wp.notna() & (p["_pass"] == 1), wp, p["_press"])
    ry = _num(p, "rushing_yards")
    p["_stuff"] = ((p["_rush"] == 1) & (ry <= 0)).astype(float)
    # the real participation file writes '' (not null) for plays with no coverage tag: that is UNKNOWN, not a category
    def _tag(v):
        return str(v).strip().upper() or None if pd.notna(v) else None
    cov = p["defense_coverage_type"].astype("object").map(_tag)
    mz = p["defense_man_zone_type"].astype("object").map(_tag)
    p["_cov_known"] = ((p["_pass"] == 1) & cov.notna()).astype(float)
    p["_single"] = p["_cov_known"] * cov.isin(SINGLE_HIGH)
    p["_two"] = p["_cov_known"] * cov.isin(TWO_HIGH)
    p["_mz_known"] = ((p["_pass"] == 1) & mz.notna()).astype(float)
    p["_man"] = p["_mz_known"] * mz.str.contains("MAN", na=False)
    if {"down", "wp", "half_seconds_remaining"} <= set(p.columns) and "xpass" in p.columns:
        nm = neutral_mask(p)
        p["_n"] = nm.astype(float)
        p["_n_oe"] = np.where(nm, p["_pass"] - _num(p, "xpass"), 0.0)
        p["_n_oe_ok"] = (nm & _num(p, "xpass").notna()).astype(float)
    else:
        p["_n"] = p["_n_oe"] = p["_n_oe_ok"] = 0.0
    return p


def _div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.where(b > 0) / b.where(b > 0)


def defense_game_table(pbp: pd.DataFrame, positions: Optional[dict] = None,
                       participation: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """One row per (season, week, defteam) of what that defence FACED / ALLOWED, using only the plays given."""
    cols = ["season", "week", "defteam"]
    if pbp is None or len(pbp) == 0:
        return pd.DataFrame(columns=cols + DEF_COLS)
    plays = _flags(_plays_with_participation(offensive_plays(pbp), participation), positions)
    plays["defteam"] = _canon(plays["defteam"])
    g = plays.groupby(cols, sort=False)[["_pass", "_rush", "_tgt", "_short", "_deep", "_comp", "_yac", "_pos_known",
                                          "_rb_t", "_te_t", "_press", "_stuff", "_cov_known", "_single", "_two",
                                          "_mz_known", "_man", "_n", "_n_oe", "_n_oe_ok"]].sum().reset_index()
    out = g[cols].copy()
    out["def_middle_closed_rate"] = _div(g["_single"], g["_cov_known"])
    out["def_two_high_rate"] = _div(g["_two"], g["_cov_known"])
    out["def_man_rate"] = _div(g["_man"], g["_mz_known"])
    out["def_zone_rate"] = 1.0 - out["def_man_rate"]
    out["def_pass_funnel_rate"] = _div(g["_n_oe"], g["_n_oe_ok"])               # neutral pass rate over expected FACED
    out["def_short_target_share_allowed"] = _div(g["_short"], g["_tgt"])
    out["def_deep_target_share_allowed"] = _div(g["_deep"], g["_tgt"])
    out["def_yac_per_rec_allowed"] = _div(g["_yac"], g["_comp"])
    out["def_rb_target_share_allowed"] = _div(g["_rb_t"], g["_pos_known"])
    out["def_te_target_share_allowed"] = _div(g["_te_t"], g["_pos_known"])
    out["def_pass_rush_pressure_rate"] = _div(g["_press"], g["_pass"])
    out["def_stuff_rate"] = _div(g["_stuff"], g["_rush"])
    return out


def offense_game_table(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, week, posteam): sack + QB-hit rate per dropback and stuffed-run rate (rushing_yards <= 0)."""
    cols = ["season", "week", "posteam"]
    if pbp is None or len(pbp) == 0:
        return pd.DataFrame(columns=cols + OFF_COLS)
    plays = _flags(_plays_with_participation(offensive_plays(pbp), None), None)
    plays["posteam"] = _canon(plays["posteam"])
    g = plays.groupby(cols, sort=False)[["_pass", "_rush", "_press", "_stuff"]].sum().reset_index()
    out = g[cols].copy()
    out["off_sack_hit_rate"] = _div(g["_press"], g["_pass"])
    out["off_stuffed_run_rate"] = _div(g["_stuff"], g["_rush"])
    return out


def defense_context_before(pbp: pd.DataFrame, season: int, week: int, positions: Optional[dict] = None,
                           participation: Optional[pd.DataFrame] = None, halflife: float = HALFLIFE_GAMES) -> pd.DataFrame:
    """Per-defence trailing EWM of DEF_COLS over games strictly before (season, week). Indexed by team; `attrs['league']`
    holds the league mean of each column over the same games (the neutral reference)."""
    pb = before(pbp, season, week)          # participation has no season / week columns: it is LEFT-joined onto these
    #                                          (already cutoff-filtered) plays, so its later games are never read
    tbl = defense_game_table(pb, positions, participation)
    if len(tbl) == 0:
        out = pd.DataFrame(columns=["n_games"] + DEF_COLS)
        out.attrs["league"] = {c: np.nan for c in DEF_COLS}
        return out
    out = ewm_by(tbl, "defteam", DEF_COLS, halflife)
    out.attrs["league"] = {c: float(pd.to_numeric(tbl[c], errors="coerce").mean()) for c in DEF_COLS}
    return out


def offense_context_before(pbp: pd.DataFrame, season: int, week: int, halflife: float = HALFLIFE_GAMES) -> pd.DataFrame:
    tbl = offense_game_table(before(pbp, season, week))
    if len(tbl) == 0:
        out = pd.DataFrame(columns=["n_games"] + OFF_COLS)
        out.attrs["league"] = {c: np.nan for c in OFF_COLS}
        return out
    out = ewm_by(tbl, "posteam", OFF_COLS, halflife)
    out.attrs["league"] = {c: float(pd.to_numeric(tbl[c], errors="coerce").mean()) for c in OFF_COLS}
    return out


# ------------------------------------------------------------------------------- 1.1 receivers and passers (profile)
def receiver_context_before(pbp: pd.DataFrame, season: int, week: int, positions: Optional[dict] = None,
                            halflife: float = RECEIVER_HALFLIFE) -> pd.DataFrame:
    """Per-receiver trailing aDOT, wopr, racr, deep-target share, catch rate, yards per reception, YAC per reception,
    target share and air-yards share over games strictly before (season, week). Indexed by gsis id."""
    cols = ["adot", "wopr", "racr", "deep_target_share", "catch_rate", "ypr", "yac_per_rec", "target_share",
            "air_yards_share"]
    pb = before(pbp, season, week)
    if pb is None or len(pb) == 0 or "receiver_player_id" not in pb.columns:
        return pd.DataFrame(columns=["n_games", "team", "pos"] + cols)
    plays = _flags(_plays_with_participation(offensive_plays(pb), None), positions)
    plays["posteam"] = _canon(plays["posteam"])
    t = plays[plays["_tgt"] == 1].copy()
    if len(t) == 0:
        return pd.DataFrame(columns=["n_games", "team", "pos"] + cols)
    t["_ay"] = _num(t, "air_yards").fillna(0.0)
    t["_ryds"] = t["_comp"] * _num(t, "yards_gained").fillna(0.0)
    t["_deep_n"] = t["_deep"]
    keys = ["season", "week", "game_id", "posteam"]
    team = t.groupby(keys, sort=False)[["_tgt", "_ay"]].sum().rename(columns={"_tgt": "tm_tgt", "_ay": "tm_ay"}).reset_index()
    rg = t.groupby(keys + ["receiver_player_id"], sort=False)[["_tgt", "_comp", "_ay", "_ryds", "_yac", "_deep_n"]].sum().reset_index()
    rg = rg.merge(team, on=keys, how="left")
    rg["target_share"] = _div(rg["_tgt"], rg["tm_tgt"])
    rg["air_yards_share"] = _div(rg["_ay"], rg["tm_ay"])
    rg["wopr"] = 1.5 * rg["target_share"] + 0.7 * rg["air_yards_share"]
    base = ewm_by(rg, "receiver_player_id", ["target_share", "air_yards_share", "wopr"], halflife)
    out = pd.DataFrame(index=base.index)
    out["n_games"] = base["n_games"]
    out["adot"] = ewm_ratio_by(rg, "receiver_player_id", "_ay", "_tgt", halflife)
    out["wopr"] = base["wopr"]
    out["racr"] = ewm_ratio_by(rg, "receiver_player_id", "_ryds", "_ay", halflife)
    out["deep_target_share"] = ewm_ratio_by(rg, "receiver_player_id", "_deep_n", "_tgt", halflife)
    out["catch_rate"] = ewm_ratio_by(rg, "receiver_player_id", "_comp", "_tgt", halflife)
    out["ypr"] = ewm_ratio_by(rg, "receiver_player_id", "_ryds", "_comp", halflife)
    out["yac_per_rec"] = ewm_ratio_by(rg, "receiver_player_id", "_yac", "_comp", halflife)
    out["target_share"] = base["target_share"]
    out["air_yards_share"] = base["air_yards_share"]
    last_team = rg.sort_values(["season", "week"], kind="mergesort").groupby("receiver_player_id", sort=False)["posteam"].last()
    out["team"] = last_team
    out["pos"] = [(positions or {}).get(i) for i in out.index]
    return out[["n_games", "team", "pos"] + cols]


def passer_context_before(pbp: pd.DataFrame, season: int, week: int, halflife: float = RECEIVER_HALFLIFE) -> pd.DataFrame:
    """Per-passer trailing aDOT, completion rate, CPOE (EWM) and cpoe_dev = EWM - all-prior-games mean (percentage points)."""
    pb = before(pbp, season, week)
    cols = ["adot", "comp_rate", "cpoe_ewm", "cpoe_dev"]
    if pb is None or len(pb) == 0 or "passer_player_id" not in pb.columns:
        return pd.DataFrame(columns=["n_games"] + cols)
    plays = _flags(_plays_with_participation(offensive_plays(pb), None), None)
    t = plays[plays["_tgt"] == 1].copy()
    if len(t) == 0:
        return pd.DataFrame(columns=["n_games"] + cols)
    t["_ay"] = _num(t, "air_yards").fillna(0.0)
    t["_cpoe"] = _num(t, "cpoe")
    t["_cpoe_n"] = t["_cpoe"].notna().astype(float)
    t["_cpoe0"] = t["_cpoe"].fillna(0.0)
    keys = ["season", "week", "game_id", "passer_player_id"]
    g = t.groupby(keys, sort=False)[["_tgt", "_comp", "_ay", "_cpoe0", "_cpoe_n"]].sum().reset_index()
    out = pd.DataFrame(index=ewm_by(g, "passer_player_id", [], halflife).index)
    out["n_games"] = ewm_by(g, "passer_player_id", [], halflife)["n_games"]
    out["adot"] = ewm_ratio_by(g, "passer_player_id", "_ay", "_tgt", halflife)
    out["comp_rate"] = ewm_ratio_by(g, "passer_player_id", "_comp", "_tgt", halflife)
    out["cpoe_ewm"] = ewm_ratio_by(g, "passer_player_id", "_cpoe0", "_cpoe_n", halflife)
    long_run = (g.groupby("passer_player_id")["_cpoe0"].sum() / g.groupby("passer_player_id")["_cpoe_n"].sum().where(lambda s: s > 0))
    out["cpoe_dev"] = out["cpoe_ewm"] - long_run.reindex(out.index)
    return out[["n_games"] + cols]


# ------------------------------------------------------------------------------------- 1.2 offensive line + trenches
def ol_health_delta(n_ol_out: float) -> float:
    """Added sack + hit rate from missing starting linemen: 0.025 per starter out / doubtful."""
    return 0.025 * float(max(n_ol_out, 0.0))


def net_pressure_mismatch(off_sack_hit_rate: float, n_ol_out: float, def_pressure_rate: float,
                          league_off_rate: float, league_def_rate: float) -> float:
    """Pass-protection vs pass-rush matchup as a RATIO where 1.0 = league-average on both sides.

    The spec's literal formula, (off + 0.025 n_ol_out) * def / league_avg, has the units of a rate (~0.09); the downstream
    interception / completion terms are written for a ratio near 1, so each side is divided by its league reference:
        ((off + 0.025 n) / league_off) * (def / league_def)."""
    vals = (off_sack_hit_rate, def_pressure_rate, league_off_rate, league_def_rate)
    if any(v is None or not math.isfinite(v) for v in vals) or league_off_rate <= 0 or league_def_rate <= 0:
        return float("nan")
    return float((off_sack_hit_rate + ol_health_delta(n_ol_out)) / league_off_rate * (def_pressure_rate / league_def_rate))


_OUT_STATUS = frozenset({"out", "doubtful", "ir", "injured reserve", "reserve/injured", "pup",
                         "physically unable to perform", "suspended"})


def _status(v) -> str:
    return str(v).strip().lower() if v is not None and pd.notna(v) else ""


def ol_starters_before(snap_counts: Optional[pd.DataFrame], players: Optional[pd.DataFrame], season: int, week: int,
                       n_games: int = 4, min_pct: float = 0.6, max_gap_weeks: int = 6) -> dict:
    """{team: {gsis_id}}: offensive linemen who started (mean offense snap share >= min_pct over their last `n_games`
    appearances before the cutoff) and appeared within the last `max_gap_weeks` week-slots. Needs the pfr -> gsis crosswalk;
    {} when either table is missing."""
    sc = before(snap_counts, season, week)
    if sc is None or len(sc) == 0 or players is None or len(players) == 0 or "pfr_player_id" not in sc.columns:
        return {}
    sc = sc[sc["position"].astype("object").map(lambda p: str(p).upper() in OL_POSITIONS)].copy()
    if len(sc) == 0:
        return {}
    xw = players[["pfr_id", "gsis_id"]].dropna().drop_duplicates("pfr_id")
    sc = sc.merge(xw, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    sc["team"] = _canon(sc["team"])
    sc["widx"] = (pd.to_numeric(sc["season"]) - 2000) * 30 + pd.to_numeric(sc["week"])
    cut = (season - 2000) * 30 + week
    out: dict = {}
    for gid, g in sc.sort_values("widx", kind="mergesort").groupby("gsis_id", sort=False):
        last = g.tail(n_games)
        if cut - float(last["widx"].iloc[-1]) > max_gap_weeks:
            continue
        if float(pd.to_numeric(last["offense_pct"], errors="coerce").mean()) >= min_pct:
            out.setdefault(str(last["team"].iloc[-1]), set()).add(gid)
    return out


def n_ol_out(injuries: Optional[pd.DataFrame], starters: dict, season: int, week: int) -> dict:
    """{team: number of its starting offensive linemen Out / Doubtful / on IR in the (season, week) report}."""
    if injuries is None or len(injuries) == 0 or not starters:
        return {}
    i = injuries[(pd.to_numeric(injuries["season"], errors="coerce") == season)
                 & (pd.to_numeric(injuries["week"], errors="coerce") == week)]
    out: dict = {}
    for gid, tm, st in zip(i["gsis_id"], _canon(i["team"]), i["report_status"]):
        if _status(st) in _OUT_STATUS and gid in starters.get(tm, ()):
            out[tm] = out.get(tm, 0) + 1
    return out


# --------------------------------------------------------------------- 1.3 practice-weighted teammate absences
_FULL_OUT = frozenset({"out", "ir", "injured reserve", "reserve/injured", "pup", "physically unable to perform",
                       "suspended"})
Q_UNKNOWN_PRACTICE = 0.25         # ASSUMPTION: a Questionable player with no practice report is treated like Limited


def practice_code(practice_status) -> str:
    """'dnp' | 'lp' | 'fp' | '' from nflverse practice_status text or a DNP / LP / FP abbreviation."""
    s = _status(practice_status)
    if s.startswith("did not") or s == "dnp":
        return "dnp"
    if s.startswith("limited") or s == "lp":
        return "lp"
    if s.startswith("full") or s == "fp":
        return "fp"
    return ""


def absence_weight(report_status, practice_status=None) -> float:
    """Probability-like weight that a listed teammate does not play.
    Out / IR / PUP / Suspended 1.0; Doubtful 0.80; Questionable + Friday DNP 0.55, + LP 0.25, + FP 0.0 (unknown practice:
    Q_UNKNOWN_PRACTICE); anything else 0.0."""
    s = _status(report_status)
    if s in _FULL_OUT:
        return 1.0
    if s == "doubtful":
        return 0.80
    if s == "questionable":
        return {"dnp": 0.55, "lp": 0.25, "fp": 0.0}.get(practice_code(practice_status), Q_UNKNOWN_PRACTICE)
    return 0.0


ROLES = ("slot", "perimeter", "mid", "rb")


def role_of(pos: str, adot: Optional[float]) -> str:
    """Receiver role: 'rb' (checkdowns), 'slot' (short, aDOT < 9, also TEs), 'perimeter' (aDOT >= 11), else 'mid'."""
    p = str(pos).upper()
    if p in ("RB", "FB"):
        return "rb"
    a = adot if adot is not None and math.isfinite(adot) else (4.0 if p == "TE" else 10.0)
    if p == "TE" and a < 11.0:
        return "slot"
    if a < 9.0:
        return "slot"
    if a >= 11.0:
        return "perimeter"
    return "mid"


def vacated_pools(absent: list) -> dict:
    """Weighted vacated opportunity by role. Each absent teammate is a dict with `weight` (absence_weight), `pos`, `adot`
    and trailing shares `tgt`, `rush`, `rz`, `air`. -> {metric: {role: weighted share}} for metrics tgt / rush / rz / air."""
    pools = {m: {} for m in ("tgt", "rush", "rz", "air")}
    for a in absent:
        w = float(a.get("weight", 0.0))
        if w <= 0:
            continue
        r = role_of(a.get("pos", ""), a.get("adot"))
        for m in pools:
            v = a.get(m)
            if v is not None and math.isfinite(v) and v > 0:
                pools[m][r] = pools[m].get(r, 0.0) + w * float(v)
    return pools


def redistribute(pool_by_role: dict, active: list, spill: float = 0.30) -> dict:
    """Allocate vacated shares to active teammates BY ROLE rather than uniformly.

    `active` = [(key, role, current_share)]. A role's pool goes (1 - spill) to the active players of the SAME role in
    proportion to their current share, and `spill` (plus the whole pool if the role has no active peer) to every active
    player in proportion to share. -> {key: added share}."""
    added = {k: 0.0 for k, _, _ in active}
    tot_all = sum(max(s, 0.0) for _, _, s in active)
    if tot_all <= 0:
        return added
    for role, pool in pool_by_role.items():
        if pool <= 0:
            continue
        peers = [(k, max(s, 0.0)) for k, r, s in active if r == role]
        tot_peer = sum(s for _, s in peers)
        same = (1.0 - spill) * pool if tot_peer > 0 else 0.0
        rest = pool - same
        for k, s in peers:
            added[k] += same * s / tot_peer
        for k, _, s in active:
            added[k] += rest * max(s, 0.0) / tot_all
    return added
