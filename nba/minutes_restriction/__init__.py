"""nba.minutes_restriction -- Arm S (structured) backtest harness for the minutes-restriction program.

Pre-registered design: ~/workspace/beezy/minutes-restriction-unders-spike-2026-11-11.md, sections 2 and 4.
Arm S predicts a player's realized minutes in a return / flagged game from STRUCTURED features only:
the official injury-report designation at decision time, games missed, baseline minutes, injury class,
and schedule flags. No text, no odds. This package is the harness only -- it builds cases, fits the
model walk-forward, and grades against the pre-registered bars. Running it against real banked data is
a separate, explicitly gated step (see scripts/minutes_arm_s_backtest.py, disabled without --execute).

CASE SCHEMA (the contract Lane 4 / Arm T joins on)
--------------------------------------------------
`cases.build_cases` returns one row per case with columns CASE_COLUMNS:

  case_id            str    f"{game_id}-{athlete_id}-{case_class}"; unique per case
  game_id            int    hoopR / ESPN game id of the case game
  player_id          int    ESPN athlete id (hoopR athlete_id)
  player             str    display name (box-score athlete_display_name)
  team               str    tricode of the player's team for the case game (nba.teams.team_code)
  opponent           str    tricode of the opposing team, when derivable, else None
  season             int    hoopR season (year the season ENDS; 2022 = 2021-22)
  season_start       int    year the season starts (2021 = 2021-22); the walk-forward split key
  season_label       str    e.g. "2021-22"
  game_date          date   ET calendar date of the case game
  tip_ts             ts ET  tip timestamp of the case game
  decision_report_ts ts ET  timestamp of the official report the designation was frozen at
                            (the last report >= DECISION_LEAD_MIN before tip); NaT when no eligible
                            report covers the player
  designation        str    official-report status at decision time: Out / Doubtful / Questionable /
                            Probable / Available, or None when the player is not listed on the
                            decision report (not listed is NOT "healthy"; it is missing)
  report_reason      str    raw reason text on the decision report row, else None
  reason_cat         str    the report's reason_cat (nba.data.nba_injury_report), else None
  games_missed       int    consecutive team games missed immediately before the case game
                            (for a game-2 case: the originating spell's count)
  missed_band        str    "0" | "1" | "2-3" | "4-9" | "10+"
  baseline_minutes   float  mean minutes over the last 8 played games before the spell / game
                            (>= 5 played games in the stint required; rotation = >= 15 mpg)
  realized_minutes   float  minutes actually played in the case game (the label)
  realized_ratio     float  realized_minutes / baseline_minutes (the regression target)
  case_class         str    "return" | "designation" | "game_two"
  is_game_two        bool   True for the secondary cohort: the game AFTER a return game
  is_injury_return   bool   True when the absence spell was injury-classified (lineage flag;
                            game-2 cases inherit it from their return case)
  absence_reason     str    modal raw DNP reason text during the spell, else None
  absence_reason_class str  modal box-score absence class (see cases.classify_absence_reason):
                            INJURY | ILLNESS | COACH | REST | PERSONAL | SUSPENSION |
                            NOT_WITH_TEAM | UNKNOWN | UNLISTED | NONE (no spell)
  injury_class       str    report reason_cat when a decision report exists, else the
                            absence_reason_class (the model's injury feature source)
  injury_location    str    light parse of the reason text (knee / ankle / ...), else None
  injury_side        str    "left" | "right" | None, parsed from the reason text
  is_back_to_back    bool   the team played a game the previous calendar day
  started            bool   the player started the case game (DESCRIPTIVE ONLY: starting lineups
                            are published after decision time, so `started` is never a model
                            feature -- see features.py)

B1 ADDENDUM (blowout.py, Argon game-state consult Nov 11, 2026): the B1 experiment adds
pregame closing-spread features (closing_spread, abs_closing_spread, abs_spread_x_starter,
team_pull_speed) in a SEPARATE feature matrix (build_features_b1); the base Arm S matrix
above is unchanged. B1's interaction uses `started` as the backtest proxy for the pregame
projected starter flag -- the timing caveat and the auto-kill bars (>= 0.15 min overall,
>= 0.35 min at |spread| >= 9.5) are documented in blowout.py.
"""
from __future__ import annotations

from nba.minutes_restriction.cases import CASE_COLUMNS, build_cases, select_designation
from nba.minutes_restriction.features import FEATURE_COLUMNS, build_features
from nba.minutes_restriction.arm_s import DEV_SEASONS, TEST_SEASONS, ArmSModel, walk_forward
from nba.minutes_restriction.grade import grade as grade_predictions, render_markdown
from nba.minutes_restriction.blowout import (
    B1_FEATURE_COLUMNS,
    ArmSB1Model,
    attach_closing_spread,
    build_features_b1,
    consensus_closing_spreads,
    grade_b1,
    render_markdown_b1,
    team_pull_speed,
    walk_forward_b1,
)

__all__ = [
    "CASE_COLUMNS",
    "FEATURE_COLUMNS",
    "DEV_SEASONS",
    "TEST_SEASONS",
    "ArmSModel",
    "build_cases",
    "build_features",
    "grade_predictions",
    "render_markdown",
    "select_designation",
    "walk_forward",
    # B1 blowout-minutes build (Argon game-state consult, Nov 11, 2026)
    "B1_FEATURE_COLUMNS",
    "ArmSB1Model",
    "attach_closing_spread",
    "build_features_b1",
    "consensus_closing_spreads",
    "grade_b1",
    "render_markdown_b1",
    "team_pull_speed",
    "walk_forward_b1",
]
