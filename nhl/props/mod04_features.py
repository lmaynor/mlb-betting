"""U1 feature-store plumbing for MOD-04 shadow pipelines (pure, zero I/O, zero GCS).

Provides:
    FORBIDDEN_FEATURE_PATTERNS  glob patterns banned from any model feature matrix
    assert_feature_schema(df)   gate raising ValueError if any column matches a forbidden pattern
    build_pregame_features(...) pure point-in-time filtering of raw frames before feature assembly
    join_labels(features, ...)  the ONLY sanctioned Label Store join (training / grading only)
"""
from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Mapping

import pandas as pd

FORBIDDEN_FEATURE_PATTERNS: tuple[str, ...] = ("*_post", "actual_*", "outcome_*")

# Timestamp columns used by pre-game snapshot / lineup feeds (never game start times like `start_utc`).
_SNAPSHOT_TS_COLS: tuple[str, ...] = (
    "fetched_at",
    "fetched_utc",
    "snapshot_ts",
    "as_of_utc",
    "updated_at",
    "timestamp",
    "ts",
)

# Keys representing box-score / realized-game tables whose rows on or after target_date must be dropped.
_BOX_FRAME_KEYS: frozenset[str] = frozenset({
    "skater_box",
    "goalie_box",
    "skaters",
    "goalies",
    "toi_splits",
    "xg_skater",
    "xg_goalie",
    "xg_team",
})


def assert_feature_schema(df: pd.DataFrame) -> None:
    """Raise ValueError if any column in `df` matches FORBIDDEN_FEATURE_PATTERNS (case-insensitive fnmatch).

    `xgfeat` builds legitimate `*_post` columns used strictly as backward as-of lookups for later dates;
    this gate guarantees they (and any `actual_*` / `outcome_*` label columns) never enter a feature matrix.
    """
    bad: list[str] = []
    lower_pats = tuple(p.lower() for p in FORBIDDEN_FEATURE_PATTERNS)
    for col in df.columns:
        name = str(col)
        low = name.lower()
        if any(fnmatch.fnmatchcase(low, pat) for pat in lower_pats):
            bad.append(name)
    if bad:
        raise ValueError(
            f"Forbidden feature column(s) matched {FORBIDDEN_FEATURE_PATTERNS}: {bad}"
        )


def _date_col(df: pd.DataFrame) -> str | None:
    for c in ("date", "game_date"):
        if c in df.columns:
            return c
    return None


def _to_utc_timestamp(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def build_pregame_features(
    target_date: str,
    cutoff_utc: str | datetime | pd.Timestamp | None,
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Pure point-in-time filter of already-loaded raw frames prior to feature assembly.

    - Copies every input DataFrame (never mutates `frames` or its DataFrames).
    - Drops every box-score row with game date >= `target_date` (and future `games` rows with date > `target_date`).
    - Drops any snapshot/lineup row timestamped strictly after `cutoff_utc` when a snapshot timestamp column exists.
    - Runs `assert_feature_schema` on every output DataFrame.
    - Returns a new dict with unchanged schemas otherwise. No I/O, no globals.
    """
    target_str = str(target_date)[:10]
    cutoff_ts = _to_utc_timestamp(cutoff_utc) if cutoff_utc is not None else None

    out: dict[str, pd.DataFrame] = {}
    for key, raw_df in frames.items():
        df = raw_df.copy()
        dcol = _date_col(df)

        is_box = key in _BOX_FRAME_KEYS or "box" in key.lower()
        ts_cols = [c for c in _SNAPSHOT_TS_COLS if c in df.columns]

        if is_box and dcol is not None:
            df = df[df[dcol].astype(str).str[:10] < target_str].copy()
        elif key == "games" and dcol is not None:
            df = df[df[dcol].astype(str).str[:10] <= target_str].copy()
        elif ts_cols and dcol is not None:
            df = df[df[dcol].astype(str).str[:10] <= target_str].copy()

        if cutoff_ts is not None and ts_cols:
            for tcol in ts_cols:
                parsed = pd.to_datetime(df[tcol], utc=True, errors="coerce")
                df = df[parsed.isna() | (parsed <= cutoff_ts)].copy()

        assert_feature_schema(df)
        out[key] = df.reset_index(drop=True)

    return out


def join_labels(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    how: str = "inner",
) -> pd.DataFrame:
    """TRAINING / GRADING ONLY: the single sanctioned Label Store join on `(game_id, player_id)`.

    Must never be called on an inference/serving path. Copies inputs before merging so callers'
    frames are not mutated.
    """
    join_keys = ["game_id", "player_id"]
    for name, frame in (("features", features), ("labels", labels)):
        missing = [k for k in join_keys if k not in frame.columns]
        if missing:
            raise ValueError(f"{name} is missing required join key(s): {missing}")
    return features.copy().merge(labels.copy(), on=join_keys, how=how)
