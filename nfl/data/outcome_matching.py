"""NFL board outcome matching via nflverse crosswalk, snap counts, and weekly rosters.

Why this exists
---------------
nflverse ``player_stats`` only emits a row when a player records at least one stat in a
game. A player who played offensive/special-teams snaps and recorded zeros has NO row in
``player_stats``, making him indistinguishable from a dressed DNP or a game-day inactive
under a ``player_stats``-only join. In addition, ad-hoc date->week lookup tables drift
around bye/Monday games.

Design
------
Given NFL board rows (``player``/``player_name``, ``team``, ``game_date``/``day``, ``stat``,
``line``) for a single ``season``, ``resolve_board_outcomes(board_df, season)`` loads the
five nflverse tables strictly via ``nfl.data.read.load`` (``players``, ``schedules``,
``player_stats``, ``snap_counts``, ``rosters_weekly``) and delegates to the pure
``resolve_from_frames`` core.

Resolution pipeline per row:
  1. Validate ``stat`` against ``STAT_SPECS``; unknown stats become ``"unresolved"``.
  2. Map ``(game_date, team)`` to ``(season, week, game_id)`` via ``schedules`` (using
     ``nfl.data.keys.ev_code`` + team aliases so JAX/JAC, LA/LAR, etc. align).
  3. Normalize the board player name (Unicode fold, punctuation strip, generational
     suffix removal ``Jr./Sr./II/III/IV/V`` via ``ev.props.norm_name``) and resolve to
     ``gsis_id`` through the ``players`` crosswalk, disambiguating namesakes by ``team``
     and that week's ``rosters_weekly`` / ``players`` metadata.
  4. Check ``player_stats`` on ``(player_id == gsis_id, game_id)``:
       * found -> ``status="stat"``, ``actual=<stat value>``, ``gradeable=True``.
  5. Otherwise join ``snap_counts`` on ``(pfr_player_id == players.pfr_id, game_id)``:
       * ``total_snaps > 0`` -> ``status="played_zero"``, ``actual=0.0``, ``gradeable=True``.
       * ``total_snaps == 0`` -> ``status="dnp"``, ``actual=NaN``, ``gradeable=False``.
  6. Otherwise (no ``snap_counts`` row) check ``rosters_weekly`` for ``(season, week)``:
       * not on that week's roster for ``team``, or ``status`` / ``game_status`` marks the
         player outside the active picture (e.g. ``INA``, ``RES``, ``DEV``, ``CUT``,
         ``RET``, ``OUT``, ``IR``) -> ``status="inactive"``, ``actual=NaN``,
         ``gradeable=False``.
  7. Anything else (missing schedule/crosswalk/pfr_id, active roster row with missing
     snap data, missing tables) -> ``status="unresolved"``, ``actual=NaN``,
     ``gradeable=False`` with an explicit ``reason``.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from ev.props import norm_name as _ev_norm_name
from ev.props import split_player
from nfl.data import read as _nfl_read
from nfl.data.keys import ev_code

STATUS_STAT = "stat"
STATUS_PLAYED_ZERO = "played_zero"
STATUS_DNP = "dnp"
STATUS_INACTIVE = "inactive"
STATUS_UNRESOLVED = "unresolved"

VALID_STATUSES = (
    STATUS_STAT,
    STATUS_PLAYED_ZERO,
    STATUS_DNP,
    STATUS_INACTIVE,
    STATUS_UNRESOLVED,
)
CLEAN_STATUSES = (
    STATUS_STAT,
    STATUS_PLAYED_ZERO,
    STATUS_DNP,
    STATUS_INACTIVE,
)

# Extra feed/book abbreviations not covered by nfl.data.keys.ev_code.
_TEAM_VARIANTS = {
    "NOR": "NO",
    "NWE": "NE",
    "GNB": "GB",
    "KAN": "KC",
    "SFO": "SF",
    "TAM": "TB",
    "SDG": "LAC",
    "RAI": "LV",
}

# Rosters_weekly statuses that represent an active, dressed-eligible roster spot.
_ACTIVE_ROSTER_STATUSES = frozenset({"ACT", "ACTIVE"})
_INACTIVE_GAME_STATUSES = frozenset({
    "INA", "INACTIVE", "OUT", "DNP", "IR", "RES", "DEV", "CUT", "RET",
    "EXE", "PUP", "NFI", "SUS", "TRD", "TRC", "TRT", "SCRATCH", "SCRATCHED",
})

# Board stat -> (candidate player_stats columns, mode).
# Mode "first" takes the first present/non-NaN column; "anytime_td" evaluates
# float(rushing_tds + receiving_tds > 0).
STAT_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
    "pass_yds": (("passing_yards",), "first"),
    "pass_tds": (("passing_tds",), "first"),
    "pass_att": (("attempts",), "first"),
    "pass_attempts": (("attempts",), "first"),
    "pass_cmp": (("completions",), "first"),
    "pass_completions": (("completions",), "first"),
    "interceptions": (("passing_interceptions", "interceptions"), "first"),
    "rush_yds": (("rushing_yards",), "first"),
    "rush_att": (("carries",), "first"),
    "rush_attempts": (("carries",), "first"),
    "rush_tds": (("rushing_tds",), "first"),
    "rec_yds": (("receiving_yards",), "first"),
    "receptions": (("receptions",), "first"),
    "targets": (("targets",), "first"),
    "rec_tds": (("receiving_tds",), "first"),
    "anytime_td": (("rushing_tds", "receiving_tds"), "anytime_td"),
}

_OUTPUT_META_COLS = ("status", "actual", "gradeable", "reason", "gsis_id", "game_id", "week")


def normalize_name(name: object) -> str:
    """Normalize a player name (accent-fold, strip punctuation and Jr./Sr./II/III/IV/V)."""
    if name is None or (isinstance(name, float) and math.isnan(name)):
        return ""
    raw, _tail = split_player(str(name))
    return _ev_norm_name(raw)


def normalize_team(team: object) -> Optional[str]:
    """Normalize a team abbreviation into canonical ev/ code space."""
    if not isinstance(team, str):
        return None
    t = team.strip().upper()
    if not t:
        return None
    return ev_code(_TEAM_VARIANTS.get(t, t))


def _clean_str(val: object) -> Optional[str]:
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s if s else None


def _col_pick(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _build_schedule_index(schedules: pd.DataFrame, season: int) -> tuple[dict, dict]:
    """Index (gameday, team_ev) -> (game_id, week) and gameday -> [(game_id, week)]."""
    by_day_team: dict[tuple[str, str], tuple[str, int]] = {}
    by_day: dict[str, list[tuple[str, int]]] = defaultdict(list)
    if schedules is None or schedules.empty:
        return by_day_team, by_day
    sub = schedules[pd.to_numeric(schedules["season"], errors="coerce") == int(season)]
    for row in sub.itertuples(index=False):
        gd = str(getattr(row, "gameday", ""))[:10]
        gid = _clean_str(getattr(row, "game_id", None))
        wk_raw = getattr(row, "week", None)
        if not gd or gid is None or pd.isna(wk_raw):
            continue
        wk = int(wk_raw)
        by_day[gd].append((gid, wk))
        for side in ("away_team", "home_team"):
            tm = normalize_team(getattr(row, side, None))
            if tm:
                by_day_team[(gd, tm)] = (gid, wk)
    return by_day_team, by_day


def _build_players_index(
    players: pd.DataFrame,
) -> tuple[dict[str, set[str]], dict[str, Optional[str]], dict[str, tuple[Optional[str], Optional[int]]]]:
    """Build norm_name -> {gsis_id}, gsis_id -> pfr_id, and gsis_id -> (latest_team, last_season)."""
    name_idx: dict[str, set[str]] = defaultdict(set)
    pfr_of: dict[str, Optional[str]] = {}
    meta_of: dict[str, tuple[Optional[str], Optional[int]]] = {}
    if players is None or players.empty:
        return name_idx, pfr_of, meta_of

    for row in players.to_dict("records"):
        gid = _clean_str(row.get("gsis_id"))
        if gid is None:
            continue
        pfr = _clean_str(row.get("pfr_id"))
        if pfr is not None:
            pfr_of[gid] = pfr
        elif gid not in pfr_of:
            pfr_of[gid] = None

        lt = normalize_team(row.get("latest_team"))
        ls_raw = row.get("last_season")
        ls = int(ls_raw) if ls_raw is not None and pd.notna(ls_raw) else None
        meta_of[gid] = (lt, ls)

        for col in ("display_name", "full_name"):
            nm = normalize_name(row.get(col))
            if nm:
                name_idx[nm].add(gid)
        last = _clean_str(row.get("last_name"))
        if last:
            for fc in ("first_name", "common_first_name", "football_name"):
                first = _clean_str(row.get(fc))
                if first:
                    nm = normalize_name(f"{first} {last}")
                    if nm:
                        name_idx[nm].add(gid)
    return name_idx, pfr_of, meta_of


def _build_roster_index(
    rosters_weekly: pd.DataFrame, season: int
) -> tuple[dict[tuple[int, str, str], list[dict[str, Any]]], set[int], dict[tuple[str, str], set[int]]]:
    """Index (week, team_ev, gsis_id) -> roster rows, covered weeks, and (team_ev, gsis_id) -> weeks."""
    by_wtg: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    weeks_present: set[int] = set()
    team_weeks: dict[tuple[str, str], set[int]] = defaultdict(set)
    if rosters_weekly is None or rosters_weekly.empty:
        return by_wtg, weeks_present, team_weeks

    sub = rosters_weekly
    if "season" in sub.columns:
        sub = sub[pd.to_numeric(sub["season"], errors="coerce") == int(season)]
    for row in sub.to_dict("records"):
        wk_raw = row.get("week")
        if wk_raw is None or pd.isna(wk_raw):
            continue
        wk = int(wk_raw)
        weeks_present.add(wk)
        gid = _clean_str(row.get("gsis_id"))
        tm = normalize_team(row.get("team"))
        if gid is None or tm is None:
            continue
        by_wtg[(wk, tm, gid)].append(row)
        team_weeks[(tm, gid)].add(wk)
    return by_wtg, weeks_present, team_weeks


def _build_stats_index(
    player_stats: pd.DataFrame, season: int
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    """Index player_stats by (player_id, game_id) and fallback (player_id, week)."""
    by_gid: dict[tuple[str, str], dict[str, Any]] = {}
    by_wk: dict[tuple[str, int], dict[str, Any]] = {}
    if player_stats is None or player_stats.empty:
        return by_gid, by_wk

    sub = player_stats
    if "season" in sub.columns:
        sub = sub[pd.to_numeric(sub["season"], errors="coerce") == int(season)]
    for row in sub.to_dict("records"):
        pid = _clean_str(row.get("player_id"))
        if pid is None:
            continue
        game_id = _clean_str(row.get("game_id"))
        if game_id is not None:
            by_gid[(pid, game_id)] = row
        wk_raw = row.get("week")
        if wk_raw is not None and pd.notna(wk_raw):
            by_wk[(pid, int(wk_raw))] = row
    return by_gid, by_wk


def _build_snap_index(
    snap_counts: pd.DataFrame, season: int
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, int], float], set[str], set[int]]:
    """Index snap_counts total snaps by (pfr_player_id, game_id) and (pfr_player_id, week)."""
    by_gid: dict[tuple[str, str], float] = {}
    by_wk: dict[tuple[str, int], float] = {}
    snap_games: set[str] = set()
    snap_weeks: set[int] = set()
    if snap_counts is None or snap_counts.empty:
        return by_gid, by_wk, snap_games, snap_weeks

    sub = snap_counts
    if "season" in sub.columns:
        sub = sub[pd.to_numeric(sub["season"], errors="coerce") == int(season)]
    if sub.empty:
        return by_gid, by_wk, snap_games, snap_weeks

    has_total = "total_snaps" in sub.columns
    snap_cols = [c for c in ("offense_snaps", "defense_snaps", "st_snaps") if c in sub.columns]

    for row in sub.to_dict("records"):
        pfr_id = _clean_str(row.get("pfr_player_id"))
        game_id = _clean_str(row.get("game_id"))
        wk_raw = row.get("week")
        wk = int(wk_raw) if wk_raw is not None and pd.notna(wk_raw) else None
        if game_id is not None:
            snap_games.add(game_id)
        if wk is not None:
            snap_weeks.add(wk)
        if pfr_id is None:
            continue

        tot: Optional[float] = None
        if has_total and row.get("total_snaps") is not None and pd.notna(row.get("total_snaps")):
            tot = float(row["total_snaps"])
        elif snap_cols:
            vals = [row.get(c) for c in snap_cols]
            if any(v is not None and pd.notna(v) for v in vals):
                tot = sum(float(v) for v in vals if v is not None and pd.notna(v))
        if tot is None:
            continue

        if game_id is not None:
            by_gid[(pfr_id, game_id)] = max(by_gid.get((pfr_id, game_id), 0.0), tot)
        if wk is not None:
            by_wk[(pfr_id, wk)] = max(by_wk.get((pfr_id, wk), 0.0), tot)
    return by_gid, by_wk, snap_games, snap_weeks


def _disambiguate_player(
    cands: set[str],
    *,
    team: Optional[str],
    week: Optional[int],
    season: int,
    roster_wtg: dict[tuple[int, str, str], list[dict[str, Any]]],
    roster_team_weeks: dict[tuple[str, str], set[int]],
    player_meta: dict[str, tuple[Optional[str], Optional[int]]],
) -> tuple[Optional[str], str]:
    """Narrow candidate gsis_ids to a single player or return (None, reason)."""
    if not cands:
        return None, "player_not_in_crosswalk"
    if len(cands) == 1:
        return next(iter(cands)), ""

    if team is not None:
        if week is not None:
            wk_team = {g for g in cands if (week, team, g) in roster_wtg}
            if len(wk_team) == 1:
                return next(iter(wk_team)), ""
            if wk_team:
                cands = wk_team
        szn_team = {g for g in cands if (team, g) in roster_team_weeks}
        if len(szn_team) == 1:
            return next(iter(szn_team)), ""
        if szn_team:
            cands = szn_team

        meta_team = {g for g in cands if player_meta.get(g, (None, None))[0] == team}
        if len(meta_team) == 1:
            return next(iter(meta_team)), ""
        if meta_team:
            cands = meta_team

    # Filter out clearly retired/historical players when an active/recent candidate exists.
    recent = {
        g for g in cands
        if (ls := player_meta.get(g, (None, None))[1]) is not None and ls >= season - 1
    }
    if len(recent) == 1:
        return next(iter(recent)), ""
    if recent:
        cands = recent

    if week is not None and team is not None:
        act = {
            g for g in cands
            if any(
                str(r.get("status") or "").strip().upper() in _ACTIVE_ROSTER_STATUSES
                for r in roster_wtg.get((week, team, g), ())
            )
        }
        if len(act) == 1:
            return next(iter(act)), ""

    return None, f"ambiguous_player:{len(cands)}_candidates"


def _extract_stat_value(stat: str, stat_row: Mapping[str, Any]) -> tuple[Optional[float], str]:
    """Extract the resolved float value for `stat` from one `player_stats` row."""
    cols, mode = STAT_SPECS[stat]
    if mode == "first":
        saw_col = False
        for col in cols:
            if col in stat_row:
                saw_col = True
                val = stat_row[col]
                if val is not None and pd.notna(val):
                    return float(val), ""
        return None, "missing_stat_column" if not saw_col else "stat_value_nan"

    # anytime_td: 1.0 iff rushing_tds + receiving_tds > 0 else 0.0
    present = [c for c in cols if c in stat_row]
    if not present:
        return None, "missing_stat_column"
    vals = [stat_row[c] for c in present]
    if all(v is None or pd.isna(v) for v in vals):
        return None, "stat_value_nan"
    td_total = sum(float(v) for v in vals if v is not None and pd.notna(v))
    return (1.0 if td_total > 0 else 0.0), ""


def _roster_row_is_inactive(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """Determine from rosters_weekly rows whether the player is absent from the active picture."""
    if not rows:
        return True, "not_on_weekly_roster"

    for row in rows:
        gs = _clean_str(row.get("game_status"))
        if gs is not None:
            gs_up = gs.upper()
            if gs_up in _INACTIVE_GAME_STATUSES:
                return True, f"roster_game_status:{gs_up}"
            if gs_up in _ACTIVE_ROSTER_STATUSES and _clean_str(row.get("status")) is None:
                return False, ""

        st = _clean_str(row.get("status"))
        if st is not None:
            st_up = st.upper()
            if st_up not in _ACTIVE_ROSTER_STATUSES:
                return True, f"roster_status:{st_up}"
    return False, ""


def resolve_from_frames(
    board_df: pd.DataFrame,
    season: int,
    *,
    players: pd.DataFrame,
    schedules: pd.DataFrame,
    player_stats: pd.DataFrame,
    snap_counts: pd.DataFrame,
    rosters_weekly: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve NFL board rows against in-memory nflverse frames (pure core; no I/O)."""
    out = board_df.copy()
    if out.empty:
        for col, default in (
            ("status", pd.Series(dtype="object")),
            ("actual", pd.Series(dtype="float64")),
            ("gradeable", pd.Series(dtype="bool")),
            ("reason", pd.Series(dtype="object")),
            ("gsis_id", pd.Series(dtype="object")),
            ("game_id", pd.Series(dtype="object")),
            ("week", pd.Series(dtype="float64")),
        ):
            if col not in out.columns:
                out[col] = default
        return out

    player_col = _col_pick(out, "player", "player_name")
    date_col = _col_pick(out, "game_date", "day", "gameday")
    team_col = _col_pick(out, "team")
    stat_col = _col_pick(out, "stat")

    sched_dt, sched_d = _build_schedule_index(schedules, season)
    name_idx, pfr_of, player_meta = _build_players_index(players)
    roster_wtg, roster_weeks, roster_team_weeks = _build_roster_index(rosters_weekly, season)
    stats_gid, stats_wk = _build_stats_index(player_stats, season)
    snap_gid, snap_wk, snap_games, snap_weeks = _build_snap_index(snap_counts, season)

    has_game_id_in_stats = player_stats is not None and "game_id" in player_stats.columns
    has_game_id_in_snaps = snap_counts is not None and "game_id" in snap_counts.columns

    statuses: list[str] = []
    actuals: list[float] = []
    gradeables: list[bool] = []
    reasons: list[str] = []
    gsis_ids: list[Optional[str]] = []
    game_ids: list[Optional[str]] = []
    weeks: list[Optional[int]] = []

    def _emit(
        status: str,
        actual: float,
        gradeable: bool,
        reason: str,
        gsis_id: Optional[str] = None,
        game_id: Optional[str] = None,
        week: Optional[int] = None,
    ) -> None:
        statuses.append(status)
        actuals.append(actual)
        gradeables.append(gradeable)
        reasons.append(reason)
        gsis_ids.append(gsis_id)
        game_ids.append(game_id)
        weeks.append(week)

    for row in out.to_dict("records"):
        stat = _clean_str(row.get(stat_col)) if stat_col else None
        if stat is None or stat not in STAT_SPECS:
            _emit(STATUS_UNRESOLVED, float("nan"), False, f"unknown_stat:{stat}")
            continue

        raw_player = row.get(player_col) if player_col else None
        _raw_name, tail_team = split_player(str(raw_player or ""))
        pnorm = normalize_name(raw_player)
        if not pnorm:
            _emit(STATUS_UNRESOLVED, float("nan"), False, "empty_player_name")
            continue

        team = normalize_team(row.get(team_col) if team_col else None) or normalize_team(tail_team)
        gdate_raw = row.get(date_col) if date_col else None
        gdate = str(gdate_raw)[:10] if gdate_raw is not None and pd.notna(gdate_raw) else ""

        game_id: Optional[str] = None
        week: Optional[int] = None
        if gdate and team and (gdate, team) in sched_dt:
            game_id, week = sched_dt[(gdate, team)]
        elif gdate and not team and gdate in sched_d:
            day_games = sched_d[gdate]
            wks = {w for _, w in day_games}
            if len(day_games) == 1:
                game_id, week = day_games[0]
            elif len(wks) == 1:
                week = next(iter(wks))

        if week is None:
            _emit(STATUS_UNRESOLVED, float("nan"), False, "no_schedule_match")
            continue

        cands = set(name_idx.get(pnorm, ()))
        gsis_id, why = _disambiguate_player(
            cands,
            team=team,
            week=week,
            season=season,
            roster_wtg=roster_wtg,
            roster_team_weeks=roster_team_weeks,
            player_meta=player_meta,
        )
        if gsis_id is None:
            _emit(STATUS_UNRESOLVED, float("nan"), False, why, game_id=game_id, week=week)
            continue

        # 1. player_stats lookup (gsis_id == player_id)
        stat_row = None
        if game_id is not None and has_game_id_in_stats:
            stat_row = stats_gid.get((gsis_id, game_id))
        else:
            stat_row = stats_wk.get((gsis_id, week))

        if stat_row is not None:
            val, err = _extract_stat_value(stat, stat_row)
            if val is None:
                _emit(STATUS_UNRESOLVED, float("nan"), False, err, gsis_id=gsis_id, game_id=game_id, week=week)
            else:
                _emit(STATUS_STAT, val, True, "", gsis_id=gsis_id, game_id=game_id, week=week)
            continue

        # 2. snap_counts lookup (pfr_player_id == players.pfr_id)
        pfr_id = pfr_of.get(gsis_id)
        tot_snaps: Optional[float] = None
        if pfr_id is not None:
            if game_id is not None and has_game_id_in_snaps:
                tot_snaps = snap_gid.get((pfr_id, game_id))
            else:
                tot_snaps = snap_wk.get((pfr_id, week))

        if tot_snaps is not None:
            if tot_snaps > 0:
                _emit(STATUS_PLAYED_ZERO, 0.0, True, "played_zero_snaps", gsis_id=gsis_id, game_id=game_id, week=week)
            else:
                _emit(STATUS_DNP, float("nan"), False, "dnp_zero_snaps", gsis_id=gsis_id, game_id=game_id, week=week)
            continue

        # 3. rosters_weekly lookup when no snap_counts row exists
        if week not in roster_weeks:
            _emit(STATUS_UNRESOLVED, float("nan"), False, "missing_rosters_weekly", gsis_id=gsis_id, game_id=game_id, week=week)
            continue

        if team is None:
            _emit(STATUS_UNRESOLVED, float("nan"), False, "missing_team_for_roster_check", gsis_id=gsis_id, game_id=game_id, week=week)
            continue

        r_rows = roster_wtg.get((week, team, gsis_id), [])
        is_inactive, inact_reason = _roster_row_is_inactive(r_rows)
        if is_inactive:
            _emit(STATUS_INACTIVE, float("nan"), False, inact_reason, gsis_id=gsis_id, game_id=game_id, week=week)
            continue

        # Active on rosters_weekly but absent from both player_stats and snap_counts.
        if pfr_id is None:
            reason = "missing_pfr_id"
        elif game_id is not None and game_id not in snap_games and week not in snap_weeks:
            reason = "missing_snap_counts_for_game"
        else:
            reason = "active_no_snap_row"
        _emit(STATUS_UNRESOLVED, float("nan"), False, reason, gsis_id=gsis_id, game_id=game_id, week=week)

    out["status"] = statuses
    out["actual"] = pd.Series(actuals, index=out.index, dtype="float64")
    out["gradeable"] = pd.Series(gradeables, index=out.index, dtype="bool")
    out["reason"] = reasons
    out["gsis_id"] = gsis_ids
    out["game_id"] = game_ids
    out["week"] = weeks
    return out


def resolve_board_outcomes(board_df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Resolve NFL board rows for `season` using nflverse tables loaded via nfl.data.read.load.

    Returns a copy of `board_df` with columns: ``status``, ``actual`` (float or NaN),
    ``gradeable`` (bool), ``reason``, ``gsis_id``, ``game_id``, and ``week``.
    """
    season_int = int(season)
    players = _nfl_read.load("players")
    schedules = _nfl_read.load("schedules")
    player_stats = _nfl_read.load("player_stats", seasons=[season_int])
    snap_counts = _nfl_read.load("snap_counts", seasons=[season_int])
    rosters_weekly = _nfl_read.load("rosters_weekly", seasons=[season_int])
    return resolve_from_frames(
        board_df,
        season_int,
        players=players,
        schedules=schedules,
        player_stats=player_stats,
        snap_counts=snap_counts,
        rosters_weekly=rosters_weekly,
    )


def summarize_resolution(resolved_df: pd.DataFrame) -> dict[str, Any]:
    """Return per-status counts, clean-resolution rate, gradeable counts, and per-stat breakdown."""
    total = int(len(resolved_df))
    raw_counts = resolved_df["status"].value_counts().to_dict() if total else {}
    counts = {s: int(raw_counts.get(s, 0)) for s in VALID_STATUSES}
    clean = sum(counts[s] for s in CLEAN_STATUSES)
    gradeable = int(resolved_df["gradeable"].sum()) if total and "gradeable" in resolved_df.columns else 0

    by_stat: dict[str, dict[str, Any]] = {}
    stat_col = _col_pick(resolved_df, "stat")
    if total and stat_col:
        for stat_name, sub in resolved_df.groupby(stat_col, sort=True):
            sub_raw = sub["status"].value_counts().to_dict()
            sub_counts = {s: int(sub_raw.get(s, 0)) for s in VALID_STATUSES}
            sub_total = int(len(sub))
            sub_clean = sum(sub_counts[s] for s in CLEAN_STATUSES)
            by_stat[str(stat_name)] = {
                "total": sub_total,
                "counts": sub_counts,
                "clean_resolved": sub_clean,
                "clean_resolution_rate": (sub_clean / sub_total) if sub_total else 0.0,
                "gradeable": int(sub["gradeable"].sum()),
            }

    reasons: dict[str, int] = {}
    if total and "reason" in resolved_df.columns:
        unres = resolved_df[resolved_df["status"] == STATUS_UNRESOLVED]
        if not unres.empty:
            reasons = {str(k): int(v) for k, v in Counter(unres["reason"].fillna("")).items()}

    return {
        "total": total,
        "counts": counts,
        "clean_resolved": clean,
        "clean_resolution_rate": (clean / total) if total else 0.0,
        "gradeable": gradeable,
        "by_stat": by_stat,
        "unresolved_reasons": reasons,
    }
