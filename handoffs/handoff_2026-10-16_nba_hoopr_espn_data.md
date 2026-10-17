# Handoff -- 2026-10-16 -- NBA data infra from the 8 zips (hoopR/ESPN mirror, ESPN-keyed props history, availability study)

Branch `feat/nba-data-minutes-hoopr` (own worktree `../mlb-betting-nba-hoopr`, cut from origin/main d5a1a6b). **Nothing deployed, nothing
written to GCS, no jobs/schedulers provisioned, no Discord, no DB, no ParlayAPI credits.** All downloads were public GitHub release files
into the session scratchpad. Python suite: 2,006 passing. No edge is claimed anywhere.

## What shipped (code)
| file | role |
|---|---|
| `nba/data/hoopr.py` | mirror of sportsdataverse ESPN NBA parquet releases -> `NBA/hoopr/` (manifest, shrink guard, sentinel, `read_dataset`) |
| `nba/props/espn_data.py` | ESPN history -> `rows/team_games` (same schema as `data.prepare`), `inactives`, `classify_reason`, `scratch_frame`, `identity_bridge` |
| `nba/props/score.py` | `load_resources(source="espn")` / `--history-source espn` (default stays sportsblaze) |
| `nba/props/features.py` | `FeatureContext(scratches=...)` + `availability="history_excl_scratch"`; unknown modes now fail before any row is processed |
| `nba/props/availability_study.py` | walk-forward study CLI (saves raw predictions beside the report; `--reuse-preds` re-renders) |
| `deploy/setup_nba_hoopr_refresh.sh` | job + scheduler `nba-hoopr-refresh` 13:45 UTC on the feeds image -- **NOT RUN** |
| tests | `test_nba_hoopr`, `test_nba_props_espn_data`, `test_nba_scratch_availability`, `test_nba_availability_study` |
| docs | `docs/analysis/nba_zip_review_2026-10-16.md`, `docs/analysis/nba_availability_study_2026-10-16.md`, `docs/solutions/integration-issues/hoopr-espn-nba-box-scores.md`, CONTEXT s19 bullet |

## Read before trusting
- **ESPN's inactive listing is partial** (53% of rotation absences not on the box; 39% COACH'S DECISION; 6% injury). It is used only to
  drop healthy scratches. A "listed out only" definition would undercount the live injury list ~10x.
- **hoopR `season` = year the season ENDS**; repo `season_year` = START year. `current_season()` is 2027 today. `player_boxscores/2027` is a
  404 until games are played -- expected, not a failure (`MISSING` with `expected=True`).
- Availability gain (-0.5% threes to -2.1% PRA MAE, CIs exclude 0 in every season) is an UPPER BOUND: training uses who actually did not dress,
  live only has the pre-game "Out" list. Accuracy, not edge.
- The scorer's default history is still SportsBlaze. The ESPN path is parity-tested on a synthetic fixture and smoke-tested on real 2026 data;
  the `--history-source espn` scorer run against a real banked props snapshot was NOT done (no snapshot; banking is paused).
- Model artifacts were not retrained or changed. The default feature list is unchanged.

## Not done / decisions for the user
1. Provision `nba-hoopr-refresh` + run the one-time backfill (rebuild the feeds image from merged main first, `deploy/build_all_images.sh SKIP_PROD=1`).
2. Bank the ESPN injuries endpoint (and/or the official report from `ak-static.cms.nba.com`, 200 verified; `official.nba.com` 403) to measure live-vs-train
   availability and to get Questionable/Doubtful.
3. nbadb's Kaggle `inactive_players` as the complete inactive history -- needs Kaggle credentials; not downloaded.
4. A P(active) x minutes model (idea from `nba-player-props-model-main`) -- only worthwhile with 2 and 3.
5. Whether to flip the scorer default to ESPN once 2026-27 games exist.
