---
title: "The NHL boxscore has no powerPlayPoints, no PP assists and no PP TOI: derive PP points from play-by-play goals"
module: nhl/data/powerplay.py, nhl/data/flatten.py, mlb_core/data/cockpit_form.py, ev/grade_props.py
tags: [nhl, nhl-api, boxscore, play-by-play, power-play, pp_points, grading]
problem_type: integration_issue
category: integration-issues
date: 2026-11-01
---

## The trap

A design note claimed `api-web.nhle.com/v1/gamecenter/{gid}/boxscore` "returns `powerPlayPoints` on every skater row", so
`pp_points` could be graded with `p.get("powerPlayPoints", 0)` and built from `powerPlayPoints - powerPlayGoals`. It does not.
Live skater row keys (checked 2026-11-01): `assists, blockedShots, faceoffWinningPctg, giveaways, goals, hits, name, pim,
playerId, plusMinus, points, position, powerPlayGoals, shifts, sog, sweaterNumber, takeaways, toi`. Only `powerPlayGoals`.

Mapping `"pp_points": "powerPlayPoints"` into `ev/grade_props.NHL_SKATER` would have been silently catastrophic:
`p.get(f, 0) or 0` returns 0 for a missing key, so **every pp_points Over would grade as a loss**.

## What does exist

Play-by-play goals carry `situationCode` (4 digits: away goalie, away skaters, home skaters, home goalie), `scoringPlayerId`,
`assist1PlayerId`, `assist2PlayerId`. `nhl.data.powerplay.goal_is_power_play` applies one rule:

    effective_skaters(side) = skaters - (1 if that side's goalie digit is 0 else 0)      # a pulled goalie is an extra attacker
    power play  <=>  effective_for > effective_against ; shootout goals never count

Validated against the master's own `power_play_goals` over 150,893 skater rows of 2023-2025: **exact match, 0 differing
rows**. The naive `skaters_for > skaters_against` misses 2.3% (6v4 with the goalie pulled during a delayed penalty is a
power-play goal), so do not simplify it. The power-play share of all NHL points came out at 22.6% (2024-25).

## Where it lives now

- ingest: `flatten_game` writes `power_play_assists` / `power_play_points` on skater rows when the game has play-by-play,
  and **None (not 0)** when it does not.
- history: older master rows had no value. **Backfilled 2026-11-01** with `python -m nhl.data.powerplay --seasons 2021,...,2026 --write`
  (279,191 of 363,355 skater rows, 2021 onward; 2019-20 stay empty, the loader keeps two seasons). A game is filled only when its
  shots data reproduces the box's own `power_play_goals` for that game (100 preseason rows and 2 regular-season rows did not, so
  they stay NaN); every filled row satisfies `power_play_points == power_play_goals + power_play_assists <= points`. Pre-write copy:
  `NHL/backups/skater_boxscores_master_pre_pp_2026-11-01.csv`. `cockpit_form._fill_nhl_power_play` remains as the fallback and skips
  the shots reads once >= 99% of the kept seasons' regular-season rows are filled. The nightly upsert writes the columns for new games ONLY once the new image is deployed; until then the 2-day re-fetch window and new games arrive without them and the loader fallback (which reads the shots when the newest 14 days are unfilled) covers them.
- grading: `ev.grade_props.nhl_actuals` fetches the play-by-play and sets `pp_points` per player; if the fetch fails the stat
  is left off the record and the quote grades `ungradable`.

## Still not available (so not modelled)

Power-play TOI, PP1 vs PP2 unit deployment and even-strength / power-play shot splits: the boxscore has total TOI only.
They would need the shift-chart endpoint (built 2026-11-05: `nhl/data/toi_splits.py`, see `nhl-shiftcharts-and-official-toi-split.md`; the NHL also publishes EV/PP/SH TOI per game). `pp_points` therefore uses trailing PP points shrunk to a position prior, not a
PP-TOI-share model. BettingPros has no PP-points market in this repo's catalog (`bettingpros_props.SPORTS["NHL"]`), so the
live feed for it is ParlayAPI's `player_power_play_points` only, and only when a sweep pulls that market.
