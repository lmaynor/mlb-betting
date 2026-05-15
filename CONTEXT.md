# Project Context

_Last updated: 2026-05-15 13:35 CST_

The standing architectural and conventions document for `lmaynor/mlb-betting`. Read this first at the start of any new session before touching code.

**This doc captures what doesn't change session-to-session.** For point-in-time status (which models are deployed, which bugs are open), see the latest handoff. For modeling theory, see `ipynb_CONTEXT`. For operational runbooks, see `deploy/*.md`.

If you change something here, treat it as a contract change -- flag it in the next commit and the next handoff.

---

## 1. What this project is

Five MLB betting systems running daily in GCP:

| System | What it predicts | Market | Status |
|---|---|---|---|
| **HR Pro v6** | P(batter hits HR in game) | DK HR yes/no props | Live (paper) |
| **NRFI Pro v17** | P(no run scored in inning 1) | DK NRFI/YRFI O/U + 1st inning 3-way ML | Live (paper) |
| **F5 Pro v5** | P(home team wins first 5 innings) | DK F5 moneyline | Live (paper) |
| **K Pro v1** | E[pitcher strikeouts] (Poisson) | DK K props (O/U) | Live (paper) |
| **OUTS** | E[pitcher outs recorded] (proxy) | DK pitcher outs O/U | Live (paper) |

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
│                                         /monitor, /monitor-ops
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
│   └── retrain_hr_meta.py        Patches feature_means into HR model meta.
│
├── HR_Pro/                       Per-system config dirs
├── NRFI_Pro_System/
├── F5_Pro_System/
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
│   └── cache_daily/
├── Scoring/
│   └── scoring_master.csv              Per-(game_pk,inning,half) runs.
│                                       AUTHORITATIVE for run targets.
│                                       Updated nightly by mlb-refresh-data.
├── Weather/
│   └── weather_master.csv              Historical weather per game.
│                                       Updated nightly by mlb-refresh-data.
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
│                                       isotonic_*.pkl
├── F5_Pro_System/
│   ├── data/
│   └── models/
│       ├── xgb_f5_v5.json
│       ├── model_meta_f5_v5.json
│       └── archive/
├── K_Pro_System/
│   ├── data/                           pitcher_k_features.csv, lineup_k_features.csv,
│   │                                   model_features.csv
│   └── models/
│       ├── xgb_k_v1.json
│       ├── model_meta_v1.json
│       └── archive/
├── {system_prefix}/
│   └── data/last_build.json            Build sentinel per system. Written on success
│                                       by each feature builder. Checked by monitor_ops.
└── probes/                             Sandbox.
```

OUTS shares K's GCS artifacts -- no separate model or feature CSV.

---

## 4. The daily loops

### Loop A: Data refresh (08:00 UTC -- `mlb-refresh-data`)
Fetches yesterday's weather (Open-Meteo archive), umpire scorecards
(umpscorecards.com), and inning-by-inning scoring (MLB Stats API) for
yesterday's games. Appends all three to GCS masters.

Scoring refresh runs at 08:00 UTC -- 2hr buffer after west coast games
finish (~midnight CT / 06:00 UTC). Adequate for regular season games.
No Statcast -- that has its own nightly job inside `build_hr_features.py`.

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
| 08:00 | `mlb-refresh-data` | Weather + umpire + scoring masters |
| 09:00 | `mlb-settle` | Settle bets, post daily recap |
| 09:30 | `mlb-monitor` | Rolling perf check, alerts |
| 12:00 | `mlb-build-all-features` | All feature builds: HR -> NRFI -> K -> F5 (dependency order) |
| 12:50 | `mlb-monitor-ops` | Infra health check after feature builds |
| 15:55 | `mlb-snapshot-morning` | SGO odds snapshot |
| 16:00 | `mlb-betting-morning` | Score all 4 runners |
| 21:55 | `mlb-snapshot-evening` | SGO odds snapshot |
| 22:00 | `mlb-betting-evening` | Score all 4 runners |

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

### SGO market coverage (all markets, settlement status)

`fetch_game_result()` returns all fields needed for every SGO market.
Add new systems by extracting from SGO snapshot + reading from game_result dict.

| SGO market | odd_id pattern | MLB API field | Status |
|---|---|---|---|
| HR yes/no | `batting_homeRuns-*-yn-yes` | `batters[name].home_runs` | Live |
| Batter hits | `batting_hits-*-ou` | `batters[name].hits` | Backlog |
| Batter total bases | `batting_totalBases-*-ou` | `batters[name].total_bases` | Backlog |
| Batter RBI | `batting_RBI-*-ou` | `batters[name].rbi` | Backlog |
| Batter runs | `points-{PLAYER}-game-ou` | `batters[name].runs` | Backlog |
| Batter strikeouts | `batting_strikeouts-*-ou` | `batters[name].strikeouts` | Backlog |
| Stolen bases | `batting_stolenBases-*-ou` | `batters[name].stolen_bases` | Backlog |
| Pitcher strikeouts | `pitching_strikeouts-*-ou` | `pitchers[name].strikeouts` | Live |
| Pitcher outs | `pitching_outs-*-ou` | `pitchers[name].outs` | Live |
| Pitcher earned runs | `pitching_earnedRuns-*-ou` | `pitchers[name].earned_runs` | Backlog |
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
- `discord-webhook-url` -- Discord webhook

**Cloud Run Jobs:**
- `mlb-retrain-f5-meta`
- `mlb-retrain-hr-meta`
- `mlb-retrain-nrfi-v17`
- `mlb-retrain-k-v1` (includes leakage guard; skip with K_SKIP_LEAKAGE_CHECK=1)

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

**Kelly floor zeroes out HR longshots.** With `min_kelly_pct=0.005` and 50%
Kelly fraction, HR bets at +600 with edges below ~7% get $0 stake.

**NRFI feature build order.** F5's builder reads
`NRFI_Pro_System/data/pitcher_start_features.csv`. NRFI must rebuild before
F5. Dependency order enforced in `/build-all-features` code -- F5 always runs after NRFI.

**`lineup_pct_L` leakage.** Was in NRFI v17 training -- carried same-game
run information into the half-inning target. Removed in retrain. Never add
same-game batter stats as features in inning-1 models.

**K build performance.** The opponent backfill pre-prepares the PA frame
once (`_prepare_pa_for_opp_features`) before the per-date loop. Do not
revert this -- the naive version was killed by gunicorn at 15min.

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
(`{system}/data/last_build.json`) catch this -- `monitor_ops` at 12:50 UTC alerts
if any sentinel is stale or status=error. The morning scoring run at 16:00 UTC is
2h10m after the alert, giving time to manually re-run the failed system via
`/build-features`. Adding runner-side sentinel checks (abort if stale, post Discord
alert) is on the backlog.

**HR settlement uses MLB Stats API boxscore (not Statcast).** `_settle_hr`
calls the MLB Stats API per game_pk. If the game is not Final, the bet is
skipped (retried tomorrow). If Final: player not in starting lineup -> void
(DK voids non-starters); starter with HR -> win; starter without HR -> loss.
No Statcast dependency for HR settlement.

---

## 9. Performance monitor

`runners/monitor_performance.py` -- fires at 09:30 UTC daily via `mlb-monitor`.

Alert thresholds (overrideable via env vars):
- `MONITOR_ROI_WARN=-15` -- ROI over last 30 bets below -15% triggers alert
- `MONITOR_HIT_RATE_DROP=10` -- hit rate more than 10pct below expected
- `MONITOR_MIN_BETS=20` -- minimum settled bets before alerting
- `MONITOR_ROLLING_WINDOW=30` -- window size

Posts weekly digest every Monday.

Expected hit rates (baselines -- update after 200 bets per system):
- HR: 7%, NRFI: 55%, F5: 52%, K: 52%, OUTS: 52%

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

Silent on clean run. Posts Discord alert only on failure.

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
- Monthly object limit -- each event returned = 1 object regardless of market count
- Typical daily cost: ~15 objects per snapshot (15 games), 2 snapshots/day = ~30/day

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
3. Extract DK bookmaker entry: `event["odds"][odd_id]["byBookmaker"]["draftkings"]`
4. Return dict keyed by player name or event_id

---

## 13. Scheduler reference

**Location:** `us-central1`
**Service URL:** `https://mlb-betting-628109313129.us-central1.run.app`
**Auth:** OIDC via `scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com`

### Full job inventory

| Job | Schedule (UTC) | Endpoint | Deadline | Body |
|---|---|---|---|---|
| `mlb-refresh-data` | `0 8 * * *` | `/refresh-data` | 300s | `{}` |
| `mlb-settle` | `0 9 * * *` | `/settle` | 600s | `{}` |
| `mlb-monitor` | `30 9 * * *` | `/monitor` | 120s | `{}` |
| `mlb-build-all-features` | `0 12 * * *` | `/build-all-features` | 1800s | `{"systems":["HR","NRFI","K","F5"],"continue_on_error":false}` |
| `mlb-monitor-ops` | `50 12 * * *` | `/monitor-ops` | 120s | `{}` |
| `mlb-snapshot-morning` | `55 15 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-betting-morning` | `0 16 * * *` | `/run` | 180s | `{"systems":["NRFI","HR","F5","K"],"run_type":"morning"}` |
| `mlb-snapshot-evening` | `55 21 * * *` | `/snapshot-odds` | 180s | `{}` |
| `mlb-betting-evening` | `0 22 * * *` | `/run` | 180s | `{"systems":["NRFI","HR","F5","K"],"run_type":"evening"}` |

### status.code values
- `-1` -- never run or ran successfully
- `0` -- success
- `2` -- error (HTTP non-2xx)
- `13` -- deadline exceeded (DEADLINE_EXCEEDED)

### Manual triggers
```bash
# Trigger a job immediately
gcloud scheduler jobs run mlb-settle --location=us-central1

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

**Don't put point-in-time state here.** That belongs in the session handoff.
