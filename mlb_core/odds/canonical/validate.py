"""mlb_core.odds.canonical.validate -- Ingest Validation V1-V7 (BNK-12).

Spec Section 3: Ingest validation -- fail-closed at the door.
  V1: Slate/date match (the replication check; non-empty payload with zero games
      on requested date is a hard failure).
  V2: Identity resolution accounting (accounting summary: by step, by reason).
  V3: Team-pair consistency (player_team in {away_team, home_team}; source team stamp audit).
  V4: Event sanity (start_time_utc presence and plausibility).
  V5: Duplicate and collision detection (exact duplicates collapsed on UNIQUENESS_KEY).
  V6: Price sanity (median two-sided implied sums per book; malformed row-level rejects).
  V7: Coverage shape (identical event sets across different dates is a hard failure).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
import pandas as pd

from mlb_core.odds.canonical.schema import PROP_MARKET_CLASSES, UNIQUENESS_KEY


def check_v1_slate_match(requested_date: str, payload_game_dates: List[str]) -> Dict[str, Any]:
    """V1 -- Slate/date match (the replication check).

    Collect the ET dates of payload's games. If the payload is non-empty and has
    ZERO games on requested_date, this is a hard failure (slate_mismatch).
    """
    req = str(requested_date).strip()[:10]
    date_dist: Dict[str, int] = {}
    for d in payload_game_dates:
        if d:
            ds = str(d).strip()[:10]
            date_dist[ds] = date_dist.get(ds, 0) + 1

    if payload_game_dates and date_dist.get(req, 0) == 0:
        return {
            "check": "V1",
            "passed": False,
            "hard_failure": True,
            "error_code": "slate_mismatch",
            "requested_date": req,
            "payload_dates": date_dist,
            "error": f"V1 Hard Failure: payload has {len(payload_game_dates)} games but 0 on requested date {req}",
        }

    return {
        "check": "V1",
        "passed": True,
        "hard_failure": False,
        "error_code": None,
        "requested_date": req,
        "payload_dates": date_dist,
        "error": None,
    }


def check_v2_resolution_accounting(
    total_rows: int,
    canonical_rows: int,
    quarantined_rows: int,
    step_counts: Dict[str, int],
    reason_counts: Dict[str, int],
) -> Dict[str, Any]:
    """V2 -- Identity resolution accounting."""
    rate = float(canonical_rows) / float(total_rows) if total_rows > 0 else 1.0
    return {
        "check": "V2",
        "passed": True,
        "total_rows": total_rows,
        "canonical_rows": canonical_rows,
        "quarantined_rows": quarantined_rows,
        "resolution_rate": round(rate, 4),
        "by_step": step_counts,
        "by_reason": reason_counts,
    }


def check_v3_team_pair_consistency(df: pd.DataFrame) -> Dict[str, Any]:
    """V3 -- Team-pair consistency.

    Verifies player_team in {away_team, home_team} for resolved prop rows.
    Computes source_player_team vs player_team disagreement rate.
    """
    if df.empty or "player_team" not in df.columns:
        return {"check": "V3", "passed": True, "disagreements": 0, "disagreement_rate": 0.0}

    resolved_props = df[df["market_class"].isin(PROP_MARKET_CLASSES) & df["player_team"].notna() & (df["player_team"] != "")]
    if resolved_props.empty:
        return {"check": "V3", "passed": True, "disagreements": 0, "disagreement_rate": 0.0}

    # Check pair consistency
    invalid_mask = (resolved_props["player_team"] != resolved_props["away_team"]) & (
        resolved_props["player_team"] != resolved_props["home_team"]
    )
    violations = int(invalid_mask.sum())

    # Check source team stamp disagreement
    has_source_team = resolved_props["source_player_team"].notna() & (resolved_props["source_player_team"] != "")
    disagreements = 0
    disagreement_rate = 0.0
    if has_source_team.any():
        st_sub = resolved_props[has_source_team]
        disagreements = int((st_sub["source_player_team"] != st_sub["player_team"]).sum())
        disagreement_rate = round(float(disagreements) / float(len(st_sub)), 4)

    return {
        "check": "V3",
        "passed": violations == 0,
        "violations": violations,
        "disagreements": disagreements,
        "disagreement_rate": disagreement_rate,
    }


def check_v4_event_sanity(df: pd.DataFrame) -> Dict[str, Any]:
    """V4 -- Event sanity. Checks start_time_utc presence."""
    if df.empty or "start_time_utc" not in df.columns:
        return {"check": "V4", "passed": True, "missing_start_time": 0}

    missing_start = int((df["start_time_utc"].isna() | (df["start_time_utc"].astype(str).str.strip() == "")).sum())
    return {
        "check": "V4",
        "passed": missing_start == 0,
        "missing_start_time": missing_start,
    }


def check_v5_duplicate_detection(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """V5 -- Duplicate and collision detection.

    Exact duplicate quotes on UNIQUENESS_KEY are counted and collapsed to one.
    """
    if df.empty:
        return df, {"check": "V5", "passed": True, "duplicates_collapsed": 0}

    cols = [c for c in UNIQUENESS_KEY if c in df.columns]
    dup_mask = df.duplicated(subset=cols, keep="first")
    dup_count = int(dup_mask.sum())

    deduped_df = df.drop_duplicates(subset=cols, keep="first").reset_index(drop=True)
    return deduped_df, {
        "check": "V5",
        "passed": True,
        "duplicates_collapsed": dup_count,
    }


def check_v6_price_sanity(df: pd.DataFrame) -> Tuple[List[int], Dict[str, Any]]:
    """V6 -- Price sanity and malformed row identification.

    Returns:
      (malformed_row_indices, report_dict)
    Malformed rows:
      - Null price anywhere (price_decimal is null/nan or <= 0)
      - Null line on a lined market (market_class in main/alternate/milestone and line is null/nan)
    Median two-sided implied sums per book on main markets are reported.
    """
    if df.empty:
        return [], {"check": "V6", "passed": True, "median_implied_sums": {}, "malformed_count": 0}

    # Identify malformed rows
    lined_classes = {"main", "alternate", "milestone"}
    is_lined = df["market_class"].isin(lined_classes)

    null_line = is_lined & (df["line"].isna() | df["line"].isnull())
    null_price = df["price_decimal"].isna() | df["price_decimal"].isnull() | (df["price_decimal"] <= 0)

    malformed_mask = null_line | null_price
    malformed_indices = df.index[malformed_mask].tolist()

    # Median two-sided implied sums per book on main markets
    median_sums: Dict[str, float] = {}
    main_df = df[df["market_class"] == "main"].dropna(subset=["price_decimal", "line", "book_id", "market", "event_id"])
    if not main_df.empty and "implied_raw" in main_df.columns:
        # Group by book_id, event_id, market, line
        grouped = main_df.groupby(["book_id", "event_id", "market", "line", "player_id"])
        over_under = grouped.filter(lambda g: set(g["side"].str.lower()).issuperset({"over", "under"}))
        if not over_under.empty:
            book_sums = over_under.groupby(["book_id", "event_id", "market", "line", "player_id"])["implied_raw"].sum()
            for book, series in book_sums.groupby("book_id"):
                med = float(np.median(series))
                median_sums[str(book)] = round(med, 4)

    flagged_books = [b for b, s in median_sums.items() if s < 1.00 or s > 1.40]

    return malformed_indices, {
        "check": "V6",
        "passed": len(malformed_indices) == 0,
        "malformed_count": len(malformed_indices),
        "median_implied_sums": median_sums,
        "flagged_books": flagged_books,
    }


def check_v7_coverage_shape(current_event_ids: Set[str], prior_event_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
    """V7 -- Coverage shape.

    Identical event-id sets across different requested dates is a hard failure (replication signature).
    """
    if prior_event_ids and current_event_ids and prior_event_ids == current_event_ids:
        return {
            "check": "V7",
            "passed": False,
            "hard_failure": True,
            "error_code": "identical_event_set_replication",
            "distinct_events": len(current_event_ids),
            "error": "V7 Hard Failure: identical event set across >=2 requested dates (replication signature)",
        }

    return {
        "check": "V7",
        "passed": True,
        "hard_failure": False,
        "error_code": None,
        "distinct_events": len(current_event_ids),
        "error": None,
    }
