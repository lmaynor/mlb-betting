"""U1 leak-proof pre-game feature assembly and LabelStore contract.

Guarantees enforced structurally:
  1. Forbidden column gate (`validate_feature_frame` / `assert_clean`): blocks any
     column matching `*_post`, `actual_*`, or `outcome_*` (case-insensitive).
  2. Strict time-travel filters:
     - `filter_history(df, target_date)`: retains ONLY rows with `date < target_date`.
     - `filter_snapshots(df, cutoff_utc)`: retains ONLY rows with `snapshot_ts_utc <= cutoff_utc`.
  3. `build_pregame_features` is a pure function that filters `history` and
     `snapshots` internally before any aggregation and never touches `LabelStore`.
  4. `LabelStore` + `join_labels` is the ONLY join path for target/outcome columns
     and is strictly for training and post-game grading.

Note: On merge, this module's feature assembly rebinds to Cluster (a)'s shared
backbone module.
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

_NON_STAT_COLS = frozenset({
    "player_id", "game_id", "team_id", "opp_id", "season_year",
    "sweater", "jersey",
})


def is_forbidden_column(col: str) -> bool:
    """Return True if column name violates the post-game/outcome naming gate."""
    low = str(col).strip().lower()
    return low.endswith("_post") or low.startswith("actual_") or low.startswith("outcome_")


def validate_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Raise ValueError if `df` contains any forbidden post-game/actual/outcome column."""
    offenders = [str(c) for c in df.columns if is_forbidden_column(str(c))]
    if offenders:
        raise ValueError(
            f"Forbidden post-game/outcome columns in feature frame: {offenders}"
        )
    return df


assert_clean = validate_feature_frame


def _parse_date_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.tz_localize(None).dt.normalize()


def _parse_target_date(target_date) -> pd.Timestamp:
    ts = pd.Timestamp(target_date)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _parse_utc_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def _parse_cutoff_utc(cutoff_utc) -> pd.Timestamp:
    ts = pd.Timestamp(cutoff_utc)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def filter_history(
    df: pd.DataFrame,
    target_date,
    date_col: str = "date",
) -> pd.DataFrame:
    """Keep ONLY rows with `date < target_date` (strictly prior to target_date)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=[] if df is None else df.columns)
    cutoff = _parse_target_date(target_date)
    dates = _parse_date_series(df[date_col])
    mask = dates.notna() & (dates < cutoff)
    return df.loc[mask].copy()


def filter_snapshots(
    df: pd.DataFrame,
    cutoff_utc,
    ts_col: str = "snapshot_ts_utc",
) -> pd.DataFrame:
    """Keep ONLY snapshot rows with `ts <= cutoff_utc`."""
    if df is None or df.empty:
        return pd.DataFrame(columns=[] if df is None else df.columns)
    cutoff = _parse_cutoff_utc(cutoff_utc)
    ts = _parse_utc_series(df[ts_col])
    mask = ts.notna() & (ts <= cutoff)
    return df.loc[mask].copy()


def _stat_columns(df: pd.DataFrame, exclude: Iterable[str]) -> list[str]:
    excl = set(exclude) | _NON_STAT_COLS
    cols = []
    for c in df.columns:
        if c in excl or is_forbidden_column(str(c)):
            continue
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            continue
        if pd.api.types.is_numeric_dtype(s):
            cols.append(c)
    return cols


def build_pregame_features(
    target_date,
    cutoff_utc,
    *,
    slate: pd.DataFrame,
    history: pd.DataFrame,
    snapshots: Optional[pd.DataFrame] = None,
    date_col: str = "date",
    ts_col: str = "snapshot_ts_utc",
) -> pd.DataFrame:
    """PURE pre-game feature builder.

    It is structurally impossible for this function to read a box-score row with
    `date >= target_date` or a snapshot row with `ts > cutoff_utc`: both inputs
    are filtered immediately on entry via `filter_history` and `filter_snapshots`,
    and `LabelStore` is never referenced.
    """
    if slate is None:
        raise ValueError("slate must be a DataFrame")
    out = slate.copy()
    validate_feature_frame(out)
    if out.empty:
        out["games_played"] = pd.Series(dtype="int64")
        return validate_feature_frame(out)

    # Structural time-travel barrier: raw `history` and `snapshots` are never used after filtering.
    prior = filter_history(history, target_date, date_col=date_col)
    snaps = filter_snapshots(snapshots, cutoff_utc, ts_col=ts_col) if snapshots is not None else None

    stat_cols = _stat_columns(prior, exclude=(date_col, "player_id"))
    if not prior.empty and "player_id" in prior.columns:
        sort_cols = ["player_id", date_col]
        if "game_id" in prior.columns:
            sort_cols.append("game_id")
        prior_sorted = prior.sort_values(sort_cols, kind="mergesort")
        slate_pids = set(out["player_id"].dropna().tolist())
        prior_sub = prior_sorted[prior_sorted["player_id"].isin(slate_pids)]

        grp = prior_sub.groupby("player_id", sort=False)
        agg_parts = {"games_played": grp.size().astype("int64")}
        if stat_cols:
            tail5 = prior_sub.groupby("player_id", sort=False).tail(5)
            tail15 = prior_sub.groupby("player_id", sort=False).tail(15)
            mean5 = tail5.groupby("player_id", sort=False)[stat_cols].mean()
            mean15 = tail15.groupby("player_id", sort=False)[stat_cols].mean()
            for col in stat_cols:
                agg_parts[f"{col}_l5"] = mean5[col]
                agg_parts[f"{col}_l15"] = mean15[col]
        agg_df = pd.DataFrame(agg_parts).reset_index()
        out = out.merge(agg_df, on="player_id", how="left")
        out["games_played"] = out["games_played"].fillna(0).astype("int64")
    else:
        out["games_played"] = 0
        for col in stat_cols:
            out[f"{col}_l5"] = np.nan
            out[f"{col}_l15"] = np.nan

    if snaps is not None and not snaps.empty and "player_id" in snaps.columns:
        snaps_sorted = snaps.sort_values(ts_col, kind="mergesort")
        latest_snaps = snaps_sorted.drop_duplicates(subset=["player_id"], keep="last").copy()
        extra_cols = [
            c for c in latest_snaps.columns
            if c != "player_id" and c not in out.columns and not is_forbidden_column(str(c))
        ]
        if extra_cols:
            out = out.merge(latest_snaps[["player_id"] + extra_cols], on="player_id", how="left")

    return validate_feature_frame(out)


class LabelStore:
    """Separate container holding post-game labels (TRAINING / GRADING ONLY).

    Never passed to or imported by `build_pregame_features`.
    """

    def __init__(self, labels: pd.DataFrame) -> None:
        if labels is None:
            raise ValueError("labels must be a DataFrame")
        self._labels = labels.copy()

    @property
    def frame(self) -> pd.DataFrame:
        return self._labels.copy()

    def join_labels(
        self,
        feature_df: pd.DataFrame,
        on: Optional[Iterable[str]] = None,
        how: str = "left",
    ) -> pd.DataFrame:
        """TRAINING / GRADING ONLY: attach outcome labels to a feature matrix."""
        return join_labels(feature_df, self, on=on, how=how)


def join_labels(
    feature_df: pd.DataFrame,
    labels: LabelStore | pd.DataFrame,
    on: Optional[Iterable[str]] = None,
    how: str = "left",
) -> pd.DataFrame:
    """TRAINING / GRADING ONLY: join outcome labels onto a feature frame.

    This is the single authorized join path between pre-game features and
    post-game outcomes.
    """
    lbl_df = labels.frame if isinstance(labels, LabelStore) else labels.copy()
    if on is None:
        candidates = [k for k in ("player_id", "game_id", "date") if k in feature_df.columns and k in lbl_df.columns]
        if not candidates:
            raise ValueError("Cannot infer join keys between feature_df and labels")
        join_keys = candidates
    else:
        join_keys = list(on)
    if not lbl_df.empty:
        lbl_df = lbl_df.drop_duplicates(subset=join_keys, keep="last")
    return feature_df.merge(lbl_df, on=join_keys, how=how)
