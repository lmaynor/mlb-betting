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
| ESPN play-by-play via the hoopR / sportsdataverse release `espn_nba_pbp/play_by_play_{season}.parquet` | yes, every FGA (full-court feet, x in -47..47, y in -25..25, hoop about (41, 0): ESPN's own "N-foot" text matches to 0.4 ft MAE) | no (`nba/data/hoopr.py` defines `pbp` as an "extra" dataset that was never backfilled) | repo is MIT, rows are ESPN's undocumented-API data, no licence from ESPN. Same risk class as the ESPN box scores the site already serves. NOT wired up: the 2026-27 top-up is the open follow-up. |
| `NBA_2004_2025_Shots.csv` (user-supplied 2026-11-03) | yes | no (built artifacts only, once someone uploads them) | Column layout, NBA game ids and zone names are the stats.nba.com `ShotChartDetail` lineage, so the NBA.com terms question applies. The owner chose to use it. |
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

## What is still open
- Upload the built artifacts (`NBA/shots/`) and deploy the service BEFORE the frontend: until both happen `/api/public/edge/shotchart`
  answers `status: no_data` and the panel says shot data is unavailable.
- Current-season shots: needs the ESPN pbp mirror (`python -m nba.data.hoopr --datasets pbp`) plus a second adapter into the same
  canonical frame (hoop (41, 0) -> (0, 5.25); fold x < 0 by negating both axes), or a licensed feed.
- Decide the NBA.com-terms question for the supplied file before the artifacts go to the production bucket.
