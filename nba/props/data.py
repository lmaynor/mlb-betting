"""nba.props.data -- load + normalise the SportsBlaze masters into the tables the feature builder needs.

Inputs (written by nba/data/*; see nba/data/flatten.py for the schema):
  NBA/player_boxscores_master.csv   one row per (game, player); `minutes` is a "MM:SS" string; played/starter bool
  NBA/games_master.csv              one row per game (away/home abbr, date, season_year, season_type)
  NBA/raw/schedule_{year}.json      the only place the real tip-off time survives (see load_tips)

Two gotchas found building this (docs/solutions/integration-issues/sportsblaze-master-date-is-utc.md):
  * the masters' `date` is the UTC DATE of the tip (flatten.py keeps ev.date[:10]); only ~24% equal the ET game day, so a
    10pm-ET game lands on the NEXT day and 104 team-dates carry two games. Rest/B2B/ordering therefore use the ET game
    day derived from the schedule dumps' tip times.
  * team_boxscores_master.csv has NaN rebounds/turnovers for 2019-2024; team totals are SUMMED FROM PLAYER ROWS
    (complete; points/FGA sums match the team box exactly).

SportsBlaze abbreviations differ from our tricodes (GS, NO, NY, SA, PHO); everything is keyed on the tricode
(nba.teams.team_code) so it joins the odds layer. SportsBlaze player ids are UUIDs (NOT ESPN ids): see identity.py.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from nba.config import AVAILABLE_SEASONS, GAMES_MASTER, PLAYER_BOX_MASTER, TIMEZONE, raw_boxscore_key, raw_schedule_key
from nba.props import config as C
from nba.teams import team_code

log = logging.getLogger(__name__)

_SB_TEAM_FIX = {"PHO": "PHX"}          # the one SportsBlaze abbreviation team_code() cannot resolve


def sb_team(abbr) -> str | None:
    """SportsBlaze team abbreviation -> our tricode (None if unknown)."""
    a = abbr.strip().upper() if isinstance(abbr, str) else ""
    if not a:
        return None
    return team_code(_SB_TEAM_FIX.get(a, a))


def parse_minutes(series: pd.Series) -> pd.Series:
    """'MM:SS' (or plain number) -> float minutes. Unparseable -> NaN."""
    s = series.astype(str).str.strip()
    parts = s.str.split(":", n=1, expand=True)
    if parts.shape[1] == 1:
        return pd.to_numeric(parts[0], errors="coerce")
    mm = pd.to_numeric(parts[0], errors="coerce")
    ss = pd.to_numeric(parts[1], errors="coerce").fillna(0.0)
    return mm + ss / 60.0


def load_masters(local_dir: str | None = None):
    """-> (player_box, games). `local_dir` reads <dir>/player_boxscores_master.csv + games_master.csv (offline work);
    otherwise mlb_core.storage (GCS when GCS_BUCKET is set, else MLB_BASE_DATA)."""
    if local_dir:
        d = Path(local_dir)
        return pd.read_csv(d / "player_boxscores_master.csv"), pd.read_csv(d / "games_master.csv")
    from mlb_core import storage
    return storage.read_csv(PLAYER_BOX_MASTER), storage.read_csv(GAMES_MASTER)


# ----------------------------------------------------------------------------- tip times
def _event_tips(events) -> dict:
    out = {}
    for ev in events or []:
        gid, ts = ev.get("id"), ev.get("date")
        if gid and ts:
            out[gid] = pd.Timestamp(ts)
    return out


def _tips_series(tips: dict) -> pd.Series:
    if not tips:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return pd.Series(tips).astype("datetime64[ns, UTC]")


def load_tips(local_dir: str | None = None, seasons=None) -> pd.Series:
    """game_id -> UTC tip-off Timestamp, from the SportsBlaze schedule dumps (NBA/raw/schedule_{year}.json)."""
    tips: dict = {}
    for y in list(seasons or AVAILABLE_SEASONS):
        try:
            if local_dir:
                f = Path(local_dir) / "raw" / f"schedule_{y}.json"
                if not f.exists():
                    continue
                raw = json.loads(f.read_text())
            else:
                from mlb_core import storage
                k = raw_schedule_key(y)
                if not storage.exists(k):
                    continue
                raw = json.loads(storage.read_bytes(k))
        except Exception as exc:                                                         # noqa: BLE001
            log.warning("schedule %s unreadable: %r", y, exc)
            continue
        tips.update(_event_tips(raw.get("events")))
    return _tips_series(tips)


def tips_from_raw_boxscores(dates, local_dir: str | None = None, max_days: int = 60) -> pd.Series:
    """Fallback for games newer than the schedule dumps (the nightly refresh caches NBA/raw/boxscores/{date}.json whose
    events carry the full ISO tip time). Bounded to `max_days` request dates."""
    tips: dict = {}
    for d in list(dict.fromkeys(str(x)[:10] for x in dates))[:max_days]:
        try:
            if local_dir:
                f = Path(local_dir) / "raw" / "boxscores" / f"{d}.json"
                if not f.exists():
                    continue
                raw = json.loads(f.read_text())
            else:
                from mlb_core import storage
                k = raw_boxscore_key(d)
                if not storage.exists(k):
                    continue
                raw = json.loads(storage.read_bytes(k))
        except Exception as exc:                                                         # noqa: BLE001
            log.warning("raw boxscore %s unreadable: %r", d, exc)
            continue
        tips.update(_event_tips(raw.get("events")))
    return _tips_series(tips)


def et_day(ts) -> pd.Timestamp:
    """UTC timestamp -> ET calendar day as a tz-naive midnight Timestamp (the repo's game-day convention)."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert(TIMEZONE).tz_localize(None).normalize()


def gather_tips(games: pd.DataFrame, local_dir: str | None = None) -> pd.Series:
    """Schedule dumps first, then the raw boxscore cache for any game the schedules do not cover."""
    tips = load_tips(local_dir)
    missing = games.loc[~games["game_id"].isin(tips.index), "date"]
    if len(missing):
        extra = tips_from_raw_boxscores(sorted(set(missing.astype(str).str[:10])), local_dir)
        if len(extra):
            tips = pd.concat([tips, extra[~extra.index.isin(tips.index)]])
    return tips


# ----------------------------------------------------------------------------- prepare
def prepare(player_box: pd.DataFrame, games: pd.DataFrame, tips: pd.Series | None = None,
            include_preseason: bool = False):
    """Normalise raw masters -> (rows, team_games).

    `tips` (game_id -> UTC tip, see load_tips) turns the masters' UTC date into the ET game day. A game with no known
    tip keeps the master date and is marked tip_known=False (the scorer reports the share; rest features for those
    rows are approximate).

    rows        : one row per (game, player) that PLAYED (minutes > 0), tricode team/opp, parsed minutes, derived stats
                  (pra, usage possessions), previous-game position group. Sorted by (tip, game_id, player_id).
    team_games  : one row per (game, team): ET date, season, tricodes, is_home, summed box totals, possessions, pace.
    """
    pb = player_box.copy()
    gm = games.copy()
    if not include_preseason:
        pb = pb[~pb["season_type"].isin(C.EXCLUDE_SEASON_TYPES)]
        gm = gm[~gm["season_type"].isin(C.EXCLUDE_SEASON_TYPES)]
    gm["date"] = pd.to_datetime(gm["date"])
    tips = tips if tips is not None else pd.Series(dtype="datetime64[ns, UTC]")
    gm = gm.reset_index(drop=True)
    gm["tip"] = gm["game_id"].map(tips) if len(tips) else pd.NaT
    gm["tip"] = pd.to_datetime(gm["tip"], utc=True)
    gm["tip_known"] = gm["tip"].notna()
    day = gm["tip"].dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.normalize()
    gm["day"] = day.where(gm["tip_known"], gm["date"])
    tip_naive = gm["tip"].dt.tz_convert("UTC").dt.tz_localize(None)
    gm["tip_sort"] = tip_naive.where(gm["tip_known"], gm["date"] + pd.Timedelta(hours=18))
    gm["away"] = gm["away_abbr"].map(sb_team)
    gm["home"] = gm["home_abbr"].map(sb_team)
    gm = gm.dropna(subset=["away", "home"])

    pb = pb.drop(columns=["date"])
    pb["min"] = parse_minutes(pb["minutes"])
    pb = pb[pb["min"].notna()]
    pb["team"] = pb["team_abbr"].map(sb_team)
    pb = pb.dropna(subset=["team"])
    pb = pb.merge(gm[["game_id", "day", "tip_sort", "tip_known", "away", "home"]], on="game_id", how="inner")
    pb = pb.rename(columns={"day": "date"})
    pb["opp"] = np.where(pb["is_home"].astype(bool), pb["away"], pb["home"])
    pb = pb.drop(columns=["away", "home"])
    pb["pra"] = pb["points"] + pb["rebounds"] + pb["assists"]
    pb["usg_poss"] = pb["field_goals_attempted"] + 0.44 * pb["free_throws_attempted"] + pb["turnovers"]

    # team-game totals from player rows (DNP rows contribute zeros, so summing everyone is exact)
    agg = pb.groupby(["game_id", "team"]).agg(
        t_pts=("points", "sum"), t_fga=("field_goals_attempted", "sum"), t_fta=("free_throws_attempted", "sum"),
        t_oreb=("rebounds_offensive", "sum"), t_reb=("rebounds", "sum"), t_tov=("turnovers", "sum"),
        t_ast=("assists", "sum"), t_fg3m=("three_points_made", "sum"), t_fg3a=("three_points_attempted", "sum"),
        t_min=("min", "sum"), t_usg=("usg_poss", "sum")).reset_index()
    keep = ["game_id", "day", "season_year", "season_type", "team", "opp", "tip_sort", "tip_known"]
    tg_home = gm.rename(columns={"home": "team", "away": "opp"})[keep].assign(is_home=True)
    tg_away = gm.rename(columns={"away": "team", "home": "opp"})[keep].assign(is_home=False)
    tg = pd.concat([tg_home, tg_away], ignore_index=True).rename(columns={"day": "date"})
    tg = tg.merge(agg, on=["game_id", "team"], how="inner")
    tg["poss"] = tg["t_fga"] + 0.44 * tg["t_fta"] - tg["t_oreb"] + tg["t_tov"]
    opp_poss = tg[["game_id", "team", "poss"]].rename(columns={"team": "opp", "poss": "opp_poss"})
    tg = tg.merge(opp_poss, on=["game_id", "opp"], how="left")
    gmin = (tg["t_min"] / 5.0).clip(lower=40.0)                      # regulation 48; OT games run longer
    tg["pace"] = 48.0 * ((tg["poss"] + tg["opp_poss"]) / 2.0) / gmin
    tg["ortg"] = 100.0 * tg["t_pts"] / tg["poss"].clip(lower=1.0)
    tg = tg.sort_values(["tip_sort", "game_id", "team"]).reset_index(drop=True)

    pb = pb[pb["min"] > C.MIN_PLAYED_MINUTES].copy()
    pb = pb.sort_values(["tip_sort", "game_id", "player_id"]).reset_index(drop=True)
    # position group = the player's PREVIOUS played game's position (a game-local label is not pre-game information)
    pb["pos_prev"] = pb.groupby("player_id")["position"].shift(1)
    pb["pos_grp"] = pb["pos_prev"].map(C.POS_GROUP)
    return pb, tg


def history_status(rows: pd.DataFrame) -> dict:
    """Freshness facts about a prepared history (used by the scorer's staleness gate)."""
    if rows is None or not len(rows):
        return {"n_rows": 0, "last_date": None, "last_season": None, "tip_known_share": None}
    last = rows["date"].max()
    return {"n_rows": int(len(rows)), "last_date": last.date().isoformat(),
            "last_season": int(rows.loc[rows["date"] == last, "season_year"].max()),
            "tip_known_share": round(float(rows["tip_known"].mean()), 4)}
