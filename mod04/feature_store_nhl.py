"""MOD-04 Cluster (a) U1 Feature Store & Label Store for NHL blocked shots.

LEAKAGE & TIME-TRAVEL CONTRACT:
  * ``build_pregame_features_nhl(target_date, cutoff_utc, data_dir, slate=None)``
    is a PURE function that physically filters every historical source at load
    time to ``date < target_date`` (ET game-date convention matching
    ``NHL/games_master.csv`` and ``NHL/skater_boxscores_master.csv``) and every
    live/lineup/odds snapshot to ``ts_utc <= cutoff_utc``.
  * Deleting ALL box-score, game, and shot rows on or after ``target_date``
    produces byte-identical feature output from ``build_pregame_features_nhl``.
  * ``load_labels_nhl(data_dir)`` is a separate module-level Label Store function
    used ONLY by training/grading code paths; feature-building functions never
    import or call it.
  * Reuses ``assert_feature_schema`` from ``mod04.feature_store`` with an
    NHL-specific label denylist.

TRAINING vs SCORE-TIME BOUNDARY:
  * Training rows (``build_training_features_nhl``) use masters + ``toi_splits``
    + ``shots_20*.csv`` ONLY (no Rotowire files).
  * Score-time rows (``build_pregame_features_nhl``) additionally consume
    pre-cutoff ``NHL/lineups/rotowire/<date>.json`` and ``EVSweep/nhl_games/``.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from mlb_core.data.player_names import norm_name
from mod04.backbone_nhl import (
    LG_PK_SHARE,
    TOI_BACKBONE_COLS,
    compute_prior_toi_stats,
    compute_toi_backbone,
    pos_group_nhl,
)
from mod04.feature_store import assert_feature_schema
from nhl.config import team_abbrev
from nhl.props.xgfeat import prior_roll

# --------------------------------------------------------------------------- constants
NHL_LABEL_DENYLIST: frozenset[str] = frozenset({
    "blocked_shots",
    "sog",
    "goals",
    "assists",
    "points",
    "hits",
    "plus_minus",
    "pim",
    "shifts",
    "toi_sec",
    "toi_total",
    "toi_ev",
    "toi_pp",
    "toi_pk",
    "toi_exp_min",
    "played",
    "giveaways",
    "takeaways",
    "power_play_goals",
    "power_play_assists",
    "power_play_points",
    "away_score",
    "home_score",
})

NHL_ARCHETYPE_TABLE: dict[int, str] = {
    0: "shot_blocking_d",
    1: "two_way_d",
    2: "pk_blocking_f",
    3: "depth_scoring_f",
}
POS_DEFAULT_BLK60: dict[str, float] = {
    "D": 4.75,
    "F": 2.30,
}
POS_DEFAULT_EXP_FRAC: dict[str, float] = {
    "D": 0.954,
    "F": 0.897,
}
SHOT_BLK_D_MIN_RATE60 = 4.75
PK_F_MIN_RATE60 = 2.50
PK_F_MIN_PK_TOI = 1.20

EB_BLK_PRIOR_MIN = 240.0
EB_WINDOW_PRIOR_MIN = 120.0
POINT_SHOT_MIN_DIST_FT = 42.0
DEFAULT_LEAGUE_TOTAL = 6.1
DEFAULT_TEAM_ATT_FOR = 58.0
DEFAULT_POINT_SHARE = 0.27
DEFAULT_SHOT_BLK_RATE = 0.25

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
    "blk_per60_l5",
    "blk_per60_l15",
    "blk_per60_l40",
    "blk_per60_szn",
    "blk_rate_eb",
    "toi_l5",
    "toi_l15",
    "toi_ev_l10",
    "toi_pk_l10",
    "n_prior_games",
    "n_prior_szn",
    "is_d",
    "archetype_id",
    "opp_att_for_l15",
    "opp_point_share_l15",
    "opp_shot_blk_rate_l15",
    "opp_pp_share_l15",
    "team_att_against_l15",
    "team_pk_share_l15",
    "game_total",
    "opp_implied_total",
    "opp_ml_win_prob",
    "rest_days",
    "is_b2b",
    "is_home",
    *TOI_BACKBONE_COLS,
]

_TEAM_ALIASES = {
    "LA": "LAK",
    "NJ": "NJD",
    "SJ": "SJS",
    "TB": "TBL",
    "UTAH": "UTA",
    "PHX": "ARI",
}


def _norm_team(abbr: Any) -> str | None:
    if abbr is None or (isinstance(abbr, float) and np.isnan(abbr)):
        return None
    s = str(abbr).strip()
    if not s:
        return None
    up = s.upper()
    if up in _TEAM_ALIASES:
        return _TEAM_ALIASES[up]
    return team_abbrev(s) or up


def _norm_team_series(s: pd.Series) -> pd.Series:
    uniq = s.dropna().unique()
    mapping = {u: _norm_team(u) for u in uniq}
    return s.map(mapping)


def _norm_name_series(s: pd.Series) -> pd.Series:
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


def _season_year_of_date(d: str) -> int:
    ts = pd.Timestamp(d)
    return int(ts.year if ts.month >= 8 else ts.year - 1)


def _resolve_path(data_dir: str | Path, *candidates: str) -> Path | None:
    root = Path(data_dir)
    for rel in candidates:
        p = root / rel
        if p.exists():
            return p
    return None


def assert_nhl_feature_schema(df: pd.DataFrame, denylist: Iterable[str] | None = None) -> None:
    """Hard schema gate reusing ``mod04.feature_store.assert_feature_schema`` with NHL labels."""
    deny = set(NHL_LABEL_DENYLIST)
    if denylist:
        deny |= {str(c) for c in denylist}
    assert_feature_schema(df, denylist=deny)


# --------------------------------------------------------------------------- Label Store (training/grading ONLY)
def load_labels_nhl(data_dir: str | Path, include_preseason: bool = False) -> pd.DataFrame:
    """Separate NHL Label Store used ONLY by training/grading code paths.

    Feature builders must NEVER import or call this function.
    """
    path = _resolve_path(data_dir, "NHL/skater_boxscores_master.csv", "skater_boxscores_master.csv")
    if path is None:
        raise FileNotFoundError(f"skater_boxscores_master.csv not found under {data_dir}")
    usecols = [
        "game_id", "season_year", "game_type", "date", "team_abbr", "is_home",
        "player_id", "name", "position", "toi_sec", "played", "blocked_shots",
    ]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    if not include_preseason:
        df = df[df["game_type"].isin([2, 3])]
    df["toi_sec"] = pd.to_numeric(df["toi_sec"], errors="coerce")
    played_bool = df["played"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df = df[played_bool & (df["toi_sec"].fillna(0.0) > 0.0)].copy()
    df["date"] = df["date"].astype(str).str[:10]
    df["team"] = _norm_team_series(df["team_abbr"])
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce").astype("int64")
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("int64")
    df["blocked_shots"] = pd.to_numeric(df["blocked_shots"], errors="coerce").fillna(0).astype(int)
    df["played"] = True

    sp_path = _resolve_path(data_dir, "NHL/toi_splits/toi_splits.csv", "toi_splits/toi_splits.csv")
    if sp_path is not None:
        sp = pd.read_csv(sp_path, usecols=["game_id", "player_id", "toi_ev", "toi_pk"], low_memory=False)
        sp["game_id"] = pd.to_numeric(sp["game_id"], errors="coerce").astype("Int64")
        sp["player_id"] = pd.to_numeric(sp["player_id"], errors="coerce").astype("Int64")
        sp = sp.dropna(subset=["game_id", "player_id"]).drop_duplicates(["game_id", "player_id"])
        df = df.merge(sp, on=["game_id", "player_id"], how="left")
    else:
        df["toi_ev"] = np.nan
        df["toi_pk"] = np.nan

    pos_grp = df["position"].map(pos_group_nhl)
    exp_frac = pos_grp.map(POS_DEFAULT_EXP_FRAC).astype(float)
    exp_sec = np.where(
        df["toi_ev"].notna() & df["toi_pk"].notna(),
        pd.to_numeric(df["toi_ev"], errors="coerce").fillna(0.0) + pd.to_numeric(df["toi_pk"], errors="coerce").fillna(0.0),
        df["toi_sec"] * exp_frac,
    )
    df["toi_exp_min"] = exp_sec / 60.0
    return df[[
        "game_id", "date", "season_year", "player_id", "name", "team",
        "toi_sec", "toi_ev", "toi_pk", "toi_exp_min", "played", "blocked_shots",
    ]].reset_index(drop=True)


# --------------------------------------------------------------------------- Loaders with strict date/cutoff filter
def _load_skater_box_before(data_dir: str | Path, max_date: str | None) -> pd.DataFrame:
    path = _resolve_path(data_dir, "NHL/skater_boxscores_master.csv", "skater_boxscores_master.csv")
    if path is None:
        return pd.DataFrame()
    cols = [
        "game_id", "season_year", "game_type", "date", "team_id", "team_abbr", "is_home",
        "player_id", "name", "position", "toi_sec", "played", "blocked_shots", "hits", "sog",
    ]
    avail = pd.read_csv(path, nrows=0).columns.tolist()
    usecols = [c for c in cols if c in avail]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    df["date"] = df["date"].astype(str).str[:10]
    if max_date is not None:
        df = df[df["date"] < max_date]
    if "game_type" in df.columns:
        df = df[df["game_type"].isin([2, 3])]
    df["toi_sec"] = pd.to_numeric(df["toi_sec"], errors="coerce")
    played_bool = df["played"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df = df[played_bool & (df["toi_sec"].fillna(0.0) > 0.0)].copy()
    df["team"] = _norm_team_series(df["team_abbr"])
    df = df[df["team"].notna()].copy()
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce").astype("int64")
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("int64")
    df["player_norm"] = _norm_name_series(df["name"])
    df["is_home"] = df["is_home"].astype(str).str.lower().isin({"true", "1", "1.0"})
    df["position"] = df["position"].fillna("C").astype(str)
    df["pos_grp"] = df["position"].map(pos_group_nhl)
    for num_col in ("blocked_shots", "hits", "sog"):
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce").fillna(0.0)
        else:
            df[num_col] = 0.0
    return df.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def _load_games_before(data_dir: str | Path, max_date: str | None) -> pd.DataFrame:
    path = _resolve_path(data_dir, "NHL/games_master.csv", "games_master.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    df["date"] = df["date"].astype(str).str[:10]
    if max_date is not None:
        df = df[df["date"] < max_date]
    if "game_type" in df.columns:
        df = df[df["game_type"].isin([2, 3])]
    if "state" in df.columns:
        df = df[df["state"].isin(["OFF", "FINAL"])]
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce").astype("int64")
    df["home"] = _norm_team_series(df["home_abbr"])
    df["away"] = _norm_team_series(df["away_abbr"])
    for c in ("home_score", "away_score", "home_goals_ex_so", "away_goals_ex_so", "home_id", "away_id"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[df["home"].notna() & df["away"].notna()].copy()


def _load_schedule_on_date(data_dir: str | Path, target_date: str) -> pd.DataFrame:
    """Read ONLY pre-game matchup columns (no scores) on ``target_date``."""
    path = _resolve_path(data_dir, "NHL/games_master.csv", "games_master.csv")
    if path is None:
        return pd.DataFrame(columns=["game_id", "season_year", "date", "away", "home"])
    cols = ["game_id", "season_year", "game_type", "date", "away_abbr", "home_abbr"]
    avail = pd.read_csv(path, nrows=0).columns.tolist()
    df = pd.read_csv(path, usecols=[c for c in cols if c in avail], low_memory=False)
    df["date"] = df["date"].astype(str).str[:10]
    df = df[df["date"] == target_date]
    if "game_type" in df.columns:
        df = df[df["game_type"].isin([2, 3])]
    if df.empty:
        return pd.DataFrame(columns=["game_id", "season_year", "date", "away", "home"])
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce").astype("int64")
    df["home"] = df["home_abbr"].map(_norm_team)
    df["away"] = df["away_abbr"].map(_norm_team)
    df = df[df["home"].notna() & df["away"].notna()].copy()
    return df[["game_id", "season_year", "date", "away", "home"]].reset_index(drop=True)


def _load_toi_splits_for_games(data_dir: str | Path, valid_game_ids: set[int]) -> pd.DataFrame:
    """Load TOI splits restricted strictly to ``valid_game_ids`` (which are all < max_date)."""
    path = _resolve_path(data_dir, "NHL/toi_splits/toi_splits.csv", "toi_splits/toi_splits.csv")
    if path is None or not valid_game_ids:
        return pd.DataFrame(columns=["game_id", "player_id", "toi_total", "toi_ev", "toi_pp", "toi_pk"])
    cols = ["game_id", "player_id", "toi_total", "toi_ev", "toi_pp", "toi_pk"]
    avail = pd.read_csv(path, nrows=0).columns.tolist()
    df = pd.read_csv(path, usecols=[c for c in cols if c in avail], low_memory=False)
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce").astype("Int64")
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["game_id", "player_id"]).copy()
    df["game_id"] = df["game_id"].astype("int64")
    df["player_id"] = df["player_id"].astype("int64")
    df = df[df["game_id"].isin(valid_game_ids)].drop_duplicates(["game_id", "player_id"]).reset_index(drop=True)
    return df


def _load_shots_team_game_before(
    data_dir: str | Path,
    prior_games: pd.DataFrame,
    min_year: int | None = None,
) -> pd.DataFrame:
    """Aggregate team-game offensive shot-attempt volume + point-shot share from ``NHL/shots/shots_20*.csv``.

    Strictly filtered to ``prior_games`` (which already satisfy ``date < max_date``).
    """
    if prior_games.empty:
        return pd.DataFrame()
    shots_dir = _resolve_path(data_dir, "NHL/shots", "shots")
    if shots_dir is None:
        return pd.DataFrame()
    files = sorted(glob.glob(str(shots_dir / "shots_20*.csv")))
    if not files:
        return pd.DataFrame()

    g_home = prior_games[["game_id", "date", "home_id", "home"]].rename(columns={"home_id": "team_id", "home": "team"})
    g_away = prior_games[["game_id", "date", "away_id", "away"]].rename(columns={"away_id": "team_id", "away": "team"})
    g_map = pd.concat([g_home, g_away], ignore_index=True).dropna(subset=["team_id"]).copy()
    g_map["game_id"] = g_map["game_id"].astype("int64")
    g_map["team_id"] = pd.to_numeric(g_map["team_id"], errors="coerce").astype("int64")
    valid_gids = set(g_map["game_id"].unique())

    cols = ["game_id", "team_id", "event", "dist_ft", "zone", "x"]
    parts: list[pd.DataFrame] = []
    for f in files:
        if min_year is not None:
            m = re.search(r"(\d{4})\.csv$", f)
            if m and int(m.group(1)) < min_year:
                continue
        try:
            avail = pd.read_csv(f, nrows=0).columns.tolist()
            use = [c for c in cols if c in avail]
            df = pd.read_csv(f, usecols=use, low_memory=False)
        except Exception:
            continue
        if df.empty or "game_id" not in df.columns or "team_id" not in df.columns:
            continue
        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce")
        df = df.dropna(subset=["game_id", "team_id"]).copy()
        df["game_id"] = df["game_id"].astype("int64")
        df["team_id"] = df["team_id"].astype("int64")
        df = df[df["game_id"].isin(valid_gids)]
        if df.empty:
            continue
        df = df[df["event"].isin(["sog", "miss", "block", "goal"])].copy()
        if df.empty:
            continue
        dist = pd.to_numeric(df["dist_ft"], errors="coerce") if "dist_ft" in df.columns else pd.Series(np.nan, index=df.index)
        has_dist = dist.notna().astype(float)
        is_point = np.where(dist.notna(), (dist >= POINT_SHOT_MIN_DIST_FT).astype(float), 0.0)
        is_blk = (df["event"] == "block").astype(float)
        df = df.assign(att_for=1.0, has_dist=has_dist, point_att=is_point, blk_att=is_blk)
        agg = (
            df.groupby(["game_id", "team_id"], sort=False)
            .agg(
                att_for=("att_for", "sum"),
                has_dist=("has_dist", "sum"),
                point_att=("point_att", "sum"),
                blk_att=("blk_att", "sum"),
            )
            .reset_index()
        )
        parts.append(agg)

    if not parts:
        return pd.DataFrame()
    res = (
        pd.concat(parts, ignore_index=True)
        .groupby(["game_id", "team_id"], as_index=False)[["att_for", "has_dist", "point_att", "blk_att"]]
        .sum()
    )
    res = res.merge(g_map[["game_id", "team_id", "team"]], on=["game_id", "team_id"], how="inner")
    return res[["game_id", "team", "att_for", "has_dist", "point_att", "blk_att"]].reset_index(drop=True)


def _load_rotowire_at(
    data_dir: str | Path,
    target_date: str,
    cutoff_utc: pd.Timestamp,
) -> dict[str, dict[str, Any]]:
    """Load Rotowire NHL lineup/injury JSON for ``target_date`` strictly at ``fetched_at <= cutoff_utc``."""
    root = Path(data_dir)
    t_dt = pd.Timestamp(target_date)
    # Check exact ET target_date first, then -1 day fallback if UTC/ET shifted
    candidates = [
        root / "NHL/lineups/rotowire" / f"{target_date}.json",
        root / "NHL/lineups/rotowire" / f"{(t_dt - pd.Timedelta(days=1)).strftime('%Y-%m-%d')}.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        day = str(payload.get("day") or "")[:10]
        if day and day != target_date:
            continue
        ts_raw = payload.get("fetched_at")
        if ts_raw:
            try:
                if _to_utc_ts(ts_raw) > cutoff_utc:
                    continue
            except Exception:
                continue
        raw_lu = payload.get("lineups") or {}
        out: dict[str, dict[str, Any]] = {}
        for t_raw, info in raw_lu.items():
            tn = _norm_team(t_raw)
            if tn and isinstance(info, dict):
                out[tn] = info
        return out
    return {}


def _load_evs_nhl_game_env(
    data_dir: str | Path,
    target_date: str,
    cutoff_utc: pd.Timestamp,
) -> dict[str, dict[str, float]]:
    """Load pre-cutoff game total and moneyline win prob per team from ``EVSweep/nhl_games/``."""
    out: dict[str, dict[str, float]] = {}
    root = Path(data_dir)
    candidates = [
        root / "EVSweep/nhl_games/predictions_latest.json",
        root / f"EVSweep/nhl_games/archive/{target_date}.json",
        root / f"EVSweep/nhl_games/archive_{target_date}.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        ts_raw = payload.get("ts")
        if ts_raw:
            try:
                if _to_utc_ts(ts_raw) > cutoff_utc:
                    continue
            except Exception:
                continue
        rows = payload.get("rows") or []
        by_game: dict[str, dict[str, Any]] = {}
        for r in rows:
            gd = str(r.get("game_date") or "")[:10]
            gk = str(r.get("game_key") or "")
            if gd and gd != target_date:
                continue
            if not gd and target_date not in gk:
                continue
            home = _norm_team(r.get("home_team"))
            away = _norm_team(r.get("away_team"))
            if not home or not away:
                continue
            g = by_game.setdefault(gk or f"{away}@{home}", {"home": home, "away": away, "total": np.nan, "home_wp": np.nan})
            stat = str(r.get("stat") or "").lower()
            side = str(r.get("side") or "").lower()
            if stat == "total" and r.get("line") is not None:
                line = float(r["line"])
                p_ov = r.get("p_fair") if r.get("p_fair") is not None else r.get("p_mkt_side")
                if p_ov is not None and np.isfinite(float(p_ov)):
                    adj = (float(p_ov) - 0.5) * 1.6 if side != "under" else (0.5 - float(p_ov)) * 1.6
                    g["total"] = float(line + adj)
                else:
                    g["total"] = line
            elif stat == "moneyline":
                p_ml = r.get("p_fair") if r.get("p_fair") is not None else r.get("p_mkt_side")
                if p_ml is not None and np.isfinite(float(p_ml)):
                    p_f = float(np.clip(float(p_ml), 0.05, 0.95))
                    if side == "home" or _norm_team(r.get("team")) == home:
                        g["home_wp"] = p_f
                    elif side == "away" or _norm_team(r.get("team")) == away:
                        g["home_wp"] = 1.0 - p_f
        for g in by_game.values():
            h, a = g["home"], g["away"]
            tot = g["total"]
            h_wp = g["home_wp"]
            if np.isfinite(h_wp):
                a_wp = 1.0 - h_wp
                out.setdefault(h, {})["ml_win_prob"] = float(h_wp)
                out.setdefault(a, {})["ml_win_prob"] = float(a_wp)
            if np.isfinite(tot):
                out.setdefault(h, {})["game_total"] = float(tot)
                out.setdefault(a, {})["game_total"] = float(tot)
        if out:
            break
    return out


# --------------------------------------------------------------------------- Rolling / Prior Table Builders
def _roll_sum_prior(s: pd.Series, key: pd.Series, window: int) -> pd.Series:
    sh = s.groupby(key, sort=False).shift(1)
    return sh.groupby(key, sort=False).rolling(window, min_periods=1).sum().droplevel(0).reindex(s.index)


def _assign_nhl_archetypes(
    pos_grp: pd.Series,
    blk60_eb: pd.Series,
    mu_pk_toi: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    arch_id = np.full(len(pos_grp), 3, dtype=int)
    d_mask = pos_grp == "D"
    f_mask = ~d_mask
    arch_id[d_mask & (blk60_eb >= SHOT_BLK_D_MIN_RATE60)] = 0
    arch_id[d_mask & (blk60_eb < SHOT_BLK_D_MIN_RATE60)] = 1
    arch_id[f_mask & ((blk60_eb >= PK_F_MIN_RATE60) | (mu_pk_toi >= PK_F_MIN_PK_TOI))] = 2
    arch_id[f_mask & ~((blk60_eb >= PK_F_MIN_RATE60) | (mu_pk_toi >= PK_F_MIN_PK_TOI))] = 3
    s_id = pd.Series(arch_id, index=pos_grp.index, dtype=int)
    return s_id, s_id.map(NHL_ARCHETYPE_TABLE)


def _compute_team_post_series(
    sk: pd.DataFrame,
    games: pd.DataFrame,
    splits: pd.DataFrame,
    shots_tg: pd.DataFrame,
) -> pd.DataFrame:
    """Strictly-prior per-team rolling environment series (shots, point share, PK/PP, goals)."""
    if sk.empty:
        return pd.DataFrame()

    # Merge TOI splits onto skater appearances if present
    sk_w = sk[["game_id", "date", "team", "toi_sec", "blocked_shots", "sog"]].copy()
    if not splits.empty:
        sk_w = sk_w.merge(splits[["game_id", "player_id", "toi_ev", "toi_pp", "toi_pk"]],
                          left_on=["game_id", sk.get("player_id")], right_on=["game_id", "player_id"], how="left")
    else:
        sk_w["toi_ev"] = np.nan
        sk_w["toi_pp"] = np.nan
        sk_w["toi_pk"] = np.nan

    # Use explicit player_id merge properly
    sk_m = sk[["game_id", "date", "team", "player_id", "toi_sec", "blocked_shots", "sog"]].copy()
    if not splits.empty:
        sk_m = sk_m.merge(splits[["game_id", "player_id", "toi_ev", "toi_pp", "toi_pk"]], on=["game_id", "player_id"], how="left")
    else:
        sk_m["toi_ev"] = np.nan
        sk_m["toi_pp"] = np.nan
        sk_m["toi_pk"] = np.nan

    t_box = (
        sk_m.groupby(["game_id", "date", "team"], sort=False)
        .agg(
            t_toi_sec=("toi_sec", "sum"),
            t_toi_pk=("toi_pk", lambda s: s.sum(min_count=1)),
            t_toi_pp=("toi_pp", lambda s: s.sum(min_count=1)),
            t_blk=("blocked_shots", "sum"),
            t_sog=("sog", "sum"),
        )
        .reset_index()
    )

    # Attach opponent + goals from games_master (strictly date < max_date)
    if not games.empty:
        gh_col = "home_goals_ex_so" if "home_goals_ex_so" in games.columns else "home_score"
        ga_col = "away_goals_ex_so" if "away_goals_ex_so" in games.columns else "away_score"
        g_home = games[["game_id", "home", "away", gh_col, ga_col]].rename(
            columns={"home": "team", "away": "opp", gh_col: "gf", ga_col: "ga"}
        )
        g_away = games[["game_id", "away", "home", ga_col, gh_col]].rename(
            columns={"away": "team", "home": "opp", ga_col: "gf", gh_col: "ga"}
        )
        g_map = pd.concat([g_home, g_away], ignore_index=True).drop_duplicates(["game_id", "team"])
        t_box = t_box.merge(g_map, on=["game_id", "team"], how="left")
    else:
        opp_lk = t_box[["game_id", "team"]].rename(columns={"team": "opp"})
        t_box = t_box.merge(opp_lk, on="game_id", how="left")
        t_box = t_box[t_box["team"] != t_box["opp"]].drop_duplicates(["game_id", "team"])
        t_box["gf"] = np.nan
        t_box["ga"] = np.nan

    if not shots_tg.empty:
        t_box = t_box.merge(shots_tg, on=["game_id", "team"], how="left")
    else:
        t_box["att_for"] = np.nan
        t_box["has_dist"] = np.nan
        t_box["point_att"] = np.nan
        t_box["blk_att"] = np.nan

    # Opponent's shot attempts for = our shot attempts against; opponent's blocks = our shots blocked
    opp_shots = t_box[["game_id", "team", "att_for", "t_blk", "t_sog"]].rename(
        columns={"team": "opp", "att_for": "att_against", "t_blk": "opp_blk_in_game", "t_sog": "opp_sog_in_game"}
    )
    t_box = t_box.merge(opp_shots, on=["game_id", "opp"], how="left")

    # Fallback shot-attempt volume when shots_20*.csv is missing: sog + opp_blk * 1.45
    est_att_for = t_box["t_sog"] + t_box["opp_blk_in_game"].fillna(14.0) * 1.45
    est_att_ag = t_box["opp_sog_in_game"].fillna(29.0) + t_box["t_blk"] * 1.45
    t_box["att_for_eff"] = t_box["att_for"].where(t_box["att_for"].notna() & (t_box["att_for"] > 0), est_att_for)
    t_box["att_against_eff"] = t_box["att_against"].where(t_box["att_against"].notna() & (t_box["att_against"] > 0), est_att_ag)

    t_box["point_share"] = np.where(t_box["has_dist"] > 0, t_box["point_att"] / t_box["has_dist"], np.nan)
    blk_num = t_box["blk_att"].where(t_box["blk_att"].notna(), t_box["opp_blk_in_game"])
    t_box["shot_blk_rate"] = np.where(t_box["att_for_eff"] > 0, blk_num / t_box["att_for_eff"], np.nan)

    t_box["pk_share"] = np.where(t_box["t_toi_pk"].notna() & (t_box["t_toi_sec"] > 0), t_box["t_toi_pk"] / t_box["t_toi_sec"], np.nan)
    t_box["pp_share"] = np.where(t_box["t_toi_pp"].notna() & (t_box["t_toi_sec"] > 0), t_box["t_toi_pp"] / t_box["t_toi_sec"], np.nan)
    t_box["win_flag"] = np.where(t_box["gf"].notna() & t_box["ga"].notna(), (t_box["gf"] > t_box["ga"]).astype(float), np.nan)

    t_box = t_box.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
    out = t_box[["game_id", "date", "team", "opp"]].copy()

    metric_cols = {
        "att_for_eff": "att_for_l15",
        "att_against_eff": "att_against_l15",
        "point_share": "point_share_l15",
        "shot_blk_rate": "shot_blk_rate_l15",
        "pk_share": "pk_share_l15",
        "pp_share": "pp_share_l15",
        "gf": "gf_l15",
        "ga": "ga_l15",
        "win_flag": "win_l15",
    }
    for src, dst in metric_cols.items():
        out[f"team_{dst}"] = prior_roll(t_box, "team", src, 15, "mean", min_periods=1)
        out[f"post_{dst}"] = t_box.groupby("team", sort=False)[src].transform(lambda s: s.rolling(15, min_periods=1).mean())
    return out


def _compute_player_prior_series(
    sk: pd.DataFrame,
    splits: pd.DataFrame,
    team_series: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Strictly-prior (``shift(1)``) player blocked-shot & TOI features aligned to ``sk`` rows."""
    if sk.empty:
        return pd.DataFrame()

    df = sk.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    if not splits.empty:
        df = df.merge(splits[["game_id", "player_id", "toi_ev", "toi_pk"]], on=["game_id", "player_id"], how="left")
    else:
        df["toi_ev"] = np.nan
        df["toi_pk"] = np.nan

    if team_series is not None and not team_series.empty and "team_pk_share_l15" in team_series.columns:
        df = df.merge(
            team_series[["game_id", "team", "team_pk_share_l15"]].drop_duplicates(["game_id", "team"]),
            on=["game_id", "team"],
            how="left",
        )
    else:
        df["team_pk_share_l15"] = np.nan

    # Stage 1 TOI backbone rolling projections (exact same function used at score time)
    toi_stats = compute_prior_toi_stats(df)

    # Blocked shots per-60 EV+PK TOI (with total-TOI position-fraction fallback when split is missing)
    pos_grp = df["position"].map(pos_group_nhl)
    exp_frac = pos_grp.map(POS_DEFAULT_EXP_FRAC).astype(float)
    toi_sec = pd.to_numeric(df["toi_sec"], errors="coerce")
    has_spl = df["toi_ev"].notna() & df["toi_pk"].notna()
    exp_min = np.where(
        toi_sec.isna(),
        np.nan,
        np.where(
            has_spl,
            (pd.to_numeric(df["toi_ev"], errors="coerce").fillna(0.0) + pd.to_numeric(df["toi_pk"], errors="coerce").fillna(0.0)) / 60.0,
            (toi_sec / 60.0) * exp_frac,
        ),
    )
    df["exp_min"] = exp_min
    df["blk_val"] = np.where(toi_sec.isna(), np.nan, pd.to_numeric(df["blocked_shots"], errors="coerce").fillna(0.0))

    prior_blk60 = pos_grp.map(POS_DEFAULT_BLK60).astype(float)
    prior_blk_pm = prior_blk60 / 60.0

    pid = df["player_id"]
    out = pd.DataFrame({
        "game_id": df["game_id"],
        "date": df["date"],
        "season_year": df["season_year"],
        "player_id": df["player_id"],
        "name": df["name"],
        "player_norm": df["player_norm"],
        "position": df["position"],
        "pos_grp": pos_grp,
        "team": df["team"],
        "is_home": df["is_home"].astype(int),
    })

    out["n_prior_games"] = toi_stats["n_prior_games"].values
    out["n_prior_szn"] = df.groupby(["player_id", "season_year"], sort=False).cumcount().astype(int)
    out["is_d"] = (pos_grp == "D").astype(int)

    for w in (5, 15, 40):
        m_sum = _roll_sum_prior(df["exp_min"], pid, w).fillna(0.0)
        b_sum = _roll_sum_prior(df["blk_val"], pid, w).fillna(0.0)
        out[f"blk_per60_l{w}"] = 60.0 * (b_sum + EB_WINDOW_PRIOR_MIN * prior_blk_pm) / (m_sum + EB_WINDOW_PRIOR_MIN)

    szn_m = df.groupby(["player_id", "season_year"], sort=False)["exp_min"].transform(lambda x: x.shift(1).cumsum()).fillna(0.0)
    szn_b = df.groupby(["player_id", "season_year"], sort=False)["blk_val"].transform(lambda x: x.shift(1).cumsum()).fillna(0.0)
    out["blk_per60_szn"] = 60.0 * (szn_b + EB_WINDOW_PRIOR_MIN * prior_blk_pm) / (szn_m + EB_WINDOW_PRIOR_MIN)

    m40 = _roll_sum_prior(df["exp_min"], pid, 40).fillna(0.0)
    b40 = _roll_sum_prior(df["blk_val"], pid, 40).fillna(0.0)
    out["blk_rate_eb"] = (b40 + EB_BLK_PRIOR_MIN * prior_blk_pm) / (m40 + EB_BLK_PRIOR_MIN)

    for c in (
        "toi_l5", "toi_l15", "toi_ev_l10", "toi_pk_l10",
        "mu_ev_toi", "sigma_ev_toi", "mu_pk_toi", "sigma_pk_toi",
        "mu_exp_toi", "sigma_exp_toi", "pk_toi_share", "toi_split_fallback_flag",
    ):
        out[c] = toi_stats[c].values

    arch_id, arch_name = _assign_nhl_archetypes(out["pos_grp"], out["blk_rate_eb"] * 60.0, out["mu_pk_toi"])
    out["archetype_id"] = arch_id
    out["archetype_name"] = arch_name

    # Rest days strictly from prior game
    prev_date = df.groupby("player_id", sort=False)["date"].shift(1)
    rest = (pd.to_datetime(df["date"]) - pd.to_datetime(prev_date)).dt.days - 1
    out["rest_days"] = rest.clip(lower=0, upper=14).fillna(3.0)
    out["is_b2b"] = (out["rest_days"] == 0).astype(int)
    return out


def _compute_player_post_table(
    sk: pd.DataFrame,
    splits: pd.DataFrame,
    target_date: str,
    team_pk_map: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Summarize strictly-prior skater appearances (``date < target_date``) into 1 row per player."""
    if sk.empty:
        return pd.DataFrame()
    df = sk.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    last = df.groupby("player_id", sort=False).tail(1).copy()
    last["game_id"] = -1
    last["date"] = target_date
    last["season_year"] = _season_year_of_date(target_date)
    last["toi_sec"] = np.nan
    last["blocked_shots"] = np.nan
    combined = pd.concat([df, last], ignore_index=True)

    # Build a synthetic team_series row for query rows if team_pk_map is given
    t_ser = pd.DataFrame()
    if team_pk_map:
        t_ser = pd.DataFrame([
            {"game_id": -1, "team": t, "team_pk_share_l15": v}
            for t, v in team_pk_map.items()
        ])
    series = _compute_player_prior_series(combined, splits, team_series=t_ser)
    return series[series["game_id"] == -1].reset_index(drop=True)


# --------------------------------------------------------------------------- Public Pregame Feature Builder
def build_pregame_features_nhl(
    target_date: str | pd.Timestamp,
    cutoff_utc: str | pd.Timestamp,
    data_dir: str | Path,
    slate: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """PURE U1 pregame NHL feature builder for ``target_date`` at ``cutoff_utc``.

    Physically loads ONLY skater/game/shot/split rows with ``date < target_date``
    and Rotowire / EVSweep snapshots timestamped ``<= cutoff_utc``.
    """
    t_date = _date_str(target_date)
    c_utc = _to_utc_ts(cutoff_utc)

    prior_sk = _load_skater_box_before(data_dir, t_date)
    prior_games = _load_games_before(data_dir, t_date)
    valid_gids = set(prior_sk["game_id"].unique()) | set(prior_games["game_id"].unique())
    prior_splits = _load_toi_splits_for_games(data_dir, valid_gids)
    prior_shots = _load_shots_team_game_before(data_dir, prior_games)

    t_series = _compute_team_post_series(prior_sk, prior_games, prior_splits, prior_shots)
    t_latest = (
        t_series.sort_values(["team", "date", "game_id"]).groupby("team", sort=False).tail(1).set_index("team")
        if not t_series.empty
        else pd.DataFrame()
    )
    team_pk_map: dict[str, float] = {}
    if not t_latest.empty and "post_pk_share_l15" in t_latest.columns:
        team_pk_map = {
            str(t): float(v)
            for t, v in t_latest["post_pk_share_l15"].items()
            if pd.notna(v)
        }

    p_hist = _compute_player_post_table(prior_sk, prior_splits, t_date, team_pk_map=team_pk_map)
    evs_env = _load_evs_nhl_game_env(data_dir, t_date, c_utc)

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
        if not sched.empty and not p_hist.empty:
            home_df = sched[["game_id", "season_year", "date", "home", "away"]].rename(columns={"home": "team", "away": "opp"}).assign(is_home=1)
            away_df = sched[["game_id", "season_year", "date", "away", "home"]].rename(columns={"away": "team", "home": "opp"}).assign(is_home=0)
            tg = pd.concat([home_df, away_df], ignore_index=True)
            cutoff_recent = (pd.Timestamp(t_date) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
            recent_ids = set(prior_sk.loc[prior_sk["date"] >= cutoff_recent, "player_id"]) if not prior_sk.empty else set()
            act = p_hist[p_hist["player_id"].isin(recent_ids)] if recent_ids else p_hist
            q = tg.merge(act[["player_id", "name", "player_norm", "position", "team"]], on="team", how="inner")
        elif not p_hist.empty:
            cutoff_recent = (pd.Timestamp(t_date) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
            act = p_hist[p_hist["player_id"].isin(set(prior_sk.loc[prior_sk["date"] >= cutoff_recent, "player_id"]))]
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

    if q.empty:
        return pd.DataFrame(columns=ID_COLUMNS + FEATURE_COLUMNS)

    p_cols = [
        "player_id", "name", "player_norm", "position", "pos_grp", "team",
        "blk_per60_l5", "blk_per60_l15", "blk_per60_l40", "blk_per60_szn", "blk_rate_eb",
        "toi_l5", "toi_l15", "toi_ev_l10", "toi_pk_l10",
        "n_prior_games", "n_prior_szn", "is_d", "archetype_id", "archetype_name",
        "rest_days", "is_b2b",
    ]
    if not p_hist.empty:
        if "player_id" in q.columns and pd.to_numeric(q["player_id"], errors="coerce").isin(set(p_hist["player_id"])).any():
            q["player_id"] = pd.to_numeric(q["player_id"], errors="coerce").astype("Int64")
            ph = p_hist[p_cols].drop_duplicates("player_id")
            q = q.merge(ph, on="player_id", how="left", suffixes=("", "_hist"))
        else:
            ph_tp = p_hist.sort_values("n_prior_games").drop_duplicates(["team", "player_norm"], keep="last")
            q = q.merge(ph_tp[p_cols], on=["team", "player_norm"], how="left", suffixes=("", "_hist"))
            miss = q["blk_rate_eb"].isna()
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

    q["player_id"] = q.get("player_id", q["player_norm"]).fillna(q["player_norm"]).astype(str)
    q["name"] = q.get("name", q.get("player", q["player_norm"])).fillna(q["player_norm"])
    q["position"] = q["position"].fillna("C")
    q["pos_grp"] = q["position"].map(pos_group_nhl)
    q["is_d"] = (q["pos_grp"] == "D").astype(int)

    for g, b60 in POS_DEFAULT_BLK60.items():
        gm = (q["pos_grp"] == g) & q["blk_rate_eb"].isna()
        q.loc[gm, "blk_rate_eb"] = b60 / 60.0
        for w in ("l5", "l15", "l40", "szn"):
            q.loc[gm, f"blk_per60_{w}"] = q.loc[gm, f"blk_per60_{w}"].fillna(b60)

    q["n_prior_games"] = q["n_prior_games"].fillna(0).astype(int)
    q["n_prior_szn"] = q["n_prior_szn"].fillna(0).astype(int)
    q["toi_l5"] = q["toi_l5"].fillna(pd.Series(np.where(q["is_d"] == 1, 20.0, 15.5), index=q.index))
    q["toi_l15"] = q["toi_l15"].fillna(q["toi_l5"])
    q["toi_ev_l10"] = q["toi_ev_l10"].fillna(pd.Series(np.where(q["is_d"] == 1, 16.5, 13.5), index=q.index))
    q["toi_pk_l10"] = q["toi_pk_l10"].fillna(pd.Series(np.where(q["is_d"] == 1, 2.0, 0.8), index=q.index))
    q["rest_days"] = q["rest_days"].fillna(3.0)
    q["is_b2b"] = q["is_b2b"].fillna(0).astype(int)
    q["is_home"] = q.get("is_home", pd.Series(0, index=q.index)).fillna(0).astype(int)

    def _t_lookup(col_series: pd.Series, field: str, default: float) -> np.ndarray:
        if t_latest.empty or field not in t_latest.columns:
            return np.full(len(col_series), default, dtype=float)
        return col_series.map(t_latest[field]).fillna(default).to_numpy(dtype=float)

    q["opp_att_for_l15"] = _t_lookup(q["opp"], "post_att_for_l15", DEFAULT_TEAM_ATT_FOR)
    q["opp_point_share_l15"] = _t_lookup(q["opp"], "post_point_share_l15", DEFAULT_POINT_SHARE)
    q["opp_shot_blk_rate_l15"] = _t_lookup(q["opp"], "post_shot_blk_rate_l15", DEFAULT_SHOT_BLK_RATE)
    q["opp_pp_share_l15"] = _t_lookup(q["opp"], "post_pp_share_l15", LG_PK_SHARE)
    q["team_att_against_l15"] = _t_lookup(q["team"], "post_att_against_l15", DEFAULT_TEAM_ATT_FOR)
    q["team_pk_share_l15"] = _t_lookup(q["team"], "post_pk_share_l15", LG_PK_SHARE)

    # Game total, opponent implied total, opponent ML win prob (EVSweep pre-cutoff if available, else strictly prior rates)
    team_gf = _t_lookup(q["team"], "post_gf_l15", 3.05)
    team_ga = _t_lookup(q["team"], "post_ga_l15", 3.05)
    opp_gf = _t_lookup(q["opp"], "post_gf_l15", 3.05)
    opp_ga = _t_lookup(q["opp"], "post_ga_l15", 3.05)
    est_team_g = 0.5 * (team_gf + opp_ga)
    est_opp_g = 0.5 * (opp_gf + team_ga)
    est_tot = np.clip(est_team_g + est_opp_g, 4.5, 8.5)
    team_w = _t_lookup(q["team"], "post_win_l15", 0.50)
    opp_w = _t_lookup(q["opp"], "post_win_l15", 0.50)
    est_opp_wp = np.clip(0.5 + 0.5 * (opp_w - team_w) - 0.04 * (q["is_home"].to_numpy(float) - 0.5), 0.25, 0.75)

    q["game_total"] = est_tot
    q["opp_ml_win_prob"] = est_opp_wp
    for team_val, env in evs_env.items():
        m = q["team"] == team_val
        if "game_total" in env:
            q.loc[m, "game_total"] = float(env["game_total"])
        if "ml_win_prob" in env:
            q.loc[m, "opp_ml_win_prob"] = float(1.0 - env["ml_win_prob"])

    q["opp_implied_total"] = np.clip(0.5 * q["game_total"] + (q["opp_ml_win_prob"] - 0.5) * 2.2, 1.5, 5.5)

    # Stage 1 TOI backbone + pre-cutoff Rotowire lineups/injuries
    rw_by_team = _load_rotowire_at(data_dir, t_date, c_utc)
    bb_input = q[["player_id", "team", "position", "name", "player_norm"]].copy()
    bb = compute_toi_backbone(
        prior_sk,
        prior_splits,
        bb_input,
        rotowire_by_team=rw_by_team,
        target_date=t_date,
        team_pk_share_map=team_pk_map,
    )
    for col in TOI_BACKBONE_COLS:
        q[col] = bb[col].values

    arch_id, arch_name = _assign_nhl_archetypes(q["pos_grp"], q["blk_rate_eb"] * 60.0, q["mu_pk_toi"])
    q["archetype_id"] = arch_id
    q["archetype_name"] = arch_name

    ordered = [c for c in ID_COLUMNS if c in q.columns] + FEATURE_COLUMNS
    out = q[ordered].reset_index(drop=True)
    assert_nhl_feature_schema(out)
    return out


# --------------------------------------------------------------------------- Vectorized Training Feature Builder
def build_training_features_nhl(
    data_dir: str | Path,
    min_date: str | None = "2022-10-01",
    max_date: str | None = None,
) -> pd.DataFrame:
    """Build strictly-prior NHL training feature matrix (masters + splits + shots only; no Rotowire)."""
    sk = _load_skater_box_before(data_dir, max_date)
    if sk.empty:
        return pd.DataFrame(columns=ID_COLUMNS + FEATURE_COLUMNS)

    min_yr = (int(min_date[:4]) - 1) if min_date else None
    games = _load_games_before(data_dir, max_date)
    valid_gids = set(sk["game_id"].unique()) | set(games["game_id"].unique())
    splits = _load_toi_splits_for_games(data_dir, valid_gids)
    shots_tg = _load_shots_team_game_before(data_dir, games, min_year=min_yr)

    t_series = _compute_team_post_series(sk, games, splits, shots_tg)
    p_series = _compute_player_prior_series(sk, splits, team_series=t_series)

    t_self = t_series[[
        "game_id", "team", "opp",
        "team_att_against_l15", "team_pk_share_l15", "team_gf_l15", "team_ga_l15", "team_win_l15",
    ]].rename(columns={"team_gf_l15": "self_gf_l15", "team_ga_l15": "self_ga_l15", "team_win_l15": "self_win_l15"})
    df = p_series.merge(t_self, on=["game_id", "team"], how="left")

    opp_cols = t_series[[
        "game_id", "team",
        "team_att_for_l15", "team_point_share_l15", "team_shot_blk_rate_l15",
        "team_pp_share_l15", "team_gf_l15", "team_ga_l15", "team_win_l15",
    ]].rename(columns={
        "team": "opp",
        "team_att_for_l15": "opp_att_for_l15",
        "team_point_share_l15": "opp_point_share_l15",
        "team_shot_blk_rate_l15": "opp_shot_blk_rate_l15",
        "team_pp_share_l15": "opp_pp_share_l15",
        "team_gf_l15": "opp_gf_l15",
        "team_ga_l15": "opp_ga_l15",
        "team_win_l15": "opp_win_l15",
    })
    df = df.merge(opp_cols, on=["game_id", "opp"], how="left")

    df["opp_att_for_l15"] = df["opp_att_for_l15"].fillna(DEFAULT_TEAM_ATT_FOR)
    df["opp_point_share_l15"] = df["opp_point_share_l15"].fillna(DEFAULT_POINT_SHARE)
    df["opp_shot_blk_rate_l15"] = df["opp_shot_blk_rate_l15"].fillna(DEFAULT_SHOT_BLK_RATE)
    df["opp_pp_share_l15"] = df["opp_pp_share_l15"].fillna(LG_PK_SHARE)
    df["team_att_against_l15"] = df["team_att_against_l15"].fillna(DEFAULT_TEAM_ATT_FOR)
    df["team_pk_share_l15"] = df["team_pk_share_l15"].fillna(LG_PK_SHARE)

    est_team_g = 0.5 * (df["self_gf_l15"].fillna(3.05) + df["opp_ga_l15"].fillna(3.05))
    est_opp_g = 0.5 * (df["opp_gf_l15"].fillna(3.05) + df["self_ga_l15"].fillna(3.05))
    df["game_total"] = np.clip(est_team_g + est_opp_g, 4.5, 8.5)
    df["opp_ml_win_prob"] = np.clip(
        0.5 + 0.5 * (df["opp_win_l15"].fillna(0.5) - df["self_win_l15"].fillna(0.5)) - 0.04 * (df["is_home"] - 0.5),
        0.25,
        0.75,
    )
    df["opp_implied_total"] = np.clip(0.5 * df["game_total"] + (df["opp_ml_win_prob"] - 0.5) * 2.2, 1.5, 5.5)

    # Training rows use masters only (no Rotowire injury/lineup adjustments)
    df["absorbed_toi_delta"] = 0.0
    df["teammate_out_count"] = 0
    df["is_player_out"] = 0
    df["is_player_dtd"] = 0
    df["rw_pp_unit"] = 0

    if min_date is not None:
        df = df[df["date"] >= min_date].reset_index(drop=True)

    ordered = [c for c in ID_COLUMNS if c in df.columns] + FEATURE_COLUMNS
    out = df[ordered].reset_index(drop=True)
    assert_nhl_feature_schema(out)
    return out
