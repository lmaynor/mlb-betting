---
title: "hoopR/ESPN NBA box scores: ESPN ids, real ET tips, the new season -- and an inactive listing that is only a PARTIAL availability feed"
module: nba/data/hoopr.py, nba/props/espn_data.py, nba/props/features.py
tags: [hoopr, sportsdataverse, espn, nba, box-scores, availability, injuries, dnp, identity, season-label]
problem_type: integration_issue
category: integration-issues
date: 2026-10-16
---

## What it is

`sportsdataverse/sportsdataverse-data` GitHub releases publish ESPN-derived NBA parquet files (no auth, no quota; the URLs hoopR's
`load_nba_*` read): `espn_nba_player_boxscores/player_box_{season}.parquet`, `espn_nba_team_boxscores/team_box_{season}.parquet`,
`espn_nba_schedules/nba_schedule_{season}.parquet`, `espn_nba_pbp/play_by_play_{season}.parquet` (~20 MB per season). Mirrored
byte-for-byte to `NBA/hoopr/{dataset}/...` by `python -m nba.data.hoopr` (sha256 manifest, shrink guard, same contract as
`nfl.data.ingest`). Checked 2026-10-16: seasons 2019-2026 player box load clean (28.8k-35.3k rows per season); `schedules/2027` exists
(1330-game 2026 schedule shape) while `player_boxscores/2027` is a 404 until games are played (expected, not a failure).

## Why it is better than SportsBlaze for `nba/props`

| | SportsBlaze masters | hoopR / ESPN |
|---|---|---|
| season 2026-27 | absent (scorer refuses STALE_HISTORY) | published as played |
| player id | opaque UUID; name bridge covers 505 of 629 crosswalk players | **ESPN athlete id** = `NBA/players_xwalk.csv` id = `ev.grade_props.nba_actuals` id |
| tip time | UTC date only (24% equal the ET day); real tip only in schedule dumps | `game_date_time` tz-aware America/New_York on every row |
| inactives | none (reconstructed from absence) | every listed DNP row has a `reason` string |
| team rebounds/turnovers | NaN 2019-2024 | populated (we still sum from player rows so both paths share code) |

## Gotchas (all verified on the real 2026 files)

1. **hoopR `season` is the year the season ENDS** (2026 = 2025-26). The repo's `season_year` is the START year (SportsBlaze 2025 =
   2025-26). `espn_data` maps `season_year = season - 1`. `hoopr.current_season()` = year+1 from August on, so on 2026-10-16 it is 2027.
2. **ESPN `season_type` codes**: 2 regular, 3 playoffs, 5 play-in (1 = preseason upstream). Play-in is its own label ("Play-In"), not
   "Playoffs", so `is_playoff` stays 0 for it and the Regular-Season-only evaluations exclude it.
3. **Exhibition games are in the files** (All-Star WORLD / STARS / STRIPES, plus LEB, GIA, USA, DUR, KEN, CHK, CAN, SHQ, WEST, EAST in
   2019-2020/2026), tagged like regular games. Any game with a team that `nba.teams.team_code` cannot resolve is dropped WHOLE; every
   real NBA team abbreviation in 2019-2026 resolves. 2019-20 includes the bubble (1,145 games vs SportsBlaze's 971 regular-season).
4. **ESPN abbreviations** (GS, NY, NO, SA, UTAH, WSH) resolve through `team_code` (ESPN abbrev column in `nba/teams.py`); no SportsBlaze
   `PHO`-style fix is needed.
5. **`did_not_play` rows have NaN minutes and NaN stats** (6,082 of 34,883 rows in 2026, ~4.6 per game). A further 46 rows are
   `did_not_play=False` with NaN minutes; 104 have 0 minutes. History = `minutes > 0`; none of these enter a player's series.
6. **THE LISTING IS PARTIAL -- do not treat "listed out" as the set of absentees.** Over 2024-2026 (7,296 team-games), of 28,224
   rotation-eligible player-games that did not play: **39% are listed COACH'S DECISION** (healthy scratches), 6% listed injury,
   2% other listed reasons (rest, illness, suspension, personal, not-with-team), and **53% are not on the box score at all**
   (inactive players generally are not listed). An absence definition built only from listed reasons counts 0.22 rotation absentees
   per row versus 2.13 for "did not dress"; it undercounts what a live injury list reports by roughly 10x. The usable signal is the
   NEGATIVE one: a listed COACH'S DECISION is a benching no injury list can contain (`espn_data.scratch_frame`).
7. `reason` is free text (228 distinct values in 2026). `espn_data.classify_reason` buckets it: COACH, SUSPENSION, NOT_WITH_TEAM,
   PERSONAL, REST, ILLNESS, UNKNOWN, else INJURY. "LEFT KNEE INJURY MANAGEMENT" is INJURY, not REST. A missing reason is UNKNOWN.

## How to use

```bash
python3 -m nba.data.hoopr --mode backfill --probe --datasets player_boxscores        # size it
MLB_BASE_DATA=/tmp/nba_store python3 -m nba.data.hoopr --mode backfill --seasons 2019-2027
python3 -m nba.props.score --history-source espn ...                                 # opt-in; default stays sportsblaze
```

Provision the nightly refresh with `deploy/setup_nba_hoopr_refresh.sh` (NOT run by the PR that added it; feeds image per
`docs/solutions/conventions/cloud-run-job-image-tag-resolution.md`).
