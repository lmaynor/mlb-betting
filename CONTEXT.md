# Project Context

_Last updated: 2026-05-23 00:25 CST_

The standing architectural and conventions document for `lmaynor/mlb-betting`. Read this first at the start of any new session before touching code.

**This doc captures what doesn't change session-to-session.** For point-in-time status (which models are deployed, which bugs are open), see the latest handoff. For modeling theory, see `ipynb_CONTEXT`. For operational runbooks, see `deploy/*.md`.

If you change something here, treat it as a contract change -- flag it in the next commit and the next handoff.

---

## 1. What this project is

Five MLB betting systems running daily in GCP:

| System | What it predicts | Market | Status |
|---|---|---|---|
| **HR Pro v6** | P(batter hits HR in game) | HR yes/no props (best onshore book) | Live (paper) |
| **NRFI Pro v17** | P(no run scored in inning 1) | NRFI/YRFI O/U + 1st inning 3-way ML (best onshore book) | Live (paper) |
| **F5 Pro v5** | P(home team wins first 5 innings) | F5 moneyline (best onshore book) | Live (paper) |
| **K Pro v1** | E[pitcher strikeouts] (NB; k_per_9_L5 * avg_ip scaling) | K props O/U (best onshore book) | Live (paper) |
| **OUTS** | E[pitcher outs recorded] (trained NegBin; retrain_outs_v1.py) | Pitcher outs O/U (best onshore book) | Live (paper) |
| **F1H** | P(home wins innings 1-4) via F5 scalar proxy | First Half ML (best onshore book) | Live (log-only) |
| **GAME** | P(home wins full game) via F5 scalar proxy | Full Game ML (best onshore book) | Live (log-only) |
| **BATTER_TB** | P(total bases > line) Normal proxy via HR model quality | Batter TB O/U (best onshore book) | Live (log-only) |
| **BATTER_HITS** | P(hits > line) Normal proxy via HR model quality | Batter hits O/U (best onshore book) | Live (log-only) |
| **PITCHER_ER** | P(earned runs > line) Gamma proxy via K model lambda | Pitcher ER O/U (best onshore book) | Live (log-only) |

OUTS is a sub-market of the K runner -- same feature CSV, same `run_k.py` -- but logged as a separate system (`system="OUTS"`) for independent tracking and settlement.

All five are paper-mode-only until each clears a 200-settled-bet gate.

The system has three responsibilities: **build features daily**, **score / size bets twice daily**, and **settle bets + monitor performance nightly**.

Model training itself is human-driven (notebooks on Windows) but is being migrated to a Cloud Run Jobs pipeline (`training/`).

---

## 2. Repo layout (the map)

```
mlb-betting/
├── main.py                       Flask entrypoint for Cloud Run service.
│                                 Routes: /healthz, /run, /build-features,
│                                         /snapshot-odds, /settle, /refresh-data,
│                                         /monitor, /monitor-ops, /retrain-weekly,
│                                         /model-health, /backfill-data
├── Dockerfile                    Single image. Used by Cloud Run service AND jobs.
├── requirements.txt              Pinned Python deps.
├── setup.py                      Makes mlb_core importable. Only prod deps --
│                                 selenium/pybaseball removed (they were notebook-only).
│
├── mlb_core/                     SHARED INFRA
│   ├── config.py                 GCS_BUCKET, DB_URL, BASE_DATA env-var resolution.
│   ├── storage.py                read_csv/write_csv/exists -- transparent GCS-or-local.
│   ├── data/
│   │   ├── statcast.py           Statcast pulls.
│   │   ├── lineups.py            get_today_schedule(date) -- MLB Stats API.
│   │   │                         _get_games_for_date() -- reused by scoring refresh.
│   │   ├── scoring.py            Inning-by-inning runs from MLB Stats API.
│   │   │                         scoring_nightly_gcs() -- called by /refresh-data.
│   │   │                         scoring_backfill_gcs() -- manual backfill by date range.
│   │   ├── weather.py            Open-Meteo forecast + STADIUMS dict.
│   │   │                         weather_nightly_gcs() -- called by /refresh-data.
│   │   ├── umpires.py            Umpire scorecard pulls.
│   │   │                         umpires_nightly_gcs() -- called by /refresh-data.
│   │   ├── savant_leaderboards.py    Six Baseball Savant leaderboard fetchers.
│   │   │                             savant_leaderboards_nightly_all_gcs() called
│   │   │                             by /refresh-data nightly (in-season only).
│   │   │                             savant_leaderboard_backfill_gcs(dataset, ...)
│   │   │                             for one-time fills via /backfill-savant.
│   │   └── game_result.py        fetch_game_result(game_pk) -- MLB Stats API linescore
│   │                             + boxscore. Returns innings/pitchers/batters dict.
│   │                             Returns None if game not Final. Used by settle_bets.
│   ├── odds/
│   │   ├── sgo.py                SGO client + 6 extractors:
│   │   │                           extract_hr_props()
│   │   │                           extract_nrfi_odds()
│   │   │                           extract_1i_3way_odds()
│   │   │                           extract_f5_odds()
│   │   │                           extract_f5_ml_odds()
│   │   │                           extract_k_odds()
│   │   │                           extract_outs_odds()
│   │   │                           extract_batter_hits_odds()
│   │   │                           extract_batter_tb_odds()
│   │   │                           extract_pitcher_er_odds()
│   │   ├── dk_scraper.py         LEGACY. Kept for resolve_team() only.
│   │   └── utils.py              american_to_implied_prob, remove_vig, kelly_stake.
│   ├── notify/
│   │   └── discord.py            post_bets / post_error / post_all_systems_summary.
│   │                             Webhook-based. post_summary() removed -- daily recap
│   │                             in post_all_systems_summary() covers all systems.
│   ├── risk/
│   │   └── exposure.py           prefetch_exposure() + apply_cap().
│   │                             One DB query per runner; _pending_stakes accumulator
│   │                             tracks within-runner exposure correctly.
│   ├── rationale.py              Canned rationale engine. Maps feature values to plain-English phrases.
│       └── bet_tracker.py        BetTracker(db_path, system). Writes to Postgres.
│   │                             Extend by adding rules to _SYSTEM_RULES in rationale.py.
│   └── tracking/
│       └── bet_tracker.py        BetTracker(db_path, system). Writes to Postgres.
│                                 log_bet() dedup on (system, game_date, game_pk, bet_type).
│                                 summary() filters by system via text() wrapper.
│
├── runners/                      DAILY JOBS
│   ├── build_hr_features.py      Nightly: build HR_Pro/data/model_features.csv.
│   ├── build_nrfi_features.py    Nightly: build NRFI pitcher features + model_features.
│   ├── build_f5_features.py      Nightly: build F5 pitcher/offense/model_features.
│   ├── build_k_features.py       Nightly: build K_Pro_System/data/model_features.csv.
│   ├── run_hr.py                 Score HR, post bets.
│   ├── run_nrfi.py               Score NRFI/YRFI O/U + 1st inning 3-way ML, post bets.
│   ├── run_f5.py                 Score F5 ML, post bets.
│   ├── run_k.py                  Score K O/U (system="K") + pitcher outs O/U
│   │                             (system="OUTS") -- two trackers, one runner.
│   ├── snapshot_odds.py          Fetch SGO slate → GCS latest.json.
│   ├── settle_bets.py            Nightly: settle all pending bets via MLB Stats API.
│   │                             fetch_game_result() called once per game_pk, cached.
│   │                             Retries non-Final games automatically on next run.
│   ├── monitor_performance.py    Daily: rolling perf check, Discord alerts, Mon digest.
│   └── monitor_ops.py            Daily: infra health check after feature builds.
│                                 Checks: scheduler job status, GCS freshness of SGO
│                                 snapshot + all 4 model_features.csv + all 4 data
│                                 masters (scoring/statcast/weather/umpires), model
│                                 artifact existence, bets pending > 3 days.
│                                 Silent on clean run. Posts Discord alert on failure.
│
├── training/                     RETRAIN PIPELINE
│   ├── retrain_f5_meta.py        Patches feature_means into F5 model meta.
│   ├── retrain_nrfi_v17.py       Full NRFI retrain.
│   ├── retrain_k_v1.py           Full K retrain with walk-forward CV + leakage guard.
│   ├── retrain_hr_meta.py        Patches feature_means into HR model meta.
│   ├── calibrate_nrfi_v17.py     Fit isotonic calibrator for NRFI v17.
│   ├── calibrate_f5_v5.py        Fit isotonic calibrator for F5 v5.
│   ├── calibrate_k_v1.py         Fit lambda calibrator for K v1.
│   ├── calibrate_hr_v6.py        Fit isotonic calibrator for HR v6.
│   ├── retrain_outs_v1.py        Full OUTS retrain (NegBin count model). (E04)
│   └── tune_hyperparams.py       Optuna hyperparameter search for all systems. (E09)
│
├── HR_Pro/                       Per-system config dirs
├── NRFI_Pro_System/
├── F5_Pro_System/
├── OUTS_Pro_System/              OUTS Pro config (shares K feature CSV)
├── K_Pro_System/
│
├── deploy/                       Operational scripts and runbooks
│   ├── deploy.sh
│   ├── SGO_DEPLOY_NOTES.md
│   └── RETRAIN_NOTES.md
│
├── .env.example                  Documents all env vars. .env is gitignored.
├── *.ipynb                       Modeling notebooks -- canonical source of logic.
├── ipynb_CONTEXT                 Summary of what each notebook does.
├── CONTEXT.md                    (this file) -- Claude project is source of truth.
│                                 Commit to repo at end of each session.
└── tests/
    ├── test_sgo_extractors.py
    └── test_pipeline.py          43 tests: BetTracker, exposure cap, all 4 settlers.
```

---

## 3. GCS layout (the data lake)

Bucket: `concrete-crow-445205-m4-mlb-data`.

```
gs://concrete-crow-445205-m4-mlb-data/
├── Statcast/
│   ├── statcast_master.csv             946k+ pitch rows, 2021-current.
│   ├── cache_daily/
│   ├── savant_{dataset}_{YYYY}.csv        Per-season cache (6 datasets:
│   │                                       exit_velocity_barrels, expected_statistics,
│   │                                       pitch_arsenals, sprint_speed,
│   │                                       bat_tracking, batter_arsenal_stats)
│   └── savant_{dataset}_master.csv        All years combined
├── Scoring/
│   └── scoring_master.csv              Per-(game_pk,inning,half) runs.
│                                       AUTHORITATIVE for run targets.
│                                       Updated nightly by mlb-refresh-data.
├── Weather/
│   └── weather_master.csv              Historical weather per game.
│                                       Updated nightly by mlb-refresh-data.
│                                       No dedicated backfill function -- use
│                                       /backfill-data with systems=["weather"].
├── Umpires/
│   └── umpscorecards_master.csv        Umpire scorecards.
│                                       Updated nightly by mlb-refresh-data.
├── Odds/
│   └── sgo/
│       ├── latest.json                 LATEST POINTER -- all runners read this.
│       └── {YYYY-MM-DD}/snapshot_{HHMM}.json
├── HR_Pro/
│   ├── data/
│   └── models/                         xgb_hr_v6.json, model_meta_hr_v6.json
├── NRFI_Pro_System/
│   ├── data/
│   └── models/                         xgb_halfinn_v17.json, model_meta_v17.json,
│                                       isotonic_calibrator_v17.pkl
├── F5_Pro_System/
│   ├── data/
│   └── models/
│       ├── xgb_f5_v5.json
│       ├── model_meta_f5_v5.json
│       ├── isotonic_calibrator_f5_v5.pkl
│       └── archive/
├── OUTS_Pro_System/              OUTS Pro config (shares K feature CSV)
├── K_Pro_System/
│   ├── data/                           pitcher_k_features.csv, lineup_k_features.csv,
│   │                                   model_features.csv
│   └── models/
│       ├── xgb_k_v1.json
│       ├── model_meta_v1.json
│       ├── lambda_calibrator_k_v1.pkl
│       └── archive/
├── {system_prefix}/
│   └── data/last_build.json            Build sentinel per system. Written on success
│                                       by each feature builder. Checked by monitor_ops.
└── probes/                             Sandbox.
```

OUTS has its own GCS artifacts since E04 (2026-05-21):
  OUTS_Pro_System/models/xgb_outs_v1.json
  OUTS_Pro_System/models/model_meta_outs_v1.json
  OUTS_Pro_System/models/isotonic_calibrator_outs_v1.pkl
OUTS still shares K's feature CSV (K_Pro_System/data/model_features.csv).
starter_outs column added to K feature CSV by build_k_features.py.

---

## 4. The daily loops

### Loop A: Data refresh (08:00 UTC -- `mlb-refresh-data`)
Fetches yesterday's weather (Open-Meteo archive), umpire scorecards
(umpscorecards.com), inning-by-inning scoring (MLB Stats API), and
Statcast pitch data for yesterday's games. Appends all four to GCS masters.
Feature builds only READ from GCS masters -- they never write them.
Statcast was previously updated inside `build_hr_features.py`; moved to
`/refresh-data` 2026-05-18 to decouple data refresh from feature builds.

Scoring refresh runs at 08:00 UTC -- 2hr buffer after west coast games
finish (~midnight CT / 06:00 UTC). Adequate for regular season games.
No Statcast -- that has its own nightly job inside `build_hr_features.py`.
Also refreshes six Savant leaderboards for the current season via
savant_leaderboards_nightly_all_gcs(). In-season: ~60s added to refresh time.
Off-season (Dec-Feb): no-op, returns status="skipped".

### Loop B: Feature builds (12:00 UTC)
| Time | Job | Notes |
|---|---|---|
| 12:00 | `mlb-build-all-features` | Runs HR -> NRFI -> K -> F5 in dependency order |

Dependency order enforced in code: F5 reads NRFI's `pitcher_start_features.csv`.
HR and K are independent. Each system writes a build sentinel to GCS on success
(`{system_prefix}/data/last_build.json`) -- checked by `monitor_ops` at 13:15 UTC.


### Loop C: Score + bet (16:00 and 22:00 UTC / 11am and 5pm CT)

```
15:55 / 21:55 UTC → /snapshot-odds  → Odds/sgo/latest.json
16:00 / 22:00 UTC → /run            → all four runners score + post bets
```

Runners post bet signals to Discord only (`post_bets`). No per-runner
performance summaries -- those come from the daily recap in `/settle`.

### Loop D: Settle + monitor (09:00–13:15 UTC)

```
09:00 UTC → /settle       → settle yesterday's bets + retry stale pending
                             posts daily recap embed (post_all_systems_summary)
09:30 UTC → /monitor      → rolling perf check, Discord alert if degraded
                             Monday: weekly digest post
12:50 UTC → /monitor-ops  → infra health check after feature builds complete
                             Silent on clean run.
```

### Full daily schedule

| Time UTC | Scheduler job | What it does |
|---|---|---|
| 09:00 | `mlb-settle` | Settle bets, post daily recap |
| 09:30 | `mlb-monitor` | Rolling perf check, alerts |
| 14:00 | `mlb-refresh-data` | Weather + umpire + scoring + Statcast masters |
| 14:30 | `mlb-build-all-features` | All feature builds: HR -> NRFI -> K -> F5 (dependency order) |
| 15:20 | `mlb-monitor-ops` | Infra health check after feature builds |
| 15:55 | `mlb-snapshot-morning` | SGO odds snapshot (opening lines) |
| 16:00 | `mlb-betting-morning` | Score all 4 runners |
| 19:00 | `mlb-snapshot-afternoon` | SGO odds snapshot (lineup confirmation) |
| 21:55 | `mlb-snapshot-evening` | SGO odds snapshot (pre-evening bets) |
| 22:00 | `mlb-betting-evening` | Score all 4 runners |
| 23:30 | `mlb-snapshot-pregame` | SGO odds snapshot (closing lines ~1hr before first pitch) |
| 00:00 | `mlb-capture-closing` | Capture closing lines for CLV calculation |

---

## 5. Contracts between components

### Model artifact contract (every system)

A model lives in GCS as two files:
- `xgb_{system}_v{N}.json` -- XGBoost booster
- `model_meta_{...}_v{N}.json` -- JSON dict with at minimum:
  ```json
  {
    "version":       "v6",
    "features":      ["feature_1", ...],
    "feature_means": {"feature_1": 0.123, ...},
    "best_iteration": 250,
    "auc_oos":        0.633
  }
  ```

**Never hardcode a feature list in a runner.** Load from meta.

### iteration_range contract

All runners use the safe pattern:
```python
ntree = getattr(booster, "best_ntree_limit", 0)
if ntree:
    return booster.predict(dm, iteration_range=(0, ntree))
return booster.predict(dm)
```
Do NOT use `iteration_range=(0, ntree) if ntree else None` -- passing `None`
crashes XGBoost ≥2.0.

### Multi-book odds contract

All extractors use `_best_book_odds_int()` in `mlb_core/odds/sgo.py`.
Picks best American odds across `ONSHORE_BOOKS` for the bettor.
Result stored as `book` column in `bets` table.

```python
ONSHORE_BOOKS = {
    "draftkings", "fanduel", "caesars", "betmgm", "espnbet", "thescore", "pointsbet",
}
BOOK_CANONICAL = {  # SGO key -> canonical name stored in DB
    "espnbet":  "thescore",  # ESPN Bet rebranded to theScore Bet 2025
    "thescore": "thescore",
    # all others map to themselves
}
```

To add/remove a book: edit both dicts in `sgo.py`. Historic bets have `book=NULL`.

### SGO snapshot contract

`Odds/sgo/latest.json` is an array of event dicts. Key fields:
- `eventID`, `status.startsAt`, `teams.{home,away}.names.medium`
- `odds[oddID].byBookmaker.draftkings.{available, odds, overUnder}`
- `players[PLAYER_ID].name`

Market IDs in use:
- HR: `batting_homeRuns-{PLAYER_MLB}-game-yn-yes`
- K: `pitching_strikeouts-{PITCHER_MLB}-game-ou-{over,under}`
- NRFI O/U: `points-all-1i-ou-{over,under}`
- **1st inn 3-way**: `points-away-1i-ml3way-away`, `points-home-1i-ml3way-home`, `points-all-1i-ml3way-draw`
- F5 totals: `points-all-1ix5-ou-{over,under}` (extracted but not bet)
- F5 ML: `points-{home,away}-1ix5-ml-{home,away}`
- **Outs**: `pitching_outs-{PITCHER_MLB}-game-ou-{over,under}`

### Database indexes (bets table)
Two indexes are created on first BetTracker init (idempotent via CREATE INDEX IF NOT EXISTS):
- `idx_bets_dedup` on `(system, game_date, game_pk, bet_type)` -- covers `is_duplicate()` hot path
- `idx_bets_pending` on `(result, game_date) WHERE result IS NULL` -- covers settle queries (Postgres only)

Both live in `_init_db()` in `mlb_core/tracking/bet_tracker.py`, each in its own `engine.begin()` + try/except block.

### Exposure cap contract
All runners use `prefetch_exposure()` + `apply_cap()` from `mlb_core.risk.exposure`:
1. Before the prediction loop: `_bankroll, _prefetched_stakes = prefetch_exposure(engine, game_pks, game_date, system="SYSTEM")` -- one DB query total, filtered to this system only
2. Maintain `_pending_stakes: dict[int, float] = {}` -- incremented after each `kelly_triggered` bet
3. Per row: `_bankroll, _cap = apply_cap(_bankroll, game_pk, _prefetched_stakes, _pending_stakes)`
4. `stake = min(kelly_stake(...), _cap)`

This ensures within-runner accumulation is tracked correctly (second bet on same game_pk sees reduced cap)
The cap is **per-system** -- NRFI, K, HR, F5 are independent markets and do not count against each other's cap.
Cross-system correlation (e.g. HR in 1st inning affecting YRFI) is light enough to handle via Kelly fraction sizing alone.

### Bet dedup contract

`BetTracker.log_bet()` checks `(system, game_date, game_pk, bet_type)` for
duplicates before inserting. Returns `-1` on duplicate -- caller should skip.
Morning run wins; evening run skips.

### Prediction logging contract

All runners log **every scored prediction**, not just qualifying ones.
`kelly_triggered=True` means the prediction cleared `min_edge` and received
a non-zero stake. `kelly_triggered=False` rows have `stake=0`.

`BetTracker.summary()` counts only `kelly_triggered=True` rows for P&L and
hit-rate stats. Filters by `self.system` via `text("SELECT * FROM bets WHERE system = :sys")`.

### Discord posting contract

Runners call `post_bets()` only -- bet signals for today's slate.
**No `post_summary()` calls from runners.** The daily recap is posted by
`settle_bets.run()` via `post_all_systems_summary()` after settlement.
This gives one clean daily embed covering all five systems (HR, NRFI, F5, K, OUTS).

### Bet type naming convention

| System | bet_type format | Examples |
|---|---|---|
| HR | `"HR"` | `"HR"` |
| NRFI | side string | `"NRFI"`, `"YRFI"`, `"1I_AWAY"`, `"1I_HOME"`, `"1I_DRAW"` |
| F5 | side string | `"HOME"`, `"AWAY"` |
| K | `"K_{SIDE}_{LINE}"` | `"K_OVER_7.5"`, `"K_UNDER_5.5"` |
| OUTS | `"OUTS_{SIDE}_{LINE}"` | `"OUTS_OVER_14.5"`, `"OUTS_UNDER_17.5"` |
| F1H | `"F1H_{SIDE}"` | `"F1H_HOME"`, `"F1H_AWAY"` |
| GAME | `"GAME_{SIDE}"` | `"GAME_HOME"`, `"GAME_AWAY"` |
| BATTER_TB | `"BATTER_TB_{SIDE}_{LINE}"` | `"BATTER_TB_OVER_1.5"`, `"BATTER_TB_UNDER_1.5"` |
| BATTER_HITS | `"BATTER_HITS_{SIDE}_{LINE}"` | `"BATTER_HITS_OVER_0.5"`, `"BATTER_HITS_UNDER_0.5"` |
| PITCHER_ER | `"PITCHER_ER_{SIDE}_{LINE}"` | `"PITCHER_ER_OVER_2.5"`, `"PITCHER_ER_UNDER_2.5"` |

### Settlement sources

All settlement uses MLB Stats API via `mlb_core.data.game_result.fetch_game_result(game_pk)`.
Returns None if game not Final (bet skipped, retried tomorrow). One API call per game_pk,
result cached and shared across all systems in the same settle run.

| System | MLB API field | Logic |
|---|---|---|
| NRFI/YRFI | `innings[0].away_runs + home_runs` | >0 = YRFI |
| 1I_AWAY | `innings[0].away_runs / home_runs` | away>0 AND home==0 |
| 1I_HOME | `innings[0].away_runs / home_runs` | home>0 AND away==0 |
| 1I_DRAW | `innings[0].away_runs / home_runs` | away==0 AND home==0 |
| F5 | `innings[0:5]` sum per side | compare home vs away; tie=push |
| HR | `batters[name].home_runs` | starter + HR > 0 = win; not starter = void |
| K | `pitchers[name].strikeouts` | vs line O/U |
| OUTS | `pitchers[name].outs` | vs line O/U |
| F1H | `innings[0:4]` runs | home vs away; push on tie; void if < 4 innings |
| GAME | all innings runs | home vs away; push on tie; void if < 5 innings (official) |
| BATTER_TB | `batters[name].total_bases` | vs line O/U; void if not starter |
| BATTER_HITS | `batters[name].hits` | vs line O/U; void if not starter |
| PITCHER_ER | `pitchers[name].earned_runs` | vs line O/U; void if not in boxscore |

### SGO market coverage (all markets, settlement status)

`fetch_game_result()` returns all fields needed for every SGO market.
Add new systems by extracting from SGO snapshot + reading from game_result dict.

| SGO market | odd_id pattern | MLB API field | Status |
|---|---|---|---|
| HR yes/no | `batting_homeRuns-*-yn-yes` | `batters[name].home_runs` | Live |
| Batter hits | `batting_hits-*-ou` | `batters[name].hits` | Live (log-only) |
| Batter total bases | `batting_totalBases-*-ou` | `batters[name].total_bases` | Live (log-only) |
| Batter RBI | `batting_RBI-*-ou` | `batters[name].rbi` | Backlog |
| Batter runs | `points-{PLAYER}-game-ou` | `batters[name].runs` | Backlog |
| Batter strikeouts | `batting_strikeouts-*-ou` | `batters[name].strikeouts` | Backlog |
| Stolen bases | `batting_stolenBases-*-ou` | `batters[name].stolen_bases` | Backlog |
| Pitcher strikeouts | `pitching_strikeouts-*-ou` | `pitchers[name].strikeouts` | Live |
| Pitcher outs | `pitching_outs-*-ou` | `pitchers[name].outs` | Live |
| Pitcher earned runs | `pitching_earnedRuns-*-ou` | `pitchers[name].earned_runs` | Live (log-only) |
| Pitcher hits allowed | `pitching_hits-*-ou` | `pitchers[name].hits_allowed` | Backlog |
| Pitcher walks | `pitching_basesOnBalls-*-ou` | `pitchers[name].walks` | Backlog |
| Pitcher pitches | `pitching_pitchesThrown-*-ou` | `pitchers[name].pitches_thrown` | Backlog |
| NRFI/YRFI O/U | `points-all-1i-ou-*` | `innings[0]` runs | Live |
| 1st inn 3-way | `points-*-1i-ml3way-*` | `innings[0]` runs | Live |
| F5 moneyline | `points-*-1ix5-ml-*` | `innings[0:5]` runs | Live |
| F3 moneyline | `points-*-1ix3-ml-*` | `innings[0:3]` runs | Backlog (needs model) |
| F7 moneyline | `points-*-1ix7-ml-*` | `innings[0:7]` runs | Backlog |
| 1st half ML | `points-*-1h-ml-*` | `innings[0:4]` runs | Backlog |
| Full game ML | `points-*-game-ml-*` | all innings runs | Backlog |
| First to score | `firstToScore-*-game-ml-*` | `innings` scan | Backlog |
| Last to score | `lastToScore-*-game-ml-*` | `innings` scan | Backlog |
| DK fantasy score | `fantasyScore-*-game-ou` | computed from batting stats | Backlog |
| Pitcher wins | `pitching_win-*-game-yn-*` | `pitchers[name].wins` | Backlog |

### game_result contract

`mlb_core.data.game_result.fetch_game_result(game_pk)` makes two MLB Stats API
calls (linescore + boxscore) and returns a structured dict. Returns None if the
game is not yet Final -- caller should skip and retry tomorrow.

```python
{
    "game_pk":  int,
    "final":    True,
    "innings":  [{"num": 1, "away_runs": 0, "home_runs": 0, "away_hits": 0, "home_hits": 0}, ...],
    "pitchers": {"luis castillo": {"starter": True, "strikeouts": 6, "outs": 17,
                                   "earned_runs": 3, "hits_allowed": 4, "walks": 3,
                                   "pitches_thrown": 108, "wins": 1, ...}},
    "batters":  {"aaron judge":   {"starter": True, "home_runs": 1, "hits": 2,
                                   "total_bases": 5, "rbi": 2, "runs": 1,
                                   "stolen_bases": 0, "strikeouts": 1, ...}},
}
```

All player names are normalized: NFD + ASCII fold + lower + strip.
Partial name matching: if exact lookup fails, tries substring match (handles Jr., accents).
`settle_bets.run()` calls this once per game_pk and caches -- shared across all systems.

### Team name resolution

SGO returns medium names ("Yankees"). Internal data uses 3-letter abbrevs.
Bridge: `mlb_core.odds.dk_scraper.resolve_team()`.

### Database / BetTracker contract

DSN format:
```
postgresql+pg8000://{user}:{password}@/{db}?unix_sock=/cloudsql/{INSTANCE}/...
```
Cloud Run reads from Secret Manager `mlb-db-url:latest`. Always include
`--add-cloudsql-instances` on every deploy.

**Cannot connect to Cloud SQL from Cloud Shell.** The Unix socket only
exists inside Cloud Run. Use the proxy endpoint for ad-hoc queries:
`curl -X POST http://localhost:8081/settle` etc.

**`bets` table has a `book TEXT` column** added 2026-05-16. Stores the canonical
onshore book name (`draftkings`, `fanduel`, `caesars`, `betmgm`, `thescore`,
`pointsbet`). NULL for bets logged before multi-book support. Auto-migrated
by `_MIGRATE_BOOK_SQL` in `bet_tracker.py` on first init.

**Always use `from mlb_core.config import DB_URL` in new routes, never `os.environ["DB_URL"]`.** The env var is `MLB_DB_URL` -- reading `DB_URL` directly causes `KeyError` at runtime. `mlb_core.config` handles the lookup correctly.

**K Pro v1 `model_meta_v1.json` had corrupt `feature_means`: `avg_ip_L5=1.0` (correct: 5.6), `k_per_9_L5=48.5` (correct: 8.66), `k_per_9_L10=48.8` (correct: 8.70).** Caused near-zero lambda predictions for all pitchers. Root cause: model trained when IP calculation bug was present. Fixed 2026-05-15 by patching GCS meta directly. K model needs full retrain with correct IP values.

---

## 6. Conventions

### Versioning
- Code: `git` on `main`
- Models: latest pointer + timestamped archive in GCS
- Schemas: documented in this file
- **CONTEXT.md**: Claude project is source of truth. Commit to repo at end
  of each session with `git add CONTEXT.md && git commit -m "docs: update CONTEXT.md"`.

### Storage abstraction
Always use `mlb_core.storage.{read_csv, write_csv, read_bytes, write_bytes, exists}`.
Never call the GCS Python client directly from runners.

### Build speed
Builds take ~2m20s. The bottleneck is `nvidia-nccl-cu12` (300MB, pulled by
xgboost). `selenium` and `pybaseball` were removed from `setup.py` -- do not
re-add them. Production deps only in `setup.py`.

### ASCII only in source files
Use ASCII-only characters in all Python string literals, comments, log messages, and docstrings. No Unicode punctuation: use `->` not `->` (U+2192), `--` not `--` (U+2014 em-dash), `>=` not `>=`. Em-dashes and Unicode arrows look identical to ASCII in the terminal but have different bytes, causing silent `str.replace()` assert failures. Enforced by convention; will be added to ruff config when linting is set up.

### Deploy script runs tests before building
`./deploy/deploy_service.sh` runs `python3 -m compileall` + `pytest tests/` before
`gcloud builds submit`. A failing test or syntax error aborts the deploy immediately.
Never invoke `gcloud builds submit` directly -- always use the deploy script so
`--add-cloudsql-instances` is preserved and tests gate the build.

### Read before write -- one deploy per task
Before editing any file: read it from the **local Cloud Shell file** (not
GitHub -- GitHub fetch can return stale cached content). Use `cat` or `sed`
to confirm exact content including whitespace. Identify all problems, write
all fixes in one `python3 -` script, verify with `grep`/`cat`, then one
commit and one deploy.

When `str.replace()` asserts fail: use `sed -n 'N,Mp' file` to read exact
whitespace from the local file before writing the replace string.

`cat >>` appends are fragile for multi-line additions -- use `python3 -` with
`str.replace()` instead. If a function already exists, appending creates a
duplicate -- Python silently uses the last definition.

**Always use `os.path.expanduser()` for the base path in patch scripts.**
Never hardcode `/root/mlb-betting` -- the Cloud Shell home is `/home/lmaynor21`.
Always start patch scripts with `base = os.path.expanduser("~/mlb-betting")`.

**Read exact anchor lines from local file before writing any patch.**
Use `sed -n 'N,Mp' file` to get the exact string including whitespace.
Never copy anchor strings from GitHub fetch output or Claude project docs --
both can be stale. Write the patch only after confirming the exact local content.
If a patch script asserts, it means the anchor was wrong -- read the file again.

### Debug logging
Add `logger.info` lines liberally when diagnosing silent failures. Log:
- What data was loaded (row counts, game_pk lists)
- What decisions were made and why rows were skipped
- Input/output counts at each stage

Silent failures are much harder to diagnose from Cloud Run logs. Err toward
more logging; remove noise after the bug is fixed if needed.

### Adding a new system
To add a new betting system:
1. Add to §1 table and §5 bet type / settlement tables
2. Create `runners/run_{sys}.py` and `runners/build_{sys}_features.py`
3. Add system to `settle_bets.py` systems loop + statcast/scoring checks
4. Add to `monitor_performance.py` systems list + `EXPECTED_HIT_RATES`
5. Add icon/color to `discord.py` `_SYSTEM_COLORS` and `post_all_systems_summary`
6. Add Cloud Scheduler jobs for feature build + wire into `/run`
7. Update CONTEXT.md §1, §2, §3, §5
8. Add rules to `mlb_core/rationale.py` `_SYSTEM_RULES` dict

OUTS is the model for a sub-market of an existing system: same runner,
separate tracker (`system="OUTS"`), no new feature build or model needed.

### Adding a new betting market (no new model needed)
When a new SGO market maps directly to an existing MLB API field in `game_result`:
1. Add extractor to `mlb_core/odds/sgo.py` following the `extract_k_odds()` pattern
2. Wire extractor into the appropriate runner (`run_k.py` for pitcher props, `run_hr.py` for batter props)
3. Add settlement logic to `_settle_k()` or `_settle_hr()` -- just read the right field from `game_cache`
4. Add bet_type to §5 naming convention table
5. Update SGO market coverage table in §5 (Status: Live)
6. Add to CONTEXT.md §5 settlement sources table

For markets that need a new model: build the model in a notebook first, then
follow the full "adding a new system" checklist above.

### Feature/column naming drift
Several places where the same concept has different names:
- `ump_total_run_impact_L30` (umpire master) ↔ `ump_k_boost_L30` (K model) -- proxied
- `model_features` vs `game_features` (F5 config had both pre-v5)
- `pitcher_is_home` (NRFI) -- `inning_topbot=="Top"` means home pitcher

When in doubt, find the notebook and match the model-meta features list exactly.

### Paper → live criteria (200-bet gate)
All five must pass:
1. ≥200 settled bets per system
2. Season ROI > 0% (HR: > -5% allowed)
3. Edge retention: avg model edge vs ROI within 15 pct points
4. Calibration: hit rate within 5 pct points of avg model probability
5. No system down more than 50 units at paper stakes

---

## 7. Cloud architecture

```
                              ┌──────────────────┐
                              │ Cloud Scheduler  │
                              │   9 cron jobs    │
                              └────────┬─────────┘
                                       │ OIDC
                                       ▼
┌────────────────┐         ┌──────────────────────┐
│  GitHub (main) │ → build │   Cloud Run service  │
└────────────────┘  manual │     mlb-betting      │
                           │   (Flask + gunicorn  │
                           │    timeout=3600s)     │
                           └──────┬───────────────┘
                                  │
                  ┌───────────────┼──────────────────┬─────────────┐
                  ▼               ▼                  ▼             ▼
            ┌──────────┐   ┌─────────────┐   ┌───────────┐  ┌────────────┐
            │   GCS    │   │ Cloud SQL   │   │   SGO     │  │  Discord   │
            │  bucket  │   │ (Postgres)  │   │   API     │  │  webhook   │
            └──────────┘   └─────────────┘   └───────────┘  └────────────┘
                  ▲
                  │
          ┌───────────────┐
          │ Cloud Run Jobs│
          │ mlb-retrain-* │
          └───────────────┘
```

**Service:** stateless HTTP, max 1 instance (≥2GB for feature builds).
Same Docker image for service AND jobs.

**Auth:** SA `mlb-betting-sa` has storage.objectAdmin, cloudsql.client,
secretmanager.secretAccessor.

**Secrets** (all in Secret Manager):
- `mlb-db-url` -- Postgres DSN with Unix socket
- `mlb-gcs-bucket` -- bucket name
- `sgo-api-key` -- SGO API key (version 3 is current -- v1 was exposed, v2
  had invisible newlines causing header errors)
- `discord-webhook-url` -- picks webhook (#daily-picks)
- `discord-webhook-summary` -- recap webhook (#daily-recap)
- `discord-ops-webhook-url` -- ops webhook (#ops-alerts)
- `discord-webhook-performance` -- performance webhook (#performance)

**Cloud Run Jobs:**
- `mlb-retrain-f5-meta` (DEPRECATED -- exits non-zero; use mlb-retrain-f5-v5)
- `mlb-retrain-hr-meta` (DEPRECATED -- exits non-zero; use mlb-retrain-hr-v6)
- `mlb-retrain-f5-v5` (full F5 retrain; added 2026-05-19)
- `mlb-retrain-hr-v6` (full HR retrain; added 2026-05-19)
- `mlb-retrain-nrfi-v17`
- `mlb-retrain-k-v1` (includes leakage guard; skip with K_SKIP_LEAKAGE_CHECK=1)
- `mlb-calibrate-nrfi` (fits isotonic calibrator for NRFI v17; run after any NRFI retrain)
- `mlb-calibrate-f5` (fits isotonic calibrator for F5 v5; run after any F5 retrain)
- `mlb-calibrate-k` (fits lambda calibrator for K v1; run after any K retrain)
- `mlb-calibrate-hr` (fits isotonic calibrator for HR v6; run after any HR retrain)
- `mlb-retrain-outs-v1` (full OUTS retrain; E04 2026-05-21)

**Cloud Build:** manual only (`gcloud builds submit`). No GitHub trigger yet.

---

## 8. Gotchas

**Build vs. deploy.** `gcloud builds submit` updates `:latest`. Cloud Run
pins to a digest at deploy time. Always run `gcloud run services update`
after a build, with `--add-cloudsql-instances` every time.

**Cloud SQL binding dropped on redeploy.** If you omit `--add-cloudsql-instances`,
the new revision loses the SQL binding and DB writes fail with `[Errno 5]`.

**Personal Google accounts can't mint Cloud Run audience tokens.** Use
`gcloud run services proxy <service> --port=8080` for manual curl tests.
Restart the proxy after a redeploy -- stale proxy returns Google's 404.

**`oddsAvailable=true` is not "today only" in SGO.** Returns 5-day window.
Always pass `startsAfter`/`startsBefore`. `et_day_window()` handles this.

**Statcast bat_score/post_bat_score are 100% null for 2021-2025.** Use
`scoring_master.csv` for run-based targets.

**Webhook messages don't trigger Discord bots.** GamblyBot ignores webhook
content. Need a real bot account for that.

**Cloud Shell uploads strip leading dots and auto-rename conflicts** to
`filename_(1).py`. Use `git pull` instead of uploading files when possible.

**`ALTER TABLE` migration poisons pg8000 connection if column already exists.**
Always run migrations in a **separate** `engine.begin()` block with its own
`try/except`, isolated from the schema creation block.

**`pd.read_sql` with pg8000 requires `text()` wrapper for named params.**
Passing a raw string with `:param` syntax to `pd.read_sql()` causes
`syntax error at or near ":"` in pg8000. Always wrap:
```python
# Wrong -- crashes with pg8000
df = pd.read_sql("SELECT * FROM bets WHERE system = :sys", conn, params={"sys": s})
# Correct
df = pd.read_sql(text("SELECT * FROM bets WHERE system = :sys"), conn, params={"sys": s})
```
This applies to any `pd.read_sql` call with named parameters against Postgres.

**Kelly floor zeroes out HR longshots.** HR uses `min_kelly_pct=0.001` (lowered from 0.005 on 2026-05-15) and 50% Kelly fraction. At +600 odds, edge=0.03 produces pct=0.0025 which clears the 0.001 floor giving ~$2.50 stake. The other systems use 0.005 which is appropriate for their shorter odds.

**NRFI feature build order.** F5's builder reads
`NRFI_Pro_System/data/pitcher_start_features.csv`. NRFI must rebuild before
F5. Dependency order enforced in `/build-all-features` code -- F5 always runs after NRFI.

**`lineup_pct_L` leakage.** Was in NRFI v17 training -- carried same-game
run information into the half-inning target. Removed in retrain. Never add
same-game batter stats as features in inning-1 models.

**K `model_features.csv` pitcher column is MLBAM integer ID, not name string.** `.str.contains()` on the `pitcher` column raises `AttributeError: Can only use .str accessor with string values`. Filter by numeric ID directly. All other name-based lookups (SGO, boxscore) go through `_pitcher_name` / `_pitcher_name_norm` columns added by `_attach_today_slate`.

**K build performance.** The opponent backfill pre-prepares the PA frame
once (`_prepare_pa_for_opp_features`) before the per-date loop. Do not
revert this -- the naive version was killed by gunicorn at 15min.

**`CURRENT_DATE` vs text column type mismatch.** The `game_date` column is stored
as `TEXT` (isoformat string). Comparing it to `CURRENT_DATE` (a Postgres `date`
type) without a cast raises `operator does not exist: text = date`. Always cast:
`game_date = CURRENT_DATE::text`. For parameterized queries, pass a Python
`date.today().isoformat()` string as a named param -- never use `CURRENT_DATE`
directly in parameterized queries.

**Bookmaker must be explicitly propagated to the results dict in each runner.**
`odds_info.get('bookmaker')` is available in the odds lookup dict but must be
copied into the row/results dict explicitly. It is NOT automatically included
in pivot tables (NRFI) or feature row merges (F5). Check each runner's
`results.append()` or `log_bet()` call includes `bookmaker` when adding new markets.

**NRFI calibration ground truth (12,662 games):** The model systematically
overestimates YRFI probability. When model says 40-45% YRFI, actual rate is
only 17%. When model says 45-50% YRFI, actual is 24%. The isotonic calibrator
is correct to map these down aggressively. High NRFI probs (80-86%) on
low-model-YRFI games are legitimate and well-supported by historical data.
Do not second-guess the calibrator on these -- it has 2,480+ games of support
in the 0.40-0.45 model YRFI bin alone.

**NRFI calibrator is fit on YRFI probs, not NRFI probs.** `calibrate_nrfi_v17.py` calls `iso.fit(oos_g['model_yrfi_prob'], oos_g['yrfi'])`. The runner must apply the calibrator to `model_yrfi_prob` and then derive `model_nrfi_prob = 1 - calibrated_yrfi`. Applying it to `model_nrfi_prob` produces a sign flip causing all games to show extreme YRFI probability. This is easy to get wrong -- the variable names look symmetric but are not.

**Calibrate scripts fit on the TRAIN slice (70%), not OOS split.** After C03
(2026-05-20) all retrain scripts use a 70/10/20 split: train (70%), val for
early stopping (10%), test for honest eval (20%). Calibrators fit on the
train slice only -- this gives adequate range coverage without leaking val/test.
Do NOT change calibrate scripts to fit on full df -- that would leak test set
into calibration.

**Calibrators must be refit after any model output range change.** Isotonic calibrators are fit on the OOS model output range. If the model is patched (feature_means fix, IP bug fix, etc.) without a full retrain, the output range may shift and the calibrator's X_min/X_max will no longer cover the new output range. sklearn clips out-of-bounds inputs to the nearest boundary value, mapping everything to 0 or 1. Always run the calibrate job after ANY change to model artifacts or feature_means, not just after a full retrain.

**Retrain sequence: always run calibrate job after retrain.** Each system has a paired retrain + calibrate job. Running retrain without calibrate leaves the runner using a stale calibrator fit on the old booster's outputs. The /retrain-weekly route fires all four retrains immediately then all four calibrate jobs 30 min later via a background thread. Manual retrain sequence per system:
  NRFI: mlb-retrain-nrfi-v17 -> mlb-calibrate-nrfi
  F5:   mlb-retrain-f5-v5    -> mlb-calibrate-f5
  K:    mlb-retrain-k-v1     -> mlb-calibrate-k
  HR:   mlb-retrain-hr-v6    -> mlb-calibrate-hr

**BATTER_TB, BATTER_HITS, and PITCHER_ER ship log-only (stake=0, kelly_triggered=False).**
All three use proxy models (Normal/Gamma approximations anchored to HR or K model output)
rather than dedicated trained models. Do not enable real Kelly sizing until post-hoc
analysis of ~100 settled bets confirms the proxy edge predicts outcomes. Gate is in
the runner code -- set stake and kelly_triggered manually after review.

**BATTER_K dropped -- no paired onshore book coverage.**
batting_strikeouts- over entries only appear on DraftKings; under entries only on
BetMGM, and the player sets don't overlap. Result: 0 matched pairs after
_best_book_odds_int(). Skip until book coverage improves.

**F1H and GAME innings sub-markets are live log-only in run_f5.py.**
F1H (first half, innings 1-4) and GAME (full game) use two-way SGO markets
(`points-*-1h-ml-*` and `points-*-game-ml-*`) and the F5 scalar proxy.
Both ship stake=0 until ~100 settled bets confirm calibration. To promote
F1H to real sizing: remove "F1H" from LOG_ONLY_SYSTEMS in run_f5.py.

**F3/F7 innings window markets are 3-way on SGO (draw/not_draw), not two-way ML.**
The extractors in sgo_innings_extractors.py assume two-way home/away format matching
extract_f5_ml_odds(). F3 and F7 need separate 3-way extractors before they can be
betted. F1H and GAME have two-way markets but the F5 proxy scalar (1.44/1.37) and
poor calibration (cal_err up to -0.135) make them unsuitable for real Kelly sizing.
All four innings window runners are shelved until dedicated models are trained.
Settlement code is in place for when they eventually ship.

**Savant leaderboards require a browser User-Agent on requests.get().**
pandas.read_csv(url) returns 403. savant_leaderboards.py uses a Chrome-style
User-Agent header. If Savant changes bot-detection, the HTML-response check
(startswith '<!') will catch it and log a warning rather than writing bad data.

**bat_tracking only available from 2023.**
DATASET_START_YEAR["bat_tracking"] = 2023. Backfill before 2023 returns empty
(not an error). Nightly refresh skips years before the dataset start year.

**Savant leaderboard data is cumulative season-to-date.**
Each nightly call overwrites the current season per-year cache file. Historic
seasons are immutable once the season ends and never re-fetched unless force=True.

**Backfill is slow by design.**
BACKFILL_SLEEP_MIN=8s, BACKFILL_SLEEP_MAX=14s between calls. Full 6-dataset
backfill takes 15-25 min. Run via /backfill-savant from Cloud Shell proxy only
-- not a Scheduler job. Gunicorn timeout 3600s is adequate for a full backfill.

**SGO API key is in Secret Manager version 3.** Version 1 was the old
exposed key. Version 2 had invisible newlines causing `Invalid leading
whitespace` errors in HTTP headers, silently returning 0 events from every
SGO snapshot. Always create secrets with
`echo -n "value" | gcloud secrets versions add --data-file=-` and verify
with `gcloud secrets versions access latest --secret=NAME | cat -A`.

**Umpire features are all NaN in production models.** The three umpire
features (`ump_overall_accuracy_L30`, `ump_k_boost_L30`,
`ump_consistency_L30`) were NaN-only in training data for all systems due
to a join bug. The K builder now correctly rolls the umpire master, but
`ump_k_boost_L30` is proxied via `ump_total_run_impact_L30`. Models were
trained and validated without umpire signal -- XGBoost handles NaN natively.

**3-way 1st inning ML is derived, not retrained.** `p_3way_away/home/draw`
are computed from existing NRFI half-probabilities. No new model needed.
The math: `p_away = p_away_half * (1 - p_home_half)`, etc.

**Pitcher outs O/U is a proxy model.** Uses `avg_ip_L5` from the K feature
CSV modelled as Normal(avg_ip, 1.5 IP std). Not a trained predictor --
edge signal will be weaker than K strikeout model.

**`scoring_master.csv` had no nightly refresh until 2026-05-14.** Bets
logged before that date may have pending settlement for game_pks not in
the master. Use `scoring_backfill_gcs()` with a date range to fix:
```python
from mlb_core.data.lineups import _get_games_for_date
from mlb_core.data.scoring import scoring_backfill_gcs
game_pks = []
for d in ["2026-05-12", "2026-05-13"]:
    game_pks.extend(g["game_pk"] for g in _get_games_for_date(d))
scoring_backfill_gcs("concrete-crow-445205-m4-mlb-data",
                     "Scoring/scoring_master.csv", game_pks)
```

**`BetTracker.summary()` was not filtering by system until 2026-05-14.**
All per-system Discord summaries showed K stats. Fixed with `text()` wrapper.

**Paper mode flag removed from Discord notifications (2026-05-17).** The 📄/💵
paper_tag was removed from bet headlines. All bets treated as cash going forward.
The `paper` column still exists in the DB for historical reference.

**HR bets store full team names in `away_team`/`home_team`** (e.g. "Red Sox", "Braves")
while all other systems store 3-letter abbrevs (e.g. "BOS", "ATL"). Root cause:
HR runner gets teams from feature CSV which uses SGO medium names. Frontend works
around this with `TEAM_ABBREV` lookup map in `picks-table.tsx` and `results/page.tsx`.
Proper fix: normalize team names in `run_hr.py` at log time. Backlog item.

**`game_date` was logged in UTC before 2026-05-17.** Bets logged at 22:00 UTC
(5pm CT) on May 16 got `game_date=2026-05-17` because UTC date had rolled over.
Fixed in `main.py` by using `date.today(_CT).isoformat()` (CT timezone). ~12 bets
in the DB have wrong dates -- not worth fixing in paper mode.

**GitHub fetch can return stale cached content.** `web_fetch` on raw GitHub
URLs can return an older version. Always read the local Cloud Shell file
with `cat` or `sed` -- that is the source of truth when `git status` is clean.
Use GitHub fetch only to understand file structure at the start of a session,
then switch to local reads before writing any patches.

**Cannot connect to Cloud SQL from Cloud Shell.** The DSN uses a Unix
socket (`/cloudsql/...`) that only exists inside Cloud Run. Use the proxy
endpoint for ad-hoc DB inspection instead of direct connections.

**`game_date` vs `run_date` in runners.** The exposure prefetch call inside
`_build_predictions()` must use `run_date` (the parameter passed to the function),
not `game_date` (which is a DataFrame column name, not a local variable at that
scope). Using `game_date` causes `NameError: name 'game_date' is not defined` at
runtime. Tests don't catch this because `_build_predictions()` is not called in
the test suite -- only the downstream settlement and exposure math is tested.

**`/build-all-features` is a single point of failure.** If it errors midway,
downstream systems silently use yesterday's feature CSVs. The build sentinels
(`{system}/data/last_build.json`) catch this at two levels: `monitor_ops` at
12:50 UTC alerts if any sentinel is stale or status=error, AND each runner now
calls `check_build_sentinel()` from `mlb_core/storage.py` at run time -- aborting
with a Discord alert rather than scoring on stale features.

**`check_build_sentinel(gcs_bucket, system_prefix)` in `mlb_core/storage.py`.**
Reads `{system_prefix}/data/last_build.json` via `read_bytes(key)` (single-arg
signature -- do not pass bucket separately). Returns `(ok, reason)`. Non-fatal
on GCS read errors (returns ok=True with warning) so a transient GCS blip does
not block betting. Called in all 4 runners immediately after snapshot load,
before any scoring work.

**K/OUTS settlement voids bets when pitcher not in boxscore.** If a pitcher is scratched before throwing a pitch, they won't appear in the MLB Stats API boxscore. The settler now voids the bet (result='void', profit=0) rather than leaving it pending indefinitely. This matches DK rules: pitcher must throw at least one pitch for props to grade.

**IL-return skip cross-references actual IL status (2026-05-20).** All three runners (NRFI, K, F5) skip a pitcher if `days_since_last_appearance > 7 AND pitcher_id in fetch_il_pitcher_ids()`. Previously the check was purely day-count based, which incorrectly blocked healthy starters mid-rotation (scheduled rest, rain delay, etc.). `fetch_il_pitcher_ids()` in `mlb_core/data/lineups.py` calls `/api/v1/teams/{teamId}/roster?rosterType=injured` for all 30 teams and returns the set of pitcher MLBAM IDs currently on IL. Fails open (returns empty set) on network error so a transient API blip never blocks betting.

**MLB Stats API injured roster can lag activations by 1-2 days.** `/roster?rosterType=injured` may still list a pitcher who was recently activated (e.g. Jack Leiter showed Active on ESPN but still on injured roster via API). The 7-day threshold mitigates this -- a pitcher on normal 5-day rotation never hits the threshold regardless of roster API lag. Real 10-day IL stints always produce gaps >= 10 days.

**HR settlement uses MLB Stats API boxscore (not Statcast).** `_settle_hr`
calls the MLB Stats API per game_pk. If the game is not Final, the bet is
skipped (retried tomorrow). If Final: player not in starting lineup -> void
(DK voids non-starters); starter with HR -> win; starter without HR -> loss.
No Statcast dependency for HR settlement.

**C01 diagnostic (2026-05-20): `platoon_edge` carries genuine signal -- keep it.**
Removing `platoon_edge` from NRFI HALFINN_FEATURES dropped OOS AUC 0.5791 -> 0.5314
(-0.0477). The concern that it reconstructed `lineup_pct_L` leakage is resolved --
the feature contributes independent platoon-matchup signal. Do not remove it.

**C03 (2026-05-20): all 4 retrain scripts use 70/10/20 train/val/test split.**
Early stopping now uses the val slice (last 1/8 of the train window); dtest is
never seen during training or tuning. Prior scripts used dtest as the eval_set
for early stopping, inflating reported AUC by ~0.003-0.005. CV loop in K retrain
also fixed -- each fold carves val from df_tr for early stopping.

**C07 (2026-05-20): K Monte Carlo uses Negative Binomial, not Poisson.**
MLB strikeout counts over-disperse relative to Poisson (variance ~1.4-1.6x mean).
`nb_alpha` is fit from full-data residuals in `retrain_k_v1.py` using method of
moments: `alpha = clip((var - mu) / mu^2, 0.01, 0.50)`. Stored in `model_meta_v1.json`
as `"nb_alpha"`. `run_k.py` loads it and passes to `_simulate_k` via function
attribute `_simulate_k._nb_alpha`. Falls back to Poisson if key missing (old meta).
nb_alpha is only valid after the first retrain following this change (2026-05-20).

**C04 (2026-05-20): retrain scripts store empirical percentile dists; PSI monitor uses interpolation.**
All 4 retrain scripts now compute `fpdists` (p5/p10/p25/p50/p75/p90/p95 + prop_1) for every
feature and store under `"feature_dists"` in `model_meta`. `monitor_drift.py` uses linear
interpolation over these percentiles to reconstruct the training distribution for PSI binning.
Falls back to Gaussian if `feature_dists` not in meta (old models). Takes effect after next retrain.

**C08 (2026-05-20): K Monte Carlo IP scaling fixed -- k_per_9_L5 * avg_ip replaces lambda * (ip/5).**
Diagnostic showed residual slope -1.13 vs IP: model over-predicted Ks at low IP, under-predicted
at high IP. Root cause: naive `lambda_k * (avg_ip_L5 / 5.0)` assumes linear K-per-IP but K rate
drops in later innings (fatigue, lineup cycling). Fix: `_simulate_k` now uses
`k_per_9_L5 / 9.0 * avg_ip_L5` as the expected K count. Falls back to penalty-only scaling
if `k_per_9_L5` is missing. `k_per_9_L5` is in `model_features.csv` and passed from `row`
at the call site.

**F5 IL-return (2026-05-20): `home_game_date` / `away_game_date` now in F5 feature CSV.**
`game_date` added to `BASE_COLS` in `_apply_joins` in `build_f5_features.py`. Columns land
as `home_game_date` and `away_game_date` in `model_features.csv`. `run_f5.py` skips any
game where either starter's last appearance is >10 days before `run_date` -- same IL-return
threshold as K and NRFI runners (T19). Takes effect after next F5 feature build (12:00 UTC).

---

**`mlb-refresh-data` scheduler job had never run (2026-05-21).** `Last attempt: None` despite `State: ENABLED`. Weather, scoring, umpires, and Statcast masters were only populated via manual backfills. Result: weather_master.csv had only 15 dates of 2026 coverage (193 rows), causing 85% null rate on weather features in NRFI/F5/HR feature CSVs. NRFI model_prob AUC collapsed to 0.500 (coin flip) due to feature imputation masking all weather signal. Fix: verify scheduler job URI and trigger manually after any deploy. Always check `/model-health` freshness block after a data gap.

**`/refresh-data` response was missing weather/scoring/umpires/Statcast results (2026-05-21).** Route ran all four fetchers but only included `savant_leaderboards` in the JSON response. Silent failures in any fetcher were invisible. Fixed: each fetcher now wrapped in individual try/except with results included in response under `fetchers` key. Response now returns `status: "partial"` if any fetcher fails.

**NRFI live AUC 0.425 despite retrain OOS AUC 0.5698 (2026-05-21).** Root cause: weather features 85% null in live feature CSV due to weather master gap (see above). XGBoost fills nulls with feature_means, collapsing all games to near-identical inputs and pushing model output toward base rate. AUC 0.500 = model outputting uniform probabilities. Fix: backfill weather master, rebuild features, retrain. Always run /model-health after a data gap to catch feature null rates before they silently kill model performance.

**`market_prob` in NRFI bets stores the book fair probability, not the model probability.** The AUC script must use `model_prob` column for NRFI, not `market_prob`. For other systems (HR, K, F5, OUTS) both columns are similar. NRFI `market_prob` AUC of 0.425 was measuring book implied prob vs outcomes -- not model signal. Confirmed: NRFI `model_prob` AUC is 0.500 (weather null issue), not 0.425.

**`fetch_il_pitcher_ids()` throws in Cloud Run causing `NameError: _il_ids` (2026-05-21).** Works locally (returns 390 IDs) but fails in Cloud Run cold start. Fixed: wrapped in try/except that fails open (empty set) in run_nrfi.py and run_k.py. The fail-open behavior matches the original design intent -- a transient API blip should never block betting.

**`/model-health` route uses `X-API-Key` auth, not OIDC.** Call directly via Cloud Run URL without proxy: `curl -s "https://mlb-betting-628109313129.us-central1.run.app/model-health" -H "X-API-Key: $KEY"`. Same pattern as all `/api/public/*` endpoints.

**AUC added to `monitor_performance.py` rolling and season stats (2026-05-21).** `_auc()` uses Mann-Whitney method (no sklearn). Alert thresholds: AUC < 0.50 = "rank-ordering backwards, retrain required"; AUC 0.50-0.52 = "near coin-flip" warning. Both appear in Discord alert embed and Monday digest. Use AUC as the primary leading indicator -- it detects model failure earlier than ROI or hit rate.

**OUTS `market_prob` AUC (0.658) exceeds `model_prob` AUC (0.556).** The book's implied probability is a better predictor of OUTS outcomes than the model. OUTS wins are driven by betting market favorites, not genuine model edge. Vulnerable to line movement and book limits. Do not promote OUTS past paper gate on ROI alone -- require positive CLV t-stat > 2 first.

**`/backfill-data` route added for weather/scoring/umpires (2026-05-21).** Accepts `start_date`, `end_date`, `systems` list. Weather loops date-by-date via `_pull_weather_date()`. Scoring resolves dates to game_pks then calls `scoring_backfill_gcs()`. Umpires calls `umpires_backfill_gcs()` directly. Run from Cloud Shell only -- not a Scheduler job. Example: `curl -s -X POST https://.../backfill-data -H "X-API-Key: $KEY" -d '{"start_date":"2026-04-01","systems":["weather"]}'`.

**Cloud Run Job creation requires `--set-cloudsql-instances` not `--add-cloudsql-instances`.** The service also requires `--service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com` -- without it the job uses the default compute SA which lacks Secret Manager access. Full working command:
```bash
gcloud run jobs create JOB_NAME \
  --image=gcr.io/concrete-crow-445205-m4/mlb-betting:latest \
  --region=us-central1 \
  --command=python \
  --args="-m,training.MODULE_NAME" \
  --memory=4Gi --cpu=2 \
  --set-secrets=MLB_GCS_BUCKET=mlb-gcs-bucket:latest,MLB_DB_URL=mlb-db-url:latest \
  --set-cloudsql-instances=concrete-crow-445205-m4:us-central1:mlb-betting-db \
  --service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com \
  --project=concrete-crow-445205-m4
```
If job already exists in error state: `gcloud run jobs update JOB_NAME --region=us-central1 --service-account=mlb-betting-sa@...` then execute.

**E02 (2026-05-21): F5 walk-forward CV loop leaked test into early stopping.** The CV loop in `retrain_f5_v5.py` was using `dtest` as the eval set for early stopping, inflating reported CV AUC by ~0.003-0.005. Fixed: each fold now carves a val slice from the train window (C03 pattern). Same fix applied earlier to NRFI and K.

**E03 (2026-05-21): `capture_closing_lines.py` had two bugs blocking CLV.** (1) `bt_upper` NameError -- variable was named `bet_type` not `bt_upper`. (2) `BetTracker("unused")` used a placeholder DB path instead of the real `MLB_DB_URL`. Both caused silent failures in the CLV capture loop. Fixed: variable renamed, real DB URL wired in.

**E04 (2026-05-21): OUTS trained model replaces Normal proxy.** `_simulate_outs()` was `Normal(avg_ip, 1.5)` -- a proxy with no training. Replaced by `retrain_outs_v1.py` which trains a `count:poisson` XGBoost model on `starter_outs` target (added to K feature CSV as `avg_ip * 3`). Model artifacts live in `OUTS_Pro_System/models/`. Runner falls back to Normal proxy if model not found in GCS. Cloud Run Job: `mlb-retrain-outs-v1`. Run after rebuilding K features.

**E06 (2026-05-21): deploy_service.sh was deploying with 0% traffic.** New revisions were created but not receiving traffic, requiring manual `gcloud run services update-traffic --to-latest`. Fixed: added `--traffic=100` to the `gcloud run services update` call in `deploy/deploy_service.sh`. Also added missing Discord webhook secrets (`DISCORD_WEBHOOK_SUMMARY`, `DISCORD_WEBHOOK_OPS`, `DISCORD_WEBHOOK_PERFORMANCE`) to the `--set-secrets` flag.

**E09 (2026-05-21): Optuna hyperparameter tuner added.** `training/tune_hyperparams.py` runs Optuna nested CV search (50 trials, 3-fold time-series inner CV) for NRFI/K/F5/HR. Writes tuned params to GCS as `{system}_tuned_params.json`. Retrain scripts pick these up automatically via `load_tuned_params()` if present. Run after E02-E07 are complete -- tuning on broken features is noise. Requires `pip install optuna --break-system-packages`.

**E10 (2026-05-21): Line movement feature added to schema and runners.** `bets` table now has `morning_odds INTEGER` and `line_move_pct REAL` columns (auto-migrated). All four runners load the morning snapshot (15:55 UTC) via `mlb_core.odds.line_movement.load_morning_odds()` and store `morning_odds` per bet. `capture_closing_lines.py` computes `line_move_pct = (closing_implied - morning_implied) / morning_implied * 100` at capture time. Positive = line moved against our bet (book shortened odds). Negative = potential value signal (line lengthened). After 200+ bets with morning_odds populated, add `morning_line_move_pct` to feature lists and retrain.

**`statcast_nightly_gcs` fetches `today-1` at 14:00 UTC (9am CT) but Statcast publishes yesterday's data around 2-3pm ET. The nightly refresh-data job silently returns 0 rows and logs "ok". Fix: dedicated `mlb-refresh-statcast` scheduler job at 21:00 UTC runs after Statcast publishes, feeding the next day's feature build.

**`/backfill-data` does not support statcast.** Use `/backfill-statcast` with `{"dates":["YYYY-MM-DD",...]}` for Statcast pitch data. `/backfill-data` only handles weather, scoring, and umpires. `/backfill-savant` handles Savant leaderboards with `{"start_year":N,"end_year":N}`.

**`/backfill-statcast` takes a `dates` list, not `start_date`/`end_date`.** Passing `{"start_date":"..."}` returns `{"error":"dates list required"}`. Correct call: `{"dates":["2026-05-19","2026-05-20"]}`.

**`capture_closing_lines.py` had three bugs blocking CLV (2026-05-22).**
(1) `bt_upper` NameError in K/OUTS branch -- E03 fix renamed the top-level
variable but missed this reference. Fixed: `bt_upper` -> `bet_type`.
(2) SGO team name lookup used `ev.get("away_team")` which is always None --
SGO event structure uses `teams.away.names.short`. Fixed: read from
`ev["teams"]["away"]["names"]["short"]` with resolve_team fallback.
(3) NRFI and F5 extractor calls passed `{ev.get("id",""): ev}` (a dict) --
extractors expect a list. Iterating a dict yields keys (strings), causing
`AttributeError: 'str' object has no attribute 'get'`. Fixed: pass `[ev]`.

**`public_api.get_picks` and `get_recent_settled` were missing CLV columns.**
`closing_odds`, `clv_pct`, `morning_odds`, `line_move_pct` not in SELECT.
CLV values written to DB correctly but never returned by public API. Fixed:
added all four columns to both SELECTs in `runners/public_api.py`.

**`mlb-capture-closing` scheduler job uses OIDC but `/capture-closing` has
no auth check.** OIDC token is sent but ignored -- route is open. Job was
returning `status: {}` (empty, meaning success) but CLV was 0 because the
route was crashing inside due to the bugs above, not auth rejection.

**`morning_odds` will be NULL for bets placed before E10 deploy (2026-05-21).** The column exists but historical bets have no morning snapshot to reference. `line_move_pct` will also be NULL for these. Only bets placed after the deploy will have both fields populated. CLV and line movement analysis should filter to `morning_odds IS NOT NULL`.

**F5 CV loop C03 fix (2026-05-21).** `retrain_f5_v5.py` walk-forward CV was leaking test into early stopping (using `dtest` as eval set). Fixed to match NRFI/K pattern: carve val slice from train, never touch test during training. Reported CV AUC was inflated ~0.003-0.005 before this fix.

**`starter_outs` column added to K feature CSV (2026-05-21).** `build_k_features.py` now computes `starter_outs = round(starter_ip * 3)` and includes it in `model_features.csv`. This is the training target for OUTS Pro v1. Must rebuild K features before running `mlb-retrain-outs-v1`.

**F5 ML extractor bookmaker used `_home_book` only.** Fixed 2026-05-20 to use
`_home_book or _away_book`. 75 historic F5 bets backfilled via one-off
`/backfill-f5-book` route (now removed). New F5 bets populate book correctly.

## 9. Performance monitor

`runners/monitor_performance.py` -- fires at 09:30 UTC daily via `mlb-monitor`.

Alert thresholds (overrideable via env vars):
- `MONITOR_ROI_WARN=-15` -- ROI over last 30 bets below -15% triggers alert
- `MONITOR_HIT_RATE_DROP=10` -- hit rate more than 10pct below expected
- `MONITOR_MIN_BETS=20` -- minimum settled bets before alerting
- `MONITOR_ROLLING_WINDOW=30` -- window size

Posts degradation alerts to #ops-alerts via DISCORD_WEBHOOK_OPS.
Posts weekly digest every Monday to #performance via DISCORD_WEBHOOK_PERFORMANCE.

Expected hit rates (baselines -- update after 200 bets per system):
- HR: 7%, NRFI: 55%, F5: 52%, K: 52%, OUTS: 52%
- F1H: 52%, GAME: 52% (update after 100 settled bets; scalar proxy, treat as placeholder)
- BATTER_TB: 52%, BATTER_HITS: 52%, PITCHER_ER: 52% (update after 100 settled bets each; proxy models, treat baselines as placeholders)

---

## 10. Ops monitor

`runners/monitor_ops.py` -- fires at 12:50 UTC daily via `mlb-monitor-ops`.

Checks (all post-feature-build):
- All 9 Cloud Scheduler jobs: last run status code
- SGO snapshot age < 26hrs
- All 4 system `model_features.csv` age < 26hrs
- All 4 data masters (scoring, statcast, weather, umpires) age < 26hrs
- All 4 model artifacts exist in GCS
- Any bets pending > 3 days

Silent on clean run. Posts to #ops-alerts via DISCORD_WEBHOOK_OPS on failure.

---

## 11. Pointers to other docs

- `ipynb_CONTEXT` -- modeling theory + per-notebook summaries
- `deploy/RETRAIN_NOTES.md` -- retrain pipeline runbook + rollback
- The notebooks (`*.ipynb`) -- canonical modeling logic
- Latest session handoff -- point-in-time state, open action items

---

## 12. SGO API reference

**Base URL:** `https://api.sportsgameodds.com`
**Client:** `mlb_core.odds.sgo.SgoClient`
**Key:** Secret Manager `sgo-api-key` version 3 (v1 exposed, v2 had newlines)

### Quota (amateur tier)
- 10 requests/minute -- client paces at 7s between calls
- Monthly entity limit: 2,500 entities/month (verified 2026-05-19 via /v2/account/usage/)
- Each event returned = 1 entity regardless of market count
- Typical daily cost: ~15 entities per snapshot (15 games max), 4 snapshots/day = ~60/day
- Monthly projection: ~1,860 entities/month -- leaves ~640 headroom for doubleheaders
- Do NOT add a 5th daily snapshot -- pushes to ~2,325/month, too close to the 2,500 limit

### Key methods
```python
client = SgoClient()
events = client.fetch_mlb_slate(run_date="2026-05-15")  # raises on error
snapshot = load_snapshot("Odds/sgo/latest.json")        # reads from GCS
```

`fetch_mlb_slate()` raises on network/auth errors -- `snapshot_odds.py` catches
and posts Discord alert. Returns `[]` on genuine empty slate (off-day) -- leaves
`latest.json` untouched so runners use yesterday's snapshot.

### Slate windowing
Always filter by ET day using `et_day_window(run_date)`:
```python
starts_after, starts_before = et_day_window("2026-05-15")
# Returns ISO8601 strings for 00:00 and 23:59 ET
```
`oddsAvailable=true` returns a 5-day window without this filter.

### Snapshot timing
DK K props for evening games are not posted until ~2-3pm ET.
Morning snapshot (15:55 UTC / 10:55am ET) may miss evening game props.
Evening snapshot (21:55 UTC / 4:55pm ET) catches all props.
Dedup in runners prevents double-logging if both snapshots have the same game.

### Adding a new extractor
Follow the `extract_k_odds()` pattern in `sgo.py`:
1. Find the market odd_id pattern from a live SGO snapshot
2. Filter `event["odds"]` by odd_id prefix
3. Call `_best_book_odds_int(entry)` -- returns `(odds_int, book_name)` for best onshore book
4. Return dict keyed by player name or event_id

---

## 13. Scheduler reference

**Location:** `us-central1`
**Service URL:** `https://mlb-betting-628109313129.us-central1.run.app`
**Auth:** OIDC via `scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com`

### Full job inventory

| Job | Schedule (UTC) | Endpoint | Deadline | Body |
|---|---|---|---|---|
| `mlb-settle` | `0 9 * * *` | `/settle` | 600s | `{}` |
| `mlb-monitor` | `30 9 * * *` | `/monitor` | 120s | `{}` |
| `mlb-refresh-data` | `0 14 * * *` | `/refresh-data` | 300s | `{}` |
| `mlb-build-all-features` | `30 14 * * *` | `/build-all-features` | 1800s | `{"systems":["HR","NRFI","K","F5"],"continue_on_error":false}` |
| `mlb-monitor-ops` | `20 15 * * *` | `/monitor-ops` | 120s | `{}` |
| `mlb-retrain-weekly` | `0 6 * * 1` | `/retrain-weekly` | 300s | `{}` |
| `mlb-refresh-statcast` | `0 21 * * *` | `/refresh-data` | 300s | `{"systems":["statcast"]}` |
| `mlb-snapshot-morning` | `55 15 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-betting-morning` | `0 16 * * *` | `/run` | 180s | `{"systems":["NRFI","HR","F5","K"],"run_type":"morning"}` |
| `mlb-snapshot-afternoon` | `0 19 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-snapshot-evening` | `55 21 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-betting-evening` | `0 22 * * *` | `/run` | 180s | `{"systems":["NRFI","HR","F5","K"],"run_type":"evening"}` |
| `mlb-snapshot-pregame` | `30 23 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-capture-closing` | `0 0 * * *` | `/capture-closing` | 300s | `{}` |
| `mlb-monitor-drift` | `0 9 * * 1` | `/monitor-drift` | 300s | `{}` |

### status.code values
- `-1` -- never run or ran successfully
- `0` -- success
- `2` -- error (HTTP non-2xx)
- `13` -- deadline exceeded (DEADLINE_EXCEEDED)

### Manual triggers
```bash
# Trigger a scheduler job immediately
gcloud scheduler jobs run mlb-settle --location=us-central1

# Delete today's bets (all systems) and re-run clean
# Step 1: delete via /reset-bets (requires SITE_API_KEY)
curl -s -X POST http://localhost:8081/reset-bets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key)" \
  -d '{"date": "2026-05-20"}' | python3 -m json.tool

# Delete a specific system only
curl -s -X POST http://localhost:8081/reset-bets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key)" \
  -d '{"date": "2026-05-20", "system": "NRFI"}' | python3 -m json.tool

# Step 2: re-run all systems after reset
gcloud scheduler jobs run mlb-snapshot-morning --location=us-central1
sleep 10
gcloud scheduler jobs run mlb-betting-morning --location=us-central1

# Or trigger evening run
gcloud scheduler jobs run mlb-snapshot-evening --location=us-central1
sleep 10
gcloud scheduler jobs run mlb-betting-evening --location=us-central1

# Run all scheduled jobs manually in order (full daily cycle)
gcloud scheduler jobs run mlb-refresh-data --location=us-central1
sleep 30
gcloud scheduler jobs run mlb-build-all-features --location=us-central1
sleep 60
gcloud scheduler jobs run mlb-monitor-ops --location=us-central1
gcloud scheduler jobs run mlb-settle --location=us-central1
gcloud scheduler jobs run mlb-monitor --location=us-central1

# Talk to the service via proxy
gcloud run services proxy mlb-betting --region=us-central1 --port=8081 &
sleep 4
curl -s -X POST http://localhost:8081/settle | python3 -m json.tool

# Build features for one system
curl -s -X POST http://localhost:8081/build-features   -H "Content-Type: application/json"   -d '{"system":"NRFI"}' | python3 -m json.tool

# Build all systems
curl -s -X POST http://localhost:8081/build-all-features   -H "Content-Type: application/json"   -d '{"systems":["HR","NRFI","K","F5"],"continue_on_error":true}' | python3 -m json.tool
```

### Adding a new scheduler job
```bash
gcloud scheduler jobs create http JOB_NAME   --location=us-central1   --schedule="CRON"   --uri="https://mlb-betting-628109313129.us-central1.run.app/ENDPOINT"   --message-body='{"key":"value"}'   --headers="Content-Type=application/json"   --oidc-service-account-email="scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com"   --oidc-token-audience="https://mlb-betting-628109313129.us-central1.run.app"   --attempt-deadline=NNNs
```
Max `attempt-deadline`: 1800s.

### monitor_ops.SCHEDULER_JOBS list
`monitor_ops.py` has a hardcoded `SCHEDULER_JOBS` list. Update it when
adding or removing jobs -- it drives the scheduler health check.

---

## 14. DK grading rules (MLB props)

These are the DraftKings house rules that govern how bets are settled.
Knowing these prevents incorrect settlement logic and bad model assumptions.

### Batter props (HR, hits, TB, RBI, runs, Ks, SB)
- **Must start**: Player must be in the starting lineup. Pinch hitters who
  never start are voided even if they record a plate appearance.
- **Must record a plate appearance**: If a starter is scratched before
  recording a PA (e.g. injury in warmups), bet is void.
- **FanDuel differs**: FanDuel only requires a plate appearance (no start
  requirement). Same player prop grades differently across books.

### Pitcher props (Ks, outs, ER, hits, walks, pitches)
- **Must throw a pitch**: If listed starter is scratched before throwing a
  pitch, bet is void.
- **No minimum innings**: Unlike some books, DK does not require a minimum
  IP for pitcher props to grade. If the starter throws 1 pitch and leaves
  due to injury, the bet grades on whatever stats were recorded.
- **Bulk/opener situations**: If a team uses an opener, the "starter" for
  prop purposes is the player DK listed at bet time. Verify the SGO market
  references the correct pitcher MLBAM ID.

### Game-level props (NRFI, F5, full game)
- **Official game requirement**: Game must be official (5 innings for the
  visiting team to have batted, or the home team completes 5 innings if
  leading) for F5 and full-game props to grade. Suspended games before
  5 innings = void.
- **NRFI**: Grades after the top and bottom of the 1st inning complete.
  Rain delay mid-inning = wait for completion. Game called before inning
  1 completes = void.
- **Rainouts/postponements**: All props void if game is postponed before
  starting. If postponed mid-game before official, props void.

### Settlement implications for this system
- HR settler: void if `not starter` (batting_order % 100 != 0) -- correct
- K/OUTS settler: no minimum IP check needed -- DK grades on whatever stats
  recorded as long as pitcher threw at least 1 pitch
- NRFI settler: if game_result has < 1 full inning, skip (not Final yet)
- F5 settler: if `len(innings) < 5`, skip -- game not yet official

---

## 15. When to update this file

- Adding/removing a system → §1, §2, §3
- Changing a contract → §5
- New market or bet type → §5 (bet type table + settlement table) + §14 (DK grading rules)
- New gotcha → §8
- New infra → §7
- Changes to file layout → §2
- Performance monitor threshold change → §9
- Ops monitor check change → §10
- Scheduler job added/removed → §13 + update monitor_ops.SCHEDULER_JOBS
- SGO API change → §12
- DK house rule change → §14
- Discord server change → §19

**Don't put point-in-time state here.** That belongs in the session handoff.

---

## 16. Beezy.VIP -- the frontend

`beezy-vip/` is the public-facing website for the betting service. It lives
as a subdirectory of this repo and is deployed separately to Vercel.

### What it is

Next.js 16 / React 19 frontend. Read-only -- it never writes
to the production `bets` table.
**`lib/db.ts` deleted.** Types (`Bet`, `SystemStats`) are now in `lib/types.ts`.
All data flows through the Cloud Run public API. Do not re-add `pg` as a dep.

**`npm test` runs 31 tests** (4 skipped) via jest + ts-jest + jsdom.
Config: `jest.config.ts`. Test file: `tests/index.test.tsx`.
Run before any commit touching schema contracts or utility functions.

**`app/results/page.tsx` is a server component.** Fetches picks + stats at
request time, passes as props to `results-client.tsx` (client component).
First paint is SSR with real data. Do not add useEffect fetches here.

**All visible pages use pure inline styles** -- Tailwind v4 with `@tailwindcss/postcss`
does not reliably generate CSS in this monorepo subdirectory. Migration completed
2026-05-18. Dead pages (teams, players, pitchers, games, recap) deleted. Articles are served statically from `beezy-vip/lib/articles-static.ts`
and `beezy-vip/content/learn/*.md` -- no DB needed.

Production URL: https://mlb-betting-rose.vercel.app (custom domain beezy.vip
pending DNS configuration).

### Repo layout

```
beezy-vip/
├── app/                          Next.js App Router pages
│   ├── page.tsx                  Landing page
│   ├── picks/                    Pick browser pages
│   ├── results/                  Results history
│   ├── models/                   Model methodology
│   ├── tools/                    Betting calculators
│   ├── learn/                    Static articles (served from repo)
│   │   └── [slug]/               Individual article pages
│   ├── dashboard/                Auth-gated member pages
│   ├── legal/                    Terms, privacy, responsible gambling, refunds
│   ├── blocked/                  Geo-restriction landing page
│   └── api/                      Route handlers (picks, stats, webhooks, cron)
├── components/
│   ├── landing/                  Hero, blotter, systems grid, how-it-works
│   ├── layout/                   Nav (with paper-mode banner), footer
│   ├── picks/                    Picks table, filter bar
│   └── ui/                       Primitives: SystemBadge, ResultPill, PnL, StatCard
├── app/
│   ├── api/
│   │   ├── picks/route.ts        Next.js proxy -- forwards ?params to Cloud Run
│   │   └── stats/route.ts        Next.js proxy -- forwards to Cloud Run
├── lib/
│   ├── betting-api.ts            Fetch-based client. Server components call Cloud Run
│   │                             directly. Client components use /api/* proxy routes.
│   ├── types.ts                  Shared TypeScript interfaces: Bet, SystemStats.
│   │                             Import from here. lib/db.ts has been deleted.
│   ├── tokens.ts                 Design tokens: B, SYSTEM_COLOR, SYSTEM_PILL,
│   │                             TEAM_ABBREV, pickLabel. Import from here.
│   ├── articles-static.ts        10 static articles (no DB needed)
│   ├── learn-db.ts               Vercel Postgres pool for learn_articles (unused)
│   ├── auth.ts                   Clerk auth helpers
│   ├── odds.ts                   Kelly, implied prob, formatting utilities
│   └── model-specs.ts            Static model metadata
├── middleware.ts                 Clerk auth + geo-blocking
├── next.config.ts
├── tailwind.config.ts            DELETED -- Tailwind v4 auto-discovers; config was breaking scanning
├── vercel.json                   Cron job definitions
└── tests/
    └── index.test.ts             Frontend unit tests (schema contract, geo, ResultPill)
```

### How the site gets its data

The site does NOT connect to Cloud SQL directly. All bet data flows through
the Cloud Run public API:

```
Server components:  Vercel SSR → GET /api/public/* → Cloud Run → Cloud SQL
Client components:  Browser → GET /api/* (Next.js proxy) → Cloud Run → Cloud SQL
```

**Critical:** `BETTING_API_URL` and `BETTING_API_KEY` are server-only env vars.
Client components (`'use client'`) cannot access `process.env` at runtime.
All client-side fetching must go through `app/api/picks/` and `app/api/stats/`.
`betting-api.ts` auto-detects `typeof window !== 'undefined'` and routes accordingly.

The Cloud Run service exposes five read-only endpoints
(six including sparkline) authenticated with
`X-API-Key` header (secret: `site-api-key` in Secret Manager):

| Endpoint | Cache | Description |
|---|---|---|
| `GET /api/public/picks/today` | 60s | Today's kelly-triggered picks |
| `GET /api/public/picks` | 60s | Filtered picks (system, date, status, book, limit, offset). Always filters to `kelly_triggered=true`. |
| `GET /api/public/picks/recent` | 120s | Last N settled picks |
| `GET /api/public/stats/summary` | 300s | Overall + per-system stats |
| `GET /api/public/stats/sparkline` | 300s | Daily cumulative P&L last 30 days |

The site's `lib/betting-api.ts` wraps these with typed fetchers. All pages
that call these are marked `export const dynamic = 'force-dynamic'` to
prevent Next.js from prerendering them without the env vars.

### Schema contract

The site uses production column names from the `bets` table. Never use the
old names -- they will cause silent data failures:

| Correct (production) | Wrong (old site v1) |
|---|---|
| `odds` | `line` |
| `profit` | `pnl` |
| `market_prob` | `implied_prob` |
| `result = 'win'/'loss'/'push'/'void'` | `result = 'W'/'L'/'P'` |

**Picks page filters:** league, market, book, date, status. Book filter maps
display names (DraftKings, FanDuel, theScore, etc.) to lowercase DB values
via `.toLowerCase().replace(/ /g, "")`. Passed as `?book=` query param to
`GET /api/public/picks`.

**Results page filters:** system, result -- client-side on already-fetched data.

This contract is enforced by `beezy-vip/tests/index.test.ts` (TypeScript
compile-time) and `tests/test_public_api.py` (runtime, included in the
pytest suite run by `./deploy/deploy_service.sh`).

### Auth

Clerk handles authentication. Keys are set as Vercel environment variables:
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `CLERK_SECRET_KEY`

Protected routes: `/dashboard/**`, `/tools/bet-tracker/**`, `/api/admin/**`.
Middleware in `middleware.ts` handles redirects.

### Geo-blocking

`middleware.ts` reads `BLOCKED_STATES` env var (comma-separated 2-letter US
state codes, e.g. `NY,WA`). Reads `x-vercel-ip-country-region` header
injected by Vercel's edge network (requires Vercel Pro plan). Default is
empty -- blocks nothing. Applies to `/picks/**`, `/dashboard/**`,
`/signup/**` only. Legal pages and tools remain accessible everywhere.

### Stripe

Stripe is wired but NOT active. `PRE_LAUNCH = true` in:
- `app/signup/page.tsx`
- `components/landing/pricing.tsx`

Do not flip these to `false` without explicit confirmation. Gate criteria:
1. >= 200 settled bets per system at gate criteria
2. Legal review complete
3. Geographic restrictions configured
4. Stripe reconciliation cron running clean for 7 days
5. Performance dashboard running clean for 14 days

Stripe reconciliation cron runs daily at 03:00 UTC via Vercel cron
(`/api/cron/stripe-reconcile`). Manual one-off: POST to
`/api/admin/reconcile-user` with `x-admin-key` header.

### learn_articles

AI-generated educational content. Stored on Vercel Postgres (separate from
Cloud SQL). DSN comes from `LEARN_DATABASE_URL` env var (auto-injected by
Vercel after provisioning a Vercel Postgres database in the Storage tab).

Table is created by POST to `/api/db/migrate` with `x-admin-key` header.
This route ONLY manages `learn_articles` -- it never touches the `bets` table.

### Design system

Bloomberg Terminal meets PrizePicks aesthetic:
- Fonts: Inter (prose/UI) + JetBrains Mono (data/numbers/tickers)
- Background: `#0a0a0c`, Surface: `#111114`, Border: `#1f1f24`
- Win/positive: `#10b981`, Loss/negative: `#ef4444`, Warning: `#f59e0b`
- System pills: NRFI green, HR amber, F5 blue, K purple, OUTS orange
- No drop shadows, no gradients, no glow effects
- Depth via surface elevation and border tone only

### Vercel deployment

- Project: `mlb-betting` on Vercel (root directory set to `beezy-vip`)
- Auto-deploys on push to `main` branch
- Required env vars: `BETTING_API_URL`, `BETTING_API_KEY`, `LEARN_DATABASE_URL`,
  `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `ADMIN_SECRET_KEY`,
  `CRON_SECRET`, `STRIPE_*` keys, `NEXT_PUBLIC_BASE_URL`, `BLOCKED_STATES`

### GCP resources added for beezy-vip

Two new secrets in Secret Manager:
- `site-api-key` -- API key for Cloud Run public API (version 1 is current)
- `site-origin` -- allowed CORS origin (value: `https://beezy.vip`)

Both mounted on the Cloud Run service via `--set-secrets` in
`deploy/deploy_service.sh`. Service account `mlb-betting-sa` has
`secretmanager.secretAccessor` on both.

Cloud Run service is now open to unauthenticated invocations (`allUsers`
has `roles/run.invoker`) so the public API endpoints are reachable from
Vercel. The existing scheduler routes (`/run`, `/settle`, etc.) are still
protected by OIDC tokens from the schedulers -- they just aren't blocked at
the IAM level anymore. Add a secret check to those routes before going live.

### Dynamic system route
Per-system pick pages (`/picks/mlb/nrfi` etc) are served by a single
dynamic route at `app/picks/mlb/[system]/page.tsx`. Invalid slugs return
404 via `notFound()`. Do not create individual static page files per system.

### Design tokens
Do not redefine `B`, `TEAM_ABBREV`, `SYSTEM_COLOR`, `SYSTEM_PILL`, or
`pickLabel` locally in components. Import from `@/lib/tokens` instead.
When adding a new system, update `lib/tokens.ts` only.

### Tailwind v4 gotchas

**The core rule: use pure inline styles for everything in beezy-vip. Do not
use Tailwind utility classes for colors, borders, backgrounds, spacing, or
layout. Tailwind v4 with `@tailwindcss/postcss` does not reliably scan and
generate CSS for utility classes in this monorepo subdirectory setup.**

Specific failures observed:
- Arbitrary values: `grid-cols-[7fr_5fr]`, `border-[#1f1f24]`, `bg-[var(--surface)]`
  -- none of these are generated. Always use `style={{ ... }}` instead.
- Semantic utilities: `border-b`, `border-r`, `h-14`, `text-muted`, `text-accent`,
  `bg-[var(--border)]` -- not generated because CSS variables aren't in the
  Tailwind config. Use inline styles with hardcoded hex values or CSS vars.
- `@source` directives and deleting `tailwind.config.ts` did not fix scanning.
- The `@import url(...)` for Google Fonts in `globals.css` interfered with
  PostCSS processing. Fonts must be loaded via `next/font/google` in `layout.tsx`.

What IS safe to use (pre-generated Tailwind utilities):
- `className="mono"` -- custom class defined in globals.css, always works
- `className="live-dot"` -- same
- `className="sticky"`, `className="hidden"`, `className="flex"` -- core
  utilities that are pre-generated, but prefer inline style for anything
  layout-critical.

The correct pattern for ALL components in beezy-vip:
```tsx
// Wrong -- Tailwind class may not generate
<div className="border-b border-[#1f1f24] bg-[var(--surface)] p-4">

// Correct -- always renders
<div style={{ borderBottom: '0.5px solid #1f1f24', background: '#111114', padding: '16px' }}>
```

Define a module-level constant `const B = '0.5px solid #1f1f24'` at the top
of each component file for consistent border values.

Other gotchas:
- `export const metadata` and `'use client'` cannot both be in the same file.
  Put metadata in a `layout.tsx` sibling and keep `'use client'` in the page.
- All data-fetching pages need `export const dynamic = 'force-dynamic'` or
  Next.js will try to prerender them at build time without env vars.
- Stripe, Clerk, and any SDK that initializes at module level with env vars
  will crash the build. Use lazy initialization (`getStripe()` function) and
  add `export const dynamic = 'force-dynamic'` to all affected routes.
- JSX elements cannot have two `style={{}}` props -- merge them into one.
- `gap` in a CSS grid must be set via inline `style={{ gap: '...' }}` when
  the grid itself uses inline style. Tailwind `gap-2` on an inline-grid div
  will not apply.

### Charts

Results page uses `recharts` (installed 2026-05-17). Available in beezy-vip.
P&L chart: cumulative units per system + ALL line + drawdown shading.
Edge chart: 7-day rolling model edge vs realized ROI.
Both use `ResponsiveContainer` with inline-styled tooltips matching design system.

### Filter bar (picks page)
Single FILTERS toggle button collapses/expands all four filter rows (Market,
Date, Result, Book). Collapsed state shows active filter summary pills inline.
Sort buttons (Date / Edge / Odds with direction toggle) always visible in the
toggle bar. URL-param driven via useSearchParams/useRouter -- no props needed.
Component: `components/picks/filter-bar.tsx`.

### Results page features

- **CSV export**: client-side, triggers download of filtered bets as `.csv`.
  Columns: Date, System, Game, Pick, Odds, Edge, Stake, Book, Result, P&L.
  Edge shown as exact percentage. Model prob not exposed.
- **Stake column**: shows dollar stake per bet.

### Sort (results page)

Sort chips: Date, Edge, Odds, P&L. Click active sort to toggle ↑/↓.
Default: Date ↓. Implemented as client-side sort on fetched picks array.

### Table columns (picks + results)

Both tables share the same column layout (2026-05-20):
`Date | System | Game | Pick | Odds | Edge | Book | Result | P&L`
`80px  65px    160px  1fr   90px  60px  80px  70px   70px`
minWidth: 860px. Book column shows canonical book name or "—" if null.

### Bet type display names

`picks-table.tsx` maps raw `bet_type` to readable labels. When adding a new
bet_type, update the `pickLabel` formatter:
- F5: `HOME`/`AWAY` → "F5 Home ML" / "F5 Away ML"
- NRFI: `1I_HOME`/`1I_AWAY`/`1I_DRAW` → "1st Inn Home/Away/Draw"
- K: `K_OVER_4.5` → "Over 4.5 Ks"
- OUTS: `OUTS_UNDER_14.5` → "Under 14.5 Outs"
- HR: `HR` → "HR Yes"

### Pick rationale (notes column)

`mlb_core/rationale.py` maps feature values to canned phrases at bet-log time.
Stored in `notes TEXT` column (already in schema, public API SELECT, and `Bet` type).
Wired: HR and NRFI. To wire K/OUTS/F5: import `build_rationale` inside the scoring
function and pass `notes=build_rationale(dict(row), "K")` to `log_bet()` using the
`morning_odds` line as the anchor. Frontend renders `bet.notes` as muted mono subtext
under the pick label in `picks-table.tsx` (desktop + mobile). Null-guarded.

### Model IP protection

Current (paper mode): exact edge shown as percentage (e.g. "12.4%"). Model prob
not exposed anywhere on the public site. At launch: gate model prob behind Pro
subscription via Clerk auth. CSV export uses exact edge values.

### Pre-launch checklist

- [ ] beezy.vip DNS → Vercel
- [ ] Clerk production keys in Vercel env vars
- [ ] beezy.vip added to Clerk allowed origins
- [ ] Stripe production price IDs set
- [x] Legal pages drafted 2026-05-21 (terms, privacy, responsible-gambling, refunds). `{LAWYER_REVIEW}` markers in each file flag sections needing attorney sign-off before flipping PRE_LAUNCH.
- [ ] `BLOCKED_STATES` configured
- [ ] Stripe reconciliation cron clean 7 days
- [ ] >= 200 settled bets per system at gate criteria
- [ ] Flip `PRE_LAUNCH = false`

### Backend backlog (not blocking launch)

- GamblyBot real Discord bot token
- Cloud Build GitHub trigger
- `ump_k_boost_L30` proper derivation
- F3/F7 moneyline models
- Cross-system Kelly coordination before going live
- Line movement signal from SGO snapshots
- Runner-side sentinel check (abort if stale features)
- `fetch_game_result` retry/backoff logic
- HR `away_team`/`home_team` abbrev normalization in `run_hr.py`
- Rate limiting on public API

### When to update this section

- Adding a new page or route → update repo layout table
- Changing the public API endpoints → update the endpoint table
- Changing the schema contract → update the schema contract table
- New Vercel env var needed → update env vars list
- New GCP resource → update GCP resources section
- New Tailwind gotcha → add to gotchas list
- PRE_LAUNCH flip → update the gate criteria status

---

## 16. Model remediation backlog

_Added 2026-05-19. Source: institutional quant audit of the full codebase._
_Last updated: 2026-05-23 00:25 CST_

Work top-to-bottom within each priority tier. Later tasks may depend on earlier ones — dependency notes are inline. Mark tasks `[x]` when the acceptance criterion is verified in a commit. When a task is complete, add the commit hash next to it.

**Rule:** No system crosses the paper→live gate until every P0 task is `[x]`.

---

### P0 — Deployment blockers

#### T01 · Fix Kelly formula
- **Files:** `mlb_core/odds/utils.py` lines 44, 54
- **Change:** `pct = max(0.0, (edge / b) * fraction)` → `pct = max(0.0, (edge * (b + 1) / b) * fraction)`. Apply to both `kelly_stake` and `kelly_pct`.
- **Why:** Formula undersizes by ~52% at -110, ~50% at +200. Conservative direction but mathematically wrong.
- **Acceptance:** Unit test in `tests/test_odds_math.py`: at p=0.55, odds=-110, fraction=1.0 → result ≈ 0.055 (not 0.0288). Test passes.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T02 · Remove `implied_win_pct` from K_FEATURES
- **Files:** `training/retrain_k_v1.py` line 63; `runners/build_k_features.py` (remove build path)
- **Change:** Delete `"implied_win_pct"` from `K_FEATURES`. Remove the feature-build step that produces it for K inputs.
- **Why:** Market-derived feature trains the model to mimic the line. Eliminates closing-line edge by construction.
- **Acceptance:** Retrain runs to completion. Log delta MAE before/after. If MAE worsens >10%, document in retrain notes — the prior number was line-mimicry.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T03 · Remove market-derived features from HR
- **Files:** `runners/build_hr_features.py` lines 627–651; HR model meta features list
- **Change:** Strip `team_moneyline` and `implied_win_pct` from the HR master join. Remove from `model_features.csv` output. Update feature list in `HR_Pro/models/model_meta_hr_v6.json`.
- **Why:** Same circularity as T02.
- **Acceptance:** HR runner startup logs no reference to `implied_win_pct` or `team_moneyline`. Next HR retrain (T11) confirms AUC delta documented.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T04 · Fix NRFI isotonic calibrator leakage
- **File:** `training/calibrate_nrfi_v17.py` lines 230–243
- **Change:** Fit `IsotonicRegression` on `train_g` only (already defined at line 211). Evaluate Brier on `oos_g`. Delete the comment block justifying full-data fit.
  ```python
  iso = IsotonicRegression(out_of_bounds="clip")
  iso.fit(train_g["model_yrfi_prob"].values, train_g["yrfi"].values)
  cal_preds = iso.predict(oos_g["model_yrfi_prob"].values)
  ```
- **Why:** Calibrator currently fit on the OOS slice. Reported Brier improvement is in-sample.
- **Acceptance:** Re-run calibration job. New honest OOS Brier logged and committed to meta. If calibrated Brier > raw Brier on OOS, flag for review — calibrator may be hurting.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T05 · Fix calibrator fit in HR, F5, K calibration scripts
- **Files:** `training/calibrate_hr_v6.py`, `training/calibrate_f5_v5.py`, `training/calibrate_k_v1.py`
- **Depends on:** T04 (establishes the correct train-only-fit pattern)
- **Change:** For HR and F5: apply the same train-only-fit pattern from T04. For K (Poisson): fit `IsotonicRegression` on `(predicted_lambda, observed_K)` pairs, train split only. Add `_load_calibrator` to HR and F5 runners matching `run_nrfi.py` lines 67–86.
- **Why:** All four calibration scripts likely share the same leakage — verify each and fix.
- **Acceptance:** All four systems produce calibrators fit on train slice only. All four runners load and apply their calibrators. Calibrated vs raw Brier/MAE logged per system.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T06 · Reconcile `top3_batter_*` feature contract
- **Files:** `training/retrain_nrfi_v17.py` lines 75–77; `training/calibrate_nrfi_v17.py` lines 63–64; `runners/build_nrfi_features.py`
- **Change (Option A — preferred):** Remove `top3_batter_woba_value_L50`, `top3_batter_is_hard_hit_L50`, `top3_batter_is_bb_L50`, `top3_batter_is_k_L50` from `HALFINN_FEATURES` in both retrain and calibrate scripts. _(Option B is full live lineup integration — defer to T12.)_
- **Add assertion** after features load in retrain: `assert all(f in df.columns for f in HALFINN_FEATURES), f"missing: {set(HALFINN_FEATURES) - set(df.columns)}"`
- **Why:** These features are declared but never joined. Model has dead features in its contract. Live and historical pipelines are misaligned.
- **Acceptance:** Assertion passes on retrain. `model_meta_v17.json` features list matches what the builder produces exactly.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T07 · Fix in-sample innings-window scalar fit
- **File:** `training/backtest_innings_windows.py` lines 211–300
- **Change:** Replace single-pass Brier minimization with year-based walk-forward. For each year Y ≥ min+1: fit scalar on years < Y, evaluate Brier on year Y. Final scalar = mean of per-year fitted scalars. Final Brier = mean of per-year OOS Briers.
- **Why:** Current scalars for F1H/GAME/F3/F7 are pure in-sample fits. Reported calibration is meaningless.
- **Acceptance:** New `innings_window_scalars.json` includes `per_year_scalars` and `per_year_brier` arrays. F1H/GAME/F3/F7 remain log-only until per-year scalar std < 0.1.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T08 · Add CLV tracking
- **Depends on:** nothing, but needed before T17 (promotion criteria)
- **Files (all touched):**
  - `mlb_core/tracking/bet_tracker.py` — add columns `closing_odds REAL`, `closing_implied_prob REAL`, `clv_pct REAL` to schema
  - `runners/capture_closing_lines.py` — new script; cron at T−5 min per game; reads unsettled bets with null `closing_odds`, pulls current SGO snapshot, writes closing odds
  - `runners/settle_bets.py` — compute `clv_pct = (entry_implied - closing_implied) / closing_implied * 100` at settlement
  - `runners/monitor_performance.py` — add `mean_clv` and CLV t-stat to rolling and season summaries
- **Why:** Without CLV you cannot statistically distinguish luck from edge in a 200-bet sample. It's the primary leading indicator of long-term profitability.
- **Acceptance:** Every newly-placed bet has `closing_odds` populated by settlement time. Weekly digest reports `mean_clv` with t-stat.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T09 · Port K leakage check to NRFI (and later HR)
- **Reference:** `training/retrain_k_v1.py` lines 255–330 (`_leakage_check`)
- **Depends on:** T06 (clean feature contract first)
- **Change:** Copy `_leakage_check` into `retrain_nrfi_v17.py`. Adapt metric: AUC delta instead of MAE delta. Warn threshold: removing any single feature improves OOS AUC by > 0.01 absolute.
- **Why:** The `lineup_pct_L` incident (caught May 2026) shows leakage can hide in non-linear interactions. Automated detection catches this earlier.
- **Acceptance:** NRFI retrain output includes `leakage_warnings: []`. Non-empty warnings trigger Discord alert.
- [x] Done · commit: pending — patch applied 2026-05-19

---

### P1 — Required before scaling

#### T10 · Add fold-by-fold dispersion to retrain output
- **Files:** `training/retrain_nrfi_v17.py`, `training/retrain_k_v1.py`
- **Change:** Call `XGBModel.walk_forward_cv` (already implemented in `mlb_core/models/base.py` lines 105–166, just not used in production retrains). Report per-fold AUC/MAE, std, and 95% bootstrap CI on the mean.
- **Why:** Single OOS split gives no estimate of metric variance. A lucky split can mask an unstable model.
- **Acceptance:** `model_meta_*.json` includes `cv_folds: [{year, auc, brier}, ...]`, `cv_mean_auc`, `cv_std_auc`, `cv_auc_ci_lo`, `cv_auc_ci_hi`.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T11 · Migrate HR and F5 training out of notebooks
- **Depends on:** T03 (HR feature cleanup), T04/T05 (calibration pattern)
- **New files:** `training/retrain_hr_v6.py`, `training/retrain_f5_v5.py`
- **Pattern:** Follow `retrain_nrfi_v17.py` exactly. Load features from GCS → walk-forward CV → OOS split → full retrain → upload artifact + meta. Use HR/F5 feature lists and XGB_PARAMS from the notebooks as the starting contract.
- **Why:** HR and F5 model training is currently unauditable. The actual model lives in a Windows notebook not in this repo.
- **Acceptance:** Both run end-to-end as Cloud Run Jobs. `retrain_hr_meta.py` and `retrain_f5_meta.py` shims deprecated (keep as stubs that print "use retrain_hr_v6.py"). Cloud Run Job definitions added to `deploy/`.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T12 · Live lineup integration
- **Depends on:** T06 (feature contract reconciled first)
- **File:** `runners/run_nrfi.py` around `_build_today_feature_rows` lines 89–165
- **Change:** Call MLB Stats API for posted lineups. Map starter ID → top-3 batter IDs. Compute `top3_batter_*` rolling stats from the historical batter features table. Flag each bet with `lineup_confidence: 'posted' | 'estimated'` in the bets table.
- **Also:** Update `deploy/add_snapshot_schedulers.sh` to shift the afternoon snapshot closer to lineup-posting time (2–3 hours before first pitch).
- **Why:** Live runner currently uses stale historical features for all lineup-dependent signals. Morning run has zero lineup data.
- **Acceptance:** Afternoon runner logs `posted_lineup` for ≥ 80% of bets. Morning run accepts `estimated_lineup` and marks bets accordingly.
- [ ] Done · commit: ___

#### T13 · Add regime indicator feature
- **Files:** All four `build_*_features.py`
- **Change:** Add `regime` column: `"pre_pitch_clock"` for `game_date < 2023-03-30`, `"post_pitch_clock"` otherwise. Add to all four feature lists. Retrain each model.
- **Why:** Pitch clock changed pace, K rates, and stolen-base success materially. Pre/post-clock data is not exchangeable.
- **Acceptance:** Feature importance check after retrain shows `regime` with non-trivial split frequency. Per-regime fold AUC logged in model meta.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T14 · Add PSI drift monitoring
- **New file:** `runners/monitor_drift.py`
- **Logic:** Weekly: compute Population Stability Index between last 7 days of live prediction feature distributions vs training-set distribution (use `feature_means` + new `feature_stds` sidecar — add `feature_stds` to retrain output). Alert if PSI > 0.25 for any top-10 feature by importance.
- **Schedule:** Cloud Scheduler, Monday mornings. Add job to `deploy/`.
- **Why:** ROI alarm fires reactively after losses. PSI catches distributional drift before it degrades predictions.
- **Acceptance:** First run establishes baseline. PSI violations post to Discord. `feature_stds` present in all four model metas.
- [x] Done · commit: pending — patch applied 2026-05-19

#### T15 · Per-book performance breakdown
- **File:** `runners/monitor_performance.py`
- **Change:** Add `_per_book_stats(df)` that groups settled bets by the `book` column. Report n, hit_rate, ROI, mean_clv per book. Include in weekly digest.
- **Depends on:** T08 (CLV tracking)
- **Why:** Book-specific profiling and limit changes are invisible without per-book stats.
- **Acceptance:** Weekly digest includes a per-book table. Alert if any book drops to n ≥ 20 bets and ROI < -20% (potential profiling signal).
- [x] Done · commit: pending — patch applied 2026-05-19

#### T16 · Add F5 bullpen features
- **File:** `runners/build_f5_features.py`
- **Change:** Add team bullpen rolling stats: `bullpen_xfip_L30`, `bullpen_k_pct_L30`, `bullpen_xwoba_L30`. Source: same Statcast master, filter to `inning > 1` and non-starting appearances. Join to F5 feature table by `home_team` / `away_team`.
- **Why:** F5 covers innings 1–5. The starter often exits in inning 4–5. Bullpen quality is the largest missing variable in the F5 model.
- **Acceptance:** F5 retrain shows AUC improvement ≥ 0.01 with bullpen features included. If < 0.01, revert and document — features must earn their inclusion.
- [x] Done · commit: pending — patch applied 2026-05-19

---

### P2 — Institutional baseline

#### T17 · Tighten paper→live promotion criteria
- **Depends on:** T08 (CLV tracking must be live)
- **File:** `CONTEXT.md` §6 "Paper → live criteria" lines 601–607
- **Replace with:**
  1. ≥ 200 settled bets per system
  2. Mean CLV ≥ +2% with t-stat > 2 over ≥ 100 bets
  3. Season ROI > 0% (HR: > -5% allowed if CLV positive)
  4. Calibration: hit rate within 3 pct points of avg model probability (was 5)
  5. No system down more than 50 units at paper stakes
  6. PSI for all top-10 features < 0.25 over the eval window
- **Acceptance:** `monitor_performance.py` enforces criteria 2–4 programmatically and blocks the Discord "ready for live" alert until all pass.
- [ ] Done · commit: ___

#### T18 · Nested hyperparameter tuning
- **Files:** Each `retrain_*.py`
- **Change:** Add Optuna-based search. Inner CV on train slice (3-fold time-series). Outer evaluation on OOS year. Search space: `max_depth ∈ [2,4]`, `learning_rate ∈ [0.01, 0.1]`, `min_child_weight ∈ [5, 50]`, `reg_alpha`, `reg_lambda`, `gamma`. Hardcoded `XGB_PARAMS` dicts become defaults only.
- **Why:** Current params are undocumented guesses or notebook-era defaults.
- **Acceptance:** Each retrain logs tuned params. `model_meta_*.json` includes `tuned_params` and `optuna_n_trials`.
- [ ] Done · commit: ___

#### T19 · Pitcher IL-return / debut handling
- **Files:** All four `build_*_features.py`
- **Change:** Compute `days_since_last_start` correctly across IL gaps. If gap > 25 days, set `coming_off_il = True` and reset rolling window accumulation from that start forward (don't average across the gap). Add `coming_off_il` to feature lists.
- **Why:** IL returnees have stale rolling stats and incorrect short_rest flags.
- **Acceptance:** Spot-check 10 known 2025 IL returns — all correctly flagged. `coming_off_il` carries non-zero feature importance.
- [ ] Done · commit: ___

#### T20 · Odds math test coverage
- **New file:** `tests/test_odds_math.py`
- **Tests:**
  - `american_to_implied_prob` at -110, +100, +200, -200
  - Vig removal: proportional method on a two-way market sums to 1.0
  - Kelly stake at known values (covers T01's formula)
  - Per-game cap respects multi-system pending stakes
- **Acceptance:** `pytest tests/test_odds_math.py` passes clean. Add to CI / Docker build step.
- [x] Done · commit: pending — patch applied 2026-05-19

---

### Backlog conventions

- **Starting a task:** Note it in the session handoff as "in progress".
- **Finishing a task:** Mark `[x]`, add commit hash, update `_Last updated` timestamp at top of this section.
- **Blocked task:** Add a `> Blocked: <reason>` line under it.
- **New tasks found during work:** Add to the appropriate priority tier with a `Txx` ID continuing the sequence.
- **Scope change to an existing task:** Edit in place and note the change date inline.
---

## 17. Post-audit bug fixes (2026-05-19)

Fixes applied beyond the T01-T20 backlog, grouped by impact tier.

### Correctness bugs

**F01 — HR vig formula centralised**
- `mlb_core/odds/utils.py`: added `devig_unilateral(market_prob, vig_pct=0.07)`.
- `runners/run_hr.py`: replaced hardcoded `market_prob / 1.07` with `devig_unilateral`.
- Every HR edge number was arithmetically correct but undocumented. Now named, testable, configurable.

**F02 — SQL injection in /dashboard and /reset-bets**
- `main.py`: `system_filter` now whitelist-validated and passed as a bound parameter.
- `/reset-and-run` and `/reset-bets` now require `X-API-Key` auth (same as public API).

**F03 — Retractable roof always set to is_outdoor=1**
- `runners/run_hr.py` line 460: `1 if roof == "open" else 1` → `1 if roof in ("open","retractable") else 0`.
- Moved weather implementation to shared `mlb_core.data.weather.fetch_live_weather_for_slate`.

**F04 — F5 calibrator applied without boundary check**
- `runners/run_f5.py`: added `X_min_`/`X_max_` range guard matching the NRFI runner pattern.

**F05 — `_norm` defined three times in settle_bets.py**
- Hoisted to module level. All three inline copies removed.

**F06 — K/OUTS push grading on integer lines**
- `runners/settle_bets.py`: whole-number line now logs a `WARNING` instead of silently grading push.
- DK uses half-point K/OUTS lines so a whole-number line signals a parsing error.

**F07 — CLV arithmetic used vig-inclusive probabilities**
- `mlb_core/tracking/bet_tracker.py` `write_closing_line`: CLV now computes on fair (no-vig) probs.
- Props use `devig_unilateral`; two-sided markets use raw implied as approximation pending complementary-side capture in `capture_closing_lines.py`.

### Reliability / operational

**F08 — Missing endpoints for T08/T14 scripts**
- `main.py`: added `POST /capture-closing` and `POST /monitor-drift` routes.
- `retrain-weekly` job list updated to `mlb-retrain-f5-v5` / `mlb-retrain-hr-v6` (T11 replacements).

**F09 — SGO snapshot staleness — runners silently scored against stale lines**
- `mlb_core/odds/sgo.py`: added `check_snapshot_freshness(gcs_key, max_age_hours=4.0)`.
- All four runners (`run_nrfi`, `run_hr`, `run_f5`, `run_k`) abort with an error log if snapshot >4h old.

**F10 — Morning/evening bet deduplication**
- `mlb_core/tracking/bet_tracker.py` `is_duplicate`: new `kelly_triggered` parameter.
- Non-triggered morning prediction no longer blocks a triggered evening bet on the same market.
- Triggered bet still blocks a second triggered bet.

**F11 — Settlement fetched game results serially**
- `runners/settle_bets.py`: `ThreadPoolExecutor(max_workers=8)` parallelises `fetch_game_result` calls.
- 15-game slate: ~7.5s → ~1s.

**F12 — `post_pitch_clock` added to builders but not to explicit feature lists**
- `training/retrain_nrfi_v17.py` `HALFINN_FEATURES`: added `"post_pitch_clock"`.
- `training/retrain_k_v1.py` `K_FEATURES`: added `"post_pitch_clock"`.

**F13 — `ump_tight_zone` in-sample quantile threshold**
- `runners/build_nrfi_features.py`: thresholds now use `expanding().quantile()` instead of full-dataset `quantile()`.

**F14 — HR name matching: exact-only misses accent variants and suffixes**
- `runners/run_hr.py`: added `difflib.get_close_matches` fuzzy fallback at cutoff=0.85.

**F15 — `build_batter_rolling` / `build_pitcher_features` used wall-clock date**
- `runners/build_hr_features.py`: both functions now accept `run_date` parameter; historical replays use the correct reference point.

**F16 — Weather fetch had no retry backoff**
- Created `mlb_core.data.weather.fetch_live_weather_for_slate` using existing `_fetch_weather` (4-attempt exponential backoff).
- `run_hr._fetch_today_weather` and `run_f5._fetch_today_weather` both replaced with the shared function.

### Testing

**F17 — Settlement grading had zero test coverage**
- `tests/test_settlement.py`: 35 test cases covering NRFI (all 5 bet types), F5 (push/win/loss/incomplete), HR (start/no-start/accent), K/OUTS (over/under/void/integer-line warning), PITCHER_ER, `_calc_profit`, and `BetTracker.is_duplicate` dedup logic.
- `tests/test_odds_math.py`: added 4 `devig_unilateral` tests.

---

## 18. Calibration remediation session (2026-05-20)

### Root cause confirmed
Edge-ROI gaps (NRFI 33pts, K 21pts, F5 19.5pts) traced to two calibrator failures:

**NRFI isotonic calibrator (isotonic_calibrator_v17.pkl):**
- Fitted X range was [0.6541, 0.8194] -- only 16 pct points wide
- boundary_lo=0.0000, boundary_hi=1.0000 (saturated both ends)
- 10/11 production games clipped to boundary -- outputting YRFI=0.0 or YRFI=1.0
- Root cause: calibrator was fit on OOS-only slice (pre-C03), sparse tail coverage
- Fix: re-ran mlb-calibrate-nrfi job -- new range [0.2927, 0.7952], Brier 0.2435->0.1929
- Confirmed: NRFI runner now logs "isotonic calibrator applied to 11/11 games"

**K lambda calibrator (lambda_calibrator_k_v1.pkl):**
- boundary_hi=15.0 -- pitchers above X_max clipped to lambda=15 (near-certain OVER)
- Non-monotonic mapping in 5-7 range (e.g. raw=8.5 -> cal=10.5, 2.2 unit jump)
- Fix: re-ran mlb-calibrate-k job -- bias +0.064 -> -0.025, MAE 1.7007->1.6824
- K edge inflation is NOT primarily lambda calibration -- model= vs NB sim gap of
  0.10-0.18 likely from fair (market) prob calculation, not model prob inflation

**nb_alpha misconfiguration (K system):**
- Configured nb_alpha=0.01, data implies 0.0315 (VMR=1.166)
- Under-dispersed by 3x but not the dominant source of edge inflation
- Addressed in next retrain (C07 -- nb_alpha now fit from residuals)

### mlb-reset-and-run-once scheduler job fix
- Was using OIDC auth -- endpoint uses X-API-Key, not OIDC -> 401 on every trigger
- Body had hardcoded date 2026-05-15
- Fixed: switched to X-API-Key header, updated date to current
- Feature build must complete before reset-and-run -- sentinel check aborts runners
  on stale features. Trigger mlb-build-all-features first if running outside normal schedule.

### Confirmed working after session
- NRFI calibrator: 11/11 games in range (was 1/11)
- K calibrator: 19/19 pitchers in range
- Both calibrate jobs complete cleanly with no degradation warnings

## 19. Discord server (beezy.vip)

*Last configured: 2026-05-20*

### What it is

Community layer for beezy.vip. Receives pick signals and daily recaps via
webhooks. Members opt into book-specific pings. Ops alerts are routed to an
admin-only channel, never surfaced to members.

Server ID: `1476027259956494533`
Server name: `beezy.vip`

---

### Server structure

```
ONBOARDING
  #verify        -- Entry gate. Unverified members see only this channel.
  #preferences   -- Self-assign book roles for tailored pick pings.

INFO
  #welcome       -- What beezy.vip is, how to read picks. Carl-bot welcome
                    message posts here on member join.
  #announcements -- System updates, paper mode progress, launch news.
  #rules         -- React with checkmark to get Paper Tester role and unlock
                    the server.

PICKS  (read-only for members, webhook-only posting)
  #daily-picks   -- post_bets() output. One thread per day.
  #daily-recap   -- post_all_systems_summary() after settlement.
  #performance   -- monitor_performance.py alerts + Monday digest.
                    Paid members only (Member, Member Pro).

COMMUNITY
  #general       -- Member discussion. Paper Testers read-only.
  #betting-theory -- Forum channel for strategy discussion. Paid members only.
  #paper-feedback -- Reactions to picks during paper mode. All members.

OPS  (Admin only)
  #ops-alerts    -- monitor_ops.py failures + post_error() output.
  #deploys       -- Manual deploy notes.
```

---

### Role hierarchy

Ordered highest to lowest. Carl-bot can only assign roles below its own position.

| Role | Color | Description |
| --- | --- | --- |
| Admin | Red `#ED4245` | Full access. You. |
| Moderator | Pink `#EB459E` | Manage messages/kick/ban. No ops or picks config. |
| Member Pro | Yellow `#FEE75C` | Paid tier post-launch. Mirrors Clerk Pro auth. |
| Member | Green `#57F287` | Paid tier post-launch. Mirrors Clerk auth. |
| Paper Tester | Blurple `#5865F2` | Free during paper mode. Assigned via #rules reaction. |
| Bot | Grey | beezy-bot + webhook posting role. |
| Carl-bot | Managed | Required for reaction roles and moderation. |
| beezy-bot | Managed | The Discord bot account used by setup/cleanup scripts. |

Book roles (grey, mentionable -- used for pick pings):
`DraftKings`, `FanDuel`, `Caesars`, `BetMGM`, `theScore`, `PointsBet`

These match `ONSHORE_BOOKS` in `mlb_core/odds/sgo.py` exactly. If a book
is added or removed from SGO, update the Discord roles to match.

State roles were considered but dropped -- book roles are sufficient for
tailored pings and lower member friction.

---

### Webhook routing

Three webhooks, each stored in Secret Manager and mounted on the Cloud Run
service as environment variables:

| Secret | Env var | Channel | Used by |
| --- | --- | --- | --- |
| `discord-webhook-url` | `DISCORD_WEBHOOK_URL` | `#daily-picks` | `post_bets()` |
| `discord-webhook-summary` | `DISCORD_WEBHOOK_SUMMARY` | `#daily-recap` | `post_all_systems_summary()` |
| `discord-ops-webhook-url` | `DISCORD_WEBHOOK_OPS` | `#ops-alerts` | `post_error()`, `post_ops_alert()` |

`post_error()` previously routed to the main picks webhook. It now uses
`DISCORD_WEBHOOK_OPS`, keeping errors out of member-facing channels.

New function `post_ops_alert(message, run_date)` added for `monitor_ops.py`
to call directly for infra health failures.

Per-system webhook override still supported: `DISCORD_WEBHOOK_{SYSTEM}`
(e.g. `DISCORD_WEBHOOK_NRFI`) takes priority over `DISCORD_WEBHOOK_URL`.

---

### Bot infrastructure

**beezy-bot** -- Discord application bot account.
- Token stored in Secret Manager as `discord-bot-token`.
- Used by `setup_discord.py` (one-time server setup) and
  `cleanup_discord.py` (role cleanup).
- Not a persistent bot -- scripts run on-demand from Cloud Shell.
- Does NOT handle real-time events. Webhook-only for all pick posting.
- See CONTEXT.md §8 gotcha: webhook messages do not trigger Discord bots.
  GamblyBot was the original bot and is still in the server but ignores
  webhook content. Real bot needed for interactive commands (backlog).

**Carl-bot** -- Third-party moderation and reaction role bot.
- Dashboard: https://carl.gg
- Handles: reaction roles (#rules gate, #preferences book roles),
  welcome message, automod (Medium preset), modlogs to #ops-alerts.
- Mute role: Moderator.
- #rules and #preferences channels need explicit Carl-bot permission overrides
  (Send Messages, Add Reactions, Manage Messages) because the default
  everyone deny blocks Carl-bot otherwise.

---

### Scripts

`setup_discord.py` -- One-time full server setup. Run from Cloud Shell.
Creates all categories, channels, roles, and seeds starter content.
Reads bot token from Secret Manager at runtime.

```bash
TOKEN=$(gcloud secrets versions access latest \
  --secret=discord-bot-token \
  --project=concrete-crow-445205-m4)
python3 ~/mlb-betting/setup_discord.py --token "$TOKEN"
```

`cleanup_discord.py` -- Deletes junk roles (ran once after Carl-bot added
platform template roles). Safe to re-run; only deletes roles not in the
KEEP_ROLES allowlist.

Both scripts live in the repo root. Do not commit bot tokens.

---

### Stripe -> Discord role sync (backlog)

When a user pays on beezy.vip via Stripe/Clerk, they should automatically
receive the `Member` or `Member Pro` Discord role. This requires:

1. A Clerk/Stripe webhook hitting a Cloud Run endpoint
2. A persistent bot (real token, not webhook) to assign roles via Discord API
3. Mapping Clerk user ID -> Discord user ID (requires OAuth link at signup)

Not blocking launch. Manual role assignment during early paid access is fine.
Design the Clerk signup flow to prompt Discord OAuth link from day one.

---

### Gotchas

**Webhook messages do not trigger Discord bots.** GamblyBot and beezy-bot
both ignore webhook content. Carl-bot reaction roles work because Carl-bot
listens to reaction events, not message content.

**Carl-bot requires explicit channel permission overrides.** The #rules and
#preferences channels have everyone deny at the channel level. Carl-bot
must be added explicitly to each channel's permission overrides with Send
Messages + Add Reactions + Manage Messages, or it gets a 403 on posting.

**Carl-bot role hierarchy.** Carl-bot's role must be positioned above any
role it assigns. If Paper Tester is moved above Carl-bot in the hierarchy,
the reaction role assignment silently fails.

**News channel and Forum channel require Community mode.** The setup script
handles this gracefully with try/except -- falls back to text channel if
Community mode is not enabled. Enable Community mode in Server Settings ->
Community to unlock both channel types.

**Book role pings require a real bot token.** post_bets() via webhook cannot
ping roles. Role pings from webhooks are silently ignored by Discord.
Implementing book-role pings requires replacing the webhook call with a
bot API call using beezy-bot's token. This is the primary driver for
eventually moving from webhooks to a persistent bot. (Backlog.)

---

### When to update this section

- Adding or removing a book from ONSHORE_BOOKS -> update book roles
- Changing webhook routing -> update webhook table
- New channel added -> update server structure
- Carl-bot config changes -> update Carl-bot subsection
- Stripe/Discord role sync implemented -> update backlog item to Live
- Real bot token wired for role pings -> update bot infrastructure section

---

## 21. Model health diagnostics session (2026-05-21)

### What we learned

Full AUC/Brier/calibration/CLV diagnostic run across all systems. Key findings:

**Signal assessment (model_prob AUC):**
- HR: 0.611 -- genuine signal, model adds value over market
- OUTS: 0.556 -- marginal; market_prob AUC (0.658) beats model, wins driven by favorites not edge
- K: 0.572 -- slight signal, needs ~200 more bets to confirm
- F5: 0.550 -- noise range at n=72
- NRFI: 0.500 -- coin flip, caused by 85% weather feature null rate (not model failure)

**Diagnostic tooling added:**
- `/model-health` route: AUC, Brier skill, cal error, ROI, edge-ROI gap, CLV, data freshness per system
- `monitor_performance.py`: AUC added to rolling/season stats and alert thresholds
- Feature null rate check script (inline, compares recent vs training means)

**The correct AUC diagnostic workflow:**
1. Check `model_prob` AUC, not `market_prob` AUC -- they differ materially for NRFI
2. Check feature null rates before concluding model is broken
3. AUC ~0.500 on a previously-working model = data pipeline problem, not model problem
4. AUC < 0.500 = model rank-ordering backwards = retrain required

### NRFI data gap root cause

`mlb-refresh-data` scheduler job had `Last attempt: None` -- never ran. Weather master
had only 15 dates of 2026 coverage. Feature null rates: temperature_f 84.7%, wind 84.7%,
is_outdoor 78.8%, platoon_edge 35.5%, days_rest 30.9%. XGBoost imputes all with feature_means,
making every game look identical -> model output clusters at base rate -> AUC 0.500.

Fix sequence after any data gap:
1. `/backfill-data` with affected systems
2. `/build-all-features`
3. Retrain + calibrate affected models
4. `/model-health` to confirm AUC recovered

### Session work completed
- Added `/model-health` route to main.py
- Added `/backfill-data` route for weather/scoring/umpires
- Fixed `/refresh-data` response to include per-fetcher results
- Added AUC to monitor_performance.py rolling/season stats and alerts
- Fixed `fetch_il_pitcher_ids()` fail-open in run_nrfi.py and run_k.py
- Identified weather master gap as root cause of NRFI AUC collapse

### Pending after this session
- Run `/backfill-data` for weather (2026-04-01 to 2026-05-19)
- Rebuild features after backfill
- Retrain + calibrate NRFI on complete weather data
- Verify `/model-health` shows NRFI AUC recovering toward 0.54-0.57
- Investigate why mlb-refresh-data scheduler job never ran

---


## 22. Engineering backlog (2026-05-21)

All items are engineering -- not data gaps. 5 years of training data (25k+ rows) is
sufficient. Live bet sample (51-110 per system) grows daily and is not the bottleneck.

Priority order within each tier. Work top-to-bottom.

---

### Immediate (this week)

#### E01 · Fix K fair probability calculation [CLOSED -- not a bug]
- Historical pre-T01 edge values inflating avg_edge. Current code correct.

#### E02 · F5 CV loop C03 leak [x]
- Fixed 2026-05-21. `retrain_f5_v5.py` CV loop now carves val from train.

#### E03 · CLV pipeline bugs [x]
- Fixed 2026-05-21: top-level bt_upper -> bet_type, real MLB_DB_URL.
- Fixed 2026-05-22: bt_upper in K/OUTS branch, SGO team name lookup
  (ev.get("away_team") -> teams.away.names.short), extractor list vs dict
  ({id:ev} -> [ev] for NRFI and F5), CLV columns missing from public API
  picks SELECT. CLV capturing from 2026-05-22 evening bets onward.

#### E04 · Build OUTS as proper regression model [x]
- Fixed 2026-05-21. `training/retrain_outs_v1.py` -- NegBin count model on starter_outs.
- `build_k_features.py` adds starter_outs column. OUTS_Pro_System/ config added.
- Cloud Run Job: mlb-retrain-outs-v1. Run after rebuilding K features.
- Runner falls back to Normal proxy if model not found in GCS.

#### E05 · NRFI 2026 drift investigation
- **Why:** Walk-forward CV shows AUC degrading: 2024=0.5985, 2025=0.5876, 2026=0.5394.
  Weather fix will recover live AUC from 0.500 but the year-over-year decay needs
  investigation. Pitch clock, opener usage, lineup construction all shifted.
- **Approach:** Compare feature distributions year-by-year. Add opener usage flag,
  pitch count efficiency, chase rate vs 2023 baseline.
- **Acceptance:** 2026 fold AUC >= 0.560 after feature additions + retrain.
- [ ] Done

#### E06 · Fix deploy script traffic routing [x]
- Fixed 2026-05-21. Added `gcloud run services update-traffic --to-latest` after deploy.
- Also added missing Discord webhook secrets to --set-secrets flag.

#### E07 · Verify mlb-capture-closing and mlb-refresh-data scheduler jobs are firing
- **Why:** mlb-refresh-data had Last attempt: None causing the weather gap.
  Same pattern likely affects mlb-capture-closing (explaining clv_n=0).
- **Check:** `gcloud scheduler jobs describe mlb-refresh-data --location=us-central1`
- **Fix if URI wrong:** `gcloud scheduler jobs update http mlb-refresh-data --location=us-central1 --uri=https://mlb-betting-628109313129.us-central1.run.app/refresh-data`
- [ ] Done

---

### Short term (next 2 weeks)

#### E08 · NRFI sub-model ensemble
- Gated: only start after NRFI live AUC >= 0.54 with n >= 200 bets.
- Split pitcher dominance / lineup quality / park+weather into sub-models.
- Stacking layer: logistic regression combining three sub-model outputs.
- [ ] Done

#### E09 · Hyperparameter tuning via Optuna [x] -- script written, pending execution
- `training/tune_hyperparams.py` written. Run after E02-E07 complete.
- `pip install optuna --break-system-packages` then `python -m training.tune_hyperparams --system NRFI --n-trials 50`
- Retrain scripts pick up tuned params automatically via `load_tuned_params()`.

#### E10 · Pre-game line movement feature [x] -- schema + runners + capture wired
- `bets` table: morning_odds INTEGER, line_move_pct REAL (auto-migrated).
- `mlb_core/odds/line_movement.py`: load_morning_odds(), compute_line_move_pct().
- All 4 runners load morning snapshot and store morning_odds per bet.
- capture_closing_lines.py computes line_move_pct at capture time.
- After 200+ bets with morning_odds populated: add to feature lists and retrain.

---

### Medium term

#### E11 · Cross-system Kelly coordination
- Global daily exposure cap: total stake across all systems <= 10% of bankroll per day.
- Only needed before going live. Paper mode single-system caps are sufficient now.
- [ ] Done

---

### Backlog conventions
- Start a task: note in session handoff as "in progress"
- Finish a task: mark [x], add date
- New tasks: add with Exx ID continuing sequence
- Blocked: add `> Blocked: reason` line

## 20. Common manual actions (code fragments)

### Deploy

```bash
cd ~/mlb-betting
./deploy/deploy_service.sh
```

### Start Cloud Run proxy for curl tests

```bash
gcloud run services proxy mlb-betting --region=us-central1 --port=8081 &
sleep 4
```

### Trigger a scheduler job immediately

```bash
gcloud scheduler jobs run mlb-settle             --location=us-central1
gcloud scheduler jobs run mlb-betting-morning    --location=us-central1
gcloud scheduler jobs run mlb-betting-evening    --location=us-central1
gcloud scheduler jobs run mlb-build-all-features --location=us-central1
gcloud scheduler jobs run mlb-refresh-data       --location=us-central1
gcloud scheduler jobs run mlb-monitor            --location=us-central1
gcloud scheduler jobs run mlb-monitor-ops        --location=us-central1
```

### Manually trigger an endpoint

```bash
curl -s -X POST http://localhost:8081/settle | python3 -m json.tool
curl -s -X POST http://localhost:8081/monitor | python3 -m json.tool
curl -s -X POST http://localhost:8081/monitor-ops | python3 -m json.tool
curl -s -X POST http://localhost:8081/snapshot-odds | python3 -m json.tool

curl -s -X POST http://localhost:8081/run   -H "Content-Type: application/json"   -d '{"systems":["NRFI","HR","F5","K"],"run_type":"morning"}' | python3 -m json.tool

curl -s -X POST http://localhost:8081/build-all-features   -H "Content-Type: application/json"   -d '{"systems":["HR","NRFI","K","F5"],"continue_on_error":true}' | python3 -m json.tool
```

### Delete bets and re-run clean

```bash
# Delete all bets for a date
curl -s -X POST http://localhost:8081/reset-bets   -H "Content-Type: application/json"   -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)"   -d '{"date": "2026-05-20"}' | python3 -m json.tool

# Delete one system only
curl -s -X POST http://localhost:8081/reset-bets   -H "Content-Type: application/json"   -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)"   -d '{"date": "2026-05-20", "system": "NRFI"}' | python3 -m json.tool

# Re-run after reset
gcloud scheduler jobs run mlb-snapshot-evening --location=us-central1
sleep 10
gcloud scheduler jobs run mlb-betting-evening --location=us-central1
```

### Secrets -- read, create, update

```bash
# Read a secret (cat -A shows invisible newlines)
gcloud secrets versions access latest --secret=discord-bot-token   --project=concrete-crow-445205-m4 | cat -A

# Create a new secret
echo -n "VALUE" | gcloud secrets create SECRET_NAME   --data-file=- --project=concrete-crow-445205-m4

# Update an existing secret
echo -n "VALUE" | gcloud secrets versions add SECRET_NAME   --data-file=- --project=concrete-crow-445205-m4
```

### Wire a secret to Cloud Run

```bash
gcloud run services update mlb-betting   --region=us-central1   --project=concrete-crow-445205-m4   --update-secrets=ENV_VAR_NAME=secret-name:latest
```

### Add a Cloud Scheduler job

```bash
gcloud scheduler jobs create http JOB_NAME   --location=us-central1   --schedule="CRON"   --uri="https://mlb-betting-628109313129.us-central1.run.app/ENDPOINT"   --message-body='{}'   --headers="Content-Type=application/json"   --oidc-service-account-email="scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com"   --oidc-token-audience="https://mlb-betting-628109313129.us-central1.run.app"   --attempt-deadline=300s
```

### Create a new Cloud Run Job

```bash
gcloud run jobs create JOB_NAME \
  --image=gcr.io/concrete-crow-445205-m4/mlb-betting:latest \
  --region=us-central1 \
  --command=python \
  --args="-m,training.MODULE_NAME" \
  --memory=4Gi --cpu=2 \
  --set-secrets=MLB_GCS_BUCKET=mlb-gcs-bucket:latest,MLB_DB_URL=mlb-db-url:latest \
  --set-cloudsql-instances=concrete-crow-445205-m4:us-central1:mlb-betting-db \
  --service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com \
  --project=concrete-crow-445205-m4
```

### Trigger a Cloud Run Job (retrain/calibrate)

```bash
# Always run calibrate immediately after retrain
gcloud run jobs execute mlb-retrain-nrfi-v17 --region=us-central1
gcloud run jobs execute mlb-calibrate-nrfi   --region=us-central1

gcloud run jobs execute mlb-retrain-hr-v6    --region=us-central1
gcloud run jobs execute mlb-calibrate-hr     --region=us-central1

gcloud run jobs execute mlb-retrain-f5-v5    --region=us-central1
gcloud run jobs execute mlb-calibrate-f5     --region=us-central1

gcloud run jobs execute mlb-retrain-k-v1     --region=us-central1
gcloud run jobs execute mlb-calibrate-k      --region=us-central1
```

### Discord bot scripts

```bash
TOKEN=$(gcloud secrets versions access latest   --secret=discord-bot-token --project=concrete-crow-445205-m4)

# One-time server setup
python3 ~/mlb-betting/setup_discord.py --token "$TOKEN"

# Clean up junk roles
python3 ~/mlb-betting/cleanup_discord.py --token "$TOKEN"
```

### Run model health check

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)
curl -s "https://mlb-betting-628109313129.us-central1.run.app/model-health" \
  -H "X-API-Key: $KEY" | python3 -m json.tool
```

### Backfill Statcast pitch data

```bash
# Takes a dates list -- NOT start_date/end_date
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-statcast" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"dates":["2026-05-19","2026-05-20"]}' \
  | python3 -m json.tool
```

### Backfill Savant leaderboards

```bash
# Backfill all 6 datasets for a year range (slow -- 15-25 min per year)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-savant" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_year":2024,"end_year":2026,"force":false}' \
  | python3 -m json.tool

# Backfill a single dataset
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-savant" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"dataset":"exit_velocity_barrels","start_year":2026,"end_year":2026,"force":true}' \
  | python3 -m json.tool
```

### Backfill weather/scoring/umpires

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)

# Weather only (most common gap)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-data" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_date": "2026-04-01", "end_date": "2026-05-19", "systems": ["weather"]}' \
  | python3 -m json.tool

# All three
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-data" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_date": "2026-04-01", "systems": ["weather", "scoring", "umpires"]}' \
  | python3 -m json.tool
```

### Retrain OUTS model

```bash
# Must rebuild K features first (adds starter_outs column)
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/build-features" \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"system":"K"}' | python3 -m json.tool

# Create job (one-time)
gcloud run jobs create mlb-retrain-outs-v1 \
  --image gcr.io/concrete-crow-445205-m4/mlb-betting:latest \
  --region us-central1 --command python \
  --args "-m,training.retrain_outs_v1" \
  --memory 4Gi --cpu 2 \
  --set-secrets MLB_GCS_BUCKET=mlb-gcs-bucket:latest \
  --add-cloudsql-instances concrete-crow-445205-m4:us-central1:mlb-betting-db \
  --project concrete-crow-445205-m4

# Run retrain
gcloud run jobs execute mlb-retrain-outs-v1 --region=us-central1 --wait
```

### Run Optuna hyperparameter tuning

```bash
pip install optuna --break-system-packages
cd ~/mlb-betting
# Run after E02-E07 complete and models are clean
python -m training.tune_hyperparams --system NRFI --n-trials 50
python -m training.tune_hyperparams --system K    --n-trials 50
python -m training.tune_hyperparams --system F5   --n-trials 50
python -m training.tune_hyperparams --system HR   --n-trials 50
```

### Commit CONTEXT.md

```bash
cd ~/mlb-betting
git add CONTEXT.md
git commit -m "docs: update CONTEXT.md"
git push
```

---

## 24. Social media content pipeline (2026-05-23)

*Last updated: 2026-05-23*

### Overview

Automated daily content pipeline that builds Twitter/X following and converts
followers to beezy.vip members and Discord joiners. Three OG image cards
served from Vercel edge runtime + two Cloud Run jobs that generate tweet
drafts via Gemini and post picks cards to Discord.

Twitter handle: @beezy_vip
Discord invite: discord.gg/HfMYCmbmE
Site: https://mlb-betting-rose.vercel.app (beezy.vip pending DNS)

---

### OG image cards (Vercel edge, @vercel/og)

Three card routes, all at beezy-vip/app/api/og/:

| Route | File | Purpose | Dimensions |
|---|---|---|---|
| /api/og/picks-card | picks-card/route.tsx | All systems top-5 by edge | 1200x675 |
| /api/og/games-card | games-card/route.tsx | F5/NRFI game picks with team gradients | 900px dynamic height |
| /api/og/props-card | props-card/route.tsx | HR/K/OUTS player props with headshots | 900px dynamic height |

All three:
- Pull live data from Cloud Run public API at render time (cache: no-store)
- Use BETTING_API_URL + BETTING_API_KEY env vars (server-only, already in Vercel)
- Use NEXT_PUBLIC_BASE_URL for self-referencing assets (logos, headshots)
- Every div with multiple children must have explicit display:flex -- @vercel/og hard requirement
- No conditional null returns inside JSX -- use empty string checks instead
- Card title text: BEEZY.VIP (all caps, matches site header)

Team color gradients: All 30 MLB teams hardcoded as { p: "R,G,B", s: "R,G,B", slug: "xxx" }
in each card route. Primary color fades to secondary to #0e0e11.
Source: official MLB RGB values documented in session 2026-05-23.

Games card: Shows away/home team logos side by side. Gradient uses featured
team colors (home for HOME bet, away for AWAY bet).

Props card: Shows team logo + circular player headshot. Headshot lookup:
1. player field from API (e.g. "Gerrit Cole")
2. Convert to key: .toLowerCase().replace(/ /g, "_") -> "gerrit_cole"
3. Look up MLBAM ID in player_map.json
4. Render img src="/headshots/{id}.jpg"

Pick label convention (must match lib/tokens.ts at all times):
- NRFI -> "No Run 1st Inning"
- YRFI -> "Run in 1st Inning"
- HOME (F5) -> "F5 Home ML"
- AWAY (F5) -> "F5 Away ML"
- K_OVER_7.5 -> "Over 7.5 Ks"
- OUTS_UNDER_14.5 -> "Under 14.5 Outs"
- HR -> "HR Yes"

---

### Static assets

Team logos: beezy-vip/public/logos/{abbrev}.png (30 files)
Downloaded from cdn.ssref.net. Abbrevs: ari, atl, bal, bos, chc, cws, cin,
cle, col, det, hou, kc, laa, lad, mia, mil, min, nym, nyy, oak, phi, pit,
sd, sf, sea, stl, tb, tex, tor, wsh.

Player headshots: beezy-vip/public/headshots/{mlbam_id}.jpg (1315 files)
Downloaded 2026-05-23 via MLB Stats API + headshot CDN with browser User-Agent.
player_map.json maps "first_last" -> MLBAM integer ID.
Refresh annually or when roster changes materially.

---

### Cloud Run jobs

| Job | Schedule | TWEET_MODE | What it does |
|---|---|---|---|
| mlb-tweet-picks | 17:00 UTC (noon ET) | picks | Games card to Discord + tweet draft to Typefully |
| mlb-tweet-recap | 10:00 UTC (5am ET) | recap | Recap tweet draft to Typefully |

Script: tweet_drafter.py in repo root. Included in Docker image via
COPY tweet_drafter.py . in Dockerfile.

Gemini: gemini-2.0-flash free tier (1500 req/day).
Key in Secret Manager as gemini-api-key.
Free tier requires key from a project with NO billing account linked.
Get from aistudio.google.com -- create fresh project, no billing.

Typefully: Free tier = 15 scheduled tweets/month (~3-4/week).
Key in Secret Manager as typefully-api-key.
Drafts endpoint: POST https://api.typefully.com/v1/drafts/
Delete unused variants after choosing -- all 3 count against the 15/month limit.

Secrets on both jobs:
- SITE_API_KEY = site-api-key:latest
- GEMINI_API_KEY = gemini-api-key:latest
- TYPEFULLY_API_KEY = typefully-api-key:latest
- DISCORD_WEBHOOK_URL = discord-webhook-url:latest

Scheduler jobs (already created):
- mlb-tweet-picks-schedule -- 0 17 * * *
- mlb-tweet-recap-schedule -- 0 10 * * *

---

### Rationale / notes wiring

mlb_core/rationale.py has rules for all 5 systems (HR, NRFI, K, OUTS, F5).
build_rationale(row_dict, system) returns up to 3 phrases joined by " . ".

Wiring status as of 2026-05-23:
- HR: wired (lazy import inside scoring function in run_hr.py)
- NRFI: wired
- F5: wired 2026-05-23 -- replaced JSON debug dict with build_rationale output
- K: wired 2026-05-23 -- added notes= kwarg to log_bet call
- OUTS: wired via K runner (market="OUTS" passed to build_rationale)

Notes field shows as italic subtext in picks-table.tsx and all three OG cards.

Gotcha: F5 previously stored a JSON dict in notes (internal debug data).
Bets before 2026-05-23 have JSON strings not rationale phrases in notes.
Frontend null-guards this -- old JSON strings show as-is. Not worth backfilling.

---

### Brand voice (Twitter)

- Confident but not loud. Data-first. Let numbers talk.
- Transparency is the brand -- post highest-edge pick win or lose
- Never cherry-pick winners after the fact
- Occasionally explain WHY the edge exists (1 sentence max)
- No hype, no LOCK, no CASH IT
- Think Bloomberg terminal meets someone who actually knows what they are doing
- Always include beezy.vip in at least one variant
- Always include discord.gg/HfMYCmbmE in at least one variant

---

### When beezy.vip DNS is configured

Update two places:
1. tweet_drafter.py: BEEZY_SITE_URL = "https://beezy.vip"
2. Discord message in post_card_to_discord(): link to https://beezy.vip/picks

### When to update this section

- New card route added or redesigned
- Headshots refreshed (update date above)
- Typefully replaced with another tool
- Gemini API key rotated or provider changed
- DNS configured for beezy.vip
- Content strategy shifts (new tweet types, threads)
- Rationale wiring status changes
