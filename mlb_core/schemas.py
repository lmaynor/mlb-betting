"""
mlb_core/schemas.py — DataFrame schema contracts for key pipeline handoffs.

validate_df() is called at GCS read/write boundaries to surface column
errors at the point of failure rather than 50 lines downstream.

No external dependencies — stdlib + pandas only.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Schema dict keys:
#   required_cols:  list[str]  — must be present
#   no_all_nan:     list[str]  — these columns must not be entirely NaN
#   min_rows:       int        — minimum row count (default 1)
#   datetime_cols:  list[str]  — must be parseable as datetime

SCHEMAS: dict[str, dict] = {
    "statcast_raw": {
        "required_cols": [
            "game_pk", "game_date", "pitcher", "batter",
            "events", "home_team", "away_team", "inning_topbot",
        ],
        "min_rows": 100,
        "datetime_cols": ["game_date"],
    },
    "scoring_master": {
        "required_cols": ["game_pk", "inning", "half", "runs"],
        "min_rows": 1,
    },
    # Per-system model_features — require the target col and game_date
    "nrfi_model_features": {
        "required_cols": ["game_pk", "game_date", "yrfi"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    "f5_model_features": {
        # Was "home_win" -- verified against the real build_f5_features.py
        # output (2026-08-19): the actual target column is "home_wins_f5".
        # This schema was never wired to a validate_df() call anywhere, so
        # the mismatch was never caught; the same wrong name was already
        # found and fixed in tune_hyperparams.py/registry.py (audit finding
        # C3.5) but this file was missed. See docs/audits/
        # 2026-08-19_feature_data_pipeline_review.md finding 2.8.
        "required_cols": ["game_pk", "game_date", "home_wins_f5"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    "hr_model_features": {
        # Was "hr_flag" -- verified against the real build_hr_features.py
        # output (2026-08-19): the actual column is "hr" (see build_player_game
        # / build_features' own "HR rate: {df.get('hr', ...)}" log line).
        # Same never-wired-up story as f5_model_features above.
        "required_cols": ["game_pk", "game_date", "hr", "batter"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    "k_model_features": {
        "required_cols": ["game_pk", "game_date", "starter_ks"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    "batter_hits_model_features": {
        "required_cols": ["game_pk", "game_date", "batter_hits", "batter"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    # Added 2026-08-19: BATTER_TB is a fully trained NegBin count model (same
    # shape as batter_hits_model_features above) but had no schema entry at
    # all, so a malformed build would pass validate_df silently where the
    # same defect in BATTER_HITS would be caught. See docs/audits/
    # 2026-08-19_feature_data_pipeline_review.md finding 2.8.
    "batter_tb_model_features": {
        "required_cols": ["game_pk", "game_date", "batter_total_bases", "batter"],
        "no_all_nan": ["game_date"],
        "min_rows": 100,
    },
    "game_model_features": {
        "required_cols": [
            "game_pk", "game_date", "home_win",
            "home_k_pct_L3", "away_k_pct_L3",
            "home_bullpen_xwoba_L14", "away_bullpen_xwoba_L14",
        ],
        "no_all_nan": ["game_date", "home_win", "home_bullpen_xwoba_L14"],
        "min_rows": 100,
    },
}


def validate_df(
    df: pd.DataFrame,
    schema_key: str,
    context: str = "",
    raise_on_error: bool = False,
) -> list[str]:
    """
    Validate df against named schema. Returns list of error strings.
    Logs each error as WARNING. If raise_on_error=True, raises ValueError
    with all errors joined.

    Usage:
        errors = validate_df(sc, "statcast_raw", context="GAME build")
        # errors is empty on success; non-empty = problems found but pipeline continues

        validate_df(model_feats, "game_model_features",
                    context="GAME build output", raise_on_error=True)
        # raises ValueError if any check fails

    Args:
        df:             DataFrame to validate.
        schema_key:     Key into SCHEMAS dict.
        context:        Free-form label prepended to error messages (e.g. "GAME build").
        raise_on_error: If True, raises ValueError when any check fails.

    Returns:
        List of error strings. Empty list means validation passed.
    """
    schema = SCHEMAS.get(schema_key)
    if schema is None:
        logger.debug("validate_df: no schema for key %r — skipping", schema_key)
        return []

    prefix = f"[{context}] " if context else ""
    errors: list[str] = []

    if df is None or not hasattr(df, "columns"):
        errors.append(f"{prefix}DataFrame is None or not a DataFrame")
        if raise_on_error:
            raise ValueError("\n".join(errors))
        return errors

    # 1. Row count
    min_rows = schema.get("min_rows", 1)
    if len(df) < min_rows:
        errors.append(f"{prefix}too few rows: {len(df)} < {min_rows}")

    # 2. Required columns
    missing = [c for c in schema.get("required_cols", []) if c not in df.columns]
    if missing:
        errors.append(f"{prefix}missing required columns: {missing}")

    # 3. No-all-NaN columns (only check if column exists)
    for col in schema.get("no_all_nan", []):
        if col in df.columns and df[col].isna().all():
            errors.append(f"{prefix}column '{col}' is entirely NaN")

    # 4. Datetime parseable
    for col in schema.get("datetime_cols", []):
        if col in df.columns:
            try:
                pd.to_datetime(df[col], errors="raise")
            except Exception:
                errors.append(f"{prefix}column '{col}' is not parseable as datetime")

    for err in errors:
        logger.warning("schema violation: %s", err)

    if raise_on_error and errors:
        raise ValueError(
            f"Schema validation failed for '{schema_key}':\n" + "\n".join(errors)
        )

    return errors
