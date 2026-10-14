---
title: "SportsBlaze NBA masters: `date` is the UTC date of the tip (76% differ from the ET game day), team box has NaN rebounds/turnovers before 2025"
module: nba/data/flatten.py, nba/props/data.py
tags: [sportsblaze, nba, dates, timezone, rest-days, back-to-back, leakage, box-scores]
problem_type: integration_issue
category: integration-issues
date: 2026-10-14
---

## Problem

Building leakage-safe NBA features from `NBA/*_master.csv` (found 2026-10-14 while building `nba/props/`):

1. **`date` in every master is the UTC date of the tip.** `flatten.py` stores `ev["date"][:10]` and the feed's `date` is a UTC
   timestamp (`2025-10-04T02:00:00.000Z` is a 10 pm ET Oct 3 game). Measured over all 9,186 games: the master date equals the
   UTC date of the tip 100% of the time and the **ET game day only 24.3%** (6,957 games land on the wrong day). Consequences: a
   10 pm ET game sits on the next calendar day, a team that plays a late game then an afternoon game shows **two games on one
   date** (104 team-dates), and rest days / back-to-backs derived from the stored date are wrong for a large share of rows.
   Anything keyed to ET game days (`ev/grade_props.py` game keys, the banker's ET-dated snapshots) will not join on this date.
2. **The tip time survives only in the schedule dumps** (`NBA/raw/schedule_{year}.json`, `events[].date`) and in the raw per-day
   boxscore cache (`NBA/raw/boxscores/{date}.json`). Game ids match the masters 1:1 (9,186/9,186).
3. **`team_boxscores_master.csv` has NaN `rebounds`/`turnovers` for seasons 2019-2024** (only 2025 is populated); player rows
   are complete and their point/FGA sums equal the team box exactly, so team totals must be summed from player rows.
4. 46 player rows have `played=True` with `0:00` minutes; "played" for modelling is `minutes > 0`. 2019-20 is truncated at the
   COVID break (971 regular-season games; the bubble falls outside the Oct 1 -> Jul 1 backfill window).
5. SportsBlaze abbreviations (`GS`, `NO`, `NY`, `SA`, `PHO`) are not our tricodes; `nba.teams.team_code` resolves all but `PHO`
   (`PHX`). Two different players are both named "Jaylin Williams" in the player table.

## Fix / convention

`nba/props/data.py` converts tip -> ET game day (`et_day`, `load_tips`, `gather_tips`), sorts by tip time, and sums team
totals from player rows. Do not use the masters' `date` as a game day. If `flatten.py` is ever touched, store the full tip
timestamp (additive column) so the schedule dumps stop being load-bearing.

## Prevention

Test any new date-derived feature (rest, B2B, "yesterday") against a late-tip game; `tests/test_nba_props_features.py::
test_et_game_day_not_utc_date` pins the behaviour.
