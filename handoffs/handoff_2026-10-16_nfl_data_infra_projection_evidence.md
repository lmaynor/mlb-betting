# Handoff -- 2026-10-16 -- NFL data infra + projection-engine evidence (branch feat/nfl-data-infra-projections)

Request: "leverage these nine repos to enhance the NFL prop offering and projections, and build out data infra; own PR, keep
the session siloed". Built in an isolated worktree off `origin/main` (d5a1a6b); nothing deployed, nothing written to GCS or
the bets table, the dirty tree of other sessions untouched. All numbers below were produced on the real NFL mirror (read-only
copy of `gs://.../NFL/nflverse`, 2018-2026) in a scratch directory.

## What came from the nine repos (and what did not)

| repo | taken | not taken / why |
|---|---|---|
| nflverse-data | the real update cadence from `workflows.md` (stats/pbp 09:00 UTC + after each game window; injuries/rosters/depth 07:00 UTC; PFR/FTN every 6 h) -- closes the "cadence unverified" item; the 13:30 UTC job time is confirmed adequate | the R release/upload code |
| nflfastR | definitions: `xpass`/`pass_oe` (already in the pbp), neutral-situation convention, explosive-play and EPA conventions -> `nfl/data/derived.py` | R EP/WP model code and the scraper; not needed, the pbp mirror already carries their output |
| nfldata (Lee Sharpe) | confirmed `games.csv` == our `schedules` mirror (closing lines, rest, roof, QBs); static `airports.csv` noted | `closing_lines`/`initial_lines`/`sc_lines`/`win_totals` are stale (<= 2021) so they add nothing over the mirror |
| fantasy-football-ai | the discipline: data contracts (-> `nfl/data/contracts.py`), leakage test on real data, held-out evaluation vs a causal baseline, residual-quantile floor/ceiling (-> `proj_p10`/`proj_p90`), publish/hold evidence gates | the dbt/DuckDB/MotherDuck warehouse, FastAPI, Next.js, RandomForest (a different product; our stack has the equivalent pieces) |
| NFLBigDataBowl | nothing | 2022 weeks 1-9 tracking data only; not available live |
| sports-quant | nothing (same call as the 2026-10-14 handoff) | NFL half scrapes PFF Premium (login) and PFR through rotating proxies to evade rate limits (ToS/evasion); picks the best of 50 seeds by seasonal accuracy (selection bias) |
| nfl-touchdown-prediction-ml | nothing | README describes files that are not in the zip; `train_and_learn.py` uses a random split on an undefined target |
| Player_Stat_Prediction | nothing | previous-season polynomial regression; weaker than what we already evaluate |
| NFL-Betting-Models | nothing | an unfinished `nfl_data_py` loader (plus a 195 MB virtualenv inside the zip) |

## Shipped

Data infra (`nfl/data/`)
- `contracts.py` -- table-level contracts -> `NFL/contracts_latest.json`; monitor_ops pages on a FAILED contract. Verified on the real
  mirror: exact player-vs-team reconciliation over 1,868 team-games, PPR formula 99.97% on offensive rows 2023-2026, and one real defect class
  found (a null-`player_id` team-penalty row every week -- docs/solutions/integration-issues/nflverse-player-stats-null-id-team-row.md).
  Simulated staleness (10-25) correctly FAILs on 15 overdue games.
- `derived.py` -- `NFL/derived/{team_game_pbp,player_game_pbp}/` (all 9 seasons in ~8 s), incremental on pbp sha256 + `DERIVED_VERSION`;
  `read.load_derived`. Sanity-checked: air yards match `player_stats` exactly, targets within lateral-pass noise, plays within 0.7 of team_stats on average (scramble / penalty-play conventions differ by a few plays per game).
- `pipeline.py` -- ingest -> derived -> contracts; `deploy/setup_nfl_refresh.sh` now runs it (job `nfl-refresh-data` IS provisioned and ran 2026-10-15 13:32 UTC).
- `ff_opportunity` registered as an opt-in `extra` dataset (ffverse repo; URL verified by HEAD only -- NOT downloaded, columns unverified).

Projection engine (`ev/daily_projections.py`, engine id -> `empirical_ewm_v2`)
- `nfl/props/engine_eval.py` -- walk-forward replay of the SERVING code (first accuracy numbers for what the site actually runs).
- Validated per-stat params (`STAT_PARAMS`: rec_yds hl 7 / w 1.0; anytime_td hl 7 / w 0.3) and `nfl/props/projection_calibration.json`
  (scale for rush_yds / receptions / pass_tds; scale + spread/total game term for rec_yds / anytime_td; pass_yds not shipped).
- New row fields: `proj_raw`, `calib_mult`, `proj_p10`, `proj_p90`. Normal-family spread stays tied to the uncalibrated mean.
- `nfl/models/ablation.py` + opt-in `pc_*` pbp-context features in `props_features.py` (leakage test has power on them).

## Results (accuracy only -- there are still NO historical NFL prop lines)

Full write-up with numbers: docs/solutions/conventions/nfl-projection-engine-changes-need-walkforward-evidence.md. In short:
engine beats L5/season baselines on every stat; opponent factor ~0; every trailing projection over-projects the quoted pool 3-7% in every
season (scale fixes MAE 5/5 seasons); rec_yds and anytime_td gain from long memory and a game term; pbp-context features did NOT help the XGBoost models.

## Open items / decisions for the owner
1. **Deploy path** (not done): merge -> `deploy/deploy_service.sh` (image must carry `nfl/props/projection_calibration.json`; it is a repo
   file under `nfl/`, covered by `COPY nfl/`) -> re-run `deploy/setup_nfl_refresh.sh` so the job runs `nfl.data.pipeline` -> one-off
   `python -m nfl.data.derived --seasons 2018-2026` (or run the pipeline in backfill mode) to populate `NFL/derived/`.
   Until the job is re-provisioned the old job keeps running plain ingest; nothing breaks.
2. The board's `proj_mean` for rec_yds/rush_yds/receptions/pass_tds/anytime_td is now ~4% lower than before (the calibration). That is the
   intended correction, but anything that compared today's numbers with yesterday's (screenshots, tweets) will see the step.
3. `beezy-vip/lib/prop-model-specs.ts` copy was updated to `empirical_ewm_v2`; the frontend tests could not be run here (no node_modules in the worktree).
4. Re-run `python -m nfl.props.engine_eval --calibrate --write-calibration nfl/props/projection_calibration.json` each offseason; the file
   records the seasons it was fitted on.
5. Still open from earlier handoffs: anytime-TD / DNP rule text from the books and Kalshi; `player_touchdown_passes` missing from
   `ev.props.STAT_KEYS`; the sweep must bank full slates before any edge claim (CLV vs close is the only gate).
6. Candidate next builds: use `ff_opportunity` expected-yards/TD series as a regression-to-the-mean input (needs a first download to verify
   columns); a projection ledger (persist each day's board, grade it against `player_stats`) so the evidence accrues live.
