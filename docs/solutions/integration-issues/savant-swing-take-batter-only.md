---
title: Savant swing_take endpoint returns batter data only, regardless of player_type param
module: mlb_core/data/auxiliary_features.py, mlb_core/data/aux_joins.py
tags: [savant, swing-take, aux-joins, pitcher-features]
problem_type: integration_error
category: integration-issues
date: 2026-05-20
---

## Problem

Joining `swing_take_master.csv` on pitcher MLBAM IDs produces at most 1 match out of hundreds of pitchers.

## Symptoms

- `batter_runs_chase`, `batter_runs_heart`, `batter_runs_shadow`, `batter_runs_waste` are nearly all NaN for pitcher feature rows
- Spot checks show the swing_take rows are actual batters (Guillorme, Ozuna, Devers) not pitchers

## Root Cause

The `/leaderboard/swing-take` Savant endpoint returns batter data regardless of the `player_type` URL parameter:
- `player_type=pitcher` returns ~600 rows that are all batters
- `player_type=batter` returns the same set

The endpoint does not expose pitcher-equivalent swing/take run values. This is a Savant data limitation, not a code bug.

## Solution

`swing_take_master.csv` contains batter MLBAM IDs only. The `player_id` column in this dataset is the batter's MLBAM ID.

Wiring:
- `join_batter_aux(batter_col="batter")` includes swing_take — correct
- `join_pitcher_aux()` and `join_game_aux()` intentionally exclude swing_take — do not add it

## Prevention

Do not add swing_take to `join_pitcher_aux()` or `join_game_aux()`. If you need a pitcher-level chase/heart metric, look for a different Savant endpoint or derive it from pitch-level statcast data.
