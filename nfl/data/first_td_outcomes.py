"""Derive NFL first-touchdown-scorer outcomes from the mirrored nflverse play-by-play.

The bankable cousin of NBA first basket (spike 2026-11-11): ParlayAPI carries `player_1st_td`
(first touchdown scorer) at 1 credit per (event x market), and the outcomes already exist in the
banked nflverse PBP (NFL/nflverse/pbp) -- a game's first touchdown is the earliest play, in
(game_id, play_id) order, with touchdown == 1.

One row per game: the scorer (td_player_id / td_player_name from nflverse, which already resolves
pass / rush / return / recovery touchdowns to the scoring player), his team (td_team, falling back
to posteam), the method (pass / rush / return / other from the nflverse flags when present), and
the play context grading may need (week, season_type, quarter, play_id). Games with no touchdown
(field-goal-only games) are omitted, never invented.

Everything is pure (DataFrame in, DataFrame out); the runner in
scripts/derive_first_scorer_outcomes.py handles I/O through nfl.data.read.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

OUTCOME_PREFIX = "FirstScorer/outcomes/americanfootball_nfl"

OUTCOME_COLUMNS = [
    "game_id", "season", "week", "season_type", "game_date",
    "home_team", "away_team", "posteam",
    "first_td_player_id", "first_td_player", "first_td_team", "first_td_method",
    "first_td_qtr", "first_td_play_id", "first_td_yards_gained", "first_td_text",
]


def outcome_key(season: int) -> str:
    return f"{OUTCOME_PREFIX}/first_td_{season}.parquet"


def _flag(row, col) -> bool:
    try:
        return float(row.get(col) or 0) == 1.0
    except (TypeError, ValueError):
        return False


def classify_td_method(row) -> str:
    if _flag(row, "pass_touchdown"):
        return "pass"
    if _flag(row, "rush_touchdown"):
        return "rush"
    if _flag(row, "return_touchdown"):
        return "return"
    if _flag(row, "pass"):
        return "pass"
    if _flag(row, "rush"):
        return "rush"
    return "other"


def derive_first_td(pbp: pd.DataFrame) -> pd.DataFrame:
    """nflverse play-by-play frame -> one row per game: its first touchdown (OUTCOME_COLUMNS)."""
    if pbp is None or pbp.empty or "touchdown" not in pbp.columns:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    p = pbp.copy()
    p = p.sort_values(["game_id", "play_id"], kind="mergesort")
    tds = p[pd.to_numeric(p["touchdown"], errors="coerce").fillna(0) == 1]
    rows = []
    for game_id, g in tds.groupby("game_id", sort=True):
        first = g.iloc[0]
        rows.append({
            "game_id": str(game_id),
            "season": first.get("season"),
            "week": first.get("week"),
            "season_type": first.get("season_type"),
            "game_date": first.get("game_date"),
            "home_team": first.get("home_team"),
            "away_team": first.get("away_team"),
            "posteam": first.get("posteam"),
            "first_td_player_id": first.get("td_player_id"),
            "first_td_player": first.get("td_player_name"),
            "first_td_team": first.get("td_team") if pd.notna(first.get("td_team")) else first.get("posteam"),
            "first_td_method": classify_td_method(first),
            "first_td_qtr": first.get("qtr"),
            "first_td_play_id": first.get("play_id"),
            "first_td_yards_gained": first.get("yards_gained"),
            "first_td_text": first.get("desc"),
        })
    return pd.DataFrame(rows, columns=OUTCOME_COLUMNS)
