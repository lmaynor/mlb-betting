# NBA zip review + data infra (2026-10-16)

Eight GitHub archives were supplied to "build out the basketball prop models and improve data infra". This is what each one was
worth, what was built from them, and what the one measured experiment showed. Nothing here claims an edge: no NBA prop odds
history exists, and the repo's standing prior (every MLB count model NO_EDGE; NHL SOG, NBA and NFL props no edge shown) is unchanged.

## Verdicts

| archive | what it is | verdict |
|---|---|---|
| `hoopR-main` (sportsdataverse) | R package + the loaders for the public ESPN-derived NBA parquet releases | **Ported (data).** The release URLs it reads are the data infra win: `nba/data/hoopr.py`. The R code is not used. |
| `nba-player-props-model-main` | 28k-line PMF production system (state-aware minutes model, hurdle 3PM, PMF calibration, SGP engine, official injury-report PDF fetch) | **Reviewed, not ported.** Its own README says `model_trails_market=True` ("a recalibration roadmap, not a market-beating signal"). Useful ideas for follow-up: P(active) minutes mixture, official injury report as a pre-game signal. Needs `nbainjuries` (PDF parsing) and LightGBM, neither in our image. |
| `nbadb-main` | nba_api -> DuckDB star-schema warehouse, also published to Kaggle `wyattowalsh/basketball` | **Follow-up.** Its docs list an `inactive_players` table (the official inactive list per game), which would be the complete availability feed the ESPN box lacks. NOT downloaded or verified (needs Kaggle credentials; stats.nba.com is blocked from our jobs). |
| `Basketball-Modelling-main` | stdlib Elo + Normal margin/total sim, ESPN ingest | Not ported: team-level Normal sim; our NBA question is player props. |
| `CS229-Rebound-ML-Project-main` | rebounds regression (SVR/XGB) | Not ported: random 70/30 row split (leaks across time) and no price comparison. |
| `nba-master` | old NBA.com scraping scripts (`bb_tools.py`) | Not ported: stats.nba.com/cdn.nba.com are blocked from Cloud Run, ESPN works. |
| `machine-learning-parlay-generator-main`, `nba-prop-prediction-model-main` | ~100-line rolling-average scripts | Nothing to port; the existing 170-feature model already contains these baselines. |

## Built (this PR)

1. `nba/data/hoopr.py` -- byte-for-byte mirror of the ESPN player box / team box / schedules (pbp opt-in) to `NBA/hoopr/`, sha256
   manifest, shrink guard, sentinel; `deploy/setup_nba_hoopr_refresh.sh` (not run). Verified live 2026-10-16: seasons 2019-2026 player
   box load (28.8k-35.3k rows/season); `schedules/2027` is published, `player_boxscores/2027` is a 404 until games are played.
2. `nba/props/espn_data.py` -- ESPN history in the exact `rows/team_games` schema `nba.props.data.prepare` returns (training features
   match through both paths on the synthetic fixture), `player_id` = ESPN athlete id, real ET tips, All-Star/exhibition
   games dropped. `score.py --history-source espn` is opt-in (default unchanged). This removes the 124-player name-bridge gap and the
   SportsBlaze "no 2026-27 season" blocker once the new season is played.
3. `inactives()` / `classify_reason()` / `scratch_frame()` and `availability="history_excl_scratch"` (opt-in feature mode).
4. `nba/props/availability_study.py` -- the walk-forward study below.

## The finding that shaped the availability work

ESPN lists DNP players with a reason, which looked like a free historical injury feed. It is **partial**: of 28,224 rotation-eligible
player-games that did not play (2024-2026), 39% are listed COACH'S DECISION, 6% injury, 2% other, and **53% are not on the box score
at all**. Counting only listed reasons gives 0.22 absentees per row versus 2.13 for "did not dress", about 10x below what a live injury
list reports. So the feed is used negatively (drop listed healthy scratches, which no injury list can contain) rather than positively.
Detail and the other gotchas: `docs/solutions/integration-issues/hoopr-espn-nba-box-scores.md`.

## Study: availability definitions (accuracy only)

Full tables: `docs/analysis/nba_availability_study_2026-10-16.md`. Mean model only, rolling origin (train <= T-2, early stop T-1,
refit <= T-1), test seasons 2023-2025, 210,844 rows, ESPN history 2019-2026, regular-season targets.

| stat | v1 MAE | + availability (any absence) | + availability (scratches excluded) |
|---|---|---|---|
| points | 4.534 | -1.50% [-1.91, -1.15] | -1.46% [-1.88, -1.11] |
| rebounds | 1.877 | -1.17% | -1.10% |
| assists | 1.331 | -0.76% | -0.73% |
| threes | 0.877 | -0.53% | -0.49% |
| PRA | 5.963 | -2.14% | -2.10% |

(MAE change versus v1, date-cluster bootstrap 95% CI in the full report; every CI excludes zero in every season.)

Reading it:
- On ESPN data the opt-in availability features reproduce the earlier SportsBlaze result (-1.4% points, -2.0% PRA): a small, real
  accuracy gain that is consistent across seasons.
- Excluding listed healthy scratches costs essentially nothing (0.04pp), and moves the training definition (1.43 absentees per row vs
  2.13) toward what the live injury list can supply. That is the argument for training on it, NOT a measured live improvement.
- **Upper bound, still.** Training uses who actually did not dress. Live only has the pre-game "Out" list (ESPN injuries endpoint), which
  the 53% of unlisted absences may or may not match. No live-vs-train comparison exists yet; it needs banked injury lists.
- **Accuracy is not edge.** A book line already contains this information. Oracle minutes would still cut MAE 9-28% (previous
  evaluation); availability features close a small part of that gap.

## Not done / next

- Provision `nba-hoopr-refresh` and run the one-time backfill (deploy decision; feeds image rebuild from merged main first).
- Bank the ESPN injuries endpoint forward (and/or the official NBA injury report) so a live-vs-training availability comparison and
  Questionable/Doubtful statuses become possible; official report PDFs are fetchable from `ak-static.cms.nba.com` (verified 200,
  `official.nba.com` returns 403) but parsing needs a PDF dependency.
- Evaluate nbadb's `inactive_players` as the complete inactive history (Kaggle credentials needed).
- A minutes model (P(active) x conditional minutes) is the highest-leverage modeling step; it needs the two feeds above to be more than a
  re-expression of features the XGB model already sees.
- The default feature set, the artifacts, the scorer default and every MLB path are untouched.
