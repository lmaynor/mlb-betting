"""mlb_core.odds.canonical.schema -- Canonical Odds Schema (BNK-12).

Defines the exact column names, order, types, empty frame constructor,
and published frame validation invariants per spec 2.1 and 5.5.
"""
from __future__ import annotations

from typing import Dict, List, Optional
import pandas as pd

CANONICAL_COLUMNS: List[str] = [
    "league",
    "event_id",
    "game_date_et",
    "start_time_utc",
    "away_team",
    "home_team",
    "player_id",
    "player_name",
    "player_team",
    "market",
    "market_class",
    "line",
    "side",
    "book_id",
    "price_american",
    "price_decimal",
    "implied_raw",
    "bid_decimal",
    "ask_decimal",
    "mid_prob",
    "is_main_line",
    "observed_at",
    "source_updated_at",
    "price_kind",
    "source",
    "source_ref",
    "source_player_id",
    "source_player_team",
    "source_extras",
]

UNIQUENESS_KEY: List[str] = [
    "league",
    "event_id",
    "player_id",
    "market",
    "line",
    "side",
    "book_id",
    "observed_at",
]

PROP_MARKET_CLASSES = frozenset({"main", "alternate", "milestone", "scorer"})

CANONICAL_DTYPES: Dict[str, str] = {
    "league": "object",
    "event_id": "object",
    "game_date_et": "object",
    "start_time_utc": "object",
    "away_team": "object",
    "home_team": "object",
    "player_id": "object",
    "player_name": "object",
    "player_team": "object",
    "market": "object",
    "market_class": "object",
    "line": "float64",
    "side": "object",
    "book_id": "object",
    "price_american": "float64",  # nullable float so NaNs are preserved
    "price_decimal": "float64",
    "implied_raw": "float64",
    "bid_decimal": "float64",
    "ask_decimal": "float64",
    "mid_prob": "float64",
    "is_main_line": "bool",
    "observed_at": "object",
    "source_updated_at": "object",
    "price_kind": "object",
    "source": "object",
    "source_ref": "object",
    "source_player_id": "object",
    "source_player_team": "object",
    "source_extras": "object",
}


def empty_frame() -> pd.DataFrame:
    """Return an empty DataFrame initialized with CANONICAL_COLUMNS and dtypes."""
    df = pd.DataFrame(columns=CANONICAL_COLUMNS)
    for col, dtype in CANONICAL_DTYPES.items():
        df[col] = df[col].astype(dtype)
    return df


def validate_published_frame(df: pd.DataFrame) -> None:
    """Enforce the spec 5.5 item-5 invariants on published canonical frames.

    Invariants:
      1. Zero duplicate uniqueness keys:
         (league, event_id, player_id, market, line, side, book_id, observed_at)
      2. player_team in event pair {away_team, home_team} (when player_team is present)
      3. Zero milestone rows carrying side 'no'
      4. Zero prop rows with null player_id
    """
    if df is None or df.empty:
        return

    # Check columns presence
    missing_cols = set(CANONICAL_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Canonical frame missing required columns: {missing_cols}")

    # Invariant 1: No duplicate uniqueness keys
    # Treat NaN as equivalent in uniqueness subset
    dup_mask = df.duplicated(subset=UNIQUENESS_KEY, keep=False)
    if dup_mask.any():
        num_dups = int(dup_mask.sum())
        sample = df.loc[dup_mask, UNIQUENESS_KEY].head(5).to_dict(orient="records")
        raise ValueError(f"Invariant violation: {num_dups} duplicate uniqueness keys found. Samples: {sample}")

    # Invariant 2: player_team in event pair {away_team, home_team}
    has_pt = df["player_team"].notna() & (df["player_team"] != "")
    if has_pt.any():
        pt_sub = df[has_pt]
        invalid_team_mask = (pt_sub["player_team"] != pt_sub["away_team"]) & (pt_sub["player_team"] != pt_sub["home_team"])
        if invalid_team_mask.any():
            violators = pt_sub[invalid_team_mask][["event_id", "player_name", "player_team", "away_team", "home_team"]]
            raise ValueError(
                f"Invariant violation: {len(violators)} rows have player_team outside event pair. Samples: {violators.head(3).to_dict(orient='records')}"
            )

    # Invariant 3: Zero milestone rows with side 'no'
    is_milestone = df["market_class"] == "milestone"
    if is_milestone.any():
        milestone_sub = df[is_milestone]
        no_side = milestone_sub["side"].astype(str).str.lower() == "no"
        if no_side.any():
            violators = milestone_sub[no_side][["event_id", "player_name", "market", "side"]]
            raise ValueError(
                f"Invariant violation: {len(violators)} milestone rows have side 'no'. Samples: {violators.head(3).to_dict(orient='records')}"
            )

    # Invariant 4: Zero prop rows with null player_id
    is_prop = df["market_class"].isin(PROP_MARKET_CLASSES)
    if is_prop.any():
        prop_sub = df[is_prop]
        null_pid = prop_sub["player_id"].isna() | (prop_sub["player_id"].astype(str).str.strip() == "")
        if null_pid.any():
            violators = prop_sub[null_pid][["event_id", "player_name", "market", "market_class"]]
            raise ValueError(
                f"Invariant violation: {len(violators)} prop rows have null player_id. Samples: {violators.head(3).to_dict(orient='records')}"
            )
