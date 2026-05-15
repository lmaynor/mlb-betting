# Project Context

_Last updated: 2026-05-14 23:15 CST_

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
│   │   └── umpires.py            Umpire scorecard pulls.
│   │                             umpires_nightly_gcs() -- called by /refresh-data.
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
│   │   ├── the_odds_api.py       DEPRECATED. Slated for deletion.
│   │   └── utils.py              american_to_implied_prob, remove_vig, kelly_stake.
│   ├── notify/
│   │   └── discord.py            post_bets / post_error / post_all_systems_summary.
│   │                             Webhook-based. post_summary() removed -- daily recap
│   │                             in post_all_systems_summary() covers all systems.
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
│   ├── settle_bets.py            Nightly: settle all pending bets from GCS sources.
│   │                             Retries stale pending bets (Statcast lag handling).
│   │                             Debug logging: pending breakdown by date/system,
│   │                             scoring master row count, game_pk lists per settler.
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
    └── test_sgo_extractors.py
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

### Loop B: Feature builds (12:00–12:45 UTC)

| Time | Job | Notes |
|---|---|---|
| 12:00 | `mlb-hr-features` | Includes Statcast nightly refresh |
| 12:15 | `mlb-nrfi-features` | Must run before F5 |
| 12:30 | `mlb-k-features` | Independent; OUTS uses same features |
| 12:45 | `mlb-f5-features` | Reads NRFI pitcher_start_features.csv |

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
13:15 UTC → /monitor-ops  → infra health check after feature builds complete
                             Silent on clean run.
```

### Full daily schedule

| Time UTC | Scheduler job | What it does |
|---|---|---|
| 08:00 | `mlb-refresh-data` | Weather + umpire + scoring masters |
| 09:00 | `mlb-settle` | Settle bets, post daily recap |
| 09:30 | `mlb-monitor` | Rolling perf check, alerts |
| 12:00 | `mlb-hr-features` | HR feature build |
| 12:15 | `mlb-nrfi-features` | NRFI feature build |
| 12:30 | `mlb-k-features` | K/OUTS feature build |
| 12:45 | `mlb-f5-features` | F5 feature build |
| 13:15 | `mlb-monitor-ops` | Infra health check |
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
                              │  12 cron jobs    │
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
F5. Scheduler reflects this (12:15 NRFI → 12:45 F5).

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

`runners/monitor_ops.py` -- fires at 13:15 UTC daily via `mlb-monitor-ops`.

Checks (all post-feature-build):
- All 12 Cloud Scheduler jobs: last run status code
- SGO snapshot age < 26hrs
- All 4 system `model_features.csv` age < 26hrs
- All 4 data masters (scoring, statcast, weather, umpires) age < 26hrs
- All 4 model artifacts exist in GCS
- Any bets pending > 3 days

Silent on clean run. Posts Discord alert only on failure.

---

## 11. Pointers to other docs

- `ipynb_CONTEXT` -- modeling theory + per-notebook summaries
- `SGO_CONTEXT` -- SGO API reference, market IDs, quota, patterns
- `SCHEDULER_CONTEXT` -- full scheduler inventory with payloads (12 jobs)
- `deploy/SGO_DEPLOY_NOTES.md` -- SGO setup runbook
- `deploy/RETRAIN_NOTES.md` -- retrain pipeline runbook + rollback
- The notebooks (`*.ipynb`) -- canonical modeling logic
- Latest session handoff -- point-in-time state, open action items

---

## 12. When to update this file

- Adding/removing a system → §1, §2, §3
- Changing a contract → §5
- New market or bet type → §5 (bet type table + settlement table)
- New gotcha → §8
- New infra → §7
- Changes to file layout → §2
- Performance monitor threshold change → §9
- Ops monitor check change → §10

**Don't put point-in-time state here.** That belongs in the session handoff.
