---
title: Statcast master is PA-level (one row per plate appearance), not pitch-level
module: mlb_core/data/statcast.py, runners/build_*_features.py
tags: [statcast, pa-level, pitch-number, feature-build, data-contract]
problem_type: logic_error
category: logic-errors
date: 2026-05-20
---

## Problem

Feature columns derived from `pitch_number == 1` or `len(group)` interpreted as pitch count are 100% NaN or meaningless. Code that expects pitch-sequence columns (`pitch_number`, `pitcher_days_since_prev_game`) fails silently.

## Symptoms

- `first_pitch_strike_pct_L5` is 100% NaN
- `pitch_count_mean_L5 ~ 24` (looks like batters faced, not pitches)
- `g[g["pitch_number"] == 1]` always returns zero rows
- `pitcher_days_since_prev_game` KeyError or all NaN

## Root Cause

`statcast_master.csv` stores one row per plate appearance outcome, not one row per pitch. Columns like `pitch_number` and `pitcher_days_since_prev_game` do not exist in the master.

Confirmation: 963,532 rows / 12,784 games = ~75.4 rows/game, matching the MLB average of ~76 PAs/game (not ~90 pitches/game).

## Solution

- `pitch_number == 1` approximation: use `g.groupby("at_bat_number", sort=False).head(1)` to get one row per PA (the first-pitch row per at-bat). Post-fix NaN rate drops to ~6%.
- `pitcher_days_since_prev_game`: derive from game dates after groupby:
  ```python
  starts["days_rest"] = starts.groupby("pitcher")["game_date"].diff().dt.days
  ```
- `len(group)` counts plate appearances, not pitches. It is approximately `batters_faced_L5` not `pitch_count_L5`.

Any feature requiring actual pitch sequences (e.g. pitch type per count, tunnel values) requires the full per-pitch Statcast feed from the Baseball Savant CSV bulk download — not `statcast_master.csv`.

## Prevention

When adding statcast-derived features: first check whether the column name appears in the master by running `pd.read_csv(key, nrows=1).columns`. If absent, derive it or use an approximation as above.
