"""Derive NBA first-basket outcomes from the mirrored ESPN play-by-play (NBA/hoopr/pbp).

WHY: book definitions of "first basket" differ -- some grade the first FIELD GOAL, others the first
POINT scored (a game can open on a free throw). The spike (2026-11-11) therefore pre-registered BOTH
settlement variants, and this table stores both, per game, so grading matches each book's own
definition where known:

  first_point_*   the first scoring play of the game, free throws included
  first_fg_*      the first made field goal (score_value >= 2, not a free throw)
  first_team_*    the same two variants for EACH team's first scorer (settles player_first_team_basket)

Input is the hoopR `espn_nba_pbp` frame as mirrored by nba.data.hoopr (group flipped extra -> core
2026-11-11 so the nightly mirror carries it). Columns read are the ones confirmed against the real
files by nba/shots/espn.py: scoring_play, shooting_play, type_text, text, athlete_id_1,
athlete_name_1, team_id, home_team_id, home_team_abbrev, away_team_abbrev, period_number, game_date,
season, season_type, and sequence_number when present.

Ordering: play-by-play is chronological as published. We sort by (game_id, period_number,
sequence_number) with a STABLE sort when sequence_number exists, and by (game_id, period_number)
stably otherwise -- never by the game clock, whose display format is not a safe sort key.

Method is classified from the play text/type: free_throw, three_pointer, dunk, layup, tip_in,
hook_shot, jump_shot, other. Everything below the I/O section is pure (DataFrame in, DataFrame out).
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

OUTCOME_PREFIX = "FirstScorer/outcomes/basketball_nba"
REGULAR, PLAYOFFS, PLAYIN = 2, 3, 5

OUTCOME_COLUMNS = [
    "game_id", "season", "season_type", "game_date",
    "home_team", "away_team",
    "first_point_player_id", "first_point_player", "first_point_team", "first_point_method",
    "first_point_period", "first_point_text",
    "first_fg_player_id", "first_fg_player", "first_fg_team", "first_fg_method",
    "first_fg_period", "first_fg_text",
    "first_team_point_home_player", "first_team_point_away_player",
    "first_team_fg_home_player", "first_team_fg_away_player",
]


def outcome_key(season: int) -> str:
    return f"{OUTCOME_PREFIX}/first_basket_{season}.parquet"


def classify_method(text: str, type_text: str = "") -> str:
    t = f"{type_text} {text}".lower()
    if "free throw" in t:
        return "free_throw"
    if "three point" in t or "three-point" in t:
        return "three_pointer"
    if "dunk" in t:
        return "dunk"
    if "layup" in t or "lay-up" in t or "driving lay" in t:
        return "layup"
    if "tip" in t:
        return "tip_in"
    if "hook" in t:
        return "hook_shot"
    if "jump" in t or "jumper" in t or "fadeaway" in t or "turnaround" in t:
        return "jump_shot"
    return "other"


def _team_abbrev(row) -> str | None:
    try:
        is_home = pd.to_numeric(pd.Series([row.get("team_id")]), errors="coerce").iloc[0] == \
            pd.to_numeric(pd.Series([row.get("home_team_id")]), errors="coerce").iloc[0]
    except Exception:  # noqa: BLE001 -- a malformed id must not kill the game
        return None
    return row.get("home_team_abbrev") if is_home else row.get("away_team_abbrev")


def _is_free_throw(row) -> bool:
    return "free throw" in f"{row.get('type_text', '')} {row.get('text', '')}".lower()


def _is_field_goal(row) -> bool:
    if _is_free_throw(row):
        return False
    sv = pd.to_numeric(pd.Series([row.get("score_value")]), errors="coerce").iloc[0]
    if pd.notna(sv):
        return sv >= 2
    return bool(row.get("shooting_play"))


def _scorer_fields(row, prefix: str) -> dict:
    return {
        f"{prefix}_player_id": row.get("athlete_id_1"),
        f"{prefix}_player": row.get("athlete_name_1"),
        f"{prefix}_team": _team_abbrev(row),
        f"{prefix}_method": classify_method(str(row.get("text", "")), str(row.get("type_text", ""))),
        f"{prefix}_period": row.get("period_number"),
        f"{prefix}_text": row.get("text"),
    }


def derive_first_basket(pbp: pd.DataFrame) -> pd.DataFrame:
    """hoopR play-by-play frame -> one row per game with both settlement variants (OUTCOME_COLUMNS).

    Games with no scoring play at all (not yet played / corrupt) are omitted, never invented."""
    if pbp is None or pbp.empty or "scoring_play" not in pbp.columns:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    p = pbp.copy()
    sort_keys = ["game_id", "period_number"] + (["sequence_number"] if "sequence_number" in p.columns else [])
    p = p.sort_values(sort_keys, kind="mergesort")
    scoring = p[p["scoring_play"].astype(bool)]
    rows = []
    for game_id, g in scoring.groupby("game_id", sort=True):
        first = g.iloc[0]
        fg_rows = g[g.apply(_is_field_goal, axis=1)]
        rec = {
            "game_id": str(game_id),
            "season": first.get("season"),
            "season_type": first.get("season_type"),
            "game_date": first.get("game_date"),
            "home_team": first.get("home_team_abbrev"),
            "away_team": first.get("away_team_abbrev"),
        }
        rec.update(_scorer_fields(first, "first_point"))
        if not fg_rows.empty:
            rec.update(_scorer_fields(fg_rows.iloc[0], "first_fg"))
        else:  # a game whose only scores were free throws has no FG variant; leave it null, do not guess
            for c in OUTCOME_COLUMNS:
                if c.startswith("first_fg_"):
                    rec[c] = None
        # per-team first scorers (player_first_team_basket), both variants
        for side, abbrev_col in (("home", "home_team_abbrev"), ("away", "away_team_abbrev")):
            team_rows = g[g.apply(lambda r: _team_abbrev(r) == first.get(abbrev_col), axis=1)]
            rec[f"first_team_point_{side}_player"] = team_rows.iloc[0].get("athlete_name_1") if not team_rows.empty else None
            team_fg = team_rows[team_rows.apply(_is_field_goal, axis=1)] if not team_rows.empty else team_rows
            rec[f"first_team_fg_{side}_player"] = team_fg.iloc[0].get("athlete_name_1") if not team_fg.empty else None
        rows.append(rec)
    return pd.DataFrame(rows, columns=OUTCOME_COLUMNS)


def build_outcomes(seasons, local_dir=None):
    """Read the mirrored pbp for `seasons` (hoopR end-year) and derive outcomes. Returns (df, season)."""
    from nba.data import hoopr
    frames = []
    for s in seasons:
        df = hoopr.read_dataset("pbp", [s], local_dir=local_dir)
        if not df.empty:
            frames.append(derive_first_basket(df))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUTCOME_COLUMNS)
