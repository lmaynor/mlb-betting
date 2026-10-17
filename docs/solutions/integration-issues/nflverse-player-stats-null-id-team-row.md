---
title: "nflverse player_stats carries one team-level row per week with a null player_id"
module: nfl/data/contracts.py, nfl/features/props_features.py
tags: [nflverse, player_stats, data-contracts, grain]
problem_type: integration_issue
category: integration-issues
date: 2026-10-16
---

## Problem

A naive grain check on the mirrored `player_stats` (`(player_id, game_id)` not null and unique) fails on every
season: each week has exactly ONE extra row whose `player_id`, `player_name`, `player_display_name` and `position` are
all null (22 rows in each of 2022-2025 -- one per week incl. the postseason -- and 5 so far in 2026).

## What the row is

A team-level bucket, not a player: every offensive stat is 0 and only the team-level misc columns are populated
(`penalties`, `penalty_yards`, sometimes `def_safeties`). It is attached to one team of one game per week. It does
not disturb the team-vs-player reconciliation (sums of passing/rushing/receiving yards, carries and attempts match
`team_stats` EXACTLY over every team-game), and `props_features` already drops it (`position` is not a skill position).

## Rule

`nfl.data.contracts.check_grain` tolerates null-id rows ONLY when every offensive counting column is zero
(`OFFENSE_ACT_COLS`) and reports them as `phantom_zero_rows`. A null-id row that carries offensive stats FAILS the
contract. Do not `dropna(subset=["player_id"])` inside a loader without that guard: it would hide the one case that
means something is wrong. Any new consumer that groups `player_stats` by player must filter the row (or never
see it because it filters by position).

## Verified facts used by the other contract thresholds (2023-2026 real mirror)

- `fantasy_points_ppr` equals the published formula for offensive players in 99.97% of rows (20,582 rows, 2023-2026), and 99.90%
  over all positions (the residual is defenders with defensive TDs, outside the formula's skill-position scope).
- `(player_id, game_id)` has no duplicates; `(team, game_id)` in `team_stats` has none; `schedules.game_id` is unique.
- nflverse publishes finished-game stats within hours (stats/pbp 09:00 UTC daily plus after each game window, injuries
  and rosters 07:00 UTC -- from the nflverse-data workflow schedule), so a game is "overdue" in the mirror from 1 day
  (warning) / 2 days (failure) after its `gameday`.
