---
title: build_model_features() team_map requires game_meta DataFrame with home_team/away_team
module: runners/build_game_features.py, runners/build_f5_features.py
tags: [feature-build, game-features, team-map, park-factor, nan]
problem_type: logic_error
category: logic-errors
date: 2026-05-24
---

## Problem

All team-level features (park factor, bullpen lookup by team) become NaN for every row. `build_model_features()` builds a `team_map` from the `game_meta` parameter — if `game_meta` is None or missing team columns, the map is empty.

## Symptoms

- `park_factor`, `bullpen_era`, and all team-derived features are NaN for all rows
- XGBoost fills them with `feature_means`, collapsing model signal on those features
- No error is raised — the join silently produces NaN

## Root Cause

`build_model_features(game_meta=game_meta_df)` relies on the caller passing a DataFrame with `game_pk`, `home_team`, `away_team` columns. If `game_meta` is not passed or the starter DataFrames don't carry team columns, `team_map = {}` and all team lookups return NaN.

## Solution

Extract `game_meta` from statcast directly before calling `build_model_features`:

```python
game_meta_df = statcast[["game_pk", "home_team", "away_team"]].drop_duplicates()
features = build_model_features(starters, game_meta=game_meta_df)
```

Applied in `build_game_features.py`. Verify any new feature builder that calls `build_model_features` passes `game_meta`.

## Prevention

When adding a new system that uses `build_model_features`: always pass `game_meta` and verify park_factor is populated in the output CSV (should be non-null for ~95% of rows).
