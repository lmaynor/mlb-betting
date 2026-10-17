# Handoff -- 2026-10-16 -- NBA availability feeds (injury banker, official report backfill, nbadb loader, pre-game availability study)

Branch `feat/nba-availability-minutes` (own worktree `../mlb-betting-nba-avail`), rebased on main after PR #74 merged. **Nothing deployed,
nothing written to GCS, no jobs/schedulers provisioned, no Docker image change.** Downloads: 2,109 public official injury-report PDFs
(~149 MB) and 8 ESPN parquet files into the session scratchpad only. Python suite: 2,072 passing. No edge is claimed.

## What shipped
| file | role |
|---|---|
| `nba/data/injury_bank.py` | ESPN injury list, change-only append-only bank + heartbeat; `state_asof` returns None when unknown (verified live: 126 rows stored, 2nd poll unchanged) |
| `nba/data/nba_injury_report.py` | official NBA injury report: URLs (hourly + quarter-hour names), fetch (403 = not published), `pdftotext -bbox` parser, ESPN-id resolution, resumable backfill CLI |
| `nba/props/official_availability.py` | per-game pre-game listing (>= 30 min before tip), coverage rules, P(played given status), five feature variants, study CLI |
| `nba/props/features.py` | `availability={(team, ET-day int): ids}` per-game lists; `availability="history_inactive"` (needs `FeatureContext(inactives=...)`) |
| `nba/data/nbadb_inactives.py` | nbadb `inactive_players` -> ESPN ids. **Fixture-tested only; never run on real data (no Kaggle credentials).** |
| `deploy/setup_nba_injury_bank.sh` | ESPN banker job + scheduler every 30 min on the feeds image -- **NOT RUN** |
| docs | `docs/solutions/integration-issues/nba-official-injury-report-pdf.md`, `docs/analysis/nba_official_availability_2026-10-16.md`, CONTEXT s19 bullet |

## Result (docs/analysis/nba_official_availability_2026-10-16.md)
Walk-forward 2024-2025, 137,933 rows, trained from 2021, mean model, accuracy only. Pre-game official-list features recover ~60% of the
"who actually did not dress" upper bound (points +1.01% vs +1.64% MAE; PRA +1.30% vs +2.23%; minutes +2.78% vs +4.74%); adding
Doubtful/Questionable minutes + own status reaches ~70%; a P(active)-weighted feature adds nothing beyond that. P(played | status):
Out 0.2%, Doubtful 2.1%, Questionable 50.1%, Probable 91.6%, Available 77.1%.

## Read before trusting
- Coverage is only 64.3% of team-games (a game with no entry for either team has no matchup line, so it is UNKNOWN, not healthy). 13 reports
  parse to zero rows (empty-list days); 6 stamps were never published. The list variants are handicapped by those NaNs.
- Official report stamps: title time != file stamp (the `05PM` file says 05:30 PM). Two filename conventions; missing objects are 403, HEAD is 403.
- **Parsing needs poppler `pdftotext`, which is NOT in the Docker image.** Banking the official report from Cloud Run needs `poppler-utils` in a
  feeds-image Dockerfile; the production image was deliberately not touched. The ESPN banker needs no new dependency.
- The state-aware P(active) x minutes MIXTURE model was not built: the study found no gain from P(active) weighting beyond the status features.
- Model artifacts, default feature list and scorer defaults are unchanged; every new mode is opt-in.

## Not done / decisions for the user
1. Provision `nba-injury-bank` (needs the feeds image rebuilt from merged main) -- this starts the forward record of what the public list said pre-tip.
2. Decide on poppler in a feeds image to bank the official report forward (a 15-minute cadence exists from 2026-01).
3. nbadb `inactive_players`: needs Kaggle credentials; coverage of the 53% unlisted absences is still unmeasured.
4. Train/serve the `list_full` feature set (own listed status + Doubtful/Questionable minutes) if the scorer is ever fed a live official list; the live scorer
   today still takes the ESPN "Out" list only.
5. Denser report stamps (hourly/quarter-hour) and a rule for empty-list days would raise the 64% coverage.
