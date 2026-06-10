---
title: Savant pitch_arsenals CSV uses "pitcher" as MLBAM ID column, not "player_id"
module: mlb_core/data/savant_leaderboards.py
tags: [savant, leaderboards, pitch-arsenals, dedup, feature-build]
problem_type: logic_error
category: integration-issues
date: 2026-05-27
---

## Problem

The `pitch_arsenals` Savant leaderboard master was silently collapsing ~750 pitcher rows per season down to 1 row per season. Feature joins produced mostly NaN for arsenal features.

## Symptoms

- Arsenal master has ~12 rows total (one per season) instead of ~9,000+ (one per pitcher-season)
- `pitch_type_pct_FF`, `pitch_type_pct_SL`, etc. are nearly all NaN after join
- `_dedup_cols()` fell back to `["year"]`-only dedup, treating the entire season as one "player"

## Root Cause

Savant `pitch_arsenals` CSV header: `year,"last_name, first_name",pitcher,n_ff,n_si,...`

The MLBAM ID column is named `pitcher`, not `player_id` (which all other Savant datasets use). `_dedup_cols()` had no candidate for this column and fell back to `["year"]` as the sole dedup key.

## Solution

Fixed 2026-05-27 (commit 127a213):
- `_dedup_cols()` now includes `["year", "pitcher"]` as a dedup candidate before the year-only fallback
- Year-only fallback removed entirely — it silently destroys multi-row data and is never a valid fallback
- `_join_pitch_arsenals()` probes for `"pitcher"` column if `"player_id"` is absent:
  ```python
  id_col = next((c for c in ["player_id", "pitcher"] if c in ar.columns), None)
  ```
- When arsenal master has < 50 rows, log a warning and skip the join rather than silently filling all NaN

## Recovery

To rebuild a corrupted master without re-fetching Savant (per-year cache files are intact):
Call `/backfill-savant` with `{"dataset":"pitch_arsenals"}` and no `force` flag — all years are skipped (already cached) but the master is rebuilt from year files in ~5s. Expect `total_rows=0`, all years `-1`.
If year files are also bad (< 50 rows), add `"force": true` to re-fetch Savant.

## Prevention

When adding a new Savant leaderboard dataset: verify the MLBAM ID column name in a sample CSV before writing the dedup logic. Do not assume it is `player_id`.
