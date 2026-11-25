"""MOD-04e U1: strictly pregame NFL feature builder (zero future leakage, zero prop inputs).

Pure over injected pandas frames. Every box-score / outcome table is filtered to
game_date < target_date (or (season, week) < target (season, week) when game_date is
unavailable), and injury / depth-chart snapshots are filtered to timestamp <= cutoff_utc
when a timestamp column exists.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from nfl.data.keys import ev_code

EWM_HALFLIFE = 4.0

BANNED_PREFIXES = ("actual_", "outcome_")
BANNED_SUFFIXES = ("_post",)
BANNED_SUBSTRINGS = ("prop_line", "prop_odds")

_TEAM_ALIASES = {
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "NOR": "NO",
    "NWE": "NE",
    "GNB": "GB",
    "KAN": "KC",
    "SFO": "SF",
    "TAM": "TB",
    "SDG": "LAC",
    "RAI": "LV",
}

_TS_CANDIDATE_COLS = (
    "retrieved_at_utc",
    "timestamp",
    "snapshot_ts",
    "updated_at",
    "date_modified",
    "dt",
    "ts",
)

_OUT_STATUSES = frozenset({"out", "doubtful", "ir", "injured reserve", "reserve/injured", "ina", "inactive"})
_OUT_WEIGHT = {
    "out": 1.0,
    "ir": 1.0,
    "injured reserve": 1.0,
    "reserve/injured": 1.0,
    "ina": 1.0,
    "inactive": 1.0,
    "doubtful": 0.8,
    "questionable": 0.4,
    "probable": 0.1,
}

FEATURE_OUTPUT_COLUMNS = [
    "player_id",
    "player_name",
    "position",
    "team",
    "opponent",
    "game_id",
    "game_date",
    "season",
    "week",
    "is_home",
    # Game environment (only market-derived inputs allowed: spread, total, implied team total)
    "spread_line",
    "team_spread",
    "total_line",
    "implied_team_total",
    "implied_opp_total",
    "indoor",
    # Depth chart & injury status as of cutoff
    "depth_chart_role",
    "injury_status",
    "injury_weight",
    # Vacated shares from teammates OUT/DOUBTFUL at cutoff
    "vacated_target_share",
    "vacated_carry_share",
    "vacated_targets",
    "vacated_carries",
    "vacated_out_count",
    # Team trailing pace / PROE / pass-rate
    "team_plays_l3",
    "team_plays_l5",
    "team_plays_szn",
    "team_plays_ewm",
    "team_pass_rate_l3",
    "team_pass_rate_l5",
    "team_pass_rate_szn",
    "team_pass_rate_ewm",
    "team_proe_l3",
    "team_proe_l5",
    "team_proe_szn",
    "team_proe_ewm",
    # Player trailing usage (L3 / L5 / season / EWM)
    "n_prior_games",
    "targets_l3",
    "targets_l5",
    "targets_szn",
    "targets_ewm",
    "target_share_l3",
    "target_share_l5",
    "target_share_szn",
    "target_share_ewm",
    "carries_l3",
    "carries_l5",
    "carries_szn",
    "carries_ewm",
    "carry_share_l3",
    "carry_share_l5",
    "carry_share_szn",
    "carry_share_ewm",
    "snap_share_l3",
    "snap_share_l5",
    "snap_share_szn",
    "snap_share_ewm",
    "routes_l3",
    "routes_l5",
    "routes_szn",
    "routes_ewm",
    "receptions_l3",
    "receptions_l5",
    "receptions_szn",
    "receptions_ewm",
    "rec_yds_l3",
    "rec_yds_l5",
    "rec_yds_szn",
    "rec_yds_ewm",
    "rush_yds_l3",
    "rush_yds_l5",
    "rush_yds_szn",
    "rush_yds_ewm",
    "pass_attempts_l3",
    "pass_attempts_l5",
    "pass_attempts_szn",
    "pass_attempts_ewm",
    "completions_l3",
    "completions_l5",
    "completions_szn",
    "completions_ewm",
    "pass_yds_l3",
    "pass_yds_l5",
    "pass_yds_szn",
    "pass_yds_ewm",
    "interceptions_l3",
    "interceptions_l5",
    "interceptions_szn",
    "interceptions_ewm",
    "rz_carries_l3",
    "rz_carries_l5",
    "rz_carries_szn",
    "rz_carries_ewm",
    "rz_targets_l3",
    "rz_targets_l5",
    "rz_targets_szn",
    "rz_targets_ewm",
    "rush_tds_ewm",
    "rec_tds_ewm",
    "non_rz_tds_ewm",
    # Trailing efficiency rates
    "catch_rate_ewm",
    "ypt_ewm",
    "ypc_ewm",
    "comp_pct_ewm",
    "int_rate_ewm",
    "rz_rush_td_rate_ewm",
    "rz_rec_td_rate_ewm",
    "explosive_td_rate_ewm",
]


def assert_feature_schema(df: pd.DataFrame) -> None:
    """Raise ValueError if any column matches *_post, actual_*, outcome_*, or contains prop_line/prop_odds."""
    bad: list[str] = []
    for col in df.columns:
        low = str(col).strip().lower()
        if (
            any(low.startswith(p) for p in BANNED_PREFIXES)
            or any(low.endswith(s) for s in BANNED_SUFFIXES)
            or any(sub in low for sub in BANNED_SUBSTRINGS)
        ):
            bad.append(str(col))
    if bad:
        raise ValueError(f"Banned post-game / prop columns in feature frame: {bad}")


def _get_frame(frames: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(frames, Mapping):
        val = frames.get(key)
    else:
        val = getattr(frames, key, None)
    if val is None or not isinstance(val, pd.DataFrame):
        return None
    return val.copy()


def _norm_date(d: Any) -> str:
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d).strip()[:10]


def _norm_cutoff(cutoff_utc: Any) -> Optional[pd.Timestamp]:
    if cutoff_utc is None:
        return None
    ts = pd.Timestamp(cutoff_utc)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


from functools import lru_cache


@lru_cache(maxsize=None)
def _ev_code_cached(s: str) -> Optional[str]:
    return ev_code(s)


def _norm_team(team: Any) -> Optional[str]:
    if team is None or (isinstance(team, float) and math.isnan(team)):
        return None
    s = str(team).strip().upper()
    if not s or s == "NAN":
        return None
    s = _TEAM_ALIASES.get(s, s)
    return _ev_code_cached(s) or s


def _map_norm_team(s: "pd.Series") -> "pd.Series":
    """Vectorized _norm_team: normalize each distinct value once, then map.

    Semantically identical to a per-row _norm_team map (team values are a tiny closed
    set); avoids ~10^5 redundant ev_code calls per feature build.
    """
    uniq = pd.unique(s)
    lut = {u: _norm_team(u) for u in uniq}
    return s.map(lut)


def _col_pick(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _resolve_row_dates(
    df: pd.DataFrame,
    sched_gid_date: dict[str, str],
    sched_swt_date: dict[tuple[int, int, str], str],
) -> pd.Series:
    dcol = _col_pick(df, "game_date", "gameday")
    out = pd.Series("", index=df.index, dtype="object")
    if dcol is not None:
        s = df[dcol].astype(str).str.slice(0, 10)
        valid = s.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
        out = out.where(~valid, s)
    if "game_id" in df.columns and sched_gid_date:
        need = out == ""
        mapped = df.loc[need, "game_id"].astype(str).map(sched_gid_date).fillna("")
        out.loc[need] = mapped
    tcol = _col_pick(df, "team", "posteam")
    if tcol is not None and "season" in df.columns and "week" in df.columns and sched_swt_date:
        need = out == ""
        if need.any():
            seasons = pd.to_numeric(df.loc[need, "season"], errors="coerce")
            weeks = pd.to_numeric(df.loc[need, "week"], errors="coerce")
            teams = df.loc[need, tcol].pipe(_map_norm_team)
            keys = [
                (int(s), int(w), t) if pd.notna(s) and pd.notna(w) and t else None
                for s, w, t in zip(seasons, weeks, teams)
            ]
            out.loc[need] = [sched_swt_date.get(k, "") if k is not None else "" for k in keys]
    return out


def _filter_strictly_before_date(
    df: Optional[pd.DataFrame],
    target_day: str,
    target_min_sw: tuple[int, int],
    sched_gid_date: dict[str, str],
    sched_swt_date: dict[tuple[int, int, str], str],
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(df.columns) if df is not None else [])
    dates = _resolve_row_dates(df, sched_gid_date, sched_swt_date)
    has_date = dates != ""
    keep = pd.Series(False, index=df.index)
    keep.loc[has_date] = dates.loc[has_date] < target_day
    if (~has_date).any() and "season" in df.columns and "week" in df.columns:
        no_d = ~has_date
        szn = pd.to_numeric(df.loc[no_d, "season"], errors="coerce")
        wk = pd.to_numeric(df.loc[no_d, "week"], errors="coerce")
        ts, tw = target_min_sw
        ok = szn.notna() & wk.notna() & ((szn < ts) | ((szn == ts) & (wk < tw)))
        keep.loc[no_d] = ok
    out = df.loc[keep].copy()
    out["_game_date"] = dates.loc[keep].values
    return out.reset_index(drop=True)


def _filter_snapshot_frame(
    df: Optional[pd.DataFrame],
    target_day: str,
    target_max_sw: tuple[int, int],
    cutoff_ts: Optional[pd.Timestamp],
    sched_gid_date: dict[str, str],
    sched_swt_date: dict[tuple[int, int, str], str],
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(df.columns) if df is not None else [])
    out = df.copy()
    # 1. Timestamp cutoff filter if any timestamp column exists
    if cutoff_ts is not None:
        for tcol in _TS_CANDIDATE_COLS:
            if tcol in out.columns:
                parsed = pd.to_datetime(out[tcol], utc=True, errors="coerce")
                if parsed.notna().any():
                    out = out.loc[parsed.notna() & (parsed <= cutoff_ts)].copy()
                    break
    if out.empty:
        return out
    # 2. Date / week upper bound (<= target_day / target week)
    dates = _resolve_row_dates(out, sched_gid_date, sched_swt_date)
    has_date = dates != ""
    if has_date.any():
        keep = pd.Series(True, index=out.index)
        keep.loc[has_date] = dates.loc[has_date] <= target_day
        out = out.loc[keep].copy()
    if "season" in out.columns and "week" in out.columns and not out.empty:
        szn = pd.to_numeric(out["season"], errors="coerce")
        wk = pd.to_numeric(out["week"], errors="coerce")
        ts, tw = target_max_sw
        ok = szn.isna() | wk.isna() | (szn < ts) | ((szn == ts) & (wk <= tw))
        out = out.loc[ok].copy()
    return out.reset_index(drop=True)


def _trailing_stats(df: pd.DataFrame, key_col: str, cols: list[str], season_col: str = "season") -> dict[str, np.ndarray]:
    """Compute l3, l5, szn, ewm strictly from prior appearances (df sorted by key_col, order)."""
    n = len(df)
    out: dict[str, np.ndarray] = {}
    if n == 0:
        for c in cols:
            for suf in ("l3", "l5", "szn", "ewm"):
                out[f"{c}_{suf}"] = np.array([], dtype=float)
        return out

    grp = df.groupby(key_col, sort=False)
    for c in cols:
        vals = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float) if c in df.columns else np.full(n, np.nan)
        s = pd.Series(vals, index=df.index)
        # l3 / l5: mean of last 3/5 prior rows (excluding current; shift(1))
        shifts = [s.groupby(df[key_col], sort=False).shift(k).to_numpy(dtype=float) for k in range(1, 6)]
        for name, kmax in (("l3", 3), ("l5", 5)):
            arr = np.column_stack(shifts[:kmax])
            cnt = np.sum(~np.isnan(arr), axis=1)
            tot = np.nansum(arr, axis=1)
            out[f"{c}_{name}"] = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)

        # season-to-date prior mean
        xf = s.fillna(0.0)
        valid = s.notna().astype(float)
        gk = [df[key_col], df[season_col]]
        cs = xf.groupby(gk, sort=False).cumsum().groupby(gk, sort=False).shift(1).fillna(0.0)
        cn = valid.groupby(gk, sort=False).cumsum().groupby(gk, sort=False).shift(1).fillna(0.0)
        out[f"{c}_szn"] = np.where(cn > 0, cs / np.maximum(cn, 1), np.nan)

        # EWM across seasons (shift(1) after per-key ewm)
        ewm_post = s.groupby(df[key_col], sort=False).ewm(
            halflife=EWM_HALFLIFE, ignore_na=True, min_periods=1
        ).mean().reset_index(level=0, drop=True).reindex(df.index)
        out[f"{c}_ewm"] = ewm_post.groupby(df[key_col], sort=False).shift(1).to_numpy(dtype=float)

    # Note: *_ewm is strictly shifted(1) over rows with _game_date < target_day.
    # For the target slate row itself, the caller attaches a dummy row per player AFTER all historical rows
    # so shift(1) pulls the exact summary of ALL historical appearances (< target_day).
    return out


def build_pregame_features(
    target_date: Any,
    cutoff_utc: Any,
    frames: Mapping[str, pd.DataFrame] | Any,
) -> pd.DataFrame:
    """Build strictly pregame features for all players on the target_date NFL slate.

    Parameters
    ----------
    target_date : str | date | datetime
        Target slate calendar date (YYYY-MM-DD).
    cutoff_utc : str | datetime | None
        Snapshot cutoff in UTC; injury and depth-chart rows with timestamp > cutoff_utc are excluded.
    frames : Mapping[str, pd.DataFrame] | object
        Injected nflverse frames: schedules, player_stats, snap_counts, depth_charts, injuries,
        plus optional players, team_stats, pbp_team, pbp_player, pbp_rz, rosters_weekly, game_lines.
    """
    target_day = _norm_date(target_date)
    cutoff_ts = _norm_cutoff(cutoff_utc)

    sched = _get_frame(frames, "schedules")
    if sched is None or sched.empty:
        return pd.DataFrame(columns=FEATURE_OUTPUT_COLUMNS)

    dcol = _col_pick(sched, "gameday", "game_date")
    if dcol is None:
        return pd.DataFrame(columns=FEATURE_OUTPUT_COLUMNS)
    sched["_gdate"] = sched[dcol].astype(str).str.slice(0, 10)
    sched["_home"] = sched["home_team"].pipe(_map_norm_team)
    sched["_away"] = sched["away_team"].pipe(_map_norm_team)
    sched["season"] = pd.to_numeric(sched["season"], errors="coerce").astype("Int64")
    sched["week"] = pd.to_numeric(sched["week"], errors="coerce").astype("Int64")

    slate = sched[sched["_gdate"] == target_day].copy()
    if slate.empty:
        return pd.DataFrame(columns=FEATURE_OUTPUT_COLUMNS)

    # Optional EVSweep game_lines override for spread/total on target slate (never prop lines)
    game_lines = _get_frame(frames, "game_lines")
    if game_lines is not None and not game_lines.empty:
        gl = game_lines.copy()
        if "game_id" in gl.columns:
            gl = gl.drop_duplicates("game_id", keep="last")
            for col in ("spread_line", "total_line"):
                if col in gl.columns:
                    mp = gl.set_index("game_id")[col]
                    mapped = slate["game_id"].map(mp)
                    if col in slate.columns:
                        slate[col] = mapped.where(mapped.notna(), slate[col])
                    else:
                        slate[col] = mapped

    # Target (season, week) bounds
    target_sw = list(zip(slate["season"].dropna().astype(int), slate["week"].dropna().astype(int)))
    target_min_sw = min(target_sw) if target_sw else (9999, 99)
    target_max_sw = max(target_sw) if target_sw else (9999, 99)

    # Build pregame-only schedule lookup maps (scores are NEVER read)
    sched_gid_date = {
        str(gid): gd for gid, gd in zip(sched["game_id"], sched["_gdate"]) if pd.notna(gid) and gd
    }
    sched_swt_date: dict[tuple[int, int, str], str] = {}
    for row in sched.to_dict("records"):
        s_raw, w_raw, gd = row.get("season"), row.get("week"), row.get("_gdate")
        if pd.isna(s_raw) or pd.isna(w_raw) or not gd:
            continue
        s_i, w_i = int(s_raw), int(w_raw)
        if row.get("_home"):
            sched_swt_date[(s_i, w_i, row["_home"])] = gd
        if row.get("_away"):
            sched_swt_date[(s_i, w_i, row["_away"])] = gd

    # Filter all historical box-score / outcome frames strictly to game_date < target_day
    ps = _filter_strictly_before_date(
        _get_frame(frames, "player_stats"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )
    snaps = _filter_strictly_before_date(
        _get_frame(frames, "snap_counts"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )
    ts = _filter_strictly_before_date(
        _get_frame(frames, "team_stats"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )
    pbp_team = _filter_strictly_before_date(
        _get_frame(frames, "pbp_team"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )
    pbp_player = _filter_strictly_before_date(
        _get_frame(frames, "pbp_player"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )
    pbp_rz = _filter_strictly_before_date(
        _get_frame(frames, "pbp_rz"), target_day, target_min_sw, sched_gid_date, sched_swt_date
    )

    # Filter snapshot frames (injuries, depth_charts, rosters_weekly) with timestamp <= cutoff_utc
    injuries = _filter_snapshot_frame(
        _get_frame(frames, "injuries"), target_day, target_max_sw, cutoff_ts, sched_gid_date, sched_swt_date
    )
    depth_charts = _filter_snapshot_frame(
        _get_frame(frames, "depth_charts"), target_day, target_max_sw, cutoff_ts, sched_gid_date, sched_swt_date
    )
    rosters = _filter_snapshot_frame(
        _get_frame(frames, "rosters_weekly"), target_day, target_max_sw, cutoff_ts, sched_gid_date, sched_swt_date
    )
    players_df = _get_frame(frames, "players")

    # Build historical player-game table strictly from < target_day frames
    hist_pg = _build_historical_player_games(ps, snaps, players_df, pbp_player, pbp_rz, sched_gid_date)
    if hist_pg.empty:
        return pd.DataFrame(columns=FEATURE_OUTPUT_COLUMNS)

    # Build team-game history (strictly < target_day)
    hist_tg = _build_historical_team_games(ts, hist_pg, pbp_team, sched_gid_date)

    # Build target-slate team rows (pregame environment only)
    slate_teams = _build_slate_teams(slate)
    team_feat_map = _compute_team_trailing_for_slate(hist_tg, slate_teams)

    # Determine each player's current team & eligibility on the target slate
    # Only players with >= 1 prior appearance (< target_day) get a row
    latest_player_team = _latest_player_team(hist_pg, depth_charts, injuries, rosters, target_max_sw)
    slate_team_set = set(slate_teams["team"])

    # Depth chart role lookup as of cutoff
    depth_role_map = _extract_depth_roles(depth_charts)
    # Injury status lookup as of cutoff
    inj_status_map, inj_weight_map = _extract_own_injuries(injuries, target_max_sw)

    # Filter to players whose current team is on the target slate AND who have prior history
    cand_players = [
        pid for pid in hist_pg["player_id"].unique()
        if latest_player_team.get(pid) in slate_team_set
    ]
    if not cand_players:
        return pd.DataFrame(columns=FEATURE_OUTPUT_COLUMNS)

    # Compute trailing stats for all candidate players by appending 1 placeholder row per player
    hist_cand = hist_pg[hist_pg["player_id"].isin(cand_players)].copy()
    last_meta = hist_cand.sort_values(["player_id", "_ord"], kind="mergesort").groupby("player_id", as_index=False).last()
    slate_by_team = slate_teams.set_index("team").to_dict("index")

    placeholder_rows = []
    for r in last_meta.itertuples(index=False):
        pid = r.player_id
        tm = latest_player_team[pid]
        st = slate_by_team[tm]
        placeholder_rows.append({
            "player_id": pid,
            "player_name": r.player_name,
            "position": r.position,
            "team": tm,
            "opponent": st["opponent"],
            "game_id": st["game_id"],
            "game_date": st["game_date"],
            "season": st["season"],
            "week": st["week"],
            "_ord": int(st["season"]) * 10000 + int(st["week"]) * 100 + 99,
            "_is_target": True,
        })
    ph_df = pd.DataFrame(placeholder_rows)
    hist_cand["_is_target"] = False

    combined = pd.concat([hist_cand, ph_df], ignore_index=True)
    combined = combined.sort_values(["player_id", "_ord"], kind="mergesort").reset_index(drop=True)

    player_trail_cols = [
        "targets", "target_share", "carries", "carry_share", "snap_share", "routes",
        "receptions", "rec_yds", "rush_yds", "pass_attempts", "completions", "pass_yds",
        "interceptions", "rz_carries", "rz_targets", "rush_tds", "rec_tds", "non_rz_tds",
    ]
    tr = _trailing_stats(combined, "player_id", player_trail_cols, season_col="season")
    for k, arr in tr.items():
        combined[k] = arr
    combined["n_prior_games"] = combined.groupby("player_id", sort=False).cumcount().astype(float)

    # Keep only the target slate rows
    out = combined[combined["_is_target"]].copy().reset_index(drop=True)

    # Efficiency EWMs from trailing components
    out["catch_rate_ewm"] = np.where(out["targets_ewm"] > 0, out["receptions_ewm"] / out["targets_ewm"], np.nan)
    out["ypt_ewm"] = np.where(out["targets_ewm"] > 0, out["rec_yds_ewm"] / out["targets_ewm"], np.nan)
    out["ypc_ewm"] = np.where(out["carries_ewm"] > 0, out["rush_yds_ewm"] / out["carries_ewm"], np.nan)
    out["comp_pct_ewm"] = np.where(
        out["pass_attempts_ewm"] > 0, out["completions_ewm"] / out["pass_attempts_ewm"], np.nan
    )
    out["int_rate_ewm"] = np.where(
        out["pass_attempts_ewm"] > 0, out["interceptions_ewm"] / out["pass_attempts_ewm"], np.nan
    )
    out["rz_rush_td_rate_ewm"] = np.where(
        out["rz_carries_ewm"] > 0, out["rush_tds_ewm"] / out["rz_carries_ewm"], np.nan
    )
    out["rz_rec_td_rate_ewm"] = np.where(
        out["rz_targets_ewm"] > 0, out["rec_tds_ewm"] / out["rz_targets_ewm"], np.nan
    )
    touches_ewm = out["carries_ewm"].fillna(0.0) + out["targets_ewm"].fillna(0.0)
    out["explosive_td_rate_ewm"] = np.where(touches_ewm > 0, out["non_rz_tds_ewm"] / touches_ewm, np.nan)

    # Attach game environment + team trailing stats
    for env_col in ("is_home", "spread_line", "team_spread", "total_line", "implied_team_total", "implied_opp_total", "indoor"):
        out[env_col] = out["team"].map(lambda t, c=env_col: slate_by_team[t][c])

    team_cols = [
        "team_plays_l3", "team_plays_l5", "team_plays_szn", "team_plays_ewm",
        "team_pass_rate_l3", "team_pass_rate_l5", "team_pass_rate_szn", "team_pass_rate_ewm",
        "team_proe_l3", "team_proe_l5", "team_proe_szn", "team_proe_ewm",
    ]
    for tc in team_cols:
        out[tc] = out["team"].map(lambda t, c=tc: team_feat_map.get(t, {}).get(c, np.nan))

    # Attach depth-chart role and injury status as of cutoff
    out["depth_chart_role"] = out["player_id"].map(depth_role_map).astype(float)
    out["injury_status"] = out["player_id"].map(inj_status_map).fillna("ACTIVE")
    out["injury_weight"] = out["player_id"].map(inj_weight_map).fillna(0.0).astype(float)

    # Vacated shares from teammates listed OUT / DOUBTFUL in the cutoff snapshot
    # Value each player's trailing EWM share strictly from hist_pg (< target_day)
    latest_usage = _latest_player_usage_ewm(hist_pg)
    vac = _compute_vacated_shares(out, injuries, latest_usage, target_max_sw)
    for vc, vals in vac.items():
        out[vc] = vals

    # Ensure canonical column ordering and zero banned columns
    for col in FEATURE_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[FEATURE_OUTPUT_COLUMNS].copy()
    assert_feature_schema(out)
    return out.sort_values(["game_id", "team", "player_id"], kind="mergesort").reset_index(drop=True)


def _build_historical_player_games(
    ps: pd.DataFrame,
    snaps: pd.DataFrame,
    players_df: Optional[pd.DataFrame],
    pbp_player: pd.DataFrame,
    pbp_rz: pd.DataFrame,
    sched_gid_date: dict[str, str],
) -> pd.DataFrame:
    if ps.empty and snaps.empty:
        return pd.DataFrame()

    # Crosswalk pfr_id -> gsis_id
    pfr_to_gsis: dict[str, str] = {}
    if players_df is not None and not players_df.empty and "pfr_id" in players_df.columns and "gsis_id" in players_df.columns:
        xw = players_df[["pfr_id", "gsis_id"]].dropna()
        pfr_to_gsis = dict(zip(xw["pfr_id"].astype(str), xw["gsis_id"].astype(str)))

    if not ps.empty:
        pcol = _col_pick(ps, "player_id", "gsis_id")
        if pcol is None:
            return pd.DataFrame()
        df = pd.DataFrame({
            "player_id": ps[pcol].astype(str),
            "player_name": ps[_col_pick(ps, "player_display_name", "player_name")] if _col_pick(ps, "player_display_name", "player_name") else ps[pcol].astype(str),
            "position": ps["position"].astype(str) if "position" in ps.columns else "",
            "team": ps[_col_pick(ps, "team", "recent_team")].pipe(_map_norm_team) if _col_pick(ps, "team", "recent_team") else None,
            "game_id": ps["game_id"].astype(str) if "game_id" in ps.columns else "",
            "season": pd.to_numeric(ps["season"], errors="coerce").fillna(0).astype(int) if "season" in ps.columns else 0,
            "week": pd.to_numeric(ps["week"], errors="coerce").fillna(0).astype(int) if "week" in ps.columns else 0,
            "_game_date": ps["_game_date"].astype(str),
            "targets": pd.to_numeric(ps["targets"], errors="coerce").fillna(0.0) if "targets" in ps.columns else 0.0,
            "receptions": pd.to_numeric(ps["receptions"], errors="coerce").fillna(0.0) if "receptions" in ps.columns else 0.0,
            "rec_yds": pd.to_numeric(ps["receiving_yards"], errors="coerce").fillna(0.0) if "receiving_yards" in ps.columns else 0.0,
            "rec_tds": pd.to_numeric(ps["receiving_tds"], errors="coerce").fillna(0.0) if "receiving_tds" in ps.columns else 0.0,
            "carries": pd.to_numeric(ps["carries"], errors="coerce").fillna(0.0) if "carries" in ps.columns else 0.0,
            "rush_yds": pd.to_numeric(ps["rushing_yards"], errors="coerce").fillna(0.0) if "rushing_yards" in ps.columns else 0.0,
            "rush_tds": pd.to_numeric(ps["rushing_tds"], errors="coerce").fillna(0.0) if "rushing_tds" in ps.columns else 0.0,
            "pass_attempts": pd.to_numeric(ps["attempts"], errors="coerce").fillna(0.0) if "attempts" in ps.columns else 0.0,
            "completions": pd.to_numeric(ps["completions"], errors="coerce").fillna(0.0) if "completions" in ps.columns else 0.0,
            "pass_yds": pd.to_numeric(ps["passing_yards"], errors="coerce").fillna(0.0) if "passing_yards" in ps.columns else 0.0,
            "pass_tds": pd.to_numeric(ps["passing_tds"], errors="coerce").fillna(0.0) if "passing_tds" in ps.columns else 0.0,
            "interceptions": pd.to_numeric(
                ps[_col_pick(ps, "passing_interceptions", "interceptions")], errors="coerce"
            ).fillna(0.0) if _col_pick(ps, "passing_interceptions", "interceptions") else 0.0,
            "sacks": pd.to_numeric(ps["sacks_suffered"], errors="coerce").fillna(0.0) if "sacks_suffered" in ps.columns else 0.0,
        })
        if "target_share" in ps.columns:
            df["_raw_tgt_share"] = pd.to_numeric(ps["target_share"], errors="coerce")
        else:
            df["_raw_tgt_share"] = np.nan
        if "routes" in ps.columns:
            df["routes"] = pd.to_numeric(ps["routes"], errors="coerce")
        else:
            df["routes"] = np.nan
    else:
        df = pd.DataFrame(columns=[
            "player_id", "player_name", "position", "team", "game_id", "season", "week", "_game_date",
            "targets", "receptions", "rec_yds", "rec_tds", "carries", "rush_yds", "rush_tds",
            "pass_attempts", "completions", "pass_yds", "pass_tds", "interceptions", "sacks",
            "_raw_tgt_share", "routes",
        ])

    # Merge snap_counts + add snap-only rows (played_zero)
    df["snap_share"] = np.nan
    if not snaps.empty:
        sn = snaps.copy()
        if "gsis_id" in sn.columns:
            sn["player_id"] = sn["gsis_id"].astype(str)
        elif "player_id" in sn.columns:
            sn["player_id"] = sn["player_id"].astype(str)
        elif "pfr_player_id" in sn.columns:
            sn["player_id"] = sn["pfr_player_id"].astype(str).map(pfr_to_gsis)
        else:
            sn["player_id"] = None
        sn = sn[sn["player_id"].notna()].copy()
        if not sn.empty:
            pct_col = _col_pick(sn, "offense_pct", "snap_share")
            off_col = _col_pick(sn, "offense_snaps", "total_snaps")
            sn["_snap_share"] = pd.to_numeric(sn[pct_col], errors="coerce") if pct_col else np.nan
            sn["_off_snaps"] = pd.to_numeric(sn[off_col], errors="coerce").fillna(0.0) if off_col else 0.0
            sn["_team"] = sn["team"].pipe(_map_norm_team) if "team" in sn.columns else None
            sn_key = sn.drop_duplicates(["player_id", "game_id"])[["player_id", "game_id", "_snap_share", "_off_snaps"]]
            df = df.merge(sn_key, on=["player_id", "game_id"], how="left")
            df["snap_share"] = df["_snap_share"].where(df["_snap_share"].notna(), df["snap_share"])
            df = df.drop(columns=["_snap_share", "_off_snaps"], errors="ignore")

            have = set(zip(df["player_id"], df["game_id"]))
            gh = sn[(sn["_off_snaps"] > 0) & (~pd.Series(zip(sn["player_id"], sn["game_id"]), index=sn.index).isin(have))].copy()
            if not gh.empty:
                ghost = pd.DataFrame({
                    "player_id": gh["player_id"].astype(str),
                    "player_name": gh["player"].astype(str) if "player" in gh.columns else gh["player_id"].astype(str),
                    "position": gh["position"].astype(str) if "position" in gh.columns else "",
                    "team": gh["_team"],
                    "game_id": gh["game_id"].astype(str) if "game_id" in gh.columns else "",
                    "season": pd.to_numeric(gh["season"], errors="coerce").fillna(0).astype(int) if "season" in gh.columns else 0,
                    "week": pd.to_numeric(gh["week"], errors="coerce").fillna(0).astype(int) if "week" in gh.columns else 0,
                    "_game_date": gh["_game_date"].astype(str),
                    "snap_share": gh["_snap_share"],
                })
                for col in ("targets", "receptions", "rec_yds", "rec_tds", "carries", "rush_yds", "rush_tds",
                            "pass_attempts", "completions", "pass_yds", "pass_tds", "interceptions", "sacks"):
                    ghost[col] = 0.0
                ghost["_raw_tgt_share"] = np.nan
                ghost["routes"] = pd.to_numeric(gh["routes"], errors="coerce") if "routes" in gh.columns else np.nan
                df = pd.concat([df, ghost], ignore_index=True)

    if df.empty:
        return df

    # Optional pbp_rz / pbp_player enrichment (strictly < target_day)
    df["rz_carries"] = np.nan
    df["rz_targets"] = np.nan
    if not pbp_rz.empty and "player_id" in pbp_rz.columns and "game_id" in pbp_rz.columns:
        rz = pbp_rz.drop_duplicates(["game_id", "player_id"])
        m = df.merge(rz[["game_id", "player_id", "rz_car", "rz_tgt"]], on=["game_id", "player_id"], how="left")
        covered = df["game_id"].isin(set(rz["game_id"]))
        df["rz_carries"] = np.where(covered, m["rz_car"].fillna(0.0), np.nan)
        df["rz_targets"] = np.where(covered, m["rz_tgt"].fillna(0.0), np.nan)
    elif not pbp_player.empty and "player_id" in pbp_player.columns and "game_id" in pbp_player.columns:
        pp = pbp_player.drop_duplicates(["game_id", "player_id"])
        cols = [c for c in ("car_rz10", "tgt_rz20", "routes") if c in pp.columns]
        if cols:
            m = df.merge(pp[["game_id", "player_id"] + cols], on=["game_id", "player_id"], how="left")
            covered = df["game_id"].isin(set(pp["game_id"]))
            if "car_rz10" in cols:
                df["rz_carries"] = np.where(covered, m["car_rz10"].fillna(0.0), np.nan)
            if "tgt_rz20" in cols:
                df["rz_targets"] = np.where(covered, m["tgt_rz20"].fillna(0.0), np.nan)
            if "routes" in cols:
                df["routes"] = df["routes"].where(df["routes"].notna(), m["routes"])

    # Compute per-game team denominators for target_share and carry_share
    df["_key_game"] = np.where(df["game_id"] != "", df["game_id"], df["season"].astype(str) + "_" + df["week"].astype(str))
    tm_tgt = df.groupby(["_key_game", "team"])["targets"].transform("sum")
    tm_car = df.groupby(["_key_game", "team"])["carries"].transform("sum")
    computed_tgt_share = np.where(tm_tgt > 0, df["targets"] / tm_tgt.where(tm_tgt > 0), np.nan)
    df["target_share"] = np.where(df["_raw_tgt_share"].notna(), df["_raw_tgt_share"], computed_tgt_share)
    df["carry_share"] = np.where(tm_car > 0, df["carries"] / tm_car.where(tm_car > 0), np.nan)

    # Non-RZ TDs (approximated as 0 when RZ table exists and player had RZ opportunities; or total TDs - RZ TDs if available)
    # Without play-by-play TD distance, track non_rz_tds as 0 when rz_carries+rz_targets > 0 and td occurred?
    # Better: keep non_rz_tds as NaN unless rz_carries/rz_targets are present, where explosive/non-RZ is approximated
    # conservatively from total TDs scaled down when player has no RZ usage.
    total_td = df["rush_tds"] + df["rec_tds"]
    has_rz = df["rz_carries"].notna() | df["rz_targets"].notna()
    had_rz_opp = (df["rz_carries"].fillna(0.0) + df["rz_targets"].fillna(0.0)) > 0
    df["non_rz_tds"] = np.where(has_rz, np.where(~had_rz_opp, total_td, 0.0), total_td * 0.25)

    # Canonical sort order using date or (season, week)
    date_int = pd.to_numeric(df["_game_date"].str.replace("-", "", regex=False), errors="coerce").fillna(0).astype(int)
    df["_ord"] = np.where(date_int > 0, date_int, df["season"] * 10000 + df["week"] * 100)
    return df.drop_duplicates(["player_id", "_key_game"], keep="last").reset_index(drop=True)


def _build_historical_team_games(
    ts: pd.DataFrame,
    hist_pg: pd.DataFrame,
    pbp_team: pd.DataFrame,
    sched_gid_date: dict[str, str],
) -> pd.DataFrame:
    if not ts.empty and "team" in ts.columns:
        t = ts.copy()
        t["team"] = t["team"].pipe(_map_norm_team)
        att = pd.to_numeric(t["attempts"], errors="coerce").fillna(0.0) if "attempts" in t.columns else 0.0
        sacks = pd.to_numeric(t["sacks_suffered"], errors="coerce").fillna(0.0) if "sacks_suffered" in t.columns else 0.0
        car = pd.to_numeric(t["carries"], errors="coerce").fillna(0.0) if "carries" in t.columns else 0.0
        pass_plays = att + sacks
        plays = pass_plays + car
        tg = pd.DataFrame({
            "team": t["team"],
            "game_id": t["game_id"].astype(str) if "game_id" in t.columns else "",
            "season": pd.to_numeric(t["season"], errors="coerce").fillna(0).astype(int) if "season" in t.columns else 0,
            "week": pd.to_numeric(t["week"], errors="coerce").fillna(0).astype(int) if "week" in t.columns else 0,
            "_game_date": t["_game_date"].astype(str),
            "team_plays": plays,
            "team_pass_rate": np.where(plays > 0, pass_plays / plays.where(plays > 0), np.nan),
        })
    elif not hist_pg.empty:
        grp = hist_pg.groupby(["team", "_key_game", "season", "week", "_game_date"], as_index=False).agg(
            pass_att=("pass_attempts", "sum"),
            sacks=("sacks", "sum"),
            carries=("carries", "sum"),
        )
        pass_plays = grp["pass_att"] + grp["sacks"]
        plays = pass_plays + grp["carries"]
        tg = pd.DataFrame({
            "team": grp["team"],
            "game_id": grp["_key_game"],
            "season": grp["season"].astype(int),
            "week": grp["week"].astype(int),
            "_game_date": grp["_game_date"],
            "team_plays": plays,
            "team_pass_rate": np.where(plays > 0, pass_plays / plays.where(plays > 0), np.nan),
        })
    else:
        return pd.DataFrame()

    tg["team_proe"] = np.nan
    if not pbp_team.empty and "team" in pbp_team.columns and "game_id" in pbp_team.columns:
        pt = pbp_team.copy()
        pt["team"] = pt["team"].pipe(_map_norm_team)
        pcol = _col_pick(pt, "off_pass_oe", "pass_oe", "proe")
        if pcol is not None:
            pt = pt.drop_duplicates(["game_id", "team"])
            m = tg.merge(pt[["game_id", "team", pcol]], on=["game_id", "team"], how="left")
            tg["team_proe"] = pd.to_numeric(m[pcol], errors="coerce").to_numpy(dtype=float)

    # Fallback PROE proxy when pbp_team is absent: pass_rate minus league neutral baseline (0.57)
    tg["team_proe"] = np.where(tg["team_proe"].notna(), tg["team_proe"], tg["team_pass_rate"] - 0.57)
    date_int = pd.to_numeric(tg["_game_date"].str.replace("-", "", regex=False), errors="coerce").fillna(0).astype(int)
    tg["_ord"] = np.where(date_int > 0, date_int, tg["season"] * 10000 + tg["week"] * 100)
    return tg.drop_duplicates(["team", "game_id"], keep="last").reset_index(drop=True)


def _build_slate_teams(slate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in slate.to_dict("records"):
        roof = str(r.get("roof") or "").strip().lower()
        indoor = 1.0 if roof in ("dome", "closed") else (np.nan if not roof or roof == "nan" else 0.0)
        spread_raw = pd.to_numeric(r.get("spread_line", np.nan), errors="coerce")
        total_raw = pd.to_numeric(r.get("total_line", np.nan), errors="coerce")
        spread_val = float(spread_raw) if pd.notna(spread_raw) else np.nan
        total_val = float(total_raw) if pd.notna(total_raw) else np.nan
        for is_home, tm, opp, sign in (
            (1.0, r.get("_home"), r.get("_away"), 1.0),
            (0.0, r.get("_away"), r.get("_home"), -1.0),
        ):
            if not tm:
                continue
            tm_spread = sign * spread_val if not math.isnan(spread_val) else np.nan
            imp_tm = (total_val / 2.0 + tm_spread / 2.0) if not (math.isnan(total_val) or math.isnan(tm_spread)) else np.nan
            imp_op = (total_val / 2.0 - tm_spread / 2.0) if not (math.isnan(total_val) or math.isnan(tm_spread)) else np.nan
            rows.append({
                "team": tm,
                "opponent": opp,
                "game_id": str(r.get("game_id")),
                "game_date": r.get("_gdate"),
                "season": int(r["season"]) if pd.notna(r.get("season")) else 0,
                "week": int(r["week"]) if pd.notna(r.get("week")) else 0,
                "is_home": is_home,
                "spread_line": spread_val,
                "team_spread": tm_spread,
                "total_line": total_val,
                "implied_team_total": imp_tm,
                "implied_opp_total": imp_op,
                "indoor": indoor,
            })
    return pd.DataFrame(rows).drop_duplicates("team", keep="first").reset_index(drop=True)


def _compute_team_trailing_for_slate(hist_tg: pd.DataFrame, slate_teams: pd.DataFrame) -> dict[str, dict[str, float]]:
    if hist_tg.empty or slate_teams.empty:
        return {}
    h = hist_tg[hist_tg["team"].isin(set(slate_teams["team"]))].copy()
    if h.empty:
        return {}
    h["_is_target"] = False
    ph = pd.DataFrame({
        "team": slate_teams["team"],
        "game_id": slate_teams["game_id"],
        "season": slate_teams["season"],
        "week": slate_teams["week"],
        "_game_date": slate_teams["game_date"],
        "team_plays": np.nan,
        "team_pass_rate": np.nan,
        "team_proe": np.nan,
        "_ord": slate_teams["season"] * 10000 + slate_teams["week"] * 100 + 99,
        "_is_target": True,
    })
    comb = pd.concat([h, ph], ignore_index=True).sort_values(["team", "_ord"], kind="mergesort").reset_index(drop=True)
    tr = _trailing_stats(comb, "team", ["team_plays", "team_pass_rate", "team_proe"], season_col="season")
    for k, arr in tr.items():
        comb[k] = arr
    targ = comb[comb["_is_target"]]
    out: dict[str, dict[str, float]] = {}
    for r in targ.to_dict("records"):
        out[r["team"]] = {k: v for k, v in r.items() if k.startswith(("team_plays_", "team_pass_rate_", "team_proe_"))}
    return out


def _latest_player_team(
    hist_pg: pd.DataFrame,
    depth_charts: pd.DataFrame,
    injuries: pd.DataFrame,
    rosters: pd.DataFrame,
    target_max_sw: tuple[int, int],
) -> dict[str, str]:
    """Latest team per player: prefer cutoff-filtered target-week depth/roster/injury, else latest box score."""
    latest: dict[str, str] = {}
    for r in hist_pg.sort_values(["player_id", "_ord"], kind="mergesort").groupby("player_id", as_index=False).last().itertuples(index=False):
        if r.team:
            latest[r.player_id] = r.team

    ts, tw = target_max_sw
    for frame, tcol in ((rosters, "team"), (injuries, "team"), (depth_charts, "team")):
        if frame is None or frame.empty or tcol not in frame.columns:
            continue
        pcol = _col_pick(frame, "gsis_id", "player_id")
        if pcol is None:
            continue
        sub = frame[frame[pcol].notna()].copy()
        if "season" in sub.columns and "week" in sub.columns:
            szn = pd.to_numeric(sub["season"], errors="coerce")
            wk = pd.to_numeric(sub["week"], errors="coerce")
            sub = sub[(szn == ts) & (wk == tw)]
        for pid, tm in zip(sub[pcol].astype(str), sub[tcol].pipe(_map_norm_team)):
            if tm:
                latest[pid] = tm
    return latest


def _extract_depth_roles(depth_charts: pd.DataFrame) -> dict[str, float]:
    if depth_charts is None or depth_charts.empty:
        return {}
    pcol = _col_pick(depth_charts, "gsis_id", "player_id")
    rcol = _col_pick(depth_charts, "depth_team", "pos_rank", "depth_rank", "role")
    if pcol is None or rcol is None:
        return {}
    dc = depth_charts[depth_charts[pcol].notna()].copy()
    for tcol in _TS_CANDIDATE_COLS:
        if tcol in dc.columns:
            dc["_ts"] = pd.to_datetime(dc[tcol], utc=True, errors="coerce")
            dc = dc.sort_values("_ts", kind="mergesort")
            break
    else:
        sort_cols = [c for c in ("season", "week") if c in dc.columns]
        if sort_cols:
            dc = dc.sort_values(sort_cols, kind="mergesort")
    dc = dc.drop_duplicates(pcol, keep="last")
    roles = pd.to_numeric(dc[rcol], errors="coerce")
    return {str(pid): float(rv) for pid, rv in zip(dc[pcol], roles) if pd.notna(rv)}


def _extract_own_injuries(injuries: pd.DataFrame, target_max_sw: tuple[int, int]) -> tuple[dict[str, str], dict[str, float]]:
    if injuries is None or injuries.empty:
        return {}, {}
    pcol = _col_pick(injuries, "gsis_id", "player_id")
    scol = _col_pick(injuries, "report_status", "status", "injury_status")
    if pcol is None or scol is None:
        return {}, {}
    inj = injuries[injuries[pcol].notna()].copy()
    if "season" in inj.columns and "week" in inj.columns:
        ts, tw = target_max_sw
        inj = inj[(pd.to_numeric(inj["season"], errors="coerce") == ts) & (pd.to_numeric(inj["week"], errors="coerce") == tw)]
    if inj.empty:
        return {}, {}
    for tcol in _TS_CANDIDATE_COLS:
        if tcol in inj.columns:
            inj["_ts"] = pd.to_datetime(inj[tcol], utc=True, errors="coerce")
            inj = inj.sort_values("_ts", kind="mergesort")
            break
    inj = inj.drop_duplicates(pcol, keep="last")
    st_map: dict[str, str] = {}
    wt_map: dict[str, float] = {}
    for pid, st in zip(inj[pcol].astype(str), inj[scol].astype(str)):
        if not isinstance(st, str):
            # Missing report_status (NaN) survives astype(str) as a float
            # under the pandas string dtype; it carries no injury info.
            continue
        clean = st.strip()
        if not clean or clean.lower() == "nan":
            continue
        st_map[pid] = clean.upper()
        wt_map[pid] = float(_OUT_WEIGHT.get(clean.lower(), 0.0))
    return st_map, wt_map


def _latest_player_usage_ewm(hist_pg: pd.DataFrame) -> dict[str, dict[str, float]]:
    if hist_pg.empty:
        return {}
    s = hist_pg.sort_values(["player_id", "_ord"], kind="mergesort").reset_index(drop=True)
    out: dict[str, dict[str, float]] = {}
    for pid, grp in s.groupby("player_id", sort=False):
        n = len(grp)
        w = 0.5 ** ((n - 1 - np.arange(n)) / EWM_HALFLIFE)
        row_u: dict[str, float] = {}
        for col in ("target_share", "carry_share", "targets", "carries"):
            vals = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
            ok = np.isfinite(vals)
            row_u[col] = float(np.sum(w[ok] * vals[ok]) / np.sum(w[ok])) if ok.any() else 0.0
        out[str(pid)] = row_u
    return out


def _compute_vacated_shares(
    out_df: pd.DataFrame,
    injuries: pd.DataFrame,
    latest_usage: dict[str, dict[str, float]],
    target_max_sw: tuple[int, int],
) -> dict[str, np.ndarray]:
    n = len(out_df)
    zeros = np.zeros(n, dtype=float)
    res = {
        "vacated_target_share": zeros.copy(),
        "vacated_carry_share": zeros.copy(),
        "vacated_targets": zeros.copy(),
        "vacated_carries": zeros.copy(),
        "vacated_out_count": zeros.copy(),
    }
    if injuries is None or injuries.empty or n == 0:
        return res
    pcol = _col_pick(injuries, "gsis_id", "player_id")
    scol = _col_pick(injuries, "report_status", "status", "injury_status")
    tcol = _col_pick(injuries, "team")
    if pcol is None or scol is None or tcol is None:
        return res

    inj = injuries[injuries[pcol].notna()].copy()
    if "season" in inj.columns and "week" in inj.columns:
        ts, tw = target_max_sw
        inj = inj[(pd.to_numeric(inj["season"], errors="coerce") == ts) & (pd.to_numeric(inj["week"], errors="coerce") == tw)]
    if inj.empty:
        return res
    for tsc in _TS_CANDIDATE_COLS:
        if tsc in inj.columns:
            inj["_ts"] = pd.to_datetime(inj[tsc], utc=True, errors="coerce")
            inj = inj.sort_values("_ts", kind="mergesort")
            break
    inj = inj.drop_duplicates(pcol, keep="last")
    inj["_team"] = inj[tcol].pipe(_map_norm_team)
    inj["_st"] = inj[scol].astype(str).str.strip().str.lower()
    flagged = inj[inj["_st"].isin(_OUT_STATUSES) & inj["_team"].notna()]
    if flagged.empty:
        return res

    by_team: dict[str, list[tuple[str, float]]] = {}
    for pid, tm, st in zip(flagged[pcol].astype(str), flagged["_team"], flagged["_st"]):
        wt = _OUT_WEIGHT.get(st, 1.0)
        by_team.setdefault(tm, []).append((pid, wt))

    for i, (pid, tm) in enumerate(zip(out_df["player_id"].astype(str), out_df["team"])):
        teammates = [item for item in by_team.get(tm, ()) if item[0] != pid]
        if not teammates:
            continue
        for opid, wt in teammates:
            u = latest_usage.get(opid)
            if u is None:
                continue
            res["vacated_target_share"][i] += wt * u["target_share"]
            res["vacated_carry_share"][i] += wt * u["carry_share"]
            res["vacated_targets"][i] += wt * u["targets"]
            res["vacated_carries"][i] += wt * u["carries"]
            res["vacated_out_count"][i] += wt
    return res
