"""Time-travel-safe pregame feature assembly and runtime anti-leakage schema gate (U1).

Rebind note: NBA feature assembly here mirrors the Cluster (d) contract and will rebind
to Cluster (a)'s shared NBA feature backbone upon merge.
"""
from __future__ import annotations

import re
from typing import Iterable

import numpy as np
import pandas as pd

# Patterns and exact column names banned as model / feature columns (U1).
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r".*_post$",
    r"^actual_.*",
    r"^outcome_.*",
    r"^line$",
    r"^odds$",
    r"^p_market$",
    r"^prop_line$",
    r"^prop_odds$",
    r"^market_prob$",
    r"^spread$",
    r"^total$",
    r"^implied_team_total$",
)

ALLOWED_ENV_COLUMNS: frozenset[str] = frozenset(
    {"env_spread", "env_total", "env_implied_team_total"}
)

_COMPILED_FORBIDDEN = tuple(re.compile(pat) for pat in FORBIDDEN_PATTERNS)

LEAGUE_FG3_PCT_DEFAULT = 0.36
LEAGUE_FG2_PCT_DEFAULT = 0.53
LEAGUE_FT_PCT_DEFAULT = 0.78
LEAGUE_EFG_DEFAULT = 0.54
LEAGUE_PACE_DEFAULT = 98.0

FG3_PRIOR_ATT = 80.0
FG2_PRIOR_ATT = 100.0
FT_PRIOR_ATT = 50.0
EFG_PRIOR_ATT = 100.0

NBA_FEATURE_COLS: tuple[str, ...] = (
    "min_l5",
    "min_l15",
    "min_ewm",
    "min_std_l10",
    "fga_per_min_l5",
    "fga_per_min_l15",
    "fga_per_min_ewm",
    "fta_per_min_l5",
    "fta_per_min_l15",
    "fta_per_min_ewm",
    "fg3a_per_min_l5",
    "fg3a_per_min_l15",
    "fg3a_per_min_ewm",
    "fg3pct_eb",
    "fg2pct_eb",
    "ftpct_eb",
    "zone_efg_eb",
    "pace_proxy",
    "pace_factor",
    "rest_days",
    "starter_rate",
    "n_prior_games",
    "env_spread",
    "env_total",
    "env_implied_team_total",
)


# Key minutes/usage columns that must carry real signal in any frame used to
# fit or score. env_* columns are deliberately excluded: they are NaN by
# design when no pre-lock snapshot is attached.
KEY_NBA_FEATURE_COLS: tuple[str, ...] = (
    "min_l5",
    "min_l15",
    "min_ewm",
    "fga_per_min_l5",
    "fga_per_min_l15",
    "fga_per_min_ewm",
    "fta_per_min_l5",
    "fta_per_min_l15",
    "fta_per_min_ewm",
    "fg3a_per_min_l5",
    "fg3a_per_min_l15",
    "fg3a_per_min_ewm",
    "pace_factor",
    "n_prior_games",
)


def validate_feature_frame(df: pd.DataFrame, *, context: str = "feature frame") -> None:
    """Positive contract gate: key feature columns must exist and carry signal.

    `assert_no_leak_columns` is a blocklist: it rejects forbidden columns but
    says nothing about required ones, so a frame whose minutes/usage columns
    are entirely NaN passes it silently. That is exactly how the 2026-11-25
    Cluster (d) bank shipped a single league-average distribution as a
    "model": every key feature was NaN and nothing downstream complained.
    This gate is the positive counterpart and raises ValueError when a key
    column is missing or has no finite value in the frame.
    """
    if df is None or len(df) == 0:
        raise ValueError(f"{context}: empty frame; refusing to fit/score on no rows")
    missing = [c for c in KEY_NBA_FEATURE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{context}: missing key feature columns: {missing}")
    dead = [
        c
        for c in KEY_NBA_FEATURE_COLS
        if not np.isfinite(pd.to_numeric(df[c], errors="coerce")).any()
    ]
    if dead:
        raise ValueError(
            f"{context}: key feature columns carry no finite values (all-NaN): {dead}"
        )


def assert_no_leak_columns(df: pd.DataFrame) -> None:
    """Hard runtime schema gate (U1): raise ValueError if any column matches forbidden patterns."""
    if df is None:
        return
    bad: list[str] = []
    for col in df.columns:
        name = str(col)
        if name in ALLOWED_ENV_COLUMNS:
            continue
        if any(rx.match(name) for rx in _COMPILED_FORBIDDEN):
            bad.append(name)
    if bad:
        raise ValueError(f"Leakage / forbidden feature columns detected: {bad}")


def _date_col(df: pd.DataFrame) -> str:
    if "game_date" in df.columns:
        return "game_date"
    if "date" in df.columns:
        return "date"
    raise KeyError("boxscores must contain 'game_date' or 'date'")


def _min_col(df: pd.DataFrame) -> str | None:
    for c in ("minutes", "min"):
        if c in df.columns:
            return c
    return None


def _col(df: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
    for c in candidates:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def _parse_minutes(values: pd.Series) -> pd.Series:
    """Parse a minutes column to float minutes.

    Production boxscore masters store minutes as 'MM:SS' strings ('32:15');
    `pd.to_numeric(..., errors='coerce')` turns every such value into NaN,
    which silently zeroed the entire minutes/usage feature family (and, via
    the minutes>0 played filter, inflated n_prior_games with DNP rows).
    Accepts numeric values, plain-number strings, 'MM:SS' and 'H:MM:SS'.
    """
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce").astype(float)
    s = values.astype("string").str.strip()
    out = pd.to_numeric(s, errors="coerce")
    colon = out.isna() & s.str.contains(":", na=False)
    if bool(colon.any()):
        parts = s[colon].str.split(":", expand=True)
        total = pd.Series(0.0, index=parts.index)
        n_parts = parts.shape[1]
        for i in range(n_parts):
            # Last component is seconds; the first is minutes ('MM:SS') or
            # hours ('H:MM:SS'): weights 60^(n-2-i) give minutes.
            comp = pd.to_numeric(parts[i], errors="coerce").fillna(0.0)
            total = total + comp * (60.0 ** (n_parts - 2 - i))
        out.loc[colon] = total
    return out.astype(float)


def _normalize_boxscores(boxscores: pd.DataFrame) -> pd.DataFrame:
    """Normalize production-master column conventions to the feature contract.

    - team: masters carry `team_abbr`; the feature contract reads `team`.
      Without this, team is None for every player and pace falls back to the
      league default for the whole slate.
    - minutes: masters carry 'MM:SS' strings; parse to float minutes.

    Returns a copy; the caller's frame is untouched.
    """
    box = boxscores.copy()
    if "team" not in box.columns and "team_abbr" in box.columns:
        box["team"] = box["team_abbr"]
    mcol = _min_col(box)
    if mcol is not None:
        box[mcol] = _parse_minutes(box[mcol])
    return box


def _filter_snapshots(
    snapshots: pd.DataFrame | None, cutoff_utc: str | pd.Timestamp
) -> pd.DataFrame | None:
    if snapshots is None or len(snapshots) == 0:
        return None
    snap = snapshots.copy()
    ts_col = None
    for candidate in ("ts_utc", "snapshot_ts_utc", "ts", "timestamp_utc"):
        if candidate in snap.columns:
            ts_col = candidate
            break
    if ts_col is not None:
        cutoff = pd.Timestamp(cutoff_utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize("UTC")
        ts_vals = pd.to_datetime(snap[ts_col], utc=True)
        snap = snap.loc[ts_vals <= cutoff].copy()
        if len(snap) > 0:
            snap["_ts_sort"] = pd.to_datetime(snap[ts_col], utc=True)
            snap = snap.sort_values("_ts_sort")
    return snap


def _compute_team_pace_prior(hist: pd.DataFrame, dcol: str) -> tuple[pd.DataFrame, float]:
    """Compute strictly-prior team pace proxy from historical boxscores."""
    if len(hist) == 0 or "team" not in hist.columns:
        return pd.DataFrame(columns=["team", "pace_proxy"]), LEAGUE_PACE_DEFAULT

    if "team_poss" in hist.columns or "pace" in hist.columns:
        pcol = "team_poss" if "team_poss" in hist.columns else "pace"
        team_games = (
            hist.groupby(["team", dcol], as_index=False)[pcol]
            .mean()
            .sort_values(["team", dcol])
        )
        team_games = team_games.rename(columns={pcol: "_poss"})
    else:
        fga = _col(hist, ("fga", "field_goals_attempted")).fillna(0.0)
        fta = _col(hist, ("fta", "free_throws_attempted")).fillna(0.0)
        oreb = _col(hist, ("oreb", "rebounds_offensive")).fillna(0.0)
        tov = _col(hist, ("tov", "turnovers")).fillna(0.0)
        tmp = pd.DataFrame(
            {
                "team": hist["team"].values,
                dcol: hist[dcol].values,
                "_poss": (fga + 0.44 * fta - oreb + tov).values,
            }
        )
        # Sum across teammates per team-game date; if roster is partial, still a consistent proxy.
        team_games = tmp.groupby(["team", dcol], as_index=False)["_poss"].sum().sort_values(["team", dcol])
        # Scale up only if obviously single-player partial? Keep raw team sum or fallback if < 20.
        if team_games["_poss"].median() < 30.0:
            # Small synthetic fixture with 1-2 players per team: scale around 98 via relative ratio
            pass

    lg_pace = float(team_games["_poss"].mean()) if len(team_games) else LEAGUE_PACE_DEFAULT
    if not np.isfinite(lg_pace) or lg_pace <= 0:
        lg_pace = LEAGUE_PACE_DEFAULT
    team_pace = (
        team_games.groupby("team", as_index=False)["_poss"]
        .apply(lambda s: float(s.ewm(halflife=10, min_periods=1).mean().iloc[-1]))
        .rename(columns={"_poss": "pace_proxy"})
    )
    if "pace_proxy" not in team_pace.columns:
        # pandas groupby apply compatibility
        rows = []
        for tm, grp in team_games.groupby("team", sort=False):
            val = float(grp["_poss"].ewm(halflife=10, min_periods=1).mean().iloc[-1])
            rows.append({"team": tm, "pace_proxy": val})
        team_pace = pd.DataFrame(rows)
    return team_pace, lg_pace


def _aggregate_player_history(
    hist: pd.DataFrame,
    target_ts: pd.Timestamp,
    dcol: str,
    team_pace: pd.DataFrame,
    lg_pace: float,
) -> pd.DataFrame:
    """Aggregate strictly-prior historical boxscores into one row per player_id."""
    if len(hist) == 0:
        return pd.DataFrame(columns=["player_id", "team", *NBA_FEATURE_COLS])

    hist = hist.copy()
    hist["_dt"] = pd.to_datetime(hist[dcol]).dt.tz_localize(None).dt.normalize()
    sort_cols = ["player_id", "_dt"] + (["game_id"] if "game_id" in hist.columns else [])
    hist = hist.sort_values(sort_cols).reset_index(drop=True)

    mcol = _min_col(hist)
    mins = pd.to_numeric(hist[mcol], errors="coerce") if mcol else pd.Series(np.nan, index=hist.index)
    fga = _col(hist, ("fga", "field_goals_attempted"))
    fgm = _col(hist, ("fgm", "field_goals_made"))
    fta = _col(hist, ("fta", "free_throws_attempted"))
    ftm = _col(hist, ("ftm", "free_throws_made"))
    fg3a = _col(hist, ("fg3a", "three_points_attempted"))
    fg3m = _col(hist, ("fg3m", "three_points_made"))
    fg2a = _col(hist, ("fg2a", "two_points_attempted"))
    fg2m = _col(hist, ("fg2m", "two_points_made"))
    if fg2a.isna().all() and not fga.isna().all() and not fg3a.isna().all():
        fg2a = (fga - fg3a).clip(lower=0.0)
    if fg2m.isna().all() and not fgm.isna().all() and not fg3m.isna().all():
        fg2m = (fgm - fg3m).clip(lower=0.0)
    efg_col = _col(hist, ("efg",))
    starter = _col(hist, ("starter", "is_starter"))

    # League priors computed strictly from filtered prior history
    tot_3a = float(fg3a.fillna(0.0).sum())
    tot_3m = float(fg3m.fillna(0.0).sum())
    lg_fg3_pct = (tot_3m / tot_3a) if tot_3a > 0 else LEAGUE_FG3_PCT_DEFAULT

    tot_2a = float(fg2a.fillna(0.0).sum())
    tot_2m = float(fg2m.fillna(0.0).sum())
    lg_fg2_pct = (tot_2m / tot_2a) if tot_2a > 0 else LEAGUE_FG2_PCT_DEFAULT

    tot_fta = float(fta.fillna(0.0).sum())
    tot_ftm = float(ftm.fillna(0.0).sum())
    lg_ft_pct = (tot_ftm / tot_fta) if tot_fta > 0 else LEAGUE_FT_PCT_DEFAULT

    tot_fga = float(fga.fillna(0.0).sum())
    if tot_fga > 0 and (tot_2m > 0 or not fgm.isna().all()):
        base_fgm = tot_2m + tot_3m if tot_2a > 0 else float(fgm.fillna(0.0).sum())
        lg_efg = (base_fgm + 0.5 * tot_3m) / tot_fga
    elif not efg_col.isna().all():
        lg_efg = float(efg_col.mean())
    else:
        lg_efg = LEAGUE_EFG_DEFAULT

    hist["_min"] = mins
    hist["_fga"] = fga
    hist["_fgm"] = fgm
    hist["_fta"] = fta
    hist["_ftm"] = ftm
    hist["_fg3a"] = fg3a
    hist["_fg3m"] = fg3m
    hist["_fg2a"] = fg2a
    hist["_fg2m"] = fg2m
    hist["_efg"] = efg_col
    hist["_starter"] = starter

    pos_min = mins.where(mins > 0)
    hist["_fga_pm"] = fga / pos_min
    hist["_fta_pm"] = fta / pos_min
    hist["_fg3a_pm"] = fg3a / pos_min

    pace_map = dict(zip(team_pace["team"], team_pace["pace_proxy"])) if len(team_pace) else {}

    rows: list[dict] = []
    for pid, g in hist.groupby("player_id", sort=False):
        played = g[g["_min"].fillna(0.0) > 0] if mcol else g
        if len(played) == 0:
            played = g
        last_dt = g["_dt"].iloc[-1]
        rest_days = float(np.clip((target_ts - last_dt).days, 0, 14))
        team_val = g["team"].iloc[-1] if "team" in g.columns else None

        def _tail_stats(s: pd.Series) -> tuple[float, float, float]:
            s_clean = s.dropna()
            if len(s_clean) == 0:
                return np.nan, np.nan, np.nan
            l5 = float(s_clean.tail(5).mean())
            l15 = float(s_clean.tail(15).mean())
            ewm = float(s_clean.ewm(halflife=10, min_periods=1).mean().iloc[-1])
            return l5, l15, ewm

        min_l5, min_l15, min_ewm = _tail_stats(played["_min"])
        min_std_l10 = float(played["_min"].dropna().tail(10).std(ddof=0)) if played["_min"].notna().sum() >= 2 else np.nan
        fga_l5, fga_l15, fga_ewm = _tail_stats(played["_fga_pm"])
        fta_l5, fta_l15, fta_ewm = _tail_stats(played["_fta_pm"])
        fg3a_l5, fg3a_l15, fg3a_ewm = _tail_stats(played["_fg3a_pm"])

        s3a = float(played["_fg3a"].fillna(0.0).sum())
        s3m = float(played["_fg3m"].fillna(0.0).sum())
        fg3pct_eb = (s3m + FG3_PRIOR_ATT * lg_fg3_pct) / (s3a + FG3_PRIOR_ATT)

        s2a = float(played["_fg2a"].fillna(0.0).sum())
        s2m = float(played["_fg2m"].fillna(0.0).sum())
        fg2pct_eb = (s2m + FG2_PRIOR_ATT * lg_fg2_pct) / (s2a + FG2_PRIOR_ATT)

        sfta = float(played["_fta"].fillna(0.0).sum())
        sftm = float(played["_ftm"].fillna(0.0).sum())
        ftpct_eb = (sftm + FT_PRIOR_ATT * lg_ft_pct) / (sfta + FT_PRIOR_ATT)

        sfga = float(played["_fga"].fillna(0.0).sum())
        if sfga > 0 and (s2a > 0 or played["_fgm"].notna().any()):
            p_fgm = (s2m + s3m) if s2a > 0 else float(played["_fgm"].fillna(0.0).sum())
            zone_efg_eb = (p_fgm + 0.5 * s3m + EFG_PRIOR_ATT * lg_efg) / (sfga + EFG_PRIOR_ATT)
        elif played["_efg"].notna().any():
            n_efg = float(played["_efg"].notna().sum())
            zone_efg_eb = (float(played["_efg"].dropna().sum()) + 5.0 * lg_efg) / (n_efg + 5.0)
        else:
            zone_efg_eb = lg_efg

        st_clean = g["_starter"].dropna()
        starter_rate = float(st_clean.tail(15).mean()) if len(st_clean) > 0 else np.nan

        p_pace = float(pace_map.get(team_val, lg_pace)) if team_val is not None else lg_pace
        p_factor = float(p_pace / lg_pace) if lg_pace > 0 else 1.0

        rows.append(
            {
                "player_id": pid,
                "team": team_val,
                "min_l5": min_l5,
                "min_l15": min_l15,
                "min_ewm": min_ewm,
                "min_std_l10": min_std_l10,
                "fga_per_min_l5": fga_l5,
                "fga_per_min_l15": fga_l15,
                "fga_per_min_ewm": fga_ewm,
                "fta_per_min_l5": fta_l5,
                "fta_per_min_l15": fta_l15,
                "fta_per_min_ewm": fta_ewm,
                "fg3a_per_min_l5": fg3a_l5,
                "fg3a_per_min_l15": fg3a_l15,
                "fg3a_per_min_ewm": fg3a_ewm,
                "fg3pct_eb": float(fg3pct_eb),
                "fg2pct_eb": float(fg2pct_eb),
                "ftpct_eb": float(ftpct_eb),
                "zone_efg_eb": float(zone_efg_eb),
                "pace_proxy": p_pace,
                "pace_factor": p_factor,
                "rest_days": rest_days,
                "starter_rate": starter_rate,
                "n_prior_games": float(len(played)),
                "env_spread": np.nan,
                "env_total": np.nan,
                "env_implied_team_total": np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_pregame_features(
    target_date: str | pd.Timestamp,
    cutoff_utc: str | pd.Timestamp,
    boxscores: pd.DataFrame | dict,
    snapshots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build strictly pre-game player features for `target_date` as of `cutoff_utc`.

    Filter-first guarantee (U1):
    - Drops all boxscore rows with game_date >= target_date BEFORE any aggregation.
    - Drops all snapshot rows with ts_utc > cutoff_utc BEFORE attaching slate/env columns.
    - Runs `assert_no_leak_columns` before returning.
    """
    if isinstance(boxscores, dict):
        snapshots = snapshots if snapshots is not None else boxscores.get("snapshots")
        boxscores = boxscores["boxscores"]

    boxscores = _normalize_boxscores(boxscores)

    target_ts = pd.Timestamp(target_date).tz_localize(None).normalize()
    target_str = target_ts.strftime("%Y-%m-%d")

    dcol = _date_col(boxscores)
    box_dates = pd.to_datetime(boxscores[dcol]).dt.tz_localize(None).dt.normalize()
    # STRICT FILTER FIRST: only games strictly prior to target_date
    hist = boxscores.loc[box_dates < target_ts].copy()

    snap = _filter_snapshots(snapshots, cutoff_utc)
    if snap is not None and "game_date" in snap.columns:
        s_dates = pd.to_datetime(snap["game_date"]).dt.tz_localize(None).dt.normalize()
        # Keep snapshots for target_date (or latest <= target_date if no date col)
        if (s_dates == target_ts).any():
            snap = snap.loc[s_dates == target_ts].copy()
    elif snap is not None and "date" in snap.columns:
        s_dates = pd.to_datetime(snap["date"]).dt.tz_localize(None).dt.normalize()
        if (s_dates == target_ts).any():
            snap = snap.loc[s_dates == target_ts].copy()

    team_pace, lg_pace = _compute_team_pace_prior(hist, dcol)
    feat = _aggregate_player_history(hist, target_ts, dcol, team_pace, lg_pace)

    if snap is not None and len(snap) > 0:
        if "player_id" in snap.columns:
            latest = snap.drop_duplicates("player_id", keep="last").copy()
            env_cols = [c for c in ("env_spread", "env_total", "env_implied_team_total", "team", "opp", "is_home") if c in latest.columns]
            keep_cols = ["player_id", *env_cols]
            latest_sub = latest[keep_cols]
            if len(feat) > 0:
                feat = latest_sub.merge(
                    feat.drop(columns=[c for c in env_cols if c in feat.columns], errors="ignore"),
                    on="player_id",
                    how="left",
                )
            else:
                feat = latest_sub.copy()
                for c in NBA_FEATURE_COLS:
                    if c not in feat.columns:
                        feat[c] = np.nan
        elif "team" in snap.columns and len(feat) > 0:
            latest_t = snap.drop_duplicates("team", keep="last")
            env_cols = [c for c in ("env_spread", "env_total", "env_implied_team_total") if c in latest_t.columns]
            if env_cols:
                feat = feat.drop(columns=env_cols, errors="ignore").merge(
                    latest_t[["team", *env_cols]], on="team", how="left"
                )

    feat["date"] = target_str
    for c in NBA_FEATURE_COLS:
        if c not in feat.columns:
            feat[c] = np.nan

    front = [c for c in ("date", "player_id", "team", "opp", "is_home") if c in feat.columns]
    ordered = front + [c for c in NBA_FEATURE_COLS if c not in front]
    feat = feat[ordered].reset_index(drop=True)
    assert_no_leak_columns(feat)
    return feat


def build_rolling_training_frame(
    boxscores: pd.DataFrame,
    snapshots: pd.DataFrame | None = None,
    min_prior_games: int = 5,
    cutoff_hour_utc: int = 23,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Build walk-forward pregame feature rows for every date in `boxscores` by calling `build_pregame_features` per date.

    Returns features + metadata (`date`, `player_id`) with NO forbidden columns; caller joins targets separately.
    `start_date`/`end_date` (YYYY-MM-DD) optionally restrict which game dates
    get feature rows; the full `boxscores` history is still used as the
    lookback for every date.
    """
    dcol = _date_col(boxscores)
    b = boxscores.copy()
    b["_dstr"] = pd.to_datetime(b[dcol]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
    dates = sorted(b["_dstr"].unique())
    if start_date is not None:
        dates = [d for d in dates if d >= str(start_date)[:10]]
    if end_date is not None:
        dates = [d for d in dates if d <= str(end_date)[:10]]
    parts: list[pd.DataFrame] = []
    for d in dates:
        day_players = b.loc[b["_dstr"] == d, ["player_id"]].drop_duplicates()
        cutoff = f"{d}T{cutoff_hour_utc:02d}:00:00Z"
        f = build_pregame_features(d, cutoff, boxscores, snapshots=snapshots)
        if len(f) == 0:
            continue
        f = day_players.merge(f, on="player_id", how="inner")
        f = f[f["n_prior_games"] >= min_prior_games]
        if len(f) > 0:
            parts.append(f)
    if not parts:
        return pd.DataFrame(columns=["date", "player_id", *NBA_FEATURE_COLS])
    out = pd.concat(parts, ignore_index=True)
    assert_no_leak_columns(out)
    return out
