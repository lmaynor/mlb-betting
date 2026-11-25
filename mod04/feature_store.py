"""MOD-04 Cluster (a) U1 Feature Store & Label Store for NBA steals and blocks.

LEAKAGE & TIME-TRAVEL CONTRACT:
  * ``build_pregame_features(target_date, cutoff_utc, data_dir, slate=None)`` is a
    PURE function that physically filters every historical source at load time to
    ``date < target_date`` (UTC tip-date convention matching ``player_boxscores_master.csv``)
    and every live/injury/minutes-cap snapshot to ``ts_utc <= cutoff_utc``.
  * Deleting ALL box-score rows on or after ``target_date`` produces byte-identical
    feature output from ``build_pregame_features``.
  * ``load_labels(data_dir)`` is a separate module-level Label Store function used ONLY by
    training/grading code paths; feature-building functions never import or call it.
  * ``assert_feature_schema(df, denylist=None)`` hard-raises ``ValueError`` on any
    ``*_post``, ``actual_*``, ``outcome_*``, or denylisted label/post-game column.

DATA SOURCE SUBSTITUTION NOTE:
  * Opponent turnover rate and live-ball turnover share are derived from
    ``NBA/hoopr/pbp/play_by_play_*.parquet`` when present (strictly ``game_date < target_date``).
    If PBP files are unavailable or unreadable (e.g., lightweight fixtures), the store
    automatically falls back to ``NBA/team_boxscores_master.csv`` / summed
    ``NBA/player_boxscores_master.csv`` turnovers for ``opp_tov_rate_l15`` with
    ``opp_live_tov_share_l15 = NaN``.
"""
from __future__ import annotations

import glob
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from mlb_core.data.player_names import norm_name
from mod04.backbone_nba import MIN_BACKBONE_COLS, compute_minutes_backbone
from nba.props.config import EXCLUDE_SEASON_TYPES, POS_GROUP
from nba.props.data import parse_minutes, sb_team
from nba.teams import team_code

# --------------------------------------------------------------------------- constants
_FORBIDDEN_PATTERNS = (
    re.compile(r".*_post$"),
    re.compile(r"^actual_.*"),
    re.compile(r"^outcome_.*"),
)
DEFAULT_LABEL_DENYLIST: frozenset[str] = frozenset({
    "steals",
    "blocks",
    "minutes",
    "played",
    "starter",
    "points",
    "rebounds",
    "assists",
    "turnovers",
    "fouls",
    "field_goals_made",
    "field_goals_attempted",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "away_points",
    "home_points",
})

ARCHETYPE_TABLE: dict[int, str] = {
    0: "ball_hawking_guard",
    1: "perimeter_guard",
    2: "wing",
    3: "rim_big",
    4: "other_big",
}
POS_GROUP_ID: dict[str, int] = {"G": 0, "W": 1, "B": 2}
POS_DEFAULT_RATE36: dict[str, tuple[float, float]] = {
    "G": (1.26, 0.43),
    "W": (1.14, 0.58),
    "B": (1.03, 1.18),
}
BALL_HAWK_STL36_MIN = 1.35
RIM_BIG_BLK36_MIN = 1.10
EB_PRIOR_MIN = 360.0
EB_WINDOW_PRIOR_MIN = 180.0
DEFAULT_LEAGUE_TOTAL = 224.0
DEFAULT_LEAGUE_PACE = 99.0
RIM_ZONES = frozenset({0})
PAINT_ZONES = frozenset({0, 1, 2, 3})

ID_COLUMNS = [
    "game_id",
    "date",
    "season_year",
    "player_id",
    "name",
    "player_norm",
    "position",
    "team",
    "opp",
    "archetype_name",
]

FEATURE_COLUMNS = [
    "stl_per36_l5",
    "stl_per36_l15",
    "stl_per36_l40",
    "stl_per36_szn",
    "stl_rate_eb",
    "blk_per36_l5",
    "blk_per36_l15",
    "blk_per36_l40",
    "blk_per36_szn",
    "blk_rate_eb",
    "min_ewm8",
    "min_l5",
    "min_l15",
    "min_l40",
    "min_std10",
    "starter_rate_l10",
    "starter_rate_szn",
    "n_prior_games",
    "n_prior_szn",
    "pf_per36_l15",
    "pf_per36_l40",
    "opp_tov_rate_l15",
    "opp_live_tov_share_l15",
    "opp_stl_allowed_l15",
    "opp_blk_allowed_l15",
    "opp_rim_share_l15",
    "opp_paint_share_l15",
    "archetype_id",
    "pos_group_id",
    "spread",
    "abs_spread",
    "total",
    "implied_team_total",
    "team_pace_l15",
    "opp_pace_l15",
    "env_pace_est",
    "rest_days",
    "is_b2b",
    "is_home",
    *MIN_BACKBONE_COLS,
]

_TEAM_MAP_EXTRA = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "PHO": "PHX",
    "UTAH": "UTA",
    "WSH": "WAS",
}


def _norm_team(abbr: Any) -> str | None:
    if abbr is None or (isinstance(abbr, float) and np.isnan(abbr)):
        return None
    s = str(abbr).strip().upper()
    if not s:
        return None
    if s in _TEAM_MAP_EXTRA:
        return _TEAM_MAP_EXTRA[s]
    return sb_team(s) or team_code(s)


def _norm_team_series(s: pd.Series) -> pd.Series:
    """Map team abbreviations via unique values (100x faster than elementwise .map)."""
    uniq = s.dropna().unique()
    mapping = {u: _norm_team(u) for u in uniq}
    return s.map(mapping)


def _norm_name_series(s: pd.Series) -> pd.Series:
    """Normalize player names via unique values."""
    uniq = s.dropna().unique()
    mapping = {u: norm_name(str(u)) for u in uniq}
    return s.astype(str).map(mapping).fillna("")


def _to_utc_ts(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def _date_str(d: Any) -> str:
    return str(pd.Timestamp(d).date()) if not isinstance(d, str) else d[:10]


def _resolve_path(data_dir: str | Path, *candidates: str) -> Path | None:
    root = Path(data_dir)
    for rel in candidates:
        p = root / rel
        if p.exists():
            return p
    return None


def assert_feature_schema(df: pd.DataFrame, denylist: Iterable[str] | None = None) -> None:
    """Hard schema gate: raise ``ValueError`` if any post-game / label column is present."""
    deny = set(DEFAULT_LABEL_DENYLIST)
    if denylist:
        deny |= {str(c) for c in denylist}
    bad: list[str] = []
    for col in df.columns:
        c = str(col)
        if c in deny or any(rx.match(c) for rx in _FORBIDDEN_PATTERNS):
            bad.append(c)
    if bad:
        raise ValueError(f"Leakage schema gate failed; forbidden columns present: {sorted(bad)}")


# --------------------------------------------------------------------------- Label Store (training/grading ONLY)
def load_labels(data_dir: str | Path, include_preseason: bool = False) -> pd.DataFrame:
    """Separate Label Store used ONLY by training/grading code paths.

    Feature builders must NEVER import or call this function.
    Returns one row per played player-game with ``game_id``, ``date``, ``season_year``,
    ``player_id``, ``team``, ``minutes``, ``played``, ``steals``, ``blocks``.
    """
    path = _resolve_path(data_dir, "NBA/player_boxscores_master.csv", "player_boxscores_master.csv")
    if path is None:
        raise FileNotFoundError(f"player_boxscores_master.csv not found under {data_dir}")
    usecols = [
        "game_id", "season_year", "season_type", "date", "team_abbr", "is_home",
        "player_id", "name", "minutes", "played", "steals", "blocks",
    ]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    if not include_preseason:
        df = df[~df["season_type"].isin(EXCLUDE_SEASON_TYPES)]
    df["minutes"] = parse_minutes(df["minutes"])
    played_bool = df["played"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df = df[played_bool & (df["minutes"].fillna(0.0) > 0.0)].copy()
    df["date"] = df["date"].astype(str).str[:10]
    df["team"] = _norm_team_series(df["team_abbr"])
    df["player_id"] = df["player_id"].astype(str)
    df["game_id"] = df["game_id"].astype(str)
    df["steals"] = pd.to_numeric(df["steals"], errors="coerce").fillna(0).astype(int)
    df["blocks"] = pd.to_numeric(df["blocks"], errors="coerce").fillna(0).astype(int)
    df["played"] = True
    return df[[
        "game_id", "date", "season_year", "player_id", "name", "team",
        "minutes", "played", "steals", "blocks",
    ]].reset_index(drop=True)


# --------------------------------------------------------------------------- Loaders with strict date/cutoff filter
def _load_player_box_before(data_dir: str | Path, max_date: str | None) -> pd.DataFrame:
    path = _resolve_path(data_dir, "NBA/player_boxscores_master.csv", "player_boxscores_master.csv")
    if path is None:
        return pd.DataFrame()
    cols = [
        "game_id", "season_year", "season_type", "date", "team_abbr", "is_home",
        "player_id", "name", "position", "starter", "played", "minutes",
        "field_goals_attempted", "free_throws_attempted", "rebounds_offensive",
        "points", "steals", "blocks", "turnovers", "fouls",
    ]
    avail = pd.read_csv(path, nrows=0).columns.tolist()
    usecols = [c for c in cols if c in avail]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    df["date"] = df["date"].astype(str).str[:10]
    if max_date is not None:
        df = df[df["date"] < max_date]
    if "season_type" in df.columns:
        df = df[~df["season_type"].isin(EXCLUDE_SEASON_TYPES)]
    df["minutes"] = parse_minutes(df["minutes"])
    played_bool = df["played"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df = df[played_bool & (df["minutes"].fillna(0.0) > 0.0)].copy()
    df["team"] = _norm_team_series(df["team_abbr"])
    df = df[df["team"].notna()].copy()
    df["player_id"] = df["player_id"].astype(str)
    df["game_id"] = df["game_id"].astype(str)
    df["player_norm"] = _norm_name_series(df["name"])
    df["starter"] = df["starter"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df["is_home"] = df["is_home"].astype(str).str.lower().isin({"true", "1", "1.0"})
    for num_col in (
        "field_goals_attempted", "free_throws_attempted", "rebounds_offensive",
        "points", "steals", "blocks", "turnovers", "fouls",
    ):
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce").fillna(0.0)
        else:
            df[num_col] = 0.0
    if "position" not in df.columns:
        df["position"] = "SF"
    df["pos_grp"] = df["position"].map(POS_GROUP).fillna("W")
    return df.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def _load_games_before(data_dir: str | Path, max_date: str | None) -> pd.DataFrame:
    path = _resolve_path(data_dir, "NBA/games_master.csv", "games_master.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    df["date"] = df["date"].astype(str).str[:10]
    if max_date is not None:
        df = df[df["date"] < max_date]
    if "season_type" in df.columns:
        df = df[~df["season_type"].isin(EXCLUDE_SEASON_TYPES)]
    df["game_id"] = df["game_id"].astype(str)
    df["home"] = _norm_team_series(df["home_abbr"])
    df["away"] = _norm_team_series(df["away_abbr"])
    return df[df["home"].notna() & df["away"].notna()].copy()


def _load_schedule_on_date(data_dir: str | Path, target_date: str) -> pd.DataFrame:
    """Read ONLY pre-tip schedule matchups (no score columns) on ``target_date``."""
    path = _resolve_path(data_dir, "NBA/games_master.csv", "games_master.csv")
    if path is not None:
        cols = ["game_id", "season_year", "season_type", "date", "away_abbr", "home_abbr"]
        avail = pd.read_csv(path, nrows=0).columns.tolist()
        df = pd.read_csv(path, usecols=[c for c in cols if c in avail], low_memory=False)
        df["date"] = df["date"].astype(str).str[:10]
        df = df[df["date"] == target_date]
        if "season_type" in df.columns:
            df = df[~df["season_type"].isin(EXCLUDE_SEASON_TYPES)]
        if not df.empty:
            df["game_id"] = df["game_id"].astype(str)
            df["home"] = df["home_abbr"].map(_norm_team)
            df["away"] = df["away_abbr"].map(_norm_team)
            df = df[df["home"].notna() & df["away"].notna()].copy()
            return df[["game_id", "season_year", "date", "away", "home"]].reset_index(drop=True)
    return pd.DataFrame(columns=["game_id", "season_year", "date", "away", "home"])


def _season_year_of_date(d: str) -> int:
    ts = pd.Timestamp(d)
    return int(ts.year if ts.month >= 8 else ts.year - 1)


def _load_pbp_team_game_before(
    data_dir: str | Path,
    max_date: str | None,
    min_year: int | None = None,
) -> pd.DataFrame:
    """Aggregate team-game turnovers, live-ball turnovers, and spread from hoopR PBP.

    Strictly filters ``game_date < max_date`` on read. Returns empty DataFrame if no PBP
    files exist so the caller can fall back cleanly to box-score turnovers.
    """
    pbp_dir = _resolve_path(data_dir, "NBA/hoopr/pbp", "hoopr/pbp")
    if pbp_dir is None:
        return pd.DataFrame()
    files = sorted(glob.glob(str(pbp_dir / "play_by_play_*.parquet")))
    if not files:
        return pd.DataFrame()

    cols = [
        "game_id", "game_date", "team_id", "home_team_id", "away_team_id",
        "home_team_abbrev", "away_team_abbrev", "home_team_spread", "type_text", "text",
    ]
    parts: list[pd.DataFrame] = []
    for f in files:
        if min_year is not None:
            m = re.search(r"(\d{4})\.parquet$", f)
            if m and int(m.group(1)) < min_year:
                continue
        try:
            df = pd.read_parquet(f, columns=cols)
        except Exception:
            continue
        df["date"] = df["game_date"].astype(str).str[:10]
        if max_date is not None:
            df = df[df["date"] < max_date]
        if df.empty:
            continue
        games_sp = (
            df.groupby("game_id", sort=False)
            .agg(
                date=("date", "first"),
                home_raw=("home_team_abbrev", "first"),
                away_raw=("away_team_abbrev", "first"),
                home_id=("home_team_id", "first"),
                away_id=("away_team_id", "first"),
                home_spread=("home_team_spread", "first"),
            )
            .reset_index()
        )
        games_sp["home"] = _norm_team_series(games_sp["home_raw"])
        games_sp["away"] = _norm_team_series(games_sp["away_raw"])

        tov_mask = df["type_text"].astype(str).str.contains("turnover", case=False, regex=False, na=False)
        tov = df.loc[tov_mask, ["game_id", "team_id", "type_text", "text"]].copy()
        if not tov.empty:
            is_steal = tov["text"].astype(str).str.contains("steal", case=False, regex=False, na=False)
            is_live = is_steal | tov["type_text"].astype(str).isin({"Bad Pass\nTurnover", "Lost Ball Turnover"})
            tov["is_live"] = is_live.astype(int)
            tov["is_steal"] = is_steal.astype(int)
            t_agg = (
                tov.groupby(["game_id", "team_id"], sort=False)
                .agg(tov_pbp=("is_live", "size"), live_tov=("is_live", "sum"), stl_pbp=("is_steal", "sum"))
                .reset_index()
            )
        else:
            t_agg = pd.DataFrame(columns=["game_id", "team_id", "tov_pbp", "live_tov", "stl_pbp"])

        home_rows = games_sp[["game_id", "date", "home", "away", "home_id", "home_spread"]].rename(
            columns={"home": "team", "away": "opp", "home_id": "team_id"}
        )
        home_rows["pbp_spread"] = -pd.to_numeric(home_rows["home_spread"], errors="coerce")
        away_rows = games_sp[["game_id", "date", "away", "home", "away_id", "home_spread"]].rename(
            columns={"away": "team", "home": "opp", "away_id": "team_id"}
        )
        away_rows["pbp_spread"] = pd.to_numeric(away_rows["home_spread"], errors="coerce")
        both = pd.concat([home_rows, away_rows], ignore_index=True)
        both = both[both["team"].notna() & both["opp"].notna()].merge(
            t_agg, on=["game_id", "team_id"], how="left"
        )
        for c in ("tov_pbp", "live_tov", "stl_pbp"):
            both[c] = both[c].fillna(0.0)
        parts.append(both[["date", "team", "opp", "pbp_spread", "tov_pbp", "live_tov", "stl_pbp"]])

    if not parts:
        return pd.DataFrame()
    res = pd.concat(parts, ignore_index=True)
    return res.drop_duplicates(["date", "team", "opp"], keep="last").sort_values(["date", "team"]).reset_index(drop=True)


def _load_shots_team_game_before(
    data_dir: str | Path,
    max_date: str | None,
    min_year: int | None = None,
) -> pd.DataFrame:
    """Aggregate team-game offensive rim & paint attempt shares from ``shots_20*.parquet``."""
    shots_dir = _resolve_path(data_dir, "NBA/shots", "shots")
    if shots_dir is None:
        return pd.DataFrame()
    files = sorted(glob.glob(str(shots_dir / "shots_20*.parquet")))
    if not files:
        return pd.DataFrame()
    cols = ["date", "team", "zone", "x", "y"]
    parts: list[pd.DataFrame] = []
    for f in files:
        if min_year is not None:
            m = re.search(r"(\d{4})\.parquet$", f)
            if m and int(m.group(1)) < min_year:
                continue
        try:
            df = pd.read_parquet(f, columns=cols)
        except Exception:
            continue
        df["date"] = df["date"].astype(str).str[:10]
        if max_date is not None:
            df = df[df["date"] < max_date]
        if df.empty:
            continue
        df["team"] = _norm_team_series(df["team"])
        df = df[df["team"].notna()]
        z = pd.to_numeric(df["zone"], errors="coerce").fillna(-1).astype(int)
        dist = np.hypot(pd.to_numeric(df["x"], errors="coerce"), pd.to_numeric(df["y"], errors="coerce") - 5.25)
        is_rim = np.where(z >= 0, z.isin(RIM_ZONES), dist <= 4.5).astype(float)
        is_paint = np.where(z >= 0, z.isin(PAINT_ZONES), (df["x"].abs() <= 8.0) & (df["y"] <= 19.0)).astype(float)
        df = df.assign(fga=1.0, rim_fga=is_rim, paint_fga=is_paint)
        agg = (
            df.groupby(["date", "team"], sort=False)
            .agg(shot_fga=("fga", "sum"), rim_fga=("rim_fga", "sum"), paint_fga=("paint_fga", "sum"))
            .reset_index()
        )
        parts.append(agg)
    if not parts:
        return pd.DataFrame()
    return (
        pd.concat(parts, ignore_index=True)
        .groupby(["date", "team"], as_index=False)[["shot_fga", "rim_fga", "paint_fga"]]
        .sum()
        .sort_values(["date", "team"])
        .reset_index(drop=True)
    )


def _load_injuries_at(
    data_dir: str | Path,
    target_date: str,
    cutoff_utc: pd.Timestamp,
) -> tuple[dict[str, dict[str, str]], dict[tuple[str, str], str]]:
    """Load latest pre-cutoff official + ESPN injury snapshots (``ts_utc <= cutoff_utc``).

    Returns ``(by_team_norm, status_by_team_player_norm)``.
    Official reports take priority over ESPN when both exist before ``cutoff_utc``.
    """
    status_by_tp: dict[tuple[str, str], str] = {}
    root = Path(data_dir)

    # 1. ESPN snapshots (filtered by fetched_at_utc or filename timestamp <= cutoff_utc)
    espn_files = sorted(glob.glob(str(root / "NBA/injuries/espn/*/injuries_*.csv")))
    best_espn_ts: pd.Timestamp | None = None
    best_espn_df: pd.DataFrame | None = None
    for f in espn_files:
        p = Path(f)
        day_dir = p.parent.name
        hhmmss = p.stem.replace("injuries_", "")
        if len(hhmmss) == 6 and day_dir[:4].isdigit():
            file_ts = pd.Timestamp(f"{day_dir}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}Z")
        else:
            continue
        if file_ts <= cutoff_utc and (best_espn_ts is None or file_ts > best_espn_ts):
            best_espn_ts = file_ts
            best_espn_df = pd.read_csv(f)
    if best_espn_df is not None and not best_espn_df.empty:
        if "fetched_at_utc" in best_espn_df.columns:
            fts = pd.to_datetime(best_espn_df["fetched_at_utc"], utc=True, errors="coerce")
            best_espn_df = best_espn_df[fts.isna() | (fts <= cutoff_utc)]
        for r in best_espn_df.itertuples(index=False):
            t = _norm_team(getattr(r, "team_abbrev", None))
            pn = norm_name(str(getattr(r, "player", "") or ""))
            st = str(getattr(r, "status", "") or "").strip()
            if t and pn and st:
                status_by_tp[(t, pn)] = st

    # 2. Official NBA injury reports (filtered strictly by ts_utc <= cutoff_utc)
    off_files = sorted(glob.glob(str(root / "NBA/injuries/official/*/report_*.csv")))
    if not off_files:
        off_files = sorted(glob.glob(str(root / "NBA/injuries/official/report_*.csv")))
    best_off_rows: list[pd.DataFrame] = []
    for f in off_files:
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if df.empty or "report_ts_et" not in df.columns:
            continue
        ts = pd.to_datetime(df["report_ts_et"], utc=True, errors="coerce")
        df = df[ts.notna() & (ts <= cutoff_utc)].copy()
        if df.empty:
            continue
        df["_ts_utc"] = ts.loc[df.index]
        if "game_date" in df.columns:
            gd = pd.to_datetime(df["game_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            # Keep reports for target_date (or within +-1 day for UTC tip-date alignment)
            t_dt = pd.Timestamp(target_date)
            valid_days = {(t_dt + pd.Timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)}
            df = df[gd.isin(valid_days)]
        if not df.empty:
            best_off_rows.append(df)
    if best_off_rows:
        all_off = pd.concat(best_off_rows, ignore_index=True)
        latest_ts = all_off["_ts_utc"].max()
        latest_off = all_off[all_off["_ts_utc"] == latest_ts]
        for r in latest_off.itertuples(index=False):
            t = _norm_team(getattr(r, "team_code", None))
            pn = norm_name(str(getattr(r, "player", "") or ""))
            st = str(getattr(r, "status", "") or "").strip()
            if t and pn and st in {"Out", "Doubtful", "Questionable", "Probable", "Available"}:
                status_by_tp[(t, pn)] = st

    by_team: dict[str, dict[str, str]] = {}
    for (t, pn), st in status_by_tp.items():
        if st in {"Out", "Doubtful"}:
            by_team.setdefault(t, {})[pn] = st
    return by_team, status_by_tp


def _load_arm_s_at(
    data_dir: str | Path,
    target_date: str | None,
    cutoff_utc: pd.Timestamp | None,
) -> pd.DataFrame:
    """Load ``arm_s_index/predictions_export.csv`` filtered strictly by cutoff and date."""
    path = _resolve_path(
        data_dir,
        "NBA/minutes_restriction/arm_s_index/predictions_export.csv",
        "minutes_restriction/arm_s_index/predictions_export.csv",
    )
    if path is None:
        return pd.DataFrame(columns=["team", "player_norm", "game_date", "pred_ratio", "pred_minutes", "is_injury_return"])
    df = pd.read_csv(path, low_memory=False)
    if "decision_report_ts" in df.columns and cutoff_utc is not None:
        dts = pd.to_datetime(df["decision_report_ts"], utc=True, errors="coerce")
        df = df[dts.isna() | (dts <= cutoff_utc)].copy()
    df["game_date"] = df["game_date"].astype(str).str[:10]
    if target_date is not None:
        df = df[df["game_date"] <= target_date]
    df["team"] = df["team"].map(_norm_team)
    df["player_norm"] = df["player"].astype(str).map(norm_name)
    df["pred_ratio"] = pd.to_numeric(df["pred_ratio"], errors="coerce")
    df["pred_minutes"] = pd.to_numeric(df["pred_minutes"], errors="coerce")
    df["is_injury_return"] = df["is_injury_return"].astype(str).str.lower().isin({"true", "1", "1.0"})
    return df[["team", "player_norm", "game_date", "pred_ratio", "pred_minutes", "is_injury_return"]].copy()


def _load_evs_game_env(
    data_dir: str | Path,
    target_date: str,
    cutoff_utc: pd.Timestamp,
) -> dict[str, dict[str, float]]:
    """Load pre-cutoff spread & total per team from ``EVSweep/nba_games/`` if present."""
    out: dict[str, dict[str, float]] = {}
    root = Path(data_dir)
    candidates = [
        root / "EVSweep/nba_games/predictions_latest.json",
        root / f"EVSweep/nba_games/archive/{target_date}.json",
        root / f"EVSweep/nba_games/archive_{target_date}.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text())
        except Exception:
            continue
        ts_raw = payload.get("ts") if isinstance(payload, dict) else None
        if ts_raw:
            try:
                if _to_utc_ts(ts_raw) > cutoff_utc:
                    continue
            except Exception:
                pass
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        by_game: dict[str, dict[str, Any]] = {}
        for r in rows:
            comm = r.get("commence")
            if comm:
                c_day = str(comm)[:10]
                if c_day != target_date and target_date not in str(r.get("game_key", "")):
                    continue
            gk = str(r.get("game_key", ""))
            g = by_game.setdefault(gk, {"home": None, "away": None, "spread_home": np.nan, "total": np.nan})
            stat = r.get("stat")
            side = r.get("side")
            line = r.get("line")
            t_team = _norm_team(r.get("team"))
            o_team = _norm_team(r.get("opp"))
            if (not t_team or not o_team) and "@" in gk:
                matchup = gk.split(":")[-1]
                parts = matchup.split("@", 1)
                if len(parts) == 2:
                    away_k, home_k = _norm_team(parts[0]), _norm_team(parts[1])
                    g["away"] = g["away"] or away_k
                    g["home"] = g["home"] or home_k
            if stat == "spread" and side == "home" and line is not None:
                if t_team and o_team:
                    g["home"] = t_team
                    g["away"] = o_team
                g["spread_home"] = float(line)
            elif stat == "total" and line is not None:
                g["total"] = float(line)
        for g in by_game.values():
            h, a = g["home"], g["away"]
            sp_h, tot = g["spread_home"], g["total"]
            if h and a:
                if np.isfinite(sp_h):
                    out[h] = {"spread": float(sp_h), "total": float(tot)}
                    out[a] = {"spread": float(-sp_h), "total": float(tot)}
                elif np.isfinite(tot):
                    out.setdefault(h, {})["total"] = float(tot)
                    out.setdefault(a, {})["total"] = float(tot)
        if out:
            break
    return out


# --------------------------------------------------------------------------- Rolling / Prior Table Builders
def _roll_sum_prior(s: pd.Series, key: pd.Series, window: int) -> pd.Series:
    sh = s.groupby(key, sort=False).shift(1)
    return sh.groupby(key, sort=False).rolling(window, min_periods=1).sum().droplevel(0).reindex(s.index)


def _roll_mean_prior(s: pd.Series, key: pd.Series, window: int, min_periods: int = 1) -> pd.Series:
    sh = s.groupby(key, sort=False).shift(1)
    return sh.groupby(key, sort=False).rolling(window, min_periods=min_periods).mean().droplevel(0).reindex(s.index)


def _roll_std_prior(s: pd.Series, key: pd.Series, window: int, min_periods: int = 3) -> pd.Series:
    sh = s.groupby(key, sort=False).shift(1)
    return sh.groupby(key, sort=False).rolling(window, min_periods=min_periods).std().droplevel(0).reindex(s.index)


def _ewm_prior(s: pd.Series, key: pd.Series, halflife: float) -> pd.Series:
    sh = s.groupby(key, sort=False).shift(1)
    return sh.groupby(key, sort=False).ewm(halflife=halflife, min_periods=1).mean().droplevel(0).reindex(s.index)


def _assign_archetypes(
    pos_grp: pd.Series,
    stl36_eb: pd.Series,
    blk36_eb: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    arch_id = np.full(len(pos_grp), 2, dtype=int)
    g_mask = pos_grp == "G"
    b_mask = pos_grp == "B"
    arch_id[g_mask & (stl36_eb >= BALL_HAWK_STL36_MIN)] = 0
    arch_id[g_mask & (stl36_eb < BALL_HAWK_STL36_MIN)] = 1
    arch_id[b_mask & (blk36_eb >= RIM_BIG_BLK36_MIN)] = 3
    arch_id[b_mask & (blk36_eb < RIM_BIG_BLK36_MIN)] = 4
    s_id = pd.Series(arch_id, index=pos_grp.index, dtype=int)
    s_name = s_id.map(ARCHETYPE_TABLE)
    return s_id, s_name


def _compute_player_prior_series(pb: pd.DataFrame) -> pd.DataFrame:
    """Strictly-prior (shift(1)) player rolling features aligned to ``pb`` rows."""
    if pb.empty:
        return pd.DataFrame()
    df = pb.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    pid = df["player_id"]

    # Position-group prior rates computed strictly from earlier games in history
    pos_prior_stl36 = df["pos_grp"].map(lambda g: POS_DEFAULT_RATE36.get(g, (1.14, 0.58))[0]).astype(float)
    pos_prior_blk36 = df["pos_grp"].map(lambda g: POS_DEFAULT_RATE36.get(g, (1.14, 0.58))[1]).astype(float)
    prior_stl_pm = pos_prior_stl36 / 36.0
    prior_blk_pm = pos_prior_blk36 / 36.0

    mins = df["minutes"].astype(float)
    stl = df["steals"].astype(float)
    blk = df["blocks"].astype(float)
    fouls = df["fouls"].astype(float)
    starter = df["starter"].astype(float)

    out = pd.DataFrame({
        "game_id": df["game_id"],
        "date": df["date"],
        "season_year": df["season_year"],
        "player_id": df["player_id"],
        "name": df["name"],
        "player_norm": df["player_norm"],
        "position": df["position"],
        "pos_grp": df["pos_grp"],
        "team": df["team"],
        "is_home": df["is_home"].astype(int),
    })

    out["n_prior_games"] = df.groupby("player_id", sort=False).cumcount()
    out["n_prior_szn"] = df.groupby(["player_id", "season_year"], sort=False).cumcount()

    # Rolling minutes + starter rate
    out["min_ewm8"] = _ewm_prior(mins, pid, 8.0)
    for w in (5, 15, 40):
        out[f"min_l{w}"] = _roll_mean_prior(mins, pid, w, 1)
    out["min_std10"] = _roll_std_prior(mins, pid, 10, 3)
    out["starter_rate_l10"] = _roll_mean_prior(starter, pid, 10, 1)
    szn_st_sum = df.groupby(["player_id", "season_year"], sort=False)["starter"].transform(
        lambda x: x.astype(float).shift(1).cumsum()
    )
    out["starter_rate_szn"] = szn_st_sum / out["n_prior_szn"].replace(0, np.nan)

    # EB-shrunk rolling per-36 steals & blocks (L5/L15/L40/season)
    for w in (5, 15, 40):
        m_sum = _roll_sum_prior(mins, pid, w).fillna(0.0)
        s_sum = _roll_sum_prior(stl, pid, w).fillna(0.0)
        b_sum = _roll_sum_prior(blk, pid, w).fillna(0.0)
        out[f"stl_per36_l{w}"] = 36.0 * (s_sum + EB_WINDOW_PRIOR_MIN * prior_stl_pm) / (m_sum + EB_WINDOW_PRIOR_MIN)
        out[f"blk_per36_l{w}"] = 36.0 * (b_sum + EB_WINDOW_PRIOR_MIN * prior_blk_pm) / (m_sum + EB_WINDOW_PRIOR_MIN)

    szn_m = df.groupby(["player_id", "season_year"], sort=False)["minutes"].transform(lambda x: x.shift(1).cumsum()).fillna(0.0)
    szn_s = df.groupby(["player_id", "season_year"], sort=False)["steals"].transform(lambda x: x.shift(1).cumsum()).fillna(0.0)
    szn_b = df.groupby(["player_id", "season_year"], sort=False)["blocks"].transform(lambda x: x.shift(1).cumsum()).fillna(0.0)
    out["stl_per36_szn"] = 36.0 * (szn_s + EB_WINDOW_PRIOR_MIN * prior_stl_pm) / (szn_m + EB_WINDOW_PRIOR_MIN)
    out["blk_per36_szn"] = 36.0 * (szn_b + EB_WINDOW_PRIOR_MIN * prior_blk_pm) / (szn_m + EB_WINDOW_PRIOR_MIN)

    # Composite hierarchical EB per-minute rate (L15/L40 blend shrunk toward position prior)
    m40 = _roll_sum_prior(mins, pid, 40).fillna(0.0)
    s40 = _roll_sum_prior(stl, pid, 40).fillna(0.0)
    b40 = _roll_sum_prior(blk, pid, 40).fillna(0.0)
    out["stl_rate_eb"] = (s40 + EB_PRIOR_MIN * prior_stl_pm) / (m40 + EB_PRIOR_MIN)
    out["blk_rate_eb"] = (b40 + EB_PRIOR_MIN * prior_blk_pm) / (m40 + EB_PRIOR_MIN)

    # Fouls/36
    for w in (15, 40):
        m_w = _roll_sum_prior(mins, pid, w).fillna(0.0)
        f_w = _roll_sum_prior(fouls, pid, w).fillna(0.0)
        out[f"pf_per36_l{w}"] = 36.0 * (f_w + 180.0 * (2.6 / 36.0)) / (m_w + 180.0)

    # Archetype
    out["pos_group_id"] = out["pos_grp"].map(POS_GROUP_ID).fillna(1).astype(int)
    arch_id, arch_name = _assign_archetypes(out["pos_grp"], out["stl_rate_eb"] * 36.0, out["blk_rate_eb"] * 36.0)
    out["archetype_id"] = arch_id
    out["archetype_name"] = arch_name

    # Rest days from previous played game
    prev_date = df.groupby("player_id", sort=False)["date"].shift(1)
    d_cur = pd.to_datetime(df["date"])
    d_prv = pd.to_datetime(prev_date)
    rest = (d_cur - d_prv).dt.days - 1
    out["rest_days"] = rest.clip(lower=0, upper=14).fillna(3.0)
    out["is_b2b"] = (out["rest_days"] == 0).astype(int)

    # Base EWM minutes for backbone
    out["mu_min_base"] = np.where(
        out["n_prior_games"] > 0,
        (out["n_prior_games"].clip(upper=20) * out["min_ewm8"].fillna(16.0) + 4.0 * np.where(out["starter_rate_l10"].fillna(0.0) >= 0.5, 26.0, 16.0))
        / (out["n_prior_games"].clip(upper=20) + 4.0),
        16.0,
    )
    return out


def _compute_player_post_table(pb: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Summarize strictly-prior boxscores (``date < target_date``) into 1 row per active player."""
    if pb.empty:
        return pd.DataFrame()
    df = pb.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    # Append a dummy future query row per player at target_date and reuse the exact shift(1) pipeline
    last = df.groupby("player_id", sort=False).tail(1).copy()
    last["game_id"] = "__query__"
    last["date"] = target_date
    last["season_year"] = _season_year_of_date(target_date)
    for c in ("minutes", "steals", "blocks", "fouls", "starter"):
        last[c] = 0.0
    combined = pd.concat([df, last], ignore_index=True)
    series = _compute_player_prior_series(combined)
    q = series[series["game_id"] == "__query__"].copy()
    return q.reset_index(drop=True)


def _compute_team_post_series(
    pb: pd.DataFrame,
    games: pd.DataFrame,
    pbp_tg: pd.DataFrame,
    shots_tg: pd.DataFrame,
) -> pd.DataFrame:
    """Strictly-prior per-team rolling environment series (pace, TOV, live-ball share, rim/paint)."""
    if pb.empty:
        return pd.DataFrame()
    # Team totals summed from strictly-prior player boxscores
    t_box = (
        pb.groupby(["game_id", "date", "team"], sort=False)
        .agg(
            t_fga=("field_goals_attempted", "sum"),
            t_fta=("free_throws_attempted", "sum"),
            t_oreb=("rebounds_offensive", "sum"),
            t_tov=("turnovers", "sum"),
            t_pts=("points", "sum"),
            t_min=("minutes", "sum"),
            t_stl=("steals", "sum"),
            t_blk=("blocks", "sum"),
        )
        .reset_index()
    )
    t_box["poss"] = np.maximum(t_box["t_fga"] + 0.44 * t_box["t_fta"] - t_box["t_oreb"] + t_box["t_tov"], 50.0)
    t_box["pace"] = t_box["poss"] * (240.0 / np.maximum(t_box["t_min"], 200.0))

    # Attach opponent from games_master (strictly date < max_date) or self-join on game_id
    if not games.empty:
        g_home = games[["game_id", "home", "away"]].rename(columns={"home": "team", "away": "opp"})
        g_away = games[["game_id", "away", "home"]].rename(columns={"away": "team", "home": "opp"})
        g_map = pd.concat([g_home, g_away], ignore_index=True).drop_duplicates(["game_id", "team"])
        t_box = t_box.merge(g_map, on=["game_id", "team"], how="left")
    else:
        opp_lookup = t_box[["game_id", "team"]].rename(columns={"team": "opp"})
        t_box = t_box.merge(opp_lookup, on="game_id", how="left")
        t_box = t_box[t_box["team"] != t_box["opp"]].drop_duplicates(["game_id", "team"])

    # Opponent defensive counts allowed (steals / blocks allowed by team = opponent's steals / blocks in game)
    opp_stats = t_box[["game_id", "team", "t_stl", "t_blk", "t_fga"]].rename(
        columns={"team": "opp", "t_stl": "opp_stl_in_game", "t_blk": "opp_blk_in_game", "t_fga": "opp_fga_in_game"}
    )
    t_box = t_box.merge(opp_stats, on=["game_id", "opp"], how="left")

    if not pbp_tg.empty:
        t_box = t_box.merge(pbp_tg, on=["date", "team", "opp"], how="left")
    else:
        t_box["tov_pbp"] = np.nan
        t_box["live_tov"] = np.nan
        t_box["stl_pbp"] = np.nan
        t_box["pbp_spread"] = np.nan

    if not shots_tg.empty:
        t_box = t_box.merge(shots_tg, on=["date", "team"], how="left")
    else:
        t_box["shot_fga"] = np.nan
        t_box["rim_fga"] = np.nan
        t_box["paint_fga"] = np.nan

    t_box = t_box.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
    tk = t_box["team"]

    tov_num = t_box["tov_pbp"].where(t_box["tov_pbp"].notna() & (t_box["tov_pbp"] > 0), t_box["t_tov"])
    t_box["tov_rate"] = 100.0 * tov_num / t_box["poss"]
    t_box["live_tov_share"] = np.where(
        t_box["tov_pbp"] > 0,
        t_box["live_tov"] / t_box["tov_pbp"],
        np.nan,
    )
    t_box["stl_allowed_100"] = 100.0 * t_box["opp_stl_in_game"].fillna(t_box["stl_pbp"]) / t_box["poss"]
    t_box["blk_allowed_100"] = 100.0 * t_box["opp_blk_in_game"] / np.maximum(t_box["t_fga"], 1.0)
    t_box["rim_share"] = np.where(t_box["shot_fga"] > 0, t_box["rim_fga"] / t_box["shot_fga"], np.nan)
    t_box["paint_share"] = np.where(t_box["shot_fga"] > 0, t_box["paint_fga"] / t_box["shot_fga"], np.nan)

    # Strictly prior rolling (shift(1)) for each team
    out = t_box[["game_id", "date", "team", "opp", "pbp_spread"]].copy()
    out["team_pace_l15"] = _roll_mean_prior(t_box["pace"], tk, 15, 1)
    out["team_pts_l15"] = _roll_mean_prior(t_box["t_pts"], tk, 15, 1)
    out["team_tov_rate_l15"] = _roll_mean_prior(t_box["tov_rate"], tk, 15, 1)
    out["team_live_tov_share_l15"] = _roll_mean_prior(t_box["live_tov_share"], tk, 15, 1)
    out["team_stl_allowed_l15"] = _roll_mean_prior(t_box["stl_allowed_100"], tk, 15, 1)
    out["team_blk_allowed_l15"] = _roll_mean_prior(t_box["blk_allowed_100"], tk, 15, 1)
    out["team_rim_share_l15"] = _roll_mean_prior(t_box["rim_share"], tk, 15, 1)
    out["team_paint_share_l15"] = _roll_mean_prior(t_box["paint_share"], tk, 15, 1)

    # Post-game inclusive values for single-date target query (only called on date < target_date data!)
    out["post_pace_l15"] = t_box.groupby("team", sort=False)["pace"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_pts_l15"] = t_box.groupby("team", sort=False)["t_pts"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_tov_rate_l15"] = t_box.groupby("team", sort=False)["tov_rate"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_live_tov_share_l15"] = t_box.groupby("team", sort=False)["live_tov_share"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_stl_allowed_l15"] = t_box.groupby("team", sort=False)["stl_allowed_100"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_blk_allowed_l15"] = t_box.groupby("team", sort=False)["blk_allowed_100"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_rim_share_l15"] = t_box.groupby("team", sort=False)["rim_share"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    out["post_paint_share_l15"] = t_box.groupby("team", sort=False)["paint_share"].transform(lambda s: s.rolling(15, min_periods=1).mean())
    return out


# --------------------------------------------------------------------------- Public Pregame Feature Builder
def build_pregame_features(
    target_date: str | pd.Timestamp,
    cutoff_utc: str | pd.Timestamp,
    data_dir: str | Path,
    slate: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """PURE U1 pregame feature builder for ``target_date`` at ``cutoff_utc``.

    Physically loads ONLY box-score / PBP / shot / game-history rows with ``date < target_date``
    and injury / arm_s_index / EVSweep snapshots timestamped ``<= cutoff_utc``.
    """
    t_date = _date_str(target_date)
    c_utc = _to_utc_ts(cutoff_utc)

    prior_pb = _load_player_box_before(data_dir, t_date)
    prior_games = _load_games_before(data_dir, t_date)
    prior_pbp = _load_pbp_team_game_before(data_dir, t_date)
    prior_shots = _load_shots_team_game_before(data_dir, t_date)

    # Build strictly prior player summary (1 row per player seen before target_date)
    p_hist = _compute_player_post_table(prior_pb, t_date)
    # Build strictly prior team summary (1 row per team seen before target_date)
    t_series = _compute_team_post_series(prior_pb, prior_games, prior_pbp, prior_shots)
    t_latest = t_series.sort_values(["team", "date", "game_id"]).groupby("team", sort=False).tail(1).set_index("team") if not t_series.empty else pd.DataFrame()

    # Determine target slate rows
    if slate is not None and not slate.empty:
        q = slate.copy()
        q["team"] = q["team"].map(_norm_team)
        q["opp"] = q["opp"].map(_norm_team) if "opp" in q.columns else None
        if "player_norm" not in q.columns:
            name_src = q["name"] if "name" in q.columns else q["player"]
            q["player_norm"] = name_src.astype(str).map(norm_name)
        q["date"] = t_date
        q["season_year"] = _season_year_of_date(t_date)
        if "game_id" not in q.columns:
            q["game_id"] = q.get("game_key", f"slate:{t_date}")
    else:
        sched = _load_schedule_on_date(data_dir, t_date)
        evs_env = _load_evs_game_env(data_dir, t_date, c_utc)
        if not sched.empty and not p_hist.empty:
            home_df = sched[["game_id", "season_year", "date", "home", "away"]].rename(columns={"home": "team", "away": "opp"}).assign(is_home=1)
            away_df = sched[["game_id", "season_year", "date", "away", "home"]].rename(columns={"away": "team", "home": "opp"}).assign(is_home=0)
            tg = pd.concat([home_df, away_df], ignore_index=True)
            # Active players on each scheduled team from strictly prior boxscores (last 45 days)
            cutoff_recent = (pd.Timestamp(t_date) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
            recent_ids = set(prior_pb.loc[prior_pb["date"] >= cutoff_recent, "player_id"]) if not prior_pb.empty else set()
            act = p_hist[p_hist["player_id"].isin(recent_ids)] if recent_ids else p_hist
            q = tg.merge(act[["player_id", "name", "player_norm", "position", "team"]], on="team", how="inner")
        elif not p_hist.empty:
            cutoff_recent = (pd.Timestamp(t_date) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
            act = p_hist[p_hist["player_id"].isin(set(prior_pb.loc[prior_pb["date"] >= cutoff_recent, "player_id"]))]
            if act.empty:
                act = p_hist
            q = act[["player_id", "name", "player_norm", "position", "team"]].copy()
            q["game_id"] = f"slate:{t_date}"
            q["date"] = t_date
            q["season_year"] = _season_year_of_date(t_date)
            q["opp"] = None
            q["is_home"] = 0
        else:
            return pd.DataFrame(columns=ID_COLUMNS + FEATURE_COLUMNS)

        for team_val, env in evs_env.items():
            m = q["team"] == team_val
            if "spread" in env:
                q.loc[m, "spread"] = env["spread"]
            if "total" in env:
                q.loc[m, "total"] = env["total"]

    if q.empty:
        return pd.DataFrame(columns=ID_COLUMNS + FEATURE_COLUMNS)

    # Join player prior features: match by player_id if present in p_hist, else by (team, player_norm) -> player_norm
    p_cols = [
        "player_id", "name", "player_norm", "position", "pos_grp", "team",
        "stl_per36_l5", "stl_per36_l15", "stl_per36_l40", "stl_per36_szn", "stl_rate_eb",
        "blk_per36_l5", "blk_per36_l15", "blk_per36_l40", "blk_per36_szn", "blk_rate_eb",
        "min_ewm8", "min_l5", "min_l15", "min_l40", "min_std10",
        "starter_rate_l10", "starter_rate_szn", "n_prior_games", "n_prior_szn",
        "pf_per36_l15", "pf_per36_l40", "archetype_id", "pos_group_id", "archetype_name",
        "rest_days", "is_b2b", "mu_min_base",
    ]
    if not p_hist.empty:
        if "player_id" in q.columns and q["player_id"].astype(str).isin(set(p_hist["player_id"])).any():
            q["player_id"] = q["player_id"].astype(str)
            ph = p_hist[p_cols].drop_duplicates("player_id")
            q = q.merge(ph, on="player_id", how="left", suffixes=("", "_hist"))
        else:
            ph_tp = p_hist.sort_values("n_prior_games").drop_duplicates(["team", "player_norm"], keep="last")
            q = q.merge(ph_tp[p_cols], on=["team", "player_norm"], how="left", suffixes=("", "_hist"))
            # Fallback for newly traded players matching by unique player_norm
            miss = q["player_id"].isna() if "player_id" in q.columns else q["stl_rate_eb"].isna()
            if miss.any():
                ph_n = p_hist.sort_values("n_prior_games").drop_duplicates("player_norm", keep="last")
                fill = q.loc[miss, ["player_norm"]].merge(ph_n[p_cols], on="player_norm", how="left")
                for c in p_cols:
                    if c != "player_norm" and c in fill.columns:
                        q.loc[miss, c] = q.loc[miss, c].where(q.loc[miss, c].notna(), fill[c].values)
        for c in ("name", "position", "team"):
            if f"{c}_hist" in q.columns:
                q[c] = q[c].where(q[c].notna(), q[f"{c}_hist"])
                q = q.drop(columns=[f"{c}_hist"])
    else:
        for c in p_cols:
            if c not in q.columns:
                q[c] = np.nan

    # Defaults for rookies / players with no prior history
    q["player_id"] = q["player_id"].fillna(q["player_norm"]).astype(str)
    q["name"] = q.get("name", q.get("player", q["player_norm"])).fillna(q["player_norm"])
    q["position"] = q["position"].fillna("SF")
    q["pos_grp"] = q["position"].map(POS_GROUP).fillna("W")
    q["pos_group_id"] = q["pos_grp"].map(POS_GROUP_ID).fillna(1).astype(int)
    for g, (s36, b36) in POS_DEFAULT_RATE36.items():
        gm = (q["pos_grp"] == g) & q["stl_rate_eb"].isna()
        q.loc[gm, "stl_rate_eb"] = s36 / 36.0
        q.loc[gm, "blk_rate_eb"] = b36 / 36.0
        for w in ("l5", "l15", "l40", "szn"):
            q.loc[gm, f"stl_per36_{w}"] = q.loc[gm, f"stl_per36_{w}"].fillna(s36)
            q.loc[gm, f"blk_per36_{w}"] = q.loc[gm, f"blk_per36_{w}"].fillna(b36)
    q["n_prior_games"] = q["n_prior_games"].fillna(0).astype(int)
    q["n_prior_szn"] = q["n_prior_szn"].fillna(0).astype(int)
    q["min_ewm8"] = q["min_ewm8"].fillna(16.0)
    for w in ("l5", "l15", "l40"):
        q[f"min_{w}"] = q[f"min_{w}"].fillna(q["min_ewm8"])
    q["min_std10"] = q["min_std10"].fillna(4.5)
    q["starter_rate_l10"] = q["starter_rate_l10"].fillna(0.0)
    q["starter_rate_szn"] = q["starter_rate_szn"].fillna(0.0)
    q["pf_per36_l15"] = q["pf_per36_l15"].fillna(2.6)
    q["pf_per36_l40"] = q["pf_per36_l40"].fillna(2.6)
    q["rest_days"] = q["rest_days"].fillna(3.0)
    q["is_b2b"] = q["is_b2b"].fillna(0).astype(int)
    q["is_home"] = q.get("is_home", pd.Series(0, index=q.index)).fillna(0).astype(int)
    q["mu_min_base"] = q["mu_min_base"].fillna(16.0)

    arch_id, arch_name = _assign_archetypes(q["pos_grp"], q["stl_rate_eb"] * 36.0, q["blk_rate_eb"] * 36.0)
    q["archetype_id"] = arch_id
    q["archetype_name"] = arch_name

    # Attach opponent & team environment from strictly prior t_latest
    def _t_lookup(col_series: pd.Series, field: str, default: float) -> np.ndarray:
        if t_latest.empty or field not in t_latest.columns:
            return np.full(len(col_series), default, dtype=float)
        return col_series.map(t_latest[field]).fillna(default).to_numpy(dtype=float)

    q["team_pace_l15"] = _t_lookup(q["team"], "post_pace_l15", DEFAULT_LEAGUE_PACE)
    q["opp_pace_l15"] = _t_lookup(q["opp"], "post_pace_l15", DEFAULT_LEAGUE_PACE)
    q["env_pace_est"] = 0.5 * (q["team_pace_l15"] + q["opp_pace_l15"])
    q["opp_tov_rate_l15"] = _t_lookup(q["opp"], "post_tov_rate_l15", 13.5)
    q["opp_live_tov_share_l15"] = _t_lookup(q["opp"], "post_live_tov_share_l15", np.nan)
    q["opp_stl_allowed_l15"] = _t_lookup(q["opp"], "post_stl_allowed_l15", 7.5)
    q["opp_blk_allowed_l15"] = _t_lookup(q["opp"], "post_blk_allowed_l15", 5.5)
    q["opp_rim_share_l15"] = _t_lookup(q["opp"], "post_rim_share_l15", 0.30)
    q["opp_paint_share_l15"] = _t_lookup(q["opp"], "post_paint_share_l15", 0.48)

    # Spread, total, implied team total (use caller/EVSweep if present, else strictly prior team pts estimate)
    if "spread" not in q.columns:
        q["spread"] = np.nan
    q["spread"] = pd.to_numeric(q["spread"], errors="coerce").fillna(0.0)
    q["abs_spread"] = q["spread"].abs()
    if "total" not in q.columns:
        q["total"] = np.nan
    q["total"] = pd.to_numeric(q["total"], errors="coerce")
    est_tot = _t_lookup(q["team"], "post_pts_l15", 112.0) + _t_lookup(q["opp"], "post_pts_l15", 112.0)
    q["total"] = q["total"].fillna(pd.Series(est_tot, index=q.index)).fillna(DEFAULT_LEAGUE_TOTAL)
    q["implied_team_total"] = 0.5 * q["total"] - 0.5 * q["spread"]

    # Injuries & arm_s_index strictly <= cutoff_utc
    raw_abs_tp, raw_status_tp = _load_injuries_at(data_dir, t_date, c_utc)
    # Map (team, player_norm) -> player_id on prior roster so backbone matches exact player_id keys
    tp_to_pid: dict[tuple[str, str], str] = {}
    if not p_hist.empty:
        for r in p_hist.itertuples(index=False):
            tp_to_pid[(str(r.team), str(r.player_norm))] = str(r.player_id)
    for r in q.itertuples(index=False):
        tp_to_pid.setdefault((str(r.team), str(r.player_norm)), str(r.player_id))

    abs_by_team: dict[str, dict[str, str]] = {}
    for team, pmap in raw_abs_tp.items():
        for pn, st in pmap.items():
            pid_key = tp_to_pid.get((team, pn), pn)
            abs_by_team.setdefault(team, {})[pid_key] = st
    status_by_player: dict[tuple[str, str], str] = {}
    for (team, pn), st in raw_status_tp.items():
        status_by_player[(team, pn)] = st
        if (team, pn) in tp_to_pid:
            status_by_player[(team, tp_to_pid[(team, pn)])] = st

    arm_df = _load_arm_s_at(data_dir, t_date, c_utc)
    arm_caps: dict[tuple[str, str], dict[str, Any]] = {}
    if not arm_df.empty:
        # Active cap on target_date (or most recent injury return within 3 days)
        t_ts = pd.Timestamp(t_date)
        recent_arm = arm_df[pd.to_datetime(arm_df["game_date"]) >= (t_ts - pd.Timedelta(days=2))]
        for r in recent_arm.sort_values("game_date").itertuples(index=False):
            arm_caps[(str(r.team), str(r.player_norm))] = {
                "cap_ratio": float(r.pred_ratio) if np.isfinite(r.pred_ratio) else 1.0,
                "cap_minutes": float(r.pred_minutes) if np.isfinite(r.pred_minutes) else None,
                "is_injury_return": bool(r.is_injury_return),
            }

    backbone_input = q[["player_id", "player_norm", "team", "spread", "rest_days", "mu_min_base"]].copy()
    backbone_input["pf_per36"] = q["pf_per36_l15"]
    bb = compute_minutes_backbone(
        prior_pb,
        backbone_input,
        absences_by_team=abs_by_team,
        status_by_player=status_by_player,
        arm_s_caps=arm_caps,
        target_date=t_date,
    )
    for col in MIN_BACKBONE_COLS:
        q[col] = bb[col].values

    ordered = [c for c in ID_COLUMNS if c in q.columns] + FEATURE_COLUMNS
    out = q[ordered].reset_index(drop=True)
    assert_feature_schema(out)
    return out


# --------------------------------------------------------------------------- Vectorized Training Feature Builder
def build_training_features(
    data_dir: str | Path,
    min_date: str | None = None,
    max_date: str | None = None,
) -> pd.DataFrame:
    """Build strictly-prior training feature matrix across historical games (no labels attached).

    Uses ``shift(1)`` within player and team histories so row ``(player, date)`` sees ONLY
    games with ``date < row.date``. Runs ``assert_feature_schema`` before returning.
    """
    pb = _load_player_box_before(data_dir, max_date)
    if pb.empty:
        return pd.DataFrame(columns=ID_COLUMNS + FEATURE_COLUMNS)
    min_yr = (int(min_date[:4]) - 1) if min_date else None
    games = _load_games_before(data_dir, max_date)
    pbp_tg = _load_pbp_team_game_before(data_dir, max_date, min_year=min_yr)
    shots_tg = _load_shots_team_game_before(data_dir, max_date, min_year=min_yr)

    p_series = _compute_player_prior_series(pb)
    t_series = _compute_team_post_series(pb, games, pbp_tg, shots_tg)

    # Attach opp onto player rows via (game_id, team)
    t_self = t_series[[
        "game_id", "team", "opp", "pbp_spread", "team_pace_l15", "team_pts_l15",
    ]].rename(columns={"team_pts_l15": "self_pts_l15"})
    df = p_series.merge(t_self, on=["game_id", "team"], how="left")

    # Opponent strictly-prior stats (shift(1) on opp's own timeline!)
    opp_cols = t_series[[
        "game_id", "team", "team_pace_l15", "team_pts_l15",
        "team_tov_rate_l15", "team_live_tov_share_l15",
        "team_stl_allowed_l15", "team_blk_allowed_l15",
        "team_rim_share_l15", "team_paint_share_l15",
    ]].rename(columns={
        "team": "opp",
        "team_pace_l15": "opp_pace_l15",
        "team_pts_l15": "opp_pts_l15",
        "team_tov_rate_l15": "opp_tov_rate_l15",
        "team_live_tov_share_l15": "opp_live_tov_share_l15",
        "team_stl_allowed_l15": "opp_stl_allowed_l15",
        "team_blk_allowed_l15": "opp_blk_allowed_l15",
        "team_rim_share_l15": "opp_rim_share_l15",
        "team_paint_share_l15": "opp_paint_share_l15",
    })
    df = df.merge(opp_cols, on=["game_id", "opp"], how="left")

    df["team_pace_l15"] = df["team_pace_l15"].fillna(DEFAULT_LEAGUE_PACE)
    df["opp_pace_l15"] = df["opp_pace_l15"].fillna(DEFAULT_LEAGUE_PACE)
    df["env_pace_est"] = 0.5 * (df["team_pace_l15"] + df["opp_pace_l15"])
    df["opp_tov_rate_l15"] = df["opp_tov_rate_l15"].fillna(13.5)
    df["opp_stl_allowed_l15"] = df["opp_stl_allowed_l15"].fillna(7.5)
    df["opp_blk_allowed_l15"] = df["opp_blk_allowed_l15"].fillna(5.5)
    df["opp_rim_share_l15"] = df["opp_rim_share_l15"].fillna(0.30)
    df["opp_paint_share_l15"] = df["opp_paint_share_l15"].fillna(0.48)
    df["spread"] = pd.to_numeric(df["pbp_spread"], errors="coerce").fillna(0.0)
    df["abs_spread"] = df["spread"].abs()
    df["total"] = (df["self_pts_l15"].fillna(112.0) + df["opp_pts_l15"].fillna(112.0)).fillna(DEFAULT_LEAGUE_TOTAL)
    df["implied_team_total"] = 0.5 * df["total"] - 0.5 * df["spread"]

    if min_date is not None:
        df = df[df["date"] >= min_date].reset_index(drop=True)

    # Join historical pre-tip arm_s_index caps (if available)
    arm_df = _load_arm_s_at(data_dir, max_date, None)
    if not arm_df.empty:
        arm_dedup = arm_df.sort_values("game_date").drop_duplicates(["team", "player_norm", "game_date"], keep="last")
        df = df.merge(
            arm_dedup.rename(columns={"game_date": "date"}),
            on=["team", "player_norm", "date"],
            how="left",
        )
    else:
        df["pred_ratio"] = np.nan
        df["pred_minutes"] = np.nan
        df["is_injury_return"] = False

    # Vectorized Stage 1 minutes distribution (exact math of nba.props.hierarchical.project_minutes_distribution)
    from scipy.special import ndtr
    from nba.props.hierarchical import (
        B2B_MIN_MPG, B2B_TRIM, BASE_MIN_CV, BLOWOUT_BENCH_GAIN, BLOWOUT_CENTER,
        BLOWOUT_MIN_CV, BLOWOUT_TRIM, FOUL_SHIFT, FOUL_WEIGHT, MAX_MEAN_MIN,
        OT_MINUTES, OT_SPREAD_DECAY, OT_SPREAD_REF, OT_WEIGHT_CLIP, OT_WEIGHT_REF,
        PF36_REF, STARTER_BLOWOUT_CAP, STARTER_MIN, TRIM_SCALE, WEIGHT_SCALE,
    )

    mu0_arr = df["mu_min_base"].to_numpy(dtype=float)
    s_arr = np.abs(df["spread"].to_numpy(dtype=float))
    rest_arr = df["rest_days"].to_numpy(dtype=float)
    pf_arr = df["pf_per36_l15"].to_numpy(dtype=float)
    pr_arr = pd.to_numeric(df["pred_ratio"], errors="coerce").to_numpy(dtype=float)
    pm_arr = pd.to_numeric(df["pred_minutes"], errors="coerce").to_numpy(dtype=float)
    ir_arr = df["is_injury_return"].eq(True).to_numpy(dtype=bool)

    starter_mask = mu0_arr >= STARTER_MIN
    delta = np.where(starter_mask, BLOWOUT_TRIM * ndtr((s_arr - BLOWOUT_CENTER) / TRIM_SCALE), 0.0)
    delta += np.where((rest_arr == 0) & (mu0_arr >= B2B_MIN_MPG), B2B_TRIM, 0.0)
    mu_mins = np.clip(mu0_arr + delta, 0.0, MAX_MEAN_MIN)

    w_blow = ndtr((s_arr - BLOWOUT_CENTER) / WEIGHT_SCALE)
    pf_scale = np.clip(np.where(np.isfinite(pf_arr), pf_arr / PF36_REF, 1.0), 0.6, 1.8)
    w_foul = FOUL_WEIGHT * pf_scale
    w_ot = np.clip(OT_WEIGHT_REF * np.exp(-(s_arr - OT_SPREAD_REF) / OT_SPREAD_DECAY), OT_WEIGHT_CLIP[0], OT_WEIGHT_CLIP[1])
    w_reg = np.maximum(1.0 - w_blow - w_foul - w_ot, 0.2)
    tot_w = w_reg + w_blow + w_foul + w_ot
    w_reg, w_blow, w_foul, w_ot = w_reg / tot_w, w_blow / tot_w, w_foul / tot_w, w_ot / tot_w

    o_blow = np.where(
        starter_mask,
        -np.maximum(mu_mins - STARTER_BLOWOUT_CAP, 4.0),
        np.minimum(BLOWOUT_BENCH_GAIN, np.maximum(MAX_MEAN_MIN - mu_mins, 0.0)),
    )
    o_foul = -np.minimum(mu_mins, -FOUL_SHIFT)
    o_ot = OT_MINUTES * np.minimum(mu_mins / 36.0, 1.0)
    centre = w_blow * o_blow + w_foul * o_foul + w_ot * o_ot
    var_m = (
        w_reg * (mu_mins * BASE_MIN_CV) ** 2
        + w_blow * (mu_mins * BLOWOUT_MIN_CV) ** 2
        + w_foul * (mu_mins * BASE_MIN_CV) ** 2
        + w_ot * (mu_mins * BASE_MIN_CV) ** 2
        + w_reg * (0.0 - centre) ** 2
        + w_blow * (o_blow - centre) ** 2
        + w_foul * (o_foul - centre) ** 2
        + w_ot * (o_ot - centre) ** 2
    )
    sd_mins = np.sqrt(np.maximum(var_m, 0.0))

    cap_mask = np.isfinite(pr_arr) & (pr_arr < 0.98)
    cap_flags = cap_mask.astype(int)
    cap_ratios = np.where(cap_mask, np.clip(pr_arr, 0.2, 1.0), 1.0)
    cap_targets = np.where(np.isfinite(pm_arr), pm_arr, mu0_arr * cap_ratios)
    mu_mins = np.where(cap_mask, np.minimum(mu_mins, np.maximum(cap_targets, 0.0)), mu_mins)
    sd_mins = np.where(cap_mask, sd_mins * 0.75, sd_mins)

    df["mu_min"] = mu_mins
    df["sigma_min"] = sd_mins
    df["vacated_min_team"] = 0.0
    df["absorbed_min_delta"] = 0.0
    df["teammate_out_count"] = 0
    df["is_player_out"] = 0
    df["is_player_doubtful"] = 0
    df["is_player_questionable"] = 0
    df["min_cap_flag"] = cap_flags
    df["min_cap_ratio"] = cap_ratios
    df["is_injury_return_flag"] = ir_arr.astype(int)

    ordered = [c for c in ID_COLUMNS if c in df.columns] + FEATURE_COLUMNS
    out = df[ordered].reset_index(drop=True)
    assert_feature_schema(out)
    return out
