---
title: "NBA shot charts: where shot coordinates come from, what the supplied file is, and why it needs a licensing decision"
module: nba/shots/, beezy-vip/app/edge/shot-chart.tsx
tags: [nba, shot-chart, shot-coordinates, licensing, stats-nba, espn, hoopr, edge]
problem_type: integration_issue
category: integration-issues
date: 2026-11-03
---

## Sources checked (2026-11-03)

| Source | Shot x/y | In our bucket | Terms |
|---|---|---|---|
| `nba_api` / stats.nba.com `ShotChartDetail` | yes | no | README: data is subject to the NBA.com Terms of Use; stats.nba.com does not answer many cloud IPs (GCP included). NOT used. |
| ESPN play-by-play via the hoopR / sportsdataverse release `espn_nba_pbp/play_by_play_{season}.parquet` | yes, every FGA (full-court feet, x in -47..47, y in -25..25, hoop about (41, 0): ESPN's own "N-foot" text matches to 0.4 ft MAE) | raw file no (`nba/data/hoopr.py` defines `pbp` as an "extra" dataset that was never backfilled); built artifacts for 2025-26 and 2026-27 yes | repo is MIT, rows are ESPN's undocumented-API data, no licence from ESPN. Same risk class as the ESPN box scores the site already serves. Used for 2025-26 / 2026-27 via `nba/shots/espn.py`. |
| `NBA_2004_2025_Shots.csv` (user-supplied 2026-11-03) | yes | built artifacts only (uploaded 2026-11-03 with the owner's OK) | Column layout, NBA game ids and zone names are the stats.nba.com `ShotChartDetail` lineage, so the NBA.com terms question applies. The owner chose to use it. |
| SportsBlaze / BettingPros | no | yes | n/a |
| Licensed feed (Sportradar, Second Spectrum / Genius, Stats Perform, BigDataBall) | yes | no | the clean route; needs a contract |

## What `nba/shots` does with the supplied file

`python -m nba.shots.build --csv NBA_2004_2025_Shots.csv --seasons 2022 2023 2024` writes `NBA/shots/shots_{year}.parquet` (one row per
attempt, ~1.6 MB a season) and `NBA/shots/league_{year}.json` (14 zones, ~330 hex cells, shot-type groups, per-defence zone tables).
Season arguments are START years (2024 = 2024-25); the file's `SEASON_1` is the END year.

Facts about the file, verified on the real data:
- Regular season only (1,230 games a season; 1,059 in 2019-20), the last game is 2025-04-13: **no 2025-26 or 2026-27 shots**, so every
  current player's chart is last season's, and rookies since then have none ("No shot data"). The UI says so.
- Frame: `LOC_X` lateral feet -25..25 (negative = the shooter's RIGHT), `LOC_Y` feet from the baseline 0..94, hoop at (0, 5.25). The
  builder keeps y <= 40 ft (heaves are not charted).
- ~0.01% of attempts repeat (same player, game, clock and spot): dropped on that key.
- Ids are NBA ids; the cockpit's are ESPN ids. They do not join, so the endpoint takes the player NAME only
  (`mlb_core.data.player_names.norm_name`); namesakes resolve to whoever shot most that season.
- Tricodes match `nba/teams.py` (PHX, NOP, UTA, GSW, ...); the endpoint folds ESPN abbreviations (NY, GS, SA, UTAH, WSH) with `team_code`.
  TEAM_ID -> tricode is the tricode present in every one of the team's games, so a test frame needs three teams.

## 2025-26 and 2026-27: the ESPN adapter (`nba/shots/espn.py`)

The supplied file stops in April 2025, so the two newer seasons come from the ESPN play-by-play (hoopR release, same risk class as the box
scores already served). `python -m nba.shots.build --espn-parquet play_by_play_2027.parquet --espn-season 2027` (season = the year it ENDS).
ESPN has no zone labels, so zones come from `nba/shots/zone_grid.json` (majority zone per 0.5 ft cell of the supplied file, 98.9%
agreement on a held-out season, restricted area exact at 4.0 ft). Calibrated and cross-checked on 2024-25, which both sources cover:
- hoop at ESPN x = 41.75; canonical y = 47 - x after folding both baskets; negative canonical x = shooter's right (right-corner-three counts
  per player correlate 0.91 with the file's, 0.72 mirrored);
- per-player FGA correlation 0.998 over 266 players (median ESPN/file 0.979; Brunson 1,182 vs 1,200);
- league FG% 46.0% vs 46.8%, 3P% 36.2% vs 36.2%, zone FG% within 0.1-1.8 points, but ESPN has ~10% fewer restricted-area attempts and ~20%
  more centre mid-range ones (ESPN's coordinates are rounded to 0.25 ft and the rim cut-off sits on a rounding edge). Every season carries
  its OWN league baseline, so player-vs-league comparisons stay internal to a season; compare zone shares across the 2024/2025 boundary
  with that offset in mind.
- Regular season only (playoffs and play-in dropped to match the file), free throws dropped, ~0.5% of plays dropped for no shooter name.

Uploaded 2026-11-03 to `gs://concrete-crow-445205-m4-mlb-data/NBA/shots/` (16 objects, 10.5 MB): 2019-2024 from the supplied file, 2025-26
(complete) and 2026-27 (through 2026-11-02) from ESPN. **The live season goes stale:** nothing rebuilds `shots_2026`/`league_2026` until a
job runs the ESPN command nightly (not provisioned; needs the pbp file fetched first, `python -m nba.data.hoopr --datasets pbp`).

## 2026-11-03 later: assisted baskets, filters, and a bug in the first ESPN upload

- The ESPN frames now carry `period`, `assisted`, `ast_id`, `ast_norm` (the passer, from the play text " (X assists)"; 63.7% of 2025-26 makes
  are assisted, Brunson's 489 assisted baskets vs 515 box-score assists after the 40 ft cut and dedupe). The supplied file has no passer,
  so assist data exists for 2025-26 and 2026-27 only (`has_assist`). These are ASSISTED BASKETS, not potential assists: a pass that did not
  become a make is NBA tracking data we do not hold.
- **Bug fixed before it reached a user:** `team_id` is float64 (`18.0`) in the real files and `home_team_id` int32, so the string compare
  that decided home/away was always False: every ESPN shooter was labelled as the away team (wrong `team`, `opp`, `home`, so the defence
  tables, home/away split and `vs` filter were wrong; shot locations and FG% were fine). The first upload (2026-11-03 ~22:40 UTC) carried
  it; the corrected artifacts were re-uploaded ~22:57 UTC. `test_espn_team_and_home_survive_the_real_files_float_team_ids` pins it.
  Lesson: a synthetic fixture with int ids hid it; mirror the real dtypes.
- Filters (`last`, `venue`, `vs`, `period`) are server-side, so the artifacts stay small; the quarter table is field-goal points only (no
  free throws in the shot data).

## What is still open
- Deploy the service BEFORE the frontend (the artifacts are already in the bucket): until the service rev carries `/api/public/edge/shotchart`
  the panel says shot data is unavailable.
- Nightly rebuild of the live season (see above).
- The NBA.com-terms question for the 2019-2024 seasons (supplied file) stays with the owner; the ESPN seasons carry ESPN's no-licence risk.
