# Project Context

_Last updated: 2026-09-02 00:39 CST_

The standing architectural and conventions document for `lmaynor/mlb-betting` (the repo) -- which hosts **beezy.fyi**, a multi-sport betting platform. Read this first at the start of any new session before touching code.

**This doc captures what doesn't change session-to-session.** For point-in-time status (which models are deployed, which bugs are open), see the latest handoff in `handoffs/`. For modeling theory, see `ipynb_CONTEXT`. For operational runbooks (common gcloud/curl fragments, deploy workflow, social media pipeline), see `RUNBOOKS.md`.

If you change something here, treat it as a contract change -- flag it in the next commit and the next handoff.

---

## 0. Platform framing (beezy.fyi) and sport labeling

The product is **beezy.fyi** -- a betting platform, NOT an MLB-only project. Sports
nest underneath the beezy umbrella as **pillars**:

- **MLB** -- the mature, live pillar: 10 systems, daily Cloud Run loops, the
  `mlb_core/` package, the MLB GCS masters. This is the bulk of this document
  (sections 1-18). Treat unqualified legacy code/data as the **mlb** pillar.
- **NBA** -- the emerging, data-collection-phase pillar (no model/odds-gated live
  betting yet). Code in `nba/`, data under the `NBA/` GCS prefix. See section 19.

**Labeling convention (apply going forward; do not mass-rename live MLB code):**
- Always say which pillar a thing belongs to -- "the mlb refresh job", "the nba
  odds accumulator" -- never leave sport ambiguous.
- New code nests by sport: both pillars are now symmetric -- `mlb/` (runners/,
  training/, systems/) and `nba/`. **Pillar restructure landed 2026-06-24**
  (branch `restructure/pillarize-mlb`, PR #21): the MLB runners/, training/, and
  per-system config dirs moved under `mlb/`; module paths became `mlb.runners.*`,
  `mlb.training.*`, `mlb.systems.<DIR>.config_*`. Shared infra stays in `mlb_core/`
  (the `core/` rename remains DEFERRED -- see below). New sport-specific GCS data
  nests under a sport prefix (`NBA/`, `OddsAccum/{sport}/`, `Enrich/edge/` is
  shared/sport-tagged inside).
  DEPLOYED 2026-06-24 (service rev `mlb-betting-00240-rvc`): the image was rebuilt
  (`deploy/deploy_service.sh`) and ALL Cloud Run Jobs re-provisioned to the `mlb.*`
  paths via `deploy/setup_model_jobs.sh` + `setup_edge_enrichment.sh` +
  `setup_fit_calibrators.sh` (the last needs `PROJECT_ID=`, not `PROJECT=`).
  Re-provisioning is mandatory after any such move -- a Cloud Run Job's `-m` command
  is baked into GCP, so a code-only change leaves jobs failing with ModuleNotFoundError.
- Truly sport-agnostic infra (e.g. `mlb_core.storage`, `mlb_core.odds.utils`) is
  reused across pillars despite the legacy `mlb_` name; a rename to a neutral
  `core/` is DEFERRED (too invasive while MLB is live) -- tracked, not done.
- Frontend (beezy.fyi) is sport-aware: the `/edge` dashboard has MLB live + an
  NBA(soon) tab; keep new UI sport-keyed so NBA slots in without a redesign.

The repo name (`lmaynor/mlb-betting`) is legacy and not worth renaming; the
**brand and mental model is beezy.fyi with mlb + nba pillars.**

---

## Table of contents

0. [Platform framing (beezy.fyi) and sport labeling](#0-platform-framing-beezyfyi-and-sport-labeling)
1. [What this project is](#1-what-this-project-is)
2. [Repo layout (the map)](#2-repo-layout-the-map)
3. [GCS layout (the data lake)](#3-gcs-layout-the-data-lake)
4. [The daily loops](#4-the-daily-loops)
5. [Contracts between components](#5-contracts-between-components)
6. [Conventions](#6-conventions)
7. [Cloud architecture](#7-cloud-architecture)
8. [SGO API reference](#8-sgo-api-reference)
9. [Scheduler reference](#9-scheduler-reference)
10. [DK grading rules (MLB props)](#10-dk-grading-rules-mlb-props)
11. [Performance monitor](#11-performance-monitor)
12. [Ops monitor](#12-ops-monitor)
13. [Beezy.FYI -- the frontend](#13-beezyvip----the-frontend)
14. [Discord server (beezy.fyi)](#14-discord-server-beezyvip)
15. [Gotchas](#15-gotchas)
16. [Backlogs](#16-backlogs)
17. [Pointers to other docs](#17-pointers-to-other-docs)
18. [When to update this file](#18-when-to-update-this-file)
19. [NBA (data-only, pre-modeling)](#19-nba-data-only-pre-modeling)

---

## 1. What this project is

beezy.fyi's **MLB pillar** (see section 0 for the platform framing). Ten MLB
betting systems running daily in GCP:

| System | What it predicts | Market | Status |
|---|---|---|---|
| **HR Pro v6** | P(batter hits HR in game) | HR yes/no props (best onshore book) | Live (paper) |
| **NRFI Pro v18** | P(no run scored in inning 1) | NRFI/YRFI O/U + 1st inning 3-way ML (best onshore book) | Live (paper) |
| **F5 Pro v5** | P(home team wins first 5 innings) | F5 moneyline (best onshore book) | Live (paper) |
| **K Pro v1** | E[pitcher strikeouts] (NB; k_per_9_L5 * avg_ip scaling) | K props O/U (best onshore book) | Live (paper) |
| **OUTS** | E[pitcher outs recorded] (trained NegBin; retrain_outs_v1.py) | Pitcher outs O/U (best onshore book) | Live (paper) |
| **F1H** | P(home wins innings 1-4) via F5 scalar proxy | First Half ML (best onshore book) | Live (log-only) |
| **GAME Pro v1** | P(home wins full game) -- binary:logistic with bullpen features (xwOBA L14, K%/BB%, fatigue IP L7) that F5 misses | Full Game ML (best onshore book) | Live (log-only, 200-bet gate) |
| **BATTER_TB** | E[batter total bases] NegBin count regressor (lambda; XBH/contact/platoon/pitcher features) | Batter TB O/U (best onshore book) | Live (paper) |
| **BATTER_HITS** | E[batter hits] NegBin count regressor (lambda; BABIP/contact/platoon/pitcher features) | Batter hits O/U (best onshore book) | Live (log-only, 200-bet gate) |
| **PITCHER_ER** | P(earned runs > line) Gamma proxy via K model lambda | Pitcher ER O/U (best onshore book) | Live (log-only) |
| **SB** | E[stolen bases] NegBin count regressor (lambda; 23 features -- on-base/speed/pitcher/**catcher** -- first system needing catcher identity) | Stolen base O/U (`player_stolen_bases`, confirmed live on ParlayAPI 2026-08-20) | **Live (log-only)**, deployed 2026-08-22 (rev `mlb-betting-00290-bbl`). Backtested NO_EDGE across 3 rounds (baseline/Optuna-tuned/+pitcher_pickoffs feature, CLV -1.63% to -1.49% throughout) -- `LOG_ONLY=True` means it silently logs to the 200-bet paper gate, no Discord posts, by design. See handoffs/handoff_2026-08-20_sb_stolen_base_model_build.md |

Batter prop runners (`BATTER_HITS`, `BATTER_TB`) require confirmed lineup
candidates and skip any SGO prop whose `event_id` does not match the feature
row `game_pk`. Do not restore historical-team fallback matching; it can assign
players to the wrong game when lineups are missing.

OUTS is a sub-market of the K runner -- same feature CSV, same `run_k.py` -- but logged as a separate system (`system="OUTS"`) for independent tracking and settlement.

All systems are paper-mode-only until each clears a 200-settled-bet gate.

The system has three responsibilities: **build features daily**, **score / size bets twice daily**, and **settle bets + monitor performance nightly**.

Model training itself is human-driven (notebooks on Windows) but is being migrated to a Cloud Run Jobs pipeline (`mlb/training/`).
`BATTER_TB` is now first-class in that pipeline via `BATTER_TB_System/`,
`mlb/runners/build_batter_tb_features.py`, `mlb/training/retrain_batter_tb_v1.py`, and
`mlb/training/calibrate_batter_tb_v1.py`.

---

## 2. Repo layout (the map)

```
mlb-betting/
├── main.py                       Flask entrypoint for Cloud Run service.
│                                 Routes: /healthz, /run, /build-features,
│                                         /snapshot-odds, /settle, /refresh-data,
│                                         /monitor, /monitor-ops, /retrain-weekly,
│                                         /model-health, /edge-analysis, /backfill-data
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
│   │   ├── game_result.py        fetch_game_result(game_pk) -- MLB Stats API linescore
│   │   │                         + boxscore. Returns innings/pitchers/batters dict.
│   │   │                         Returns None if game not Final. Used by settle_bets.
│   │   ├── auxiliary_features.py B-Ref pitching (FIP/WHIP/SO9/BB9), Savant swing_take,
│   │   │                         team_schedule (travel_miles/home_away_streak/series_game_num),
│   │   │                         manager_hooks (avg_starter_outs_L30/pct_quick_hooks_L30/
│   │   │                         pct_quality_starts_L30) fetchers + GCS loaders.
│   │   │                         CLI: PYTHONPATH=. python3 -m mlb_core.data.auxiliary_features
│   │   │                         <dataset> [--force]. Writes AuxData/ masters to GCS.
│   │   │                         norm_statcast_name() converts "Last, First" -> bref name_norm.
│   │   ├── id_resolver.py        (date,teams)->game_pk + (name,team,date)->MLBAM id
│   │   │                         via MLB Stats API (cached). Bridges odds -> game data.
│   │   └── aux_joins.py          Shared join helpers for all 7 feature builders.
│   │                             join_pitcher_aux() -- NRFI, K (bref + team_schedule + manager_hooks).
│   │                             join_game_aux()    -- F5, GAME (team_schedule + manager_hooks x2).
│   │                             join_batter_aux()  -- HR, BATTER_HITS, BATTER_TB (swing_take + team_schedule x2).
│   │                             All joins are left joins; missing data yields NaN, never drops rows.
│   ├── odds/
│   │   ├── sgo.py                SGO client + 10 extractors:
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
│   │   ├── parlay_adapter.py     ParlayAPI payload -> SGO snapshot shape + merge
│   │   │                         (inning fallback). Primary live-odds path. See s8.
│   │   └── utils.py              american_to_implied_prob, remove_vig, kelly_stake,
│   │                             american_to_decimal, devig_two_way (shin/proportional).
│   ├── notify/
│   │   └── discord.py            post_bets / post_error / post_all_systems_summary.
│   │                             Webhook-based. post_summary() removed -- daily recap
│   │                             in post_all_systems_summary() covers all systems.
│   ├── risk/
│   │   └── exposure.py           prefetch_exposure() + apply_cap().
│   │                             One DB query per runner; _pending_stakes accumulator
│   │                             tracks within-runner exposure correctly.
│   ├── rationale.py              Canned rationale engine. Maps feature values to plain-English phrases.
│   │                             Extend by adding rules to _SYSTEM_RULES in rationale.py.
│   ├── registry.py               Single source of truth for all system config (SystemConfig dataclass).
│   │                             SYSTEMS dict + CANONICAL_ORDER + get_system() + active_systems().
│   │                             Adding a new system requires ONE entry here instead of editing 8+ files.
│   │                             Import: from mlb_core.registry import SYSTEMS, get_system, active_systems
│   ├── schemas.py                Lightweight DataFrame validation. validate_df(df, schema_key, ...).
│   │                             8 named schemas: statcast_raw, scoring_master, nrfi/f5/hr/k/
│   │                             batter_hits/game model_features. Logs WARNING per violation;
│   │                             raises ValueError if raise_on_error=True.
│   └── tracking/
│       └── bet_tracker.py        BetTracker(db_path, system). Writes to Postgres.
│                                 log_bet() dedup on (system, game_date, game_pk, bet_type).
│                                 summary() filters by system via text() wrapper.
│
├── mlb/                         MLB PILLAR (symmetric with nba/; see section 0)
│   │                            Restructured 2026-06-24: runners/, training/, and the
│   │                            per-system config dirs moved under mlb/. Shared infra
│   │                            stays in mlb_core/ (rename to core/ still DEFERRED).
│   │                            New pillars (NFL/NHL) follow this nesting template.
│   ├── runners/                 DAILY JOBS (was top-level runners/)
│   │   ├── build_hr_features.py      Nightly: build HR_Pro/data/model_features.csv.
│   ├── build_nrfi_features.py    Nightly: build NRFI pitcher features + model_features.
│   ├── build_f5_features.py      Nightly: build F5 pitcher/offense/model_features.
│   ├── build_k_features.py       Nightly: build K_Pro_System/data/model_features.csv.
│   ├── run_hr.py                 Score HR, post bets.
│   ├── run_nrfi.py               Score NRFI/YRFI O/U + 1st inning 3-way ML, post bets.
│   ├── run_f5.py                 Score F5 ML, post bets.
│   ├── run_k.py                  Score K O/U (system="K") + pitcher outs O/U
│   │                             (system="OUTS") -- two trackers, one runner.
│   ├── run_batter_hits.py        Score BATTER_HITS O/U via NegBin CDF. LOG_ONLY=True
│   │                             until 200-bet gate cleared.
│   ├── build_batter_hits_features.py  Nightly: build BATTER_HITS_System/data/model_features.csv.
│   ├── run_sb.py                 Score SB (stolen base) O/U via NegBin CDF. LOG_ONLY=True
│   │                             (new system, added 2026-08-20). Dual devig path (two-sided
│   │                             + one-sided "Yes"-only, confirmed live -- see s8).
│   ├── build_sb_features.py      Nightly: build SB_Pro_System/data/model_features.csv.
│   │                             Target is NOT statcast-derived (statcast cannot see SB/CS
│   │                             events at all, verified live) -- comes from
│   │                             mlb_core.data.sb_boxscore's MLB Stats API boxscore backfill.
│   │                             First builder needing a catcher join (join_catcher_aux).
│   ├── run_game.py               Score GAME ML (HOME/AWAY moneyline) via GAME Pro v1.
│   │                             LOG_ONLY=True until 200-bet gate cleared.
│   ├── build_game_features.py    Nightly: build GAME_Pro_System/data/model_features.csv.
│   │                             Computes starter rolling stats (L3), bullpen rolling stats
│   │                             (L14 xwOBA/K%/BB%, L7 fatigue IP), team offense wOBA (L20).
│   ├── snapshot_odds.py          Fetch SGO slate -> GCS latest.json.
│   ├── settle_bets.py            Nightly: settle all pending bets via MLB Stats API.
│   │                             fetch_game_result() called once per game_pk, cached.
│   │                             Retries non-Final games automatically on next run.
│   ├── monitor_performance.py    Daily: rolling perf check, Discord alerts, Mon digest.
│   ├── monitor_ops.py            Daily: infra health check after feature builds.
│   │                             Checks: scheduler job status, GCS freshness of SGO
│   │                             snapshot + all model_features.csv + all data
│   │                             masters (scoring/statcast/weather/umpires), model
│   │                             artifact existence, bets pending > 3 days.
│   │                             Silent on clean run. Posts Discord alert on failure.
│   ├── fast_alert_loop.py        The intraday +EV pager (own schedule, not part of
│   │                             Loop A-D below): Pinnacle-anchored outlier_scan vs
│   │                             fresh BettingPros snapshots, Discord alert on NEW
│   │                             +EV quotes only (per-day dedup). Discord embed is a
│   │                             double group-by (2026-08-20): one field per
│   │                             sportsbook (books ordered by their best EV), alerts
│   │                             within a book ordered by EV -- replaced the old
│   │                             one-field-per-alert layout. The separate raw-game_pk
│   │                             "Lineup events" field was removed the same day (not
│   │                             actionable on its own); lineup_events() still drives
│   │                             the per-alert hot-game badge/sort priority. Every
│   │                             POSTED alert is also logged to the `bets` table
│   │                             (system="EV", flat stake) for profitability tracking
│   │                             -- see s5 "EV bet tracking" + s5 Discord posting
│   │                             contract + deploy/setup_fast_alert.sh.
│   └── kalshi_alert.py           Same pager pattern for mlb.analysis.kalshi_vs_books
│                                 (soft book vs Kalshi no-vig mid, not vs Pinnacle) --
│                                 read-only against odds_history, no snapshot of its
│                                 own. 6x/day, offset after mlb-kalshi-capture + the
│                                 mlb-snapshot-* cadence land. Every posted alert also
│                                 logs to the `bets` table (system="EV", pooled with
│                                 fast_alert_loop's own -- 2026-08-20, see s5 "EV bet
│                                 tracking"). See deploy/setup_kalshi_alert_job.sh.
│
│   ├── training/                RETRAIN PIPELINE (was top-level training/)
│   │   ├── retrain_f5_meta.py        Patches feature_means into F5 model meta.
│   ├── retrain_nrfi_v17.py       Full NRFI retrain. (v17 -- superseded)
│   ├── retrain_nrfi_v18.py       Full NRFI v18 retrain (E05+E08: sub-model ensemble).
│   ├── calibrate_nrfi_v18.py     Fit isotonic calibrator for NRFI v18.
│   ├── retrain_k_v1.py           Full K retrain with walk-forward CV + leakage guard.
│   ├── retrain_hr_meta.py        Patches feature_means into HR model meta.
│   ├── calibrate_nrfi_v17.py     Fit isotonic calibrator for NRFI v17.
│   ├── calibrate_f5_v5.py        Fit isotonic calibrator for F5 v5.
│   ├── calibrate_k_v1.py         Fit lambda calibrator for K v1.
│   ├── calibrate_hr_v6.py        Fit isotonic calibrator for HR v6.
│   ├── retrain_outs_v1.py        Full OUTS retrain (NegBin count model). (E04)
│   ├── retrain_batter_hits_v1.py Full BATTER_HITS retrain (NegBin count:poisson on batter_hits).
│   ├── calibrate_batter_hits_v1.py  Fit isotonic lambda calibrator for BATTER_HITS v1.
│   ├── retrain_game_v1.py        Full GAME retrain (binary:logistic on home_win, CV 2023-2025).
│   ├── calibrate_game_v1.py      Fit IsotonicRegression calibrator for GAME v1 (Brier eval).
│   └── tune_hyperparams.py       Optuna hyperparameter search for all systems. (E09)
│
│   └── systems/                 Per-system config dirs (was top-level; import as
│       │                        mlb.systems.<DIR>.config_<sys>)
│       ├── HR_Pro/
│       ├── NRFI_Pro_System/
│       ├── F5_Pro_System/
│       ├── OUTS_Pro_System/     OUTS Pro config (shares K feature CSV)
│       ├── K_Pro_System/
│       ├── BATTER_HITS_System/  BATTER_HITS config (config_batter_hits.py + __init__.py)
│       └── GAME_Pro_System/     GAME config (config_game.py + __init__.py)
│                                42 features: starters L3 (xwOBA+whiff+hard-hit), bullpen L14
│                                (xwOBA/K%/BB%/whiff/hard-hit/fatigue), offense L20 (wOBA+hard-hit),
│                                park/weather. +10 vs original: whiff_pct_L3, hard_hit_allowed_L3
│                                (starters), bullpen_whiff_pct_L14, bullpen_hard_hit_L14,
│                                team_hard_hit_L20 (home+away = 10 columns)
│
├── mlb/analysis/                 OFFLINE odds/backtest toolkit (not in daily loops):
│   ├── odds_history.py           Parquet store: write_partition/read_history/coverage_report
│   ├── bettingpros_to_parquet.py BettingPros CSV -> odds_history (historical, P0.3)
│   ├── parlayapi_to_history.py   ParlayAPI OddsAccum -> odds_history (forward feed)
│   ├── kalshi_to_history.py, kalshi_history.py, kalshi_vs_books.py
│   │                             Kalshi (no-vig exchange) as a sharp-reference feed +
│   │                             soft-book +EV scanner vs its mid (game_ml/totals/runline/nrfi)
│   ├── gen_preds.py              Score a system's full historical model_features.csv with the
│   │                             SAME production artifacts the live runner uses (booster+meta+
│   │                             calibrator). SPECS dict = per-system market/feature_csv/id_col.
│   ├── walkforward.py            Leakage-proof OOS: train on game_date<cutoff, score >=cutoff,
│   │                             reusing each system's production training contract verbatim.
│   │                             WF_SYS = K/OUTS/BATTER_HITS/BATTER_TB/HR/GAME/SB -- the only
│   │                             systems with a single-booster contract (NRFI/F5/etc. need a
│   │                             model BUILT, not tuned).
│   ├── backtest_market.py        Join preds -> odds_history real lines, line-shop, settle,
│   │                             edge-bucket ROI/CLV. verdict() (2026-08) codifies the go/no-go:
│   │                             PROMOTE_CANDIDATE/NO_EDGE/INSUFFICIENT_N (significant low-edge
│   │                             CLV via mlb_core.risk.clv.clv_verdict + monotonic edge ladder).
│   │                             See docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md.
│   ├── model_bakeoff.py, hr_model_bakeoff.py
│   │                             Train several model families per system OOS (incl. xgb_optuna,
│   │                             a real per-system walk-forward-safe search), backtest each via
│   │                             backtest_market.verdict(). --tune/--persist/--resume/--notify
│   │                             (bakeoff_persist.py: Analysis/bakeoff/runs/{run_id}/, GCS-
│   │                             durable, survives a Cloud Shell disconnect or VM reclaim --
│   │                             see mlb-bakeoff Cloud Run Job in s7). hr_model_bakeoff.py adds
│   │                             HR-specific candidates (xhr_poisson) + a YES/NO side-split.
│   ├── bakeoff_tuning.py, bakeoff_persist.py, bakeoff_report.py
│   │                             Support modules for the above: Optuna search (month-folded,
│   │                             pre-cutoff only -- never touches the walk-forward holdout),
│   │                             GCS persistence + resume/notify, and a markdown handoff
│   │                             renderer for a persisted run (bakeoff_report.py).
│   ├── hr_softline.py            HR-YES soft-book +EV vs a SHARP low-vig anchor (book_vig-tagged
│   │                             sharp/soft books) -- a market-structure strategy, independent of
│   │                             the model. --validate settles flagged quotes vs real outcomes.
│   ├── book_vig.py, quote_survival.py
│   │                             Empirical per-(market,book) vig fit + stale-quote survival
│   │                             analysis; posted weekly to Discord by mlb-weekly-survival.
│   └── alt_line_scan.py, diagnose_bettingpros_ou.py, model_vs_market.py, odds_freshness.py,
│       outlier_scan.py, verify_odds_history.py, nrfi_market.py, gen_nrfi_preds.py, ...
│                                 Assorted diagnostics/scanners (alt-line pricing, BettingPros
│                                 O/U parsing checks, model-vs-market comparison, snapshot
│                                 freshness, outlier detection, odds_history integrity, NRFI backtest).
├── notebooks/                    Modeling notebooks (moved from root 2026-06-24).
├── scripts/                      One-off ops scripts (cleanup_discord, debug_ops, ...).
│                                 bettingpros_api.py = local CLI for the HR/multi-market
│                                 odds scrape (Cloud Run Job is mlb-backfill-bettingpros).
├── deploy/                       Operational scripts and runbooks
│   ├── deploy.sh
│   ├── SGO_DEPLOY_NOTES.md
│   └── RETRAIN_NOTES.md
│
├── handoffs/                     Dated session handoff files (point-in-time state)
├── .env.example                  Documents all env vars. .env is gitignored.
├── *.ipynb                       Modeling notebooks -- canonical source of logic.
├── ipynb_CONTEXT                 Summary of what each notebook does.
├── CONTEXT.md                    (this file) -- Claude project is source of truth.
│                                 Commit to repo at end of each session.
├── RUNBOOKS.md                   Common manual actions, Claude Code workflow,
│                                 social media pipeline.
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
│   ├── scoring_master.csv              Per-(game_pk,inning,half) runs.
│   │                                   AUTHORITATIVE for run targets.
│   │                                   Updated nightly by mlb-refresh-data.
│   └── sb_boxscore_master.csv          Per-(game_pk,batter) stolen_bases/caught_stealing,
│                                       team, starter flag, batting_order -- from MLB Stats
│                                       API boxscore (mlb_core.data.sb_boxscore), NOT
│                                       statcast (which cannot see these events at all,
│                                       verified live 2026-08-20). AUTHORITATIVE target
│                                       for the SB model. Backfilled 2023-03-01+ (regular +
│                                       postseason only) via scripts/backfill_sb_boxscore.py.
│                                       Nightly refresh wired via sb_nightly_gcs(), called
│                                       from /refresh-data alongside scoring_nightly_gcs().
├── Weather/
│   └── weather_master.csv              Historical weather per game.
│                                       Updated nightly by mlb-refresh-data.
│                                       No dedicated backfill function -- use
│                                       /backfill-data with systems=["weather"].
├── Umpires/
│   └── umpscorecards_master.csv        Umpire scorecards.
│                                       Updated nightly by mlb-refresh-data.
├── Odds/
│   ├── sgo/                            LIVE snapshot (now ParlayAPI-primary, SGO
│   │   ├── latest.json                 inning-merged; SGO-shaped -- runners read this).
│   │   └── {YYYY-MM-DD}/snapshot_{HHMM}.json   (8x/day; see s8)
│   ├── bettingpros/                    HISTORICAL odds backfill (raw store),
│   │   └── {market}/{YYYY-MM-DD}.csv    partitioned; 29 markets. Written by the
│   │                                   mlb-backfill-bettingpros Cloud Run Job.
│   └── history/                        odds_history Parquet ANALYTICS store.
│       ├── market={m}/date={YYYY-MM-DD}/part-0.parquet
│       └── _coverage/{market}.json     per-market coverage (backtest gating)
├── OddsAccum/baseball_mlb/             ParlayAPI capture (banked by the snapshot
│   ├── raw/{date}/props_{HHMM}.json     job); feeds odds_history (parlayapi).
│   ├── {date}/{kind}_{HHMM}.csv         best-book flattened rows
│   ├── latest.json                     pointer + status
│   └── _credits/{YYYY-MM}.json         implicit monthly credit tally (guard)
├── Alerts/{YYYY-MM-DD}/                Per-day state for the intraday +EV pagers
│   │                                   (fast_alert_loop.py, kalshi_alert.py). Not
│   │                                   read by anything else -- pure notify-dedup.
│   ├── notified.parquet                fast_alert_loop: quote keys already pinged
│   │                                   today (market/game_pk/player_id/line/
│   │                                   selection/book) -- never re-ping the same one.
│   ├── log.parquet                     fast_alert_loop: full alert rows, all
│   │                                   snapshots -- feeds the nightly odds_alert
│   │                                   resolve/scorecard pass.
│   ├── kalshi_notified.parquet         kalshi_alert: same dedup role, separate file
│   │                                   so the two pagers' state never collides.
│   ├── kalshi_log.parquet              kalshi_alert: same audit-trail role.
│   └── lineup_state.json               fast_alert_loop: last-seen posted lineups,
│                                       for lineup-POSTED/CHANGED "hot game" detection.
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
├── OUTS_Pro_System/
│   └── models/                         xgb_outs_v1.json, model_meta_outs_v1.json,
│                                       isotonic_calibrator_outs_v1.pkl
│                                       (Shares K_Pro_System/data/model_features.csv)
├── K_Pro_System/
│   ├── data/                           pitcher_k_features.csv, lineup_k_features.csv,
│   │                                   model_features.csv (includes starter_outs)
│   └── models/
│       ├── xgb_k_v1.json
│       ├── model_meta_v1.json
│       ├── lambda_calibrator_k_v1.pkl
│       └── archive/
├── BATTER_HITS_System/
│   ├── data/                           batter_hits_features.csv, pitcher_hits_features.csv,
│   │                                   model_features.csv
│   └── models/
│       ├── xgb_batter_hits_v1.json
│       ├── model_meta_batter_hits_v1.json  (includes nb_alpha for NegBin CDF)
│       ├── lambda_calibrator_batter_hits_v1.pkl
│       └── archive/
├── SB_Pro_System/                      Added 2026-08-20 (stolen base model).
│   ├── data/                           sb_batter_features.csv, model_features.csv
│   └── models/
│       ├── xgb_sb_v1.json
│       ├── model_meta_sb_v1.json       (includes nb_alpha for NegBin CDF)
│       ├── lambda_calibrator_sb_v1.pkl
│       └── archive/
├── GAME_Pro_System/
│   ├── data/                           starter_game_features.csv, bullpen_game_features.csv,
│   │                                   team_offense_features.csv, model_features.csv
│   └── models/
│       ├── xgb_game_v1.json
│       ├── model_meta_game_v1.json      (features, feature_means, best_iteration, auc_oos)
│       ├── isotonic_calibrator_game_v1.pkl
│       └── archive/
├── AuxData/
│   ├── fangraphs_pitching_master.csv   FIP/WHIP/SO9/BB9 per pitcher-season (B-Ref via FanGraphs).
│   │                                   (This file used to be documented here as
│   │                                   bref_pitching_master.csv, which does not exist --
│   │                                   the real GCS object and the code's own
│   │                                   _FG_PREFIX both say fangraphs_pitching_master.csv.
│   │                                   Fixed 2026-08-19, see docs/audits/
│   │                                   2026-08-19_feature_data_pipeline_review.md finding 2.8.)
│   │                                   Refreshed nightly by auxiliary_features_nightly_gcs()
│   │                                   via /refresh-data (wired in 2026-08-19 -- see §15.4
│   │                                   for why it wasn't before).
│   ├── swing_take_master.csv           Batter swing/take run values per (MLBAM ID, season).
│   │                                   Fetched from Savant /leaderboard/swing-take.
│   │                                   player_id is BATTER MLBAM ID only -- see §15.4 gotcha.
│   ├── team_schedule_master.csv        travel_miles, home_away_streak, series_game_num
│   │                                   per (team, game_pk). Built from MLB Stats API schedule.
│   ├── manager_hooks_master.csv        avg_starter_outs_L30, pct_quick_hooks_L30,
│   │                                   pct_quality_starts_L30 per (team, game_pk).
│   │                                   Built from statcast_master groupby pitcher-game.
│   ├── catcher_poptime_master.csv      Added 2026-08-20 (SB model). maxeff_arm_2b_3b_sba,
│   │                                   exchange_2b_3b_sba, pop_2b_sba/_cs/_sb + 3B variants,
│   │                                   per (catcher MLBAM id via player_id, season).
│   │                                   pybaseball.statcast_catcher_poptime() -- real,
│   │                                   working function in an already-installed dependency;
│   │                                   no new data source. Backfilled 2015-2026.
│   ├── catcher_identity_master.csv     Added 2026-08-20 (SB model). away_catcher_id,
│   │                                   home_catcher_id per game_pk -- starting catcher
│   │                                   MLBAM id per side, from the boxscore's own
│   │                                   position=="C" field (mlb_core.data.lineups
│   │                                   catcher_backfill_gcs() / get_starting_catchers()).
│   │                                   Backfilled 2023-03-01+ via
│   │                                   scripts/backfill_catcher_identity.py. Nightly
│   │                                   refresh via catcher_identity_nightly_gcs().
│   └── {prefix}/data/last_build.json  Freshness sentinel per aux dataset.
├── {system_prefix}/
│   └── data/last_build.json            Build sentinel per system. Written on success
│                                       by each feature builder. Checked by monitor_ops.
├── Analysis/bakeoff/                   model_bakeoff.py / hr_model_bakeoff.py --persist output
│   ├── runs/{run_id}/                  run_id = {cutoff}_{git_sha7}_{HHMMSS_UTC}
│   │   ├── run_meta.json               git sha/branch, cutoff/until, gates, tune params,
│   │   │                               timestamps, status, systems_completed (drives --resume)
│   │   ├── scorecard.csv               one row per system x model, incl. verdict columns
│   │   ├── candidates/{system}_{model}.csv   every settled bet (backtest()'s candidates frame)
│   │   └── tuning/{system}_tuned.json, {system}_trials.csv
│   │                                   tuned params for THIS run only -- never the production
│   │                                   tuned_params.json key tune_hyperparams.py reads (see
│   │                                   docs/solutions/conventions/bakeoff-tuned-params-storage.md)
│   └── latest.json                     best-effort {run_id, prefix} pointer, updated on completion
└── probes/                             Sandbox.
```

---

## 4. The daily loops

### Loop A: Data refresh (14:00 UTC -- `mlb-refresh-data`)

Fetches yesterday's weather (Open-Meteo archive), umpire scorecards
(umpscorecards.com), inning-by-inning scoring (MLB Stats API), and
Statcast pitch data for yesterday's games. Appends all four to GCS masters.
Feature builds only READ from GCS masters -- they never write them.
Statcast was previously updated inside `build_hr_features.py`; moved to
`/refresh-data` 2026-05-18 to decouple data refresh from feature builds.

Scoring refresh runs at 14:00 UTC -- a healthy multi-hour buffer after west
coast games finish (~midnight CT / 06:00 UTC). Adequate for regular season games.
Also refreshes six Savant leaderboards for the current season via
`savant_leaderboards_nightly_all_gcs()`. In-season: ~60s added to refresh time.
Off-season (Dec-Feb): no-op, returns status="skipped".

Also refreshes the four AuxData sources (FanGraphs pitching, Savant
swing-take, team schedule, manager hooks) via `auxiliary_features_nightly_gcs()`.
Wired in 2026-08-19 -- this function's own docstring had claimed since it was
written that it was "Called by /refresh-data," but nothing ever actually
called it; 3 of the 4 sources sat frozen for 11+ weeks (since ~2026-06-02)
with no scheduled refresh and no freshness alert (now added to
`monitor_ops.DATA_MASTER_KEYS`). See docs/audits/
2026-08-19_feature_data_pipeline_review.md finding 2.3. Same in-season-only
guard as the Savant leaderboards.

### Loop B: Feature builds (14:30 UTC)

| Time | Job | Notes |
|---|---|---|
| 14:30 | `mlb-build-all-features` | Runs HR -> NRFI -> K -> F5 in dependency order |

Dependency order enforced in code: F5 reads NRFI's `pitcher_start_features.csv`.
HR and K are independent. Each system writes a build sentinel to GCS on success
(`{system_prefix}/data/last_build.json`) -- checked by `monitor_ops` at 15:20 UTC.

### Loop C: Score + bet (16:00 and 22:00 UTC / 11am and 5pm CT)

```
8x/day -> /snapshot-odds  -> Odds/sgo/latest.json  (ParlayAPI primary + SGO inning merge; see s8)
16:00 / 22:00 UTC -> /run -> all runners score + post bets
```
Snapshots now run 8x/day (ParlayAPI), concentrated in the 18:00-23:00 UTC
lineup->closing window; SGO inning markets fetched on 4 of them, carried forward
on the rest. The pre-/run snapshots are 15:55 and 21:55. See s8 for the cadence,
credit pacing, and `ODDS_PRIMARY` cutover.

Runners post bet signals to Discord only (`post_bets`). No per-runner
performance summaries -- those come from the daily recap in `/settle`.

### Loop D: Settle + monitor (09:00-15:20 UTC)

```
09:00 UTC -> /settle       -> settle yesterday's bets + retry stale pending
                              posts daily recap embed (post_all_systems_summary)
09:30 UTC -> /monitor      -> rolling perf check, Discord alert if degraded
                              Monday: weekly digest post
15:20 UTC -> /monitor-ops  -> infra health check after feature builds complete
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
| 15:55 | `mlb-snapshot-1555` | Odds snapshot (ParlayAPI+SGO inning) -- pre-morning |
| 16:00 | `mlb-betting-morning` | Score all runners |
| 18:55 | `mlb-snapshot-1855` | Odds snapshot (parlay-only, carry inning) |
| 20:25 | `mlb-snapshot-2025` | Odds snapshot (SGO inning) -- night lineups |
| 21:00 | `mlb-refresh-statcast` | Refresh statcast (after Savant publishes) |
| 21:25 | `mlb-snapshot-2125` | Odds snapshot (parlay-only, carry inning) |
| 21:55 | `mlb-snapshot-2155` | Odds snapshot (SGO inning) -- pre-evening |
| 22:00 | `mlb-betting-evening` | Score all runners |
| 23:05 | `mlb-snapshot-2305` | Odds snapshot (SGO inning) -- closing |
| 00:00 | `mlb-capture-closing` | Capture closing lines for CLV calculation |
| 01:25 | `mlb-snapshot-0125` | Odds snapshot (parlay, `day_offset=1`) -- tomorrow's lines |
| 03:25 | `mlb-snapshot-0325` | Odds snapshot (parlay, `day_offset=1`) -- tomorrow |

Snapshot jobs are ParlayAPI-primary (env `ODDS_PRIMARY=parlay`); see s8. The old
4 `mlb-snapshot-{morning,afternoon,evening,pregame}` jobs were replaced by these 8.

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
crashes XGBoost >=2.0.

### Multi-book odds contract

All extractors use `_best_book_odds_int()` in `mlb_core/odds/sgo.py`.
Picks best American odds for the bettor across all qualifying books; result
stored as `book` column in `bets` table.

**Book qualification is now a DENYLIST** (changed 2026-06-29 with the ParlayAPI
migration): **every US book qualifies** as onshore; only `OFFSHORE_BOOKS`
(bovada, betfair, pinnacle, betonline, mybookie, ...) are excluded. This widens
best-line selection (the line-shopping lever) to all US books ParlayAPI carries
(draftkings, fanduel, betmgm, caesars, bet365, betrivers, fanatics, hardrock,
novig, parx, underdog, espnbet->thescore, pointsbet).

```python
OFFSHORE_BOOKS = {"bovada", "betfair", "pinnacle", "betonline", "mybookie", ...}
# _best_book_odds_int / _best_book_odds_for_line: skip `if book in OFFSHORE_BOOKS`.
# ONSHORE_BOOKS (known-US set) + ONSHORE_BOOKS_PRIORITY drive line-selection
# priority + canonicalization, not the gate. BOOK_CANONICAL maps espnbet->thescore;
# unknown books canonical to themselves.
```

To exclude a book: add it to `OFFSHORE_BOOKS`. The ParlayAPI adapter
(`mlb_core/odds/parlay_adapter.py`) passes every non-offshore book through.

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

This ensures within-runner accumulation is tracked correctly (second bet on same game_pk sees reduced cap).
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
This gives one clean daily embed covering all active systems.

**`post_bets()`'s embed is grouped by sportsbook (2026-08-20 follow-up),
same double group-by as the +EV alert pagers' embeds** (see `_grouped_bet_fields()`
in `mlb_core/notify/discord.py`, a direct parallel of
`fast_alert_loop._grouped_fields()`): one field per book (books ordered by
that book's own best edge, descending), bets within a book ordered by edge.
Applies uniformly to every system that calls `post_bets()` -- no
per-system opt-in needed, it's the one shared function.

**RULE: a log-only system's Discord-bound rows must be gated on
`kelly_triggered`, same as a graduated system's.** `post_bets()` itself
posts whatever list it's handed, unconditionally -- every caller is
responsible for filtering to `kelly_triggered=True` rows first (the
model systems all do this: `if triggered: bet_rows.append(...)` before
`post_bets(bet_rows, ...)`). Bug found 2026-08-20 (user report: a
negative-edge Cade Cavalli PITCHER_ER prediction posted to #daily-picks):
PITCHER_ER's sub-market loop in `run_k.py` and F1H's identical sub-market
loop in `run_f5.py` both appended to their Discord-bound list
unconditionally, with no `kelly_triggered` gate at all -- so while each is
log-only (kelly_triggered structurally always False), `post_bets()` was
receiving the FULL scored slate every run, including negative-edge,
zero-stake predictions, and posting all of it looking like real picks.
Every prediction is still logged to the DB regardless (the "log every
scored prediction" contract), only the Discord-bound rows need the gate.
Fixed in both files same day.

`mlb.runners.fast_alert_loop` (the intraday +EV pager, see s14) posts its own
embed directly rather than via `post_bets()` -- its alerts come from
`odds_history` scan rows (market/selection/book codes), not a bet dict. It
reuses `mlb_core.notify.discord`'s shared presentation helpers rather than
formatting its own strings: `book_display()` (canonical book key -> display
name, e.g. `hardrock` -> `Hard Rock Bet`), `market_label()` (odds_history
market code -> human noun, e.g. `k_ou` -> `Strikeouts`), and `ev_alert_emoji()`
(EV-magnitude tiering, same visual ladder as `_edge_emoji` but with no
"no signal" floor since scan rows are pre-filtered to `ev >= min_ev`). Any
new Discord-posting runner should reuse these instead of inventing its own
ad hoc text formatting -- that drift (raw market codes, lowercase book names,
one wall-of-text description instead of embed fields) is what made the +EV
pager's alerts hard to read before the 2026-08-16 restructure.

2026-08-20: the embed's fields are now a double group-by -- one field PER
SPORTSBOOK (`fast_alert_loop._grouped_fields()`), book groups ordered by
that book's own best EV, alerts within a group ordered by EV. Replaces the
old one-field-per-alert layout, which made "what's DraftKings got today"
a matter of reading every field's value text. The separate "Lineup events"
summary field (a semicolon-joined list of raw game_pks) was removed the
same day as unnecessary -- not actionable on its own; `lineup_events()`
still drives the per-alert hot-game badge (🔥) and sort priority.

`mlb.runners.kalshi_alert` (shipped 2026-08-16, same day) is the second
consumer of this pattern: same shared helpers, same per-day dedup-parquet
convention (`Alerts/{day}/kalshi_*.parquet`, kept separate from
fast_alert_loop's own state files), same `DISCORD_WEBHOOK_ALERTS ->
DISCORD_WEBHOOK_URL` fallback -- but its own `notify()`/field-building code,
not a shared one, since its rows carry a genuinely different shape (a Kalshi
mid AND a book-pack consensus to show, not one anchor).

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
| SB | `"SB_{SIDE}_{LINE}"` (+ `"SB_2PLUS_2.0"` threshold) | `"SB_OVER_0.5"`, `"SB_UNDER_0.5"` |
| PITCHER_ER | `"PITCHER_ER_{SIDE}_{LINE}"` | `"PITCHER_ER_OVER_2.5"`, `"PITCHER_ER_UNDER_2.5"` |
| EV | underlying system's own format + `"_{book}"` | `"K_OVER_7.5_draftkings"`, `"HR_hardrock"` |

2+/3+ threshold sub-markets (2026-08-19, K/OUTS/BATTER_TB/BATTER_HITS only --
see s5 "2+/3+ threshold sub-markets" below): `"{SYSTEM}_{N}PLUS_{N}.0"`, e.g.
`"K_2PLUS_2.0"`, `"OUTS_3PLUS_3.0"`, `"BATTER_TB_2PLUS_2.0"`,
`"BATTER_HITS_3PLUS_3.0"`. Deliberately reuses each system's existing
`"{SYSTEM}_{SIDE}_{LINE}"` bet_type CONSTRUCTION line unchanged (`side` is
just `"{N}PLUS"` instead of `"OVER"`/`"UNDER"`, `line` is `float(N)`) --
settlement/Discord/frontend each detect the `PLUS` suffix on `side` and grade
one-sided (HR-style: exactly N is a WIN, no push, since there's no
complementary "under N" side to this bet at all).

### EV bet tracking (posted +EV alerts, both pagers) -- added 2026-08-20

**Is the soft-line +EV strategy actually profitable?** Before this, the
only answer was `odds_alert.py`'s CLV-style `resolved.parquet` scorecard --
does the flagged price hold up vs a LATER quote, not vs a real settled
outcome (its own docstring calls real ROI settlement "a follow-up"). Every
alert `fast_alert_loop.notify()` **and** `kalshi_alert.notify()` actually
post is now ALSO logged into the same `bets` table every model system
uses, under one pooled `system="EV"`, at a flat stake (env
`EV_STAKE_UNIT`, default 100 -- there's no model probability to
Kelly-size by here, so a flat unit makes ROI directly comparable across
alerts). `kelly_triggered` is always `True`: a posted alert already
cleared its pager's own EV/books threshold, so by construction every
logged row IS the signal.

**The two pagers pool into ONE system, not `system="EV"` +
`system="EV_KALSHI"`.** They scan overlapping markets (both cover
hr_yn/k_ou/outs_ou/btb_ou/bhits_ou; kalshi_alert additionally covers the
game-level markets below) with different fair-price anchors (Pinnacle-
consensus vs Kalshi mid) -- when they independently flag the IDENTICAL
real-world quote (same market/game_pk/player/line/book), that's the same
bet you'd only strike once, and the shared `(system, game_date, game_pk,
player, bet_type)` dedup key correctly collapses it to one row instead of
double-counting P&L for it.

`bet_type` reuses the underlying market's OWN convention (see table
above), suffixed with `"_{book}"` (`fast_alert_loop._ev_bet_type()`, the
single shared market -> bet_type mapping both pagers' own
`_log_ev_bets()` call) so two different books flagging the identical prop
don't collide on that dedup key. Settlement dispatches through
`settle_bets._settle_ev()`, which sniffs the `bet_type` prefix and
delegates to the EXACT SAME settler a real bet on that market would use --
an EV alert on `K_OVER_7.5` grades identically to a real K-system bet on
that line, because it's the same market/selection/line, just a different
source (a soft-book-vs-consensus or soft-book-vs-Kalshi scan instead of
the model). Most settlers parse `bet_type` by a fixed-position prefix, so
the trailing book suffix is inert to their parsing (HR/K/OUTS/PITCHER_ER/
BATTER_TB/BATTER_HITS) -- but NRFI's exact-string match (`"NRFI"`/
`"YRFI"`), F5's exact-string match (`"HOME"`/`"AWAY"`, no prefix at all),
and the innings-window settler's `rsplit("_", 1)` (kalshi_alert's
`nrfi_ou`/`game_ml`/`f5_ml`, the game-level markets it trusts most --
`mlb.analysis.kalshi_vs_books.LIQUID`) all need the suffix stripped back
off first (`settle_bets._strip_ev_book_suffix()`) rather than touching
their parsing, since that parsing also grades the real, live NRFI/F5/GAME
systems' own bets. `game_total`/`game_rl` are carried in `odds_history`
(`bettingpros_to_parquet.BP_TO_HISTORY`'s `system=""` entries) but have no
settle_bets settler at all yet -- `_ev_bet_type()` returns `None` for
both (`_EV_UNSETTLEABLE_MARKETS`), so they're logged nowhere rather than
guessed at.

**Deliberately NOT registered in `mlb_core.registry.SYSTEMS` /
`CANONICAL_ORDER`.** `monitor_performance.py`'s `CANONICAL_ORDER` loop
drives the live suppression-gate + Discord performance-alert machinery,
calibrated for model systems (AUC, calibration, `expected_hit_rate`) --
none of which apply to a book-vs-consensus outlier feed. Keeping EV out of
that registry means it can never trip a false suppression-gate alert, and
never needs a feature_csv/model_artifact/build_sentinel it doesn't have.
Consequences of this scope boundary:
- EV settles via the normal nightly `/settle` job (`settle_bets.SYSTEM_MAP`
  + `ALL_SYSTEMS` both include it) and is fully queryable --
  `BetTracker(db, system="EV").summary()` -- and is **not** covered by
  `monitor_performance.py` or `monitor_ops.py`.
- **2026-08-20 follow-up: EV DOES render in the `#daily-recap` embed now**
  (the user asked for it explicitly). `post_all_systems_summary()`
  renders `CANONICAL_ORDER + _EXTRA_RECAP_SYSTEMS` (a small LOCAL list in
  `mlb_core/notify/discord.py`, currently just `["EV"]`) -- a deliberately
  separate list from the registry, so this stays a read-only display
  addition and does NOT put EV back into `monitor_performance.py`'s
  gate-driving loop (that file still only walks `CANONICAL_ORDER` itself,
  untouched). EV's icon (`📡`) is a small local override
  (`_EXTRA_RECAP_ICONS`) in the same spot, since it has no `SYSTEMS` entry
  to read one from. Before this fix, the recap's TOP-LINE "Combined paper
  P&L" total had already been silently including EV's contribution
  (`post_all_systems_summary`'s `total_pnl`/`settled_count` sum over
  every key in the passed-in `systems_stats` dict, not just
  `CANONICAL_ORDER`) with no corresponding field shown -- this closes
  that quiet discrepancy, it doesn't introduce a new one.
- `capture_closing_lines.py` does not capture closing lines/CLV for EV rows
  either (not wired in this pass) -- `closing_odds`/`clv_pct` stay NULL.
- A retrospective real-outcome settlement of ~14 days of already-posted
  alerts (2026-08-06..19, ex the 2026-08-10..17 window where Kalshi was
  still incorrectly a bettable book in this same scan -- see finding C4.1,
  `backtest_market.OFFSHORE`) showed +9.2% ROI / 54.2% hit rate across
  ~1500 decided bets, positive in every market (hr_yn/k_ou/outs_ou/
  btb_ou/bhits_ou) -- promising, but a one-off ad hoc analysis, not yet a
  standing figure this tracking will keep current automatically. Re-check
  via `BetTracker(db, system="EV").summary()` after a few more weeks of
  real settlement before drawing a firm conclusion.

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
| SB | `batters[name].stolen_bases` | vs line O/U; void if not starter |
| PITCHER_ER | `pitchers[name].earned_runs` | vs line O/U; void if not in boxscore |
| EV | (delegates) | `_settle_ev` sniffs the `bet_type` prefix and calls whichever settler above matches -- see "EV bet tracking" |

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
| Stolen bases | `batting_stolenBases-*-ou` | `batters[name].stolen_bases` | Built 2026-08-20 (SB system) -- extractor is `extract_stolen_base_odds()`, NOT a plain `_extract_player_ou_props()` call, see s15 gotcha |
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
| Full game ML | `points-*-game-ml-*` | all innings runs | Live (log-only, GAME Pro v1) |
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

`bets` also has `closing_odds REAL`, `closing_implied_prob REAL`, `clv_pct REAL`
(T08), `morning_odds INTEGER`, `line_move_pct REAL` (E10). All auto-migrated.

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
Use ASCII-only characters in all Python string literals, comments, log messages, and docstrings. No Unicode punctuation: use `->` not Unicode arrow, `--` not em-dash, `>=` not Unicode greater-equal. Em-dashes and Unicode arrows look identical to ASCII in the terminal but have different bytes, causing silent `str.replace()` assert failures. Enforced by convention; will be added to ruff config when linting is set up.

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
1. Add entry to `mlb_core/registry.py` SYSTEMS dict (required -- drives monitor_ops,
   monitor_performance, discord, and future auto-wiring)
2. Add to §1 table and §5 bet type / settlement tables
3. Create `mlb/runners/run_{sys}.py` and `mlb/runners/build_{sys}_features.py`
   - **Both** must have `if __name__ == "__main__":` block (required for Cloud Run Job invocation)
   - Put the per-system config dir under `mlb/systems/{SYS}_System/` (with `__init__.py`);
     import as `from mlb.systems.{SYS}_System.config_{sys} import ...`
4. Add system to `mlb/runners/settle_bets.py` systems loop + statcast/scoring checks
5. Add to `main.py` VALID_SYSTEMS, builders dict, _run_system, build_features_handler
   (main.py is NOT yet driven by the registry -- update both until migration is done)
6. Add Cloud Scheduler jobs for feature build + wire into `/run`
7. Create Cloud Run Jobs (build + retrain + calibrate) with explicit task-timeout flags
8. Update CONTEXT.md §1, §2, §3, §5
9. Add rules to `mlb_core/rationale.py` `_SYSTEM_RULES` dict
10. Add schema entry to `mlb_core/schemas.py` (SCHEMAS dict)
11. No Dockerfile change needed for a new system: the single `COPY mlb/ ./mlb/`
    line already covers new dirs under `mlb/runners/`, `mlb/training/`, and
    `mlb/systems/`. (Cloud Run Job `-m` commands still point at `mlb.runners.*` /
    `mlb.training.*` -- provision them in step 7.)
12. Add an entry to `mlb/runners/monitor_drift.py`'s `SYSTEM_CONFIG` dict
    (gcs_meta + gcs_features) -- **NOT auto-populated by the registry** (see
    the list right below), so skipping this step means the new system gets
    zero PSI/drift monitoring with no error or warning. Added 2026-08-17
    (finding C6.4) after BATTER_HITS/BATTER_TB/GAME sat with this exact gap
    for months because this step didn't exist yet.

Step 1 (registry) also auto-populates:
- `monitor_ops.py` FEATURE_KEYS + MODEL_KEYS
- `monitor_performance.py` EXPECTED_HIT_RATES + system loop
- `discord.py` _SYSTEM_ICONS + post_all_systems_summary loop

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

### Adding a 2+/3+ threshold sub-market (no new model needed either)
Shipped 2026-08-19 for K/OUTS/BATTER_TB/BATTER_HITS -- a different shape from
the section above (that one's for a brand-new SGO market mapping straight to
a `game_result` field; this one's for a NEW ONE-SIDED THRESHOLD reusing an
EXISTING count model's own probability, just evaluated at a different N).
Came from a real user request ("model 2+/3+ HR/TB/hits/Ks -- even at 80-to-1
for a 2% modeled chance, that's still worth betting").

**Why this was mostly plumbing, not modeling**: BATTER_TB/BATTER_HITS/K/OUTS
already fit a full NegBin count distribution (`mu`/`nb_alpha`), not just "beat
this one line" -- `P(X>=N)` for ANY N is a pure CDF re-evaluation
(`mlb.runners.run_batter_hits._negbin_p_over(N-0.5, mu, nb_alpha)`; K's own
Monte Carlo simulator, `_simulate_k`, already computes every `p_Nplus` rung
per pitcher and was just throwing them away). **HR is the exception** -- it's
a binary "at least 1" classifier, not a count model, so HR 2+/3+ needs an
actual new count regression, not just this pattern. Deferred; see
[[project_ops_incident_2026-08-19_hr_odds]] memory for why the existing bakeoff's
`xhr_poisson` candidate isn't a shortcut here (still only outputs P(HR>=1),
needs a `main`-absent feature, never successfully scored in any bakeoff run).

**Two genuinely different market shapes for the odds side** (confirmed via
real captured ParlayAPI data, not assumed) -- `mlb_core/odds/parlay_adapter.py`'s
`_props_odds()` now preserves every quoted point (not just each book's
canonical line) in a new `alt_lines` field on the SGO event
(`{statID: {playerID: {point_str: {side: {book: odds_str}}}}}`, JSON-safe):
- K's strikeout market has a dedicated one-sided ladder prop (`point=N`
  directly IS "N+ Ks", no complementary "under N" quote at all).
- OUTS/BATTER_TB/BATTER_HITS have no such ladder -- an alt line, when quoted
  at all, is an ordinary second two-sided O/U market one tick below the
  threshold (`point=N-0.5`'s "over" side = "N+").

`mlb_core/odds/sgo.py`'s `extract_{k,outs,batter_tb,batter_hits}_alt_line_odds(events, n)`
read `alt_lines` (never `odds` -- the canonical dict has no line dimension for
these at all) and return the same `{player_name: {odds, line, ...}}` shape
every other extractor uses. `mlb_core/risk/threshold_bets.score_threshold_bet()`
holds the shared one-sided scoring math (mirrors `run_hr.py`'s own
`devig_unilateral` + empirical `book_vig.get_vig()` pattern, since there's no
complementary side to pair a two-way devig against -- conservative 10%
default vig market key, e.g. `"k_2plus"`, until book_vig's weekly refit has
real settled history to fit against).

**Exposure cap, a deliberate design call, not an oversight**: a 2+/3+ bet on
the same player/game as the main line shares that SAME per-game system cap
(`mlb_core.risk.exposure`) -- not given a separate allowance. Revisit if that
turns out too conservative once there's real settled data.

Settlement (`settle_bets.py`'s `_settle_k`/`_settle_batter_props`) and both
UI-facing formatters (`mlb_core/notify/discord.py`'s `_format_bet_headline`,
`beezy-vip/lib/tokens.ts`'s `pickLabel`) all detect the bet_type's `PLUS`
suffix and grade/render one-sided (HR-style: exactly N is a WIN, not a push --
there's no "under N" side to tie against). Getting this wrong was the
easiest mistake to make touching this: the pre-existing generic
`actual==line -> push` / `"Over"/"Under"` two-way logic in every one of those
three files would otherwise silently mishandle a `PLUS` bet_type (settlement
would wrongly push an exact hit; Discord's K/OUTS branch would print the raw
"2PLUS" string; BATTER_TB/BATTER_HITS's Discord branch and the frontend's
`pickLabel` would actively mislabel it "Under" since neither had a fallback
branch for anything other than `OVER`/`UNDER`).

### Feature/column naming drift
Several places where the same concept has different names:
- `ump_total_run_impact_L30` (umpire master) vs `ump_k_boost_L30` (K model) -- proxied
- `model_features` vs `game_features` (F5 config had both pre-v5)
- `pitcher_is_home` (NRFI) -- `inning_topbot=="Top"` means home pitcher

When in doubt, find the notebook and match the model-meta features list exactly.

### Paper -> live criteria (200-bet gate)

Current criteria (will be tightened by T17 once CLV stabilizes):
1. >=200 settled bets per system
2. Season ROI > 0% (HR: > -5% allowed)
3. Edge retention: avg model edge vs ROI within 15 pct points
4. Calibration: hit rate within 5 pct points of avg model probability
5. No system down more than 50 units at paper stakes

T17 (planned, see §16) tightens to: mean CLV >= +2% with t-stat > 2 over >=100 bets;
hit rate within 3 pct points; PSI for all top-10 features < 0.25.

---

## 7. Cloud architecture

```
                              ┌──────────────────┐
                              │ Cloud Scheduler  │
                              │  ~17 cron jobs   │
                              └──┬────────────┬──┘
                            OIDC │            │ OAuth (Run API)
                                 ▼            ▼
┌────────────────┐  ┌──────────────────────┐  ┌───────────────────────┐
│  GitHub (main) │→ │   Cloud Run service  │  │   Cloud Run Jobs      │
└────────────────┘  │     mlb-betting      │  │  mlb-build-all-       │
               build│   (Flask + gunicorn  │  │   features (daily)    │
               manual    timeout=3600s)    │  │  mlb-retrain-*        │
                    └──────┬───────────────┘  └──────────┬────────────┘
                           │                             │
                ┌──────────┴─────────────────────────────┘
                │          │                  │             │
                ▼          ▼                  ▼             ▼
          ┌──────────┐   ┌─────────────┐   ┌───────────┐  ┌────────────┐
          │   GCS    │   │ Cloud SQL   │   │   SGO     │  │  Discord   │
          │  bucket  │   │ (Postgres)  │   │   API     │  │  webhook   │
          └──────────┘   └─────────────┘   └───────────┘  └────────────┘
```

**Service:** stateless HTTP, max 1 instance (>=2GB for feature builds).
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
- `site-api-key` -- API key for Cloud Run public API
- `site-origin` -- allowed CORS origin(s), comma-separated when both apex and www are enabled
- `discord-bot-token` -- beezy-bot bot account
- `gemini-api-key` -- tweet drafting (see RUNBOOKS.md social pipeline)
- `typefully-api-key` -- tweet scheduling

**Cloud Run Jobs:**
- `mlb-retrain-f5-meta` (DEPRECATED -- exits non-zero; use mlb-retrain-f5-v5)
- `mlb-retrain-hr-meta` (DEPRECATED -- exits non-zero; use mlb-retrain-hr-v6)
- `mlb-retrain-f5-v5` (full F5 retrain; added 2026-05-19)
- `mlb-retrain-hr-v6` (full HR retrain; added 2026-05-19)
- `mlb-retrain-nrfi-v18`
- `mlb-retrain-k-v1` (includes leakage guard; skip with K_SKIP_LEAKAGE_CHECK=1)
- `mlb-calibrate-nrfi` (fits isotonic calibrator for NRFI v18; run after any NRFI retrain)
- `mlb-calibrate-f5` (fits isotonic calibrator for F5 v5; run after any F5 retrain)
- `mlb-calibrate-k` (fits lambda calibrator for K v1; run after any K retrain)
- `mlb-calibrate-hr` (fits isotonic calibrator for HR v6; run after any HR retrain)
- `mlb-retrain-outs-v1` (full OUTS retrain; E04 2026-05-21)
- `mlb-retrain-batter-hits` (full BATTER_HITS retrain; run after build_batter_hits_features)
- `mlb-calibrate-batter-hits` (fit lambda calibrator for BATTER_HITS v1; run after retrain)
- `mlb-retrain-game-v1` (full GAME Pro v1 retrain; binary:logistic on home_win)
- `mlb-calibrate-game` (fit isotonic calibrator for GAME v1; run after retrain)
- `mlb-build-all-features` (daily feature build; chains all 7 builders in dependency order:
  HR -> NRFI -> K -> F5 -> BATTER_HITS -> BATTER_TB -> GAME via `/bin/sh -c "cmd1 && cmd2 && ..."`
  so any failure aborts subsequent builders; 4Gi/2CPU; 3600s task timeout.
  Triggered by Scheduler via OAuth + Run API scope, NOT OIDC/service -- see §9 and §15.9.
  Scheduler attempt-deadline=1800s (max allowed); job execution timeout=3600s set on the job.
  This is the only build job actually in the daily loop -- see E9/B2.4 note below.)

**Orphaned Cloud Run Jobs:** `mlb-build-batter-hits-features` and `mlb-build-game-features`
(both predated the 2026-06-24 pillarize restructure, superseded by `mlb-build-all-features`,
confirmed 2026-08-17 via E9/B2.4) were deleted 2026-09-01 during a GCP cost/efficiency
review, along with two never-scheduled NBA jobs (`parlay-accum-nba-game-lines`,
`parlay-accum-nba-props` -- zero executions ever, no scheduler pointed at either). If
either MLB job's functionality is ever needed standalone again, re-provision via a real
script rather than hand-creating -- see `deploy/setup_build_all_features.sh` for the
pattern.

**Cloud Run Jobs (one-off, not scheduled -- not part of the count below):**
- `mlb-bakeoff` (the model bake-off tuning exercise: `mlb.analysis.model_bakeoff` +
  `mlb.analysis.hr_model_bakeoff` via `bash -c`, optuna pip-installed at container start
  rather than added to requirements.txt; 8Gi/4CPU/21600s timeout/1 retry -- safe because
  `--resume` makes a retry a no-op for whatever already finished. `--notify` pings
  `#ops-alerts` on completion/failure instead of requiring a check-in. Provisioned by
  `deploy/setup_bakeoff_job.sh`; trigger with
  `gcloud run jobs execute mlb-bakeoff --region=us-central1 --async`. Built specifically
  because a Cloud Shell VM got reclaimed mid-run three times -- `tmux` only survives a
  client disconnect, not the underlying VM disappearing. Analysis-only, same as the rest
  of `mlb/analysis/` -- never wired into any daily loop.)

All 21 Cloud Run Jobs have explicit task timeouts set (added 2026-05-24):
retrain jobs: 7200s, calibrate jobs: 1800s, build jobs: 3600s, tweet jobs: 300s.
Default 600s was silently allowing long retrains to be killed mid-run.

**Cloud Build:** manual only (`gcloud builds submit`). No GitHub trigger yet.

---

## 8. Odds providers (ParlayAPI primary, SGO inning fallback)

**As of the ParlayAPI migration**, the live MLB snapshot (`Odds/sgo/latest.json`,
written by `mlb/runners/snapshot_odds.py`) is a per-game **merge**, controlled by
env `ODDS_PRIMARY` (currently `parlay` as of 2026-08-10; app default if unset is
`sgo` -- always check the live Cloud Run env, don't assume):

**`ODDS_PRIMARY` and the snapshot cadence are coupled, not independent.** The
8x/day cadence below assumes `parlay` primary (only 4 of the 8 windows call
SGO at all). Setting `ODDS_PRIMARY=sgo` makes **every** window call SGO
directly via a path with no graceful degradation -- a single SGO failure
500s the whole request instead of just skipping the inning-market merge.
This combination overloaded SGO's amateur tier and took the snapshot down
for 24+ hours on 2026-08-09/10 before being caught by `monitor_ops`; see
`docs/solutions/integration-issues/odds-primary-cadence-mismatch.md`. Do not
flip back to `sgo` without also cutting the cadence back to ~4x/day.
- **ParlayAPI primary** for the markets it covers -- player props (HR, K, OUTS,
  BATTER_HITS, BATTER_TB, PITCHER_ER, **SB** added 2026-08-20) + game moneyline
  (h2h). Pulled via `nba.odds.parlayapi.ParlayApiClient`, converted to SGO
  shape by `mlb_core.odds.parlay_adapter` (so the runners + `sgo.py`
  extractors are UNCHANGED -- the snapshot keeps the SGO event/oddID/byBookmaker
  shape). **SB's market (`player_stolen_bases`) was confirmed live 2026-08-20
  via a direct probe of ParlayAPI's own `/props/markets` catalog endpoint and
  a real event query** -- 11 catalog books, 5-6 responding on a real checked
  game. Unlike every other ParlayAPI market this repo consumes, it genuinely
  ships in two shapes across books (real two-sided Over/Under on some books,
  a one-sided "Yes"-only price on others) -- `sgo.extract_stolen_base_odds()`
  is a hand-written extractor for this reason, not the shared
  `_extract_player_ou_props()` every other O/U prop uses.
- **SGO fallback** only for inning markets ParlayAPI cannot express:
  NRFI/YRFI (`points-all-1i-ou-*`), 1st-inn 3-way, F5 (`points-all-1ix5-*`),
  F5-ML, F1H. One cheap `fetch_mlb_slate` call supplies these; the adapter
  splices them per game.
- The same ParlayAPI pull also banks `OddsAccum/baseball_mlb/` artifacts
  (credit unification) -- the standalone `parlay-accum-mlb-*` schedules
  (created by `deploy/setup_parlay_schedules.sh`) were SUPPOSED to be
  retired once this landed, but that claim was never actually verified: they
  were found still `ENABLED` and silently burning the shared ParlayAPI
  credit pool outside this ledger on 2026-08-27 (see
  docs/solutions/integration-issues/parlayapi-credit-exhaustion-zombie-jobs-mislabeled-sgo.md).
  Paused that day -- confirm `gcloud scheduler jobs list` shows them `PAUSED`
  (not just this doc) before assuming they're actually inert. Forward `odds_history` feed comes
  from these via `mlb/analysis/parlayapi_to_history.py` (`source="parlayapi"`);
  historical comes from BettingPros. Cutover: shadow-run via `/snapshot-odds`
  with `{"provider":"parlay","out_prefix":"Odds/sgo/_shadow"}`, diff, then flip
  `ODDS_PRIMARY=parlay` (`gcloud run services update --update-env-vars`).
  Rollback: set it back to `sgo` (no redeploy) -- **but see the cadence warning
  above first**. Settlement is provider-independent.

  **Cadence:** 8 snapshots/day, concentrated 18:00-23:00 UTC (lineup->closing,
  the bulk night slate moves most). SGO inning markets fetched on only 4 runs
  (`include_sgo=true`: the two pre-/run times 15:55/21:55, night lineups, close);
  the other 4 carry inning markets forward (`include_sgo=false`) so SGO stays
  ~4x/day under its ~2500-entity free tier. The two late runs use `day_offset=1`
  to bank tomorrow's slate (next-day lines post ~9pm ET); ParlayAPI events are
  dated per-event from commence_time so multi-day slates resolve correctly.
  **Credit budget:** an implicit guard in `snapshot_odds` paces ParlayAPI spend
  EVENLY toward `PARLAY_CREDIT_CEILING` (19,500) -- allowance grows linearly
  (`CEILING * day/days_in_month`), tracked in `OddsAccum/baseball_mlb/_credits/
  {month}.json`; over-pace runs skip per-event props (game lines only). Pushes
  to ~20k without ever exceeding. Schedulers: `deploy/add_snapshot_schedulers.sh`
  (8-job cadence post-cutover; `LEGACY=1` keeps the 2 SGO jobs pre-cutover).

### SGO (legacy / inning fallback)

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

## 9. Scheduler reference

**Location:** `us-central1`
**Service URL:** `https://mlb-betting-628109313129.us-central1.run.app`
**Auth (service jobs):** OIDC via `scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com`
**Auth (Cloud Run Job triggers):** OAuth with `--oauth-service-account-email scheduler-invoker@...`
and `--oauth-token-scope https://www.googleapis.com/auth/cloud-platform`. Required when the
scheduler calls the Run API (`run.googleapis.com/v2/...`) to trigger a Cloud Run Job. Do NOT
use `--oidc-*` flags for these -- OIDC is for Cloud Run service endpoints only.

### Full job inventory

| Job | Schedule (UTC) | Endpoint | Deadline | Body |
|---|---|---|---|---|
| `mlb-settle` | `0 9 * * *` | `/settle` | 600s | `{}` |
| `mlb-monitor` | `30 9 * * *` | `/monitor` | 120s | `{}` |
| `mlb-refresh-data` | `0 14 * * *` | `/refresh-data` | 300s | `{}` |
| `mlb-build-all-features` | `30 14 * * *` | Run API `.../jobs/mlb-build-all-features:run` (OAuth -- see Auth note above) | 1800s | `{}` |
| `mlb-monitor-ops` | `20 15 * * *` | `/monitor-ops` | 120s | `{}` |
| `mlb-retrain-weekly` | `0 6 * * 1` | `/retrain-weekly` | 300s | `{}` |
| `mlb-refresh-statcast` | `0 21 * * *` | `/refresh-data` | 300s | `{"systems":["statcast"]}` |
| `mlb-snapshot-1555` | `55 15 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":true}` |
| `mlb-betting-morning` | `0 16 * * *` | `/run` | 180s | `{"systems":[...full list...],"run_type":"morning"}` |
| `mlb-snapshot-1855` | `55 18 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":false}` |
| `mlb-betting-afternoon` | `5 19 * * *` | `/run` | 180s | `{"systems":[...full list...],"run_type":"afternoon"}` |
| `mlb-snapshot-2025` | `25 20 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":true}` |
| `mlb-snapshot-2125` | `25 21 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":false}` |
| `mlb-snapshot-2155` | `55 21 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":true}` |
| `mlb-betting-evening` | `0 22 * * *` | `/run` | 180s | `{"systems":[...full list...],"run_type":"evening"}` |
| `mlb-snapshot-2305` | `5 23 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":true}` |
| `mlb-snapshot-0125` | `25 1 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":false,"day_offset":1}` |
| `mlb-snapshot-0325` | `25 3 * * *` | `/snapshot-odds` | 180s | `{"include_sgo":false,"day_offset":1}` |
| `mlb-betting-pregame` | `35 23 * * *` | `/run` | 180s | `{"systems":[...full list...],"run_type":"pregame"}` |
| `mlb-capture-closing` | `0 0 * * *` | `/capture-closing` | 300s | `{}` |
| `mlb-monitor-drift` | `0 9 * * 1` | `/monitor-drift` | 300s | `{}` |
| `mlb-tweet-picks-schedule` | `0 17 * * *` | tweet_drafter (Job) | -- | none (Job's own env has TWEET_MODE=picks) |
| `mlb-tweet-recap-schedule` | `0 10 * * *` | tweet_drafter (Job) | -- | none (Job's own env has TWEET_MODE=recap) |

### Scoring paired to snapshots (2026-06-25)

Scoring (`/run`) now fires ~5 min after EVERY odds snapshot, not just morning +
evening: `mlb-betting-afternoon` (after the 19:00 snapshot) and
`mlb-betting-pregame` (after the 23:30 snapshot) were added so late-appearing
markets (evening-game props posted mid-afternoon, lineups confirmed near first
pitch) get scored. This is safe because `BetTracker.log_bet()` is **strict
first-wins** on `(system, game_date, game_pk, bet_type)` -- extra runs only log
brand-new markets, never double-log or re-price an existing bet. No dedup code
change was needed.

Provision via `deploy/setup_betting_schedulers.sh` (idempotent). It sets ALL FOUR
`/run` jobs with the AUTHORITATIVE body `{"systems":[...],"run_type":...}` where
the list mirrors `main.py` `DEFAULT_RUN_SYSTEMS`:
`["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"]`. Use `VALID_SYSTEMS`
names (`1IOU`/`1I`, NOT the legacy `NRFI`). Keep the script's `SYSTEMS_JSON` in
sync with `DEFAULT_RUN_SYSTEMS` when adding a system. (Earlier the live bodies
omitted `BATTER_TB`/`1I` -- this script is the single source of truth so that
can't drift. Supersedes the old clone-based `add_betting_schedulers.sh`.)

The `mlb-build-all-features` Cloud Run Job is provisioned by
`deploy/setup_build_all_features.sh` (idempotent). It was previously created by
hand and got missed in the 2026-06-24 pillarize re-provisioning (its `-m` command
stayed on the old `runners.*` path -> `ModuleNotFoundError` -> stale sentinels ->
runner aborts). Re-run it after any module move.

### status.code values
- `-1` -- never run or ran successfully
- `0` -- success
- `2` -- error (HTTP non-2xx)
- `13` -- deadline exceeded (DEADLINE_EXCEEDED)

### monitor_ops scheduler health check
`monitor_ops._check_schedulers()` enumerates every enabled job directly from
the Cloud Scheduler API (`client.list_jobs(...)`) -- no allowlist, nothing
to update when adding or removing a job. A hardcoded `SCHEDULER_JOBS` list
used to live in `monitor_ops.py` and this section used to say it "drives
the scheduler health check," but that filter was the root cause of a real
incident (PERMISSION_DENIED/OOM failures on newer jobs went unnoticed for
days because they weren't in the list) and was deleted 2026-08-17 (finding
C6.8) once the health check itself was rewritten to check everything.

For manual trigger commands, deploy fragments, and Cloud Run Job creation see
**RUNBOOKS.md**.

---

## 10. DK grading rules (MLB props)

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

## 11. Performance monitor

`mlb/runners/monitor_performance.py` -- fires at 09:30 UTC daily via `mlb-monitor`.

Alert thresholds (overrideable via env vars):
- `MONITOR_ROI_WARN=-15` -- ROI over last 30 bets below -15% triggers alert
- `MONITOR_HIT_RATE_DROP=10` -- hit rate more than 10pct below expected
- `MONITOR_MIN_BETS=20` -- minimum settled bets before alerting
- `MONITOR_ROLLING_WINDOW=30` -- window size

Posts degradation alerts to #ops-alerts via DISCORD_WEBHOOK_OPS.
Posts weekly digest every Monday to #performance via DISCORD_WEBHOOK_PERFORMANCE.

AUC added to rolling/season stats 2026-05-21. Uses Mann-Whitney method (no sklearn).
Alert thresholds: AUC < 0.50 = "rank-ordering backwards, retrain required"; AUC
0.50-0.52 = "near coin-flip" warning. AUC is the primary leading indicator --
it detects model failure earlier than ROI or hit rate.

Per-book stats (T15): groups settled bets by `book` column, reports n, hit_rate,
ROI, mean_clv per book. Alert if any book reaches n >= 20 and ROI < -20%
(potential profiling signal).

### Model-health gate (Task B -- 2026-06-10)

`monitor_performance.run()` also writes `Gates/model_gates.json` after every
monitor run. Gate logic (thresholds overrideable via env vars):
- **Suppression is ROI-ONLY**: suppress if rolling ROI < `GATE_ROI_MIN=-20%`.
  ROI is the only un-biased "is this working" signal. The first-inning run-sim
  spike (2026-06-11, `handoff_runsim_decision`) proved bet-sample `auc_model`
  and `cal_err` are SELECTION-BIASED -- a system can show bet-sample AUC < 0.50
  while earning +11% ROI (OUTS), because the bets are a censored anti-market
  subsample. So AUC/cal are recorded for observability and still drive a
  NON-suppressing Discord alert (`_check_alerts`), but they do NOT auto-suppress.
- `_rolling_stats` carries both `auc` (market_prob, legacy alert) and `auc_model`
  (model_prob) + `avg_model_prob`; the gate file records auc_model/auc_market/
  avg_model_prob/cal_err for diagnosis even though only ROI gates.
- `MIN_GATE_N=30` settled bets minimum before gate may activate (prevents cold-start shutdown)
- Hysteresis: 2 consecutive runs meeting condition before flip; 2 consecutive clean runs to recover
- Manual override: `SystemConfig.force_gate = "on"/"off"` in registry wins over all metrics

`mlb_core/risk/gates.py::is_suppressed(system)` is called once per runner before the scoring loop.
Returns False on any I/O error (fail-open). When suppressed, runner logs every prediction but
sets `kelly_triggered=False, stake=0` -- data continues to accumulate for recovery.
State flips post to #ops-alerts with AUC/ROI/n context.

Runners with gate wiring: run_nrfi ("1IOU"), run_hr ("HR"), run_k ("K" + "OUTS"),
run_batter_hits ("BATTER_HITS"), run_game ("GAME"). F5 and sub-runners (F1H) inherit via the
`LOG_ONLY_SYSTEMS` set in run_f5.py (OR semantics; both static and dynamic suppression apply).

### Model-health endpoint (Task A -- 2026-06-10)

`GET /model-health` now returns three additional fields per system:
- `flags`: list of active flags (`inverted`, `miscalibrated`, `negative_roi`, `underpowered`, `no_edge`)
- `health`: composite verdict with precedence: underpowered -> inverted -> miscalibrated -> no_edge -> degraded -> moderate -> healthy
- `recommended_action`: short human string keyed to `health`

Thresholds: `MIN_HEALTH_N=20` (env: `HEALTH_MIN_N`), `CAL_ERR_TOL=0.10` (`HEALTH_CAL_TOL`),
`ROI_FLOOR=-10` (`HEALTH_ROI_FLOOR`). Constants in main.py at module level.

### Edge-analysis endpoint (Task #2 -- adverse-selection diagnostic, 2026-06-11)

`GET /edge-analysis` stratifies settled, kelly-triggered bets by EDGE bucket
(the gap between model_prob and the de-vigged line: <5%, 5-10%, 10-15%, 15-20%,
>=20%) and reports per bucket per system: n, mean_edge, hit_rate, avg_model_prob,
realized_cal_err, roi, mean_clv, clv_tstat. Read-only.

Purpose: detect adverse selection. If ROI/mean_clv DECLINE as the edge bucket
grows, the biggest apparent edges are mostly model error (winner's curse on
max-disagreement bets) -> cap/down-weight extreme edges + calibrate before Kelly.
If they hold/rise, the gaps are real EV. CLV is a strong test in liquid markets
(GAME, F5), weak in thin prop/inning markets (use ROI there). This is the keystone
that should parameterize the calibration-before-sizing change (Task #3); the
de-vig audit (Task #4) corrects the market side of the gap (`fair_prob`).

The first /edge-analysis run (2026-06-11) confirmed adverse selection: realized_cal_err
goes from ~+0.03 (<5% gap) to ~-0.31 (>=20% gap) across EVERY system -- the biggest
gaps are overwhelmingly model overconfidence (model says ~0.77, wins ~0.46), not edge.
Small/moderate gaps are well-calibrated and profitable. Also surfaced: `mean_clv`
values were implausible (+35% to +68%) -- a CLV-computation bug, now FIXED (below).

### CLV recomputed price-based (2026-06-11)

`BetTracker.write_closing_line` previously computed CLV as a probability-RELATIVE
quantity `(entry_fair - closing_fair)/closing_fair*100`. Two defects: (1) the sign
was inverted (positive should mean beating the close), and (2) dividing by the
closing fair prob blew the value up to +-35-68% whenever closing_fair was small or
came from a cross-line/mismatched complement (s15.3). CLV is now the industry-standard
PRICE-based ratio: `mlb_core.odds.utils.clv_pct_from_prices(entry_odds, closing_odds)
= (decimal_entry/decimal_close - 1)*100`. Bounded, correctly signed (positive = we got
a better price than the close), computed on the same side's raw prices so vig cancels,
and independent of the devig/complement path (so the cross-line risk no longer touches
CLV). `closing_prob` is still stored (devigged, reference only) but no longer feeds clv_pct.

Repair history: `POST /capture-closing {"backfill_clv": true}` (or
`capture_closing_lines.backfill_clv()`) recomputes clv_pct for all rows with a closing
line using the price formula. Idempotent. Run once after deploy.

### De-vig methods (Task #4 building block -- 2026-06-11)

`mlb_core/odds/utils.py` now offers three two-way de-vig methods:
`remove_vig` (proportional, current default used by all runners), `shin_two_way`
(Shin 1992, models vig as insider protection), `log_two_way` (power/odds-ratio).
`devig_two_way(a, b, method=...)` selects. Shin/log apply a favorite-longshot
correction (shade favorites more than proportional). These are READY but NOT yet
wired into runners -- swapping the de-vig method is a deliberate change gated on an
audit (which method best calibrates to realized outcomes). The audit needs a
snapshot-archive -> game_pk -> outcome join (both-side closing odds vs results) that
is not available from the bets table (runners log only the chosen side's odds), so it
is a deliberate next build. Priority is BELOW calibration: /edge-analysis showed the
dominant error is model-side overconfidence (now addressed by Task #3), not the
market/de-vig side. Cheapest decision gate: re-run /edge-analysis after calibration +
CLV land; if market-side miscalibration is negligible, the de-vig swap is unnecessary.

### Prediction calibration + edge cap (Task #3 -- 2026-06-11)

`mlb_core/risk/calibration.py::apply(system, prob) -> (calibrated_prob, was_calibrated)`
calibrates model_prob against REALIZED OUTCOMES (not the market) BEFORE edge is
computed, correcting the overconfidence above. Per-system isotonic calibrators are
fit by `mlb/training/fit_prediction_calibrators.py` (Cloud Run Job `mlb-fit-calibrators`,
reads ALL settled predictions in the bets table -- placed and unplaced, since settle
ignores kelly_triggered) and stored at `Calibration/{system}_prediction_calibrator.pkl`.

Wired into every scoring runner (run_nrfi/hr/k[K+OUTS]/batter_hits/game/f5): after
side selection, `model_prob, _cal = apply(SYSTEM, model_prob); edge = model_prob - fair`.
- SAFE ROLLOUT / fail-open: no calibrator (or any error) -> prob unchanged,
  was_calibrated=False -> system behaves exactly as before. Calibrators take effect
  only after `mlb-fit-calibrators` runs.
- Interim edge cap: `EDGE_CAP=0.20` (env `EDGE_CAP`). A bet whose POST-calibration
  edge still exceeds the cap is skipped (kelly_triggered=False). The cap fires ONLY
  when was_calibrated=True, so it never acts on a raw uncalibrated edge.
- Calibration against outcomes layers ON TOP of any training-time calibrator (NRFI
  v18 isotonic, etc.) -- it corrects the residual live overconfidence those miss.

Rollout: deploy -> run `mlb-fit-calibrators` -> calibrators exist -> next betting run
calibrates + caps. Re-run the fit job weekly (or after notable bet volume).

NOTE (gate gap fixed in the same change): run_f5 previously did NOT read the
suppression gate, so F5 being suppressed in `Gates/model_gates.json` had no effect.
run_f5 now calls `is_suppressed("F5")` (main ML) and `is_suppressed("F1H")` (innings
submarket), generalizing the empty `LOG_ONLY_SYSTEMS` set.

Expected hit rates (baselines -- update after 200 bets per system):
- HR: 7%, NRFI: 55%, F5: 52%, K: 52%, OUTS: 52%
- BATTER_HITS: 52%, BATTER_TB: 52%, GAME: 52% (update after 200 settled bets; dedicated trained models)
- F1H: 52%, PITCHER_ER: 52% (update after 100 settled bets each; proxy models, treat baselines as placeholders)

---

## 12. Ops monitor

`mlb/runners/monitor_ops.py` -- fires at 15:20 UTC daily via `mlb-monitor-ops`.

Checks (all post-feature-build):
- All Cloud Scheduler jobs: last run status code
- SGO snapshot age < 26hrs
- All system `model_features.csv` age < 26hrs
- All data masters (scoring, statcast, weather, umpires) age < 26hrs
- All model artifacts exist in GCS
- Any bets pending > 3 days

Silent on clean run. Posts to #ops-alerts via DISCORD_WEBHOOK_OPS on failure.

`Gates/model_gates.json` -- GCS key for the dynamic suppression gate file. Written by
`monitor_performance.run()`. Read by `mlb_core/risk/gates.py::is_suppressed()` in each
runner. Absent before first post-deploy monitor run; all runners fail open on missing file.

`monitor_drift.py` (T14) runs Monday at 09:00 UTC via `mlb-monitor-drift`.
Computes PSI between last 7 days of live prediction feature distributions vs
training-set distribution (via `feature_dists` empirical percentiles in model meta).
Alerts if PSI > 0.25 for any top-10 feature by importance.

---

## 13. Beezy.FYI -- the frontend

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

Production URL: `https://beezy.fyi`. `https://www.beezy.fyi` is attached in
Vercel and redirects to the apex. The old Vercel preview domain
`https://mlb-betting-rose.vercel.app` may still exist, but do not use it in
public copy, OG cards, Discord copy, or tweet drafts. Backend public API
canonical domain: `https://api.beezy.fyi`.

Public social handle: X/Twitter `@beezy_fyi`
(`https://x.com/beezy_fyi`). Keep site links, OG cards, Discord copy, and
tweet drafts aligned to `beezy.fyi` / `@beezy_fyi`.

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
│   ├── cheat-sheet/              Mobile-optimized picks card page
│   └── api/                      Route handlers (picks, stats, webhooks, cron, og)
├── components/
│   ├── landing/                  Hero, blotter, systems grid, how-it-works
│   ├── layout/                   Nav, bottom-nav, footer
│   ├── picks/                    Picks table, filter bar, date bar
│   ├── today/                    Slate strip
│   └── ui/                       Primitives: SystemBadge, ResultPill, PnL, StatCard
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
Server components:  Vercel SSR -> GET /api/public/* -> Cloud Run -> Cloud SQL
Client components:  Browser -> GET /api/* (Next.js proxy) -> Cloud Run -> Cloud SQL
```

**Critical:** `BETTING_API_URL` and `BETTING_API_KEY` are server-only env vars.
Client components (`'use client'`) cannot access `process.env` at runtime.
All client-side fetching must go through `app/api/picks/` and `app/api/stats/`.
`betting-api.ts` auto-detects `typeof window !== 'undefined'` and routes accordingly.

The Cloud Run service exposes read-only endpoints authenticated with
`X-API-Key` header (secret: `site-api-key` in Secret Manager):

| Endpoint | Cache | Description |
|---|---|---|
| `GET /api/public/picks/today` | 60s | Today's kelly-triggered picks |
| `GET /api/public/picks` | 60s | Filtered picks (system, date, status, book, limit, offset). Always filters to `kelly_triggered=true`. |
| `GET /api/public/picks/recent` | 120s | Last N settled picks |
| `GET /api/public/stats/summary` | 300s | Overall + per-system stats |
| `GET /api/public/stats/sparkline` | 300s | Daily cumulative P&L last 30 days |
| `GET /api/public/stats/sparkline?system=X` | 300s | Per-system sparkline |

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

**Dell 1996 catalog aesthetic** (fully implemented 2026-06-10). Every terminal-palette
color (`#10b981`, `#ef4444`, `#3b82f6`, `#f59e0b`, `#71717a`) has been replaced
site-wide. OG image routes (`/api/og/`) and `legal/` pages intentionally excluded.

#### Colors

| Role | Value | Notes |
|---|---|---|
| Page background | `#0a0a0c` | CSS var `--bg` |
| Surface | `#111114` | CSS var `--surface` |
| Hard border | `#000` | Page frame, card edges |
| Inner border | `#1f1f24` | CSS var `--border`; between surface elements |
| Win / positive | `#b3bd95` | Sage -- Dell Latitude Notebooks tint |
| Loss / negative | `#d77a7a` | Salmon -- Dell OptiPlex GX tint |
| Info / sky | `#9ab6c8` | Sky -- Dellware catalog tint |
| Periwinkle | `#8c9ae0` | PowerEdge Servers |
| Peach | `#e6915d` | Dimension / OptiPlex Gs |
| Lime | `#c0d4a7` | Edge/model signal (OptiPlex G Series) |
| Steel | `#a5b8c0` | Neutral active state (Dimension XPS Pro) |
| Muted text | `#888890` | WCAG AA 5.0:1 on dark bg |
| Links | `#9999ff` | Classic hyperlink blue (7.25:1 on dark bg) |
| CTA yellow | `#fcc20f` | Dell "BUY a DELL" sticker yellow |
| Hero panel | `#0e1f0d` | Dark green; low-key, not alarming |

System pill assignment: NRFI = sage, HR = salmon, F5 = sky, K = periwinkle,
OUTS = peach, BATTER_TB = sky, BATTER_HITS = sky, GAME = steel.

#### Typography

CSS classes defined in `app/globals.css`:
- `.dell-display` -- Arial Black, 900 weight, uppercase. H1 headlines, logo.
- `.dell-heading` -- Arial, 700 weight, uppercase, tracked. Section labels, eyebrows, badges.
- `.times` -- Georgia (primary), Times New Roman (fallback), serif. Body copy, descriptions.
  Georgia is a universally available system font -- no web font load needed. Reliable on Vercel edge.
- `.mono` -- JetBrains Mono, loaded via `next/font/google`. Data, numbers, odds, tickers.

Google Fonts loaded in `app/layout.tsx`: Inter (CSS var `--font-inter`) and
JetBrains Mono (CSS var `--font-mono`). No serif loaded via Google Fonts --
Georgia is the system fallback for `.times` everywhere including OG image routes.

#### Layout rules

- `borderRadius: 0` everywhere. No rounded corners.
- All borders `1px solid` (never `0.5px solid`).
- Page frame: `8px solid #000` on `.page-frame` class.
- No drop shadows. No gradients. No emissive glows.
- Hard borders only for depth.

#### Nav

Sticky top nav (`components/layout/nav.tsx`):
- Logo area: `flex: 1`. Contains 28x28px dashed placeholder box + "BEEZY.FYI" text.
  Replace the `<div>` placeholder with `<img>` when the final mark is ready.
- Nav links: natural width, visually centered by the two `flex: 1` flanking sections.
- Right section: `flex: 1, justifyContent: 'flex-end'`. X icon + auth + Discord sticker.
- Mobile: `.nav-desktop` hidden; `.mobile-only` shows auth + X + Discord compact.

#### Tokens file

Do not redefine `B`, `TEAM_ABBREV`, `SYSTEM_COLOR`, `SYSTEM_PILL`, or `pickLabel`
locally in components. Import from `@/lib/tokens` instead. `B` is `'1px solid #000'`
(hard frame border). `B_INNER` is the inner surface border. When adding a new system,
update `lib/tokens.ts` for Dell tint colors/labels and `lib/pick-systems.ts` for routing.

### Vercel deployment

- Project: `mlb-betting` on Vercel (root directory set to `beezy-vip`)
- Auto-deploys on push to `main` branch
- Canonical domain: `beezy.fyi` (apex), with `www.beezy.fyi` attached as the
  secondary domain and redirecting to the apex.
- Required env vars: `BETTING_API_URL`, `BETTING_API_KEY`, `LEARN_DATABASE_URL`,
  `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `ADMIN_SECRET_KEY`,
  `CRON_SECRET`, `STRIPE_*` keys, `NEXT_PUBLIC_BASE_URL`, `BLOCKED_STATES`
- Set `NEXT_PUBLIC_BASE_URL=https://beezy.fyi`.
- Set `BETTING_API_URL=https://api.beezy.fyi`.

### GCP resources for beezy-vip

Two new secrets in Secret Manager:
- `site-api-key` -- API key for Cloud Run public API (version 1 is current)
- `site-origin` -- allowed CORS origins (comma-separated; value: `https://beezy.fyi,https://www.beezy.fyi`)

Both mounted on the Cloud Run service via `--set-secrets` in
`deploy/deploy_service.sh`. Service account `mlb-betting-sa` has
`secretmanager.secretAccessor` on both.

Cloud Run service is now open to unauthenticated invocations (`allUsers`
has `roles/run.invoker`) so the public API endpoints are reachable from
Vercel. The existing scheduler routes (`/run`, `/settle`, etc.) are still
protected by OIDC tokens from the schedulers -- they just aren't blocked at
the IAM level anymore.

### Dynamic system route
Per-system pick pages (`/picks/mlb/nrfi`, `/picks/mlb/hr`,
`/picks/mlb/batter-tb`, etc.) are served by a single dynamic route at
`app/picks/mlb/[system]/page.tsx`. `lib/pick-systems.ts` is the source of
truth for valid slug -> system mappings and powers the all-model grid at
`/picks/mlb`, the filter bar, and per-system metadata. Invalid slugs return
404 via `notFound()`. Do not create individual static page files per system.

As of 2026-08-27, the public all-model route covers:
`NRFI`, `1I`, `F3`, `F5`, `F1H`, `F7`, `GAME`, `HR`, `BATTER_TB`,
`BATTER_HITS`, `BATTER_K`, `K`, `OUTS`, `PITCHER_ER`, and `SB` (added
2026-08-20 alongside the SB model itself). **`EV`** (pooled +EV alert
tracking, s5 "EV bet tracking") is deliberately NOT on this route -- same
reasoning as it being excluded from `registry.CANONICAL_ORDER`: it isn't a
model with its own training methodology, it's a cross-market tracking
pool over alerts already priced by the systems above. It's still wired
into the generic system taxonomy (`lib/tokens.ts` color/label/`pickLabel`,
which resolves an EV row's `bet_type` back to its underlying market so it
formats identically to a native pick) and shows up wherever real `bets`
rows render regardless of system (Results page, main picks table, CLV
tracker) -- just not as a browsable `/picks/mlb/ev` page. It's also
excluded from the two curated public share surfaces (Daily Card / OG
picks-card image) since those showcase model picks specifically.

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

Sort chips: Date, Edge, Odds, P&L. Click active sort to toggle direction.
Default: Date desc. Implemented as client-side sort on fetched picks array.

### Table columns (picks + results)

Both tables share the same column layout (2026-05-20):
`Date | System | Game | Pick | Odds | Edge | Book | Result | P&L`
`80px  65px    160px  1fr   90px  60px  80px  70px   70px`
minWidth: 860px. Book column shows canonical book name or em-dash if null.

### Bet type display names

`picks-table.tsx` maps raw `bet_type` to readable labels. When adding a new
bet_type, update the `pickLabel` formatter in `lib/tokens.ts`:
- F5: `HOME`/`AWAY` -> "F5 Home ML" / "F5 Away ML"
- NRFI: `1I_HOME`/`1I_AWAY`/`1I_DRAW` -> "1st Inn Home/Away/Draw"
- K: `K_OVER_4.5` -> "Over 4.5 Ks"
- OUTS: `OUTS_UNDER_14.5` -> "Under 14.5 Outs"
- HR: `HR` -> "HR Yes"

### Pick rationale (notes column)

`mlb_core/rationale.py` maps feature values to canned phrases at bet-log time.
Stored in `notes TEXT` column (already in schema, public API SELECT, and `Bet` type).
Wiring status as of 2026-05-23: HR, NRFI, F5, K, OUTS all wired.
Frontend renders `bet.notes` as muted mono subtext under the pick label in
`picks-table.tsx` (desktop + mobile), split on `' . '` into bullet points.
Null-guarded.

### Model IP protection

Current (paper mode): exact edge shown as percentage (e.g. "12.4%"). Model prob
not exposed anywhere on the public site. At launch: gate model prob behind Pro
subscription via Clerk auth. CSV export uses exact edge values.

### Pre-launch checklist

- [ ] beezy.fyi DNS -> Vercel nameservers fully propagated
- [ ] api.beezy.fyi mapped to Cloud Run public API
- [ ] Clerk production keys in Vercel env vars
- [ ] beezy.fyi added to Clerk allowed origins
- [ ] Stripe production price IDs set
- [x] Legal pages drafted 2026-05-21 (terms, privacy, responsible-gambling, refunds). `{LAWYER_REVIEW}` markers in each file flag sections needing attorney sign-off before flipping PRE_LAUNCH.
- [ ] `BLOCKED_STATES` configured
- [ ] Stripe reconciliation cron clean 7 days
- [ ] >= 200 settled bets per system at gate criteria
- [ ] Flip `PRE_LAUNCH = false`

### Cheat sheet page (`/cheat-sheet`)

Mobile-optimized, screenshot-ready pick card page. Added 2026-05-25.

**Files:**
- `beezy-vip/app/cheat-sheet/page.tsx` -- server component; resolves headshot and
  logo URLs server-side, sorts by edge, passes enriched bets to client
- `beezy-vip/app/cheat-sheet/cheat-sheet-client.tsx` -- interactive client component
- `beezy-vip/app/cheat-sheet/layout.tsx` -- strips global nav/footer for clean screenshot

**Features:**
- Default limit: 5 picks, expandable
- Filter chip tabs: All / Game Picks (NRFI, F5) / Pitcher Props (K, OUTS) / Player Props (HR, HITS)
- Per-card rationale expand toggle
- 390px fixed-width card design optimized for iOS Twitter screenshots
- Left panel (86px): headshot (`objectFit: contain`, bottom-anchored) or team logos for game picks
- Rank badge overlaid top-left, system badge above player name, edge panel right
- Share button (Web Share API with download fallback)

**Correct slug logic (TypeScript and Python must match):**
```
lower -> space->_ -> hyphen->_ -> strip [^\wÀ-ɏ] (dots, apostrophes, etc.)
```

Nav: "Cheat Sheet" link added to `beezy-vip/components/layout/nav.tsx`.

### Headshot system

Player headshots live in `beezy-vip/public/headshots/`.

**player_map.json** (`beezy-vip/public/headshots/player_map.json`):
- Maps `slug` -> MLB MLBAM integer ID
- 1272 entries as of 2026-05-25 (full 2026 active roster + historical players)
- Keys are normalized slugs (same algorithm as `playerSlug()` above) -- no dots,
  apostrophes, or raw hyphens in any key
- Updated from MLB Stats API via `python scripts/process_headshots.py --refresh-map`
- `--refresh-map` merges active season roster without dropping existing entries
  (retains IL/minors/retired players that may still appear in picks)

**Headshot files:** `{slug}.png`, transparent background (rembg-processed).

**Background removal tool:** `rembg[cpu]` (ONNX Runtime, ~50MB).
Do NOT use `backgroundremover` -- it pulls PyTorch + CUDA + moviepy (~5GB).
Install: `pip install "rembg[cpu]"`.

**Scripts:**
- `scripts/process_headshots.py` -- download + rembg-process headshots
  - No args: today's picks only
  - `player_name ...`: specific players
  - `--all`: all missing (no existing PNG)
  - `--refresh-map` / `--sync`: re-sync player_map from MLB API first, then process missing
- `scripts/fetch_missing_headshots.sh` -- Cloud Shell bulk download, hardcoded list
  of known missing players with their MLBAM IDs

See RUNBOOKS.md for the full reconciliation flow.

**Lookup in page.tsx:**
`headshotUrl()` tries accented slug first (`randy_vásquez`), then NFD-normalized
ASCII fallback (`randy_vasquez`), then MLB CDN via MLBAM ID. Local bg-removed PNG
always wins over CDN.

**Max Muncy duplicate:** Two players named Max Muncy (IDs 571970 and 691777).
player_map keeps the first one encountered. Both will appear in player picks
under the same slug `max_muncy` -- last write wins in the map.

### Mobile shell

`components/layout/bottom-nav.tsx` -- fixed-bottom tab bar visible only on mobile
(controlled by `.mobile-only` class in `globals.css`). 5 tabs: Today / Picks /
Results / Tools / More. Body has `padding-bottom: calc(56px + env(safe-area-inset-bottom))`
on mobile so content doesn't hide behind the bar.

On mobile, the top nav collapses to logo + auth only -- bottom tabs handle navigation.

### Tailwind v4 gotchas

See §15.10 Frontend gotchas for the full list -- the core rule is **use pure
inline styles everywhere**.

---

## 14. Discord server (beezy.fyi)

*Last configured: 2026-05-20*

### What it is

Community layer for beezy.fyi. Receives pick signals and daily recaps via
webhooks. Members opt into book-specific pings. Ops alerts are routed to an
admin-only channel, never surfaced to members.

Server ID: `1476027259956494533`
Server name: `beezy.fyi`

### Server structure

```
ONBOARDING
  #verify        -- Entry gate. Unverified members see only this channel.
  #preferences   -- Self-assign book roles for tailored pick pings.

INFO
  #welcome       -- What beezy.fyi is, how to read picks. Carl-bot welcome
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

### Webhook routing

Each webhook is stored in Secret Manager and mounted on the Cloud Run
service as an environment variable:

| Secret | Env var | Channel | Used by |
| --- | --- | --- | --- |
| `discord-webhook-url` | `DISCORD_WEBHOOK_URL` | `#daily-picks` | `post_bets()` |
| `discord-webhook-summary` | `DISCORD_WEBHOOK_SUMMARY` | `#daily-recap` | `post_all_systems_summary()` |
| `discord-ops-webhook-url` | `DISCORD_WEBHOOK_OPS` | `#ops-alerts` | `post_error()`, `post_ops_alert()` |
| `discord-webhook-performance` | `DISCORD_WEBHOOK_PERFORMANCE` | `#performance` | `monitor_performance.py` (rolling perf alerts + Monday digest), `weekly_survival_report.py` |
| `discord-webhook-alerts` (secret not created yet; both jobs' `--set-secrets` already reference it) | `DISCORD_WEBHOOK_ALERTS` | recommended: new `#soft-line-alerts` channel (2026-08-16 recommendation, not yet built) -- falls back to `#daily-picks` via `DISCORD_WEBHOOK_URL` until then | `mlb.runners.fast_alert_loop`, `mlb.runners.kalshi_alert` (both intraday +EV pagers) |

`post_error()` previously routed to the main picks webhook. It now uses
`DISCORD_WEBHOOK_OPS`, keeping errors out of member-facing channels.

`post_ops_alert(message, run_date)` added for `monitor_ops.py` to call directly
for infra health failures.

Per-system webhook override still supported: `DISCORD_WEBHOOK_{SYSTEM}`
(e.g. `DISCORD_WEBHOOK_NRFI`) takes priority over `DISCORD_WEBHOOK_URL`.

### Bot infrastructure

**beezy-bot** -- Discord application bot account.
- Token stored in Secret Manager as `discord-bot-token`.
- Used by `setup_discord.py` (one-time server setup) and
  `cleanup_discord.py` (role cleanup).
- Not a persistent bot -- scripts run on-demand from Cloud Shell.
- Does NOT handle real-time events. Webhook-only for all pick posting.
- See §15.7 gotcha: webhook messages do not trigger Discord bots.
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

### Stripe -> Discord role sync (backlog)

When a user pays on beezy.fyi via Stripe/Clerk, they should automatically
receive the `Member` or `Member Pro` Discord role. This requires:

1. A Clerk/Stripe webhook hitting a Cloud Run endpoint
2. A persistent bot (real token, not webhook) to assign roles via Discord API
3. Mapping Clerk user ID -> Discord user ID (requires OAuth link at signup)

Not blocking launch. Manual role assignment during early paid access is fine.
Design the Clerk signup flow to prompt Discord OAuth link from day one.

### Discord gotchas

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

## 15. Gotchas

Grouped by domain. Within each group, ordered roughly by frequency you'll hit them.

### 15.1 Deploy and infra (Cloud Run, Cloud SQL, gcloud)

**Build vs. deploy.** `gcloud builds submit` updates `:latest`. Cloud Run
pins to a digest at deploy time. Always run `gcloud run services update`
after a build, with `--add-cloudsql-instances` every time.

**Cloud SQL binding dropped on redeploy.** If you omit `--add-cloudsql-instances`,
the new revision loses the SQL binding and DB writes fail with `[Errno 5]`.

**Personal Google accounts can't mint Cloud Run audience tokens.** Use
`gcloud run services proxy <service> --port=8080` for manual curl tests.
Restart the proxy after a redeploy -- stale proxy returns Google's 404.

**Cannot connect to Cloud SQL from Cloud Shell.** The DSN uses a Unix
socket (`/cloudsql/...`) that only exists inside Cloud Run. Use the proxy
endpoint for ad-hoc DB inspection instead of direct connections.

**Cloud Shell uploads strip leading dots and auto-rename conflicts** to
`filename_(1).py`. Use `git pull` instead of uploading files when possible.

**SGO API key is in Secret Manager version 3.** Version 1 was the old
exposed key. Version 2 had invisible newlines causing `Invalid leading
whitespace` errors in HTTP headers, silently returning 0 events from every
SGO snapshot. Always create secrets with
`echo -n "value" | gcloud secrets versions add --data-file=-` and verify
with `gcloud secrets versions access latest --secret=NAME | cat -A`.

**Image registry is `gcr.io`, NOT Artifact Registry.** Always use
`gcr.io/concrete-crow-445205-m4/mlb-betting:latest` for job `--image`. The
Artifact Registry path returns NOT_FOUND. The deploy script pushes to `gcr.io` --
confirm with `gcloud container images list --repository=gcr.io/concrete-crow-445205-m4`.

**GitHub fetch can return stale cached content.** `web_fetch` on raw GitHub
URLs can return an older version. Always read the local Cloud Shell file
with `cat` or `sed` -- that is the source of truth when `git status` is clean.

**`mlb-refresh-data` scheduler job had never run (2026-05-21).**
`Last attempt: None` despite `State: ENABLED`. Always verify scheduler job URI
after any deploy. Check `/model-health` freshness block after a data gap.

### 15.2 Database and migrations (pg8000, ALTER TABLE, text())

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

**`CURRENT_DATE` vs text column type mismatch.** The `game_date` column is stored
as `TEXT` (isoformat string). Comparing it to `CURRENT_DATE` (a Postgres `date`
type) without a cast raises `operator does not exist: text = date`. Always cast:
`game_date = CURRENT_DATE::text`. For parameterized queries, pass a Python
`date.today().isoformat()` string as a named param -- never use `CURRENT_DATE`
directly in parameterized queries.

**Always use `from mlb_core.config import DB_URL` in new routes, never `os.environ["DB_URL"]`.**
The env var is `MLB_DB_URL` -- reading `DB_URL` directly causes `KeyError` at runtime.
`mlb_core.config` handles the lookup correctly.

**`BetTracker.summary()` was not filtering by system until 2026-05-14.**
All per-system Discord summaries showed K stats. Fixed with `text()` wrapper.

### 15.3 SGO odds and extractors

**`oddsAvailable=true` is not "today only" in SGO.** Returns 5-day window.
Always pass `startsAfter`/`startsBefore`. `et_day_window()` handles this.

**Bookmaker must be explicitly propagated to the results dict in each runner.**
`odds_info.get('bookmaker')` is available in the odds lookup dict but must be
copied into the row/results dict explicitly. It is NOT automatically included
in pivot tables (NRFI) or feature row merges (F5). Check each runner's
`results.append()` or `log_bet()` call includes `bookmaker` when adding new markets.

**F5 ML extractor bookmaker used `_home_book` only.** Fixed 2026-05-20 to use
`_home_book or _away_book`. 75 historic F5 bets backfilled via one-off
`/backfill-f5-book` route (now removed). New F5 bets populate book correctly.

**SGO snapshot staleness -- runners silently scored against stale lines (F09).**
`mlb_core/odds/sgo.py`: added `check_snapshot_freshness(gcs_key, max_age_hours=4.0)`.
All four runners (`run_nrfi`, `run_hr`, `run_f5`, `run_k`) abort with an error
log if snapshot >4h old.

**F3/F7 innings window markets are 3-way on SGO (draw/not_draw), not two-way ML.**
The extractors in `sgo_innings_extractors.py` assume two-way home/away format
matching `extract_f5_ml_odds()`. F3 and F7 need separate 3-way extractors before
they can be betted. F1H and GAME have two-way markets but the F5 proxy scalar
(1.44/1.37) and poor calibration (cal_err up to -0.135) make them unsuitable
for real Kelly sizing. All four innings window runners are shelved until
dedicated models are trained. Settlement code is in place for when they
eventually ship.

**BATTER_K dropped -- no paired onshore book coverage.**
`batting_strikeouts-` over entries only appear on DraftKings; under entries
only on BetMGM, and the player sets don't overlap. Result: 0 matched pairs
after `_best_book_odds_int()`. Skip until book coverage improves.

**Cross-line odds mixing in batter prop extractors (fixed 2026-06-10).**
Within a single SGO odd_id (e.g. `batting_hits-PLAYERID-game-ou-under`),
different books post different lines -- DK may have u0.5 at -200 while BetMGM
has u1.5 at +200. `_best_book_odds_int()` picked the highest American odds
value regardless of line, so BetMGM's u0.5 price (+200) was selected as the
"best" under odds for what DK reported as a 1.5-line bet. Result: the model
saw a massive fake edge on BATTER_HITS and BATTER_TB unders, and 82 poisoned
`BATTER_HITS_UNDER_1.5` bets were logged between 2026-05-25 and 2026-06-10
with inflated paper P&L (~+$933 fake). Those bets were deleted from the DB.
Fix: `_best_book_odds_for_line(entry, target_line)` restricts odds selection
to books whose `overUnder` matches the canonical line. `_dk_line_float` was
also updated to iterate `ONSHORE_BOOKS_PRIORITY` (DK first) instead of an
unordered set so the canonical line always comes from the most liquid book.
`BATTER_HITS_UNDER_0.5` bets with positive odds are NOT poisoned -- under 0.5
hits is the hitless prop and positive odds are correct for that market.

### 15.4 Feature builds (statcast, usecols, build_model_features)

**Statcast bat_score/post_bat_score are 100% null for 2021-2025.** Use
`scoring_master.csv` for run-based targets.

**Statcast `usecols` optimization reduces memory ~8x for feature builds.**
`mlb_core/storage.read_csv` passes `**kwargs` through to `pd.read_csv`. Feature
builders that only need a subset of the ~80 statcast columns should pass:
`usecols=lambda c: c in _STATCAST_COLS` where `_STATCAST_COLS` is a set of the
~12 needed columns. Without this, a 1500-day backfill on `statcast_master.csv`
(6M+ rows x 80+ cols) OOMs in 4Gi. With it, same load fits in ~500MB. Example:
`build_game_features.py` uses 13 columns; full-width load was killed by
signal 9 at row 3M.

**BATTER_HITS statcast load uses `usecols` (18 columns) -- this note used to say
the opposite.** Corrected 2026-08-17 (finding E1): this gotcha claimed the load
was "intentionally full-width (no `usecols`)," contradicting the actual, current
code and the backlog's own §16 E15 entry (`[x] Done 2026-06-01 -- 18 cols
identified, _STATCAST_COLS added, job downsized to 2CPU/4Gi`). If you're adding a
new statcast-derived feature to this builder, verify its source column is in
`build_batter_hits_features.py`'s `_STATCAST_COLS` set, not the other way around.

**`build_model_features()` team_map requires `game_meta` with `home_team`/`away_team`.**
If starter DataFrames don't carry team columns, the team_map is empty and all
team-level features (park factor, bullpen lookup) become NaN. Pattern: extract
`game_meta` from statcast directly as
`game_meta_df = statcast[["game_pk","home_team","away_team"]].drop_duplicates()`
in `run()`, then pass as parameter to `build_model_features(game_meta=game_meta_df)`.
Applied in `build_game_features.py`.

**Auto-detect first build for long lookback.** Feature builders that normally
use a 90-day incremental lookback need a first-build branch with 1500 days
(5 seasons) of data to populate the CV folds. Pattern:
```python
_is_first_build = not exists("GAME_Pro_System/data/model_features.csv")
LOOKBACK = 1500 if _is_first_build else 90
```
Without this, the first retrain after a new system has only the current season
(3-4 months) and walk-forward CV folds for 2023/2024/2025 have 0 rows each.

**`write_build_sentinel` signature is `(system: str, result: dict)`, NOT `(bucket, system, date)`.**
Calling it with 3 positional args causes a TypeError. Correct usage:
`write_build_sentinel("GAME", {"status": "ok", "run_date": run_date})`
The bucket is read from `GCS_BUCKET` env var internally. All 6 build runners
use this 2-arg form.

**BATTER_HITS `game_date` KeyError after merge collision.** `game_agg` and
`opp_info` both have a `game_date` column. Merging them produces
`game_date_x`/`game_date_y`, breaking any downstream `sort_values("game_date")`.
Fix: `opp_info.drop(columns=["game_date"], errors="ignore")` before merging.
Same pattern applies any time two DataFrames share a column name that you only
need from one side.

**`home_win` target for GAME must be derived from `scoring_master`, not `statcast`.**
`statcast` has `bat_score`/`post_bat_score` which are 100% null for 2021-2025.
Derive via: load scoring_master, pivot `half` (top=away, bot=home), sum runs
per half per game_pk, then `home_win = (bot_runs > top_runs).astype(int)`.
Drop ties (bot == top). This gives ~58k rows for 2021-2025, all labeled.
Building with `home_win = np.nan` for all rows produces 0 training rows.

**NRFI feature build order.** F5's builder reads
`NRFI_Pro_System/data/pitcher_start_features.csv`. NRFI must rebuild before
F5. Dependency order enforced in `/build-all-features` code -- F5 always runs after NRFI.

**`/build-all-features` is a single point of failure.** If it errors midway,
downstream systems silently use yesterday's feature CSVs. The build sentinels
(`{system}/data/last_build.json`) catch this at two levels: `monitor_ops` at
15:20 UTC alerts if any sentinel is stale or status=error, AND each runner
calls `check_build_sentinel()` from `mlb_core/storage.py` at run time --
aborting with a Discord alert rather than scoring on stale features.

**`check_build_sentinel(gcs_bucket, system_prefix)` in `mlb_core/storage.py`.**
Reads `{system_prefix}/data/last_build.json` via `read_bytes(key)` (single-arg
signature -- do not pass bucket separately). Returns `(ok, reason)`. Non-fatal
on GCS read errors (returns ok=True with warning) so a transient GCS blip does
not block betting. Called in all 4 runners immediately after snapshot load,
before any scoring work.

**`statcast_nightly_gcs` fetches `today-1` at 14:00 UTC** (9am CT) but Statcast
publishes yesterday's data around 2-3pm ET. The nightly refresh-data job silently
returns 0 rows and logs "ok". Fix: dedicated `mlb-refresh-statcast` scheduler job
at 21:00 UTC runs after Statcast publishes, feeding the next day's feature build.

**`/backfill-data` does not support statcast.** Use `/backfill-statcast` with
`{"dates":["YYYY-MM-DD",...]}` for Statcast pitch data. `/backfill-data` only
handles weather, scoring, and umpires. `/backfill-savant` handles Savant
leaderboards with `{"start_year":N,"end_year":N}`.

**`/backfill-statcast` takes a `dates` list, not `start_date`/`end_date`.**
Passing `{"start_date":"..."}` returns `{"error":"dates list required"}`.
Correct call: `{"dates":["2026-05-19","2026-05-20"]}`.

**Savant leaderboards require a browser User-Agent on requests.get().**
`pandas.read_csv(url)` returns 403. `savant_leaderboards.py` uses a
Chrome-style User-Agent header. If Savant changes bot-detection, the
HTML-response check (startswith '<!') will catch it and log a warning rather
than writing bad data.

**bat_tracking only available from 2023.**
`DATASET_START_YEAR["bat_tracking"] = 2023`. Backfill before 2023 returns empty
(not an error). Nightly refresh skips years before the dataset start year.

**Savant leaderboard data is cumulative season-to-date.** Each nightly call
overwrites the current season per-year cache file. Historic seasons are
immutable once the season ends and never re-fetched unless force=True.

**Backfill is slow by design.** `BACKFILL_SLEEP_MIN=8s, BACKFILL_SLEEP_MAX=14s`
between calls. Full 6-dataset backfill takes 15-25 min. Run via
`/backfill-savant` from Cloud Shell proxy only -- not a Scheduler job.
Gunicorn timeout 3600s is adequate for a full backfill.

**`PYTHONPATH=.` required when running feature builders outside Docker.** `mlb_core`
is installed as a package inside the Docker image but not in the local virtualenv.
Running `python3 mlb/runners/build_nrfi_features.py` directly raises
`ModuleNotFoundError: No module named 'mlb_core'`. Always use:
`PYTHONPATH=. python3 mlb/runners/build_nrfi_features.py`
for local testing. Inside Cloud Run this is not needed (setup.py installs the package).

**Savant swing_take is batter data only -- never join on pitcher MLBAM IDs.**
The `/leaderboard/swing-take` endpoint returns batter data regardless of the
`player_type` URL parameter. `player_type=pitcher` returns ~600 rows that are
all batters (Guillorme, Ozuna, Devers, etc.) not pitchers. `type=pitcher` returns
0 rows. Joining swing_take on pitcher MLBAM IDs produces at most 1 match out of
hundreds (near-zero overlap). swing_take is intentionally excluded from
`join_pitcher_aux()` and `join_game_aux()`. It is only wired into
`join_batter_aux(batter_col="batter")` using the batter's own MLBAM ID.
Output columns: `batter_runs_chase`, `batter_runs_heart`, `batter_runs_shadow`,
`batter_runs_waste`.

**HR's `opp_pitcher_id` is the batter's FIRST pitcher faced that game, not
necessarily the pitcher who actually allowed the HR.** `build_player_game()`
aggregates each batter-game to one row (`hr = max` across all that batter's
PAs that game) but attributes opponent-pitcher features
(`opp_pitcher_id=("pitcher","first")`) from only the first-faced pitcher. If
a batter's HR came off a mid-game reliever after starting against the
starter, the model still sees the starter's rolling stats as "the opposing
pitcher" for that row. This is a known, documented simplification, not a
bug to fix casually -- the market itself (HR yes/no, any pitcher, that game)
is game-grained, not pitcher-grained, so this is a genuine judgment call
about model grain, not an oversight. A fully pitcher-specific model would
need to move to per-PA rows, a materially bigger redesign. Confirmed via a
real traced game (José Ramírez, `game_pk` 824452, 2026-04-19) where this
happened to match anyway; that was coincidence, not guaranteed. See
docs/audits/2026-08-19_feature_data_pipeline_review.md finding 2.8.

**manager_hooks `compute_manager_hooks` must group by `(game_pk, dominant_half)`, not `game_pk`.**
Using `groupby("game_pk")["bf"].idxmax()` picks only one pitcher per entire game,
silently dropping either the home or away starter. The correct groupby is
`groupby(["game_pk", "dominant_half"])["bf"].idxmax()` to get one starter per
game side. `dominant_half` is the mode of `inning_topbot` per pitcher-game
("Top" = away pitcher, "Bot" = home pitcher). Correct output: ~25,700 rows
(one per team per game) vs the wrong ~12,850 (one per game). NaN=~120 is
expected warmup (first 30 games per team have no L30 history).

**Expected NaN rates after auxiliary joins.** These are normal and not bugs:
- FIP/WHIP/SO9/BB9 ~9% NaN (bref name match gaps for rookies / name format drift)
- travel_miles/home_away_streak/series_game_num < 5% NaN (team_schedule coverage)
- avg_starter_outs_L30 ~120 rows NaN (30-game warmup period per team at season start)
- batter_runs_chase/heart/shadow/waste varies by season (Savant coverage from 2023+)
- arm_angle ~97% NaN (newer Statcast field, absent pre-2024)
- n_thruorder ~95% NaN (newer Statcast field, absent pre-2024)
All of these are handled by XGBoost natively via feature_means imputation.

**`pitch_arsenals` CSV uses `pitcher` as the MLBAM ID column, not `player_id`.**
The `_dedup_cols()` function formerly fell back to `["year"]`-only dedup when no
named ID column matched, silently collapsing ~750 pitcher rows/season to 1.
Fixed 2026-05-27 (commit 127a213): `["year","pitcher"]` added as a dedup
candidate; year-only fallback removed entirely.
To rebuild a corrupted master without re-fetching Savant (the per-year cache
files are intact): call `/backfill-savant` with `{"dataset":"pitch_arsenals"}`
and no `force` flag -- all years are skipped (already cached) but the master is
rebuilt from the year files in ~5s. Expect `total_rows=0`, all years `-1`.
If year files are also bad (< 50 rows), add `"force": true` to re-fetch Savant.

**`scoring_master.csv` had no nightly refresh until 2026-05-14.** Bets logged
before that date may have pending settlement for game_pks not in the master.
Use `scoring_backfill_gcs()` with a date range to fix.

**Umpire features are all NaN in production models.** The three umpire
features (`ump_overall_accuracy_L30`, `ump_k_boost_L30`, `ump_consistency_L30`)
were NaN-only in training data for all systems due to a join bug. The K builder
now correctly rolls the umpire master, but `ump_k_boost_L30` is proxied via
`ump_total_run_impact_L30`. Models were trained and validated without umpire
signal -- XGBoost handles NaN natively.

**F13 -- `ump_tight_zone` in-sample quantile threshold.** `mlb/runners/build_nrfi_features.py`:
thresholds now use `expanding().quantile()` instead of full-dataset `quantile()`.

**F15 -- `build_batter_rolling` / `build_pitcher_features` used wall-clock date.**
`mlb/runners/build_hr_features.py`: both functions now accept `run_date` parameter;
historical replays use the correct reference point.

**F16 -- Weather fetch had no retry backoff.** Created
`mlb_core.data.weather.fetch_live_weather_for_slate` using existing `_fetch_weather`
(4-attempt exponential backoff). `run_hr._fetch_today_weather` and
`run_f5._fetch_today_weather` both replaced with the shared function.

**`mlb-refresh-data` weather gap caused NRFI AUC 0.500 (2026-05-21).** Weather
master had only 15 dates of 2026 coverage -> 85% null rate on weather features
-> XGBoost fills nulls with feature_means, collapsing all games to near-identical
inputs -> model output clusters at base rate. The right fix sequence after any
data gap: `/backfill-data` -> `/build-all-features` -> retrain + calibrate ->
`/model-health` to confirm.

**Statcast master is PA-level (one row per plate appearance), not pitch-level.**
Confirmed: 963,532 rows / 12,784 games ≈ 75.4 rows/game, matching MLB average
~76 PAs/game. Columns like `pitch_number` and `pitcher_days_since_prev_game` do
not exist in the master. Aggregations that call `len(g)` count plate appearances,
not pitches -- so `pitch_count_mean_L5 ≈ 24` is actually batters faced, not the
~90 pitches actually thrown. This makes `pitch_count_mean_L5` redundant with
`avg_bf_L5`. No code fix needed (XGBoost uses the signal correctly), but do not
interpret the raw value as a pitch count. Any feature that depends on pitch
sequences (e.g. `pitch_number == 1`) requires the full per-pitch Statcast feed,
not the PA-level master.

**`pitcher_days_since_prev_game` does not exist in the statcast master.**
NRFI and F5 builders formerly read this non-existent column, leaving `days_rest`
97% NaN. Fix (commit a56dc53 / ebc088c): after `starts` is built via groupby,
compute: `starts["days_rest"] = starts.groupby("pitcher")["game_date"].diff().dt.days`.
Applied in both `build_nrfi_features.py` and `build_f5_features.py`.

**`pitch_number` is absent from the statcast master.** The master stores one row
per PA outcome; individual pitch rows are not retained. `first_pitch_strike_pct_L5`
was 100% NaN because `g[g["pitch_number"] == 1]` always returned zero rows. Fix
(commit a56dc53) in `build_nrfi_features.py`: fallback branch uses
`g.groupby("at_bat_number", sort=False).head(1)` to approximate the first pitch
per PA. Post-fix NaN rate is ~6% (matching ump feature NaN from game_pk join gaps).

**Statcast's `events` column cannot see stolen base / caught stealing / pickoff
plays AT ALL, regardless of `group_by` -- confirmed live 2026-08-20, not just a
PA-level-collapse side effect.** Building the SB model, the working assumption
was that `stolen_base_2b`/`caught_stealing_2b`/etc. were real Statcast event
values that this repo's own PA-level dedup might be discarding. Live-fetched 4
real days (2026-08-15..18, ~56 games) using this repo's exact production URL
from `mlb_core/data/statcast.py` and found **zero** such values anywhere in
`events` -- only genuine plate-appearance-ending outcomes ever appear. An A/B
test varying `group_by` (name / none / name-event) produced byte-identical row
counts across all three, ruling out `group_by` as the cause (contradicting a
guess in an earlier version of the PA-level gotcha above). Something else in
the fixed query parameters (`hfAB=`, `hfSit=`, etc.) restricts results to
AB-terminal events only. **Any feature needing real SB/CS/pickoff data must
come from a new source, not statcast_master** -- confirmed working
alternative: MLB Stats API's `/api/v1.1/game/{game_pk}/feed/live` play-by-play
carries these cleanly (`eventType: "caught_stealing_2b"`, pitcher resolved via
`matchup.pitcher`, runner/catcher named in the free-text `description`). SB's
`stolen_bases`/`caught_stealing` target instead comes from the MLB Stats API
**boxscore** (already used for settlement, see `mlb_core/data/sb_boxscore.py`)
-- simpler than play-by-play and sufficient for a per-game count target,
though it can't give attempt-level context (inning, pitcher-at-the-time) the
way play-by-play could for a future, more granular iteration.

### 15.5 Model artifacts and calibrators

**Calibrators must be refit after any model output range change.** Isotonic
calibrators are fit on the OOS model output range. If the model is patched
(feature_means fix, IP bug fix, etc.) without a full retrain, the output range
may shift and the calibrator's X_min/X_max will no longer cover the new output
range. sklearn clips out-of-bounds inputs to the nearest boundary value, mapping
everything to 0 or 1. Always run the calibrate job after ANY change to model
artifacts or feature_means, not just after a full retrain.

**Retrain sequence: always run calibrate job after retrain.** Each system has
a paired retrain + calibrate job. Running retrain without calibrate leaves the
runner using a stale calibrator fit on the old booster's outputs. The
`/retrain-weekly` route fires all four retrains immediately then all four
calibrate jobs 30 min later via a background thread. Manual retrain sequence
per system:
```
NRFI: mlb-retrain-nrfi-v18 -> mlb-calibrate-nrfi
F5:   mlb-retrain-f5-v5    -> mlb-calibrate-f5
K:    mlb-retrain-k-v1     -> mlb-calibrate-k
HR:   mlb-retrain-hr-v6    -> mlb-calibrate-hr
```

**Calibrate scripts fit on the TRAIN slice (70%), not OOS split.** After C03
(2026-05-20) all retrain scripts use a 70/10/20 split: train (70%), val for
early stopping (10%), test for honest eval (20%). Calibrators fit on the
train slice only -- this gives adequate range coverage without leaking val/test.
Do NOT change calibrate scripts to fit on full df -- that would leak test set
into calibration.

**NRFI calibrator is fit on YRFI probs, not NRFI probs.** `calibrate_nrfi_v17.py`
calls `iso.fit(oos_g['model_yrfi_prob'], oos_g['yrfi'])`. The runner must apply
the calibrator to `model_yrfi_prob` and then derive
`model_nrfi_prob = 1 - calibrated_yrfi`. Applying it to `model_nrfi_prob`
produces a sign flip causing all games to show extreme YRFI probability. This
is easy to get wrong -- the variable names look symmetric but are not.

**NRFI calibration ground truth (12,662 games):** The model systematically
overestimates YRFI probability. When model says 40-45% YRFI, actual rate is
only 17%. When model says 45-50% YRFI, actual is 24%. The isotonic calibrator
is correct to map these down aggressively. High NRFI probs (80-86%) on
low-model-YRFI games are legitimate and well-supported by historical data.
Do not second-guess the calibrator on these -- it has 2,480+ games of support
in the 0.40-0.45 model YRFI bin alone.

**K `model_meta_v1.json` had corrupt `feature_means`** before 2026-05-15:
`avg_ip_L5=1.0` (correct: 5.6), `k_per_9_L5=48.5` (correct: 8.66),
`k_per_9_L10=48.8` (correct: 8.70). Caused near-zero lambda predictions for
all pitchers. Root cause: model trained when IP calculation bug was present.
Fixed 2026-05-15 by patching GCS meta directly. K model needs full retrain
with correct IP values.

**C01 diagnostic (2026-05-20): `platoon_edge` carries genuine signal -- keep it.**
Removing `platoon_edge` from NRFI HALFINN_FEATURES dropped OOS AUC 0.5791 ->
0.5314 (-0.0477). The concern that it reconstructed `lineup_pct_L` leakage is
resolved -- the feature contributes independent platoon-matchup signal.
Do not remove it.

**C03 (2026-05-20): all 4 retrain scripts use 70/10/20 train/val/test split.**
Early stopping now uses the val slice (last 1/8 of the train window); dtest is
never seen during training or tuning. Prior scripts used dtest as the eval_set
for early stopping, inflating reported AUC by ~0.003-0.005. CV loop in K
retrain also fixed -- each fold carves val from df_tr for early stopping.

**C04 (2026-05-20): retrain scripts store empirical percentile dists; PSI monitor
uses interpolation.** All 4 retrain scripts now compute `fpdists`
(p5/p10/p25/p50/p75/p90/p95 + prop_1) for every feature and store under
`"feature_dists"` in `model_meta`. `monitor_drift.py` uses linear interpolation
over these percentiles to reconstruct the training distribution for PSI binning.
Falls back to Gaussian if `feature_dists` not in meta (old models). Takes effect
after next retrain.

**C07 (2026-05-20): K Monte Carlo uses Negative Binomial, not Poisson.**
MLB strikeout counts over-disperse relative to Poisson (variance ~1.4-1.6x mean).
`nb_alpha` is fit from full-data residuals in `retrain_k_v1.py` using method of
moments: `alpha = clip((var - mu) / mu^2, 0.01, 0.50)`. Stored in
`model_meta_v1.json` as `"nb_alpha"`. `run_k.py` loads it and passes to
`_simulate_k` via function attribute `_simulate_k._nb_alpha`. Falls back to
Poisson if key missing (old meta). nb_alpha is only valid after the first
retrain following this change (2026-05-20).

**C08 (2026-05-20): K Monte Carlo IP scaling fixed -- k_per_9_L5 * avg_ip
replaces lambda * (ip/5).** Diagnostic showed residual slope -1.13 vs IP:
model over-predicted Ks at low IP, under-predicted at high IP. Root cause:
naive `lambda_k * (avg_ip_L5 / 5.0)` assumes linear K-per-IP but K rate drops
in later innings (fatigue, lineup cycling). Fix: `_simulate_k` now uses
`k_per_9_L5 / 9.0 * avg_ip_L5` as the expected K count. Falls back to
penalty-only scaling if `k_per_9_L5` is missing.

**`lineup_pct_L` leakage.** Was in NRFI v17 training -- carried same-game
run information into the half-inning target. Removed in retrain. Never add
same-game batter stats as features in inning-1 models.

**OUTS v1 retrain metrics (2026-05-27):** R²=0.042, MAE=2.388, RMSE=3.060,
calibration_bias=+0.111, 27 features, best_iteration=708. `market_prob` AUC
(0.637) substantially exceeds `model_prob` AUC (0.559). The book's implied
probability is a far better predictor of starter durability than the pre-game
model. OUTS wins are driven by betting market favorites, not genuine model edge.
Vulnerable to line movement and book limits. Do not promote OUTS past the paper
gate on ROI alone -- require positive CLV t-stat > 2 first. Longer-term fix
candidates: binary P(≥5 IP) target, manager tendency features, or remove from
active betting entirely until model improves.

**NRFI v18 retrain metrics (2026-05-27):** cv_mean_auc=0.5698, auc_oos=0.5907,
fold breakdown: 2024=0.5944, 2025=0.5834, 2026=0.5316 (1,287 samples -- thin).
The 2026 fold decline (0.5316) is partly sample-size noise but also reflects
the E05 drift concern. The `days_rest` and `first_pitch_strike_pct` fixes
populated those features correctly but did not shift OOS AUC meaningfully
(pre-fix run was 0.5908). Both features were already being seen by XGBoost via
NaN imputation with feature_means; the fix changes the values but the signal
was weak regardless.

**GAME Pro v1 first retrain metrics (2026-05-24):** AUC OOS 0.5565, walk-forward
AUC 0.5441, Brier 0.2451, 42 features, best_iteration=15. The low best_iter=15
signals the model is regularizing heavily with current data volume -- a candidate
for Optuna tuning once the 200-bet gate clears.

**SB v1 first retrain + backtest metrics (2026-08-20):** 22 features,
`count:poisson`, base rate 6.28% of batter-games have >=1 SB. Walk-forward CV
stable across years (MAE 0.1144-0.1265, R² 0.045-0.047 -- comparable to
OUTS v1's R²=0.042, low in absolute terms but normal for this model class).
`nb_alpha` hit the top of its `[0.01, 0.50]` clip range -- SB is a rare,
heavily zero-inflated count; worth revisiting the ceiling on a future retrain
with more data. Rolling walk-forward backtest (`--start 2024-04-01 --end
2026-08-19`, real historical odds, edge>=10%): 341 bets/19 windows, ROI
-3.05% to -3.13%, CLV -1.63% -- **verdict NO_EDGE** (ROI and CLV agree, both
negative, not the positive-ROI/negative-CLV soft-line-artifact shape). Same
bucket as K/OUTS/BATTER_HITS.

Optuna-tuned same day (`tune_hyperparams --system SB --n-trials 50`,
optuna had to be pip-installed first): max_depth 4->3, learning_rate
0.03->0.09, tighter regularization. Promoted into `retrain_sb_v1.py`'s
static `XGB_PARAMS` (required for the backtest to see it at all --
walkforward.py reads that module attribute directly, it never calls
`run()`, so the GCS-tuned-params pickup inside `run()` alone would've been
silently skipped). Retrain barely moved (MAE 0.1152->0.1149, R²
0.045->0.046). Backtest re-run: 328 bets, ROI improved to -0.17%/-0.10%
(z~=0.0, ~breakeven) but **CLV held at exactly -1.63%**, and the larger
`n_books>=4` sub-sample stayed solidly negative both times (-5.31% ->
-2.98%). Per CLV being the lower-variance/faster-converging signal (see
`mlb_core/risk/clv.py` docstring), an unchanged negative CLV means this is
still NO_EDGE -- the ROI drift toward breakeven is noise from a ~4%-
different bet sample, not a real edge appearing. Tuned params kept as the
new default anyway (better fit efficiency, no downside); `LOG_ONLY=True`
stays. Full breakdown in
`handoffs/handoff_2026-08-20_sb_stolen_base_model_build.md`.

**23rd feature added 2026-08-21: `pitcher_pickoffs`** (successful pickoffs
by the opposing pitcher, B-Ref `PO` column, real 2024 distribution median 0/
75th-pct 1/max 9 -- sparse), prompted by reviewing two external stolen-base
modeling projects the user shared. Real gain-based importance from the
retrained booster: `pitcher_pickoffs` 1.5%, on par with `pitcher_cs_allowed`
(1.6%) -- genuine signal, not dead weight, despite the sparsity.
`sb_per_game_L50`+`sb_season` still dominate at 60% combined. Backtest
re-run: 338 bets, ROI -1.19%/-1.08%, **CLV -1.49%** (vs -1.63% both prior
rounds -- a small move the right way, but noise-scale on this sample size,
nowhere near the +2.0% promotion bar). **Verdict still NO_EDGE.** Full
breakdown (incl. why the R project's true pickoff-*attempt*-rate stat isn't
cheaply available to us) in the handoff's 2026-08-21 addendum.

**`walkforward.py`'s `_resolve_contract()` has a hardcoded tuple of
`*_FEATURES` attribute names to look up on a system's `retrain_*.py` module
-- adding a new system to `WF_SYS` without adding its `"{SYS}_FEATURES"`
string to that tuple fails with `"no *_FEATURES list and no exclusion set"`
even though the module correctly exports the list.** Hit adding SB
(2026-08-20); same trap will hit the next new system added to `WF_SYS` too.
Check `_resolve_contract`'s `_get(mod, "K_FEATURES", "OUTS_FEATURES", ...)`
call and add the new system's feature-list name every time `WF_SYS` grows.

### 15.6 Runners and scoring

**Kelly floor zeroes out HR longshots.** HR uses `min_kelly_pct=0.001` (lowered
from 0.005 on 2026-05-15) and 50% Kelly fraction. At +600 odds, edge=0.03 produces
pct=0.0025 which clears the 0.001 floor giving ~$2.50 stake. The other systems
use 0.005 which is appropriate for their shorter odds.

**K `model_features.csv` pitcher column is MLBAM integer ID, not name string.**
`.str.contains()` on the `pitcher` column raises `AttributeError: Can only use
.str accessor with string values`. Filter by numeric ID directly. All other
name-based lookups (SGO, boxscore) go through `_pitcher_name` /
`_pitcher_name_norm` columns added by `_attach_today_slate`.

**K build performance.** The opponent backfill pre-prepares the PA frame once
(`_prepare_pa_for_opp_features`) before the per-date loop. Do not revert this --
the naive version was killed by gunicorn at 15min.

**3-way 1st inning ML is derived, not retrained.** `p_3way_away/home/draw` are
computed from existing NRFI half-probabilities. No new model needed.
The math: `p_away = p_away_half * (1 - p_home_half)`, etc.

**Pitcher outs O/U used to be a proxy model (Normal(avg_ip, 1.5)).** E04
(2026-05-21) replaced this with a trained `count:poisson` XGBoost model on
`starter_outs` target. Runner falls back to Normal proxy if model not found in GCS.

**BATTER_TB is a dedicated trained NegBin count model.** `run_batter_tb.py`
uses XGBoost `count:poisson` predicting expected total bases (lambda), then
applies NegBin CDF against the live book line. It requires confirmed lineup
candidates and skips any prop where SGO `event_id` does not match feature
`game_pk`. Do not restore the old historical-team fallback; it can assign a
player to the wrong game.

**PITCHER_ER ships log-only (stake=0, kelly_triggered=False).** It uses a Gamma
proxy anchored to K model output rather than a dedicated trained model. Do not
enable real Kelly sizing until post-hoc analysis of ~100 settled bets confirms
the proxy edge predicts outcomes.

**BATTER_HITS ships log-only via a trained NegBin model (LOG_ONLY=True gate).**
`run_batter_hits.py` uses XGBoost `count:poisson` predicting expected hits
(lambda), then applies NegBin CDF against the live book line. This is NOT a
proxy -- it is a dedicated trained model (`retrain_batter_hits_v1.py`).
LOG_ONLY remains True until 200 settled bets confirm calibration. To promote:
set `LOG_ONLY = False` in runner. Retrain sequence:
`build_batter_hits_features -> (optional) tune_hyperparams BATTER_HITS ->
retrain_batter_hits_v1 -> calibrate_batter_hits_v1`.

**F1H innings sub-market is live log-only in run_f5.py (still uses F5 scalar proxy).**
F1H (first half, innings 1-4) uses the two-way SGO market (`points-*-1h-ml-*`)
and the F5 scalar proxy. Ships stake=0 until ~100 settled bets confirm
calibration. To promote F1H to real sizing: remove "F1H" from
`LOG_ONLY_SYSTEMS` in `run_f5.py`.

**GAME Pro v1 is a dedicated model, NOT the F5 scalar proxy.**
`mlb/runners/run_game.py` uses `GAME_Pro_System/models/xgb_game_v1.json` -- a
`binary:logistic` model trained on `home_win` with 42 features including bullpen
(xwOBA L14, K%, BB%, whiff_pct L14, hard_hit L14, fatigue IP L7), starter rolling
stats (xwOBA+whiff+hard-hit L3), and team offense (wOBA+hard-hit L20).
`extract_game_ml_odds()` in `sgo.py` provides the full-game two-way ML odds.
GAME ships LOG_ONLY=True until 200 settled bets confirm calibration.

**IL-return skip cross-references actual IL status (2026-05-20).** All three
runners (NRFI, K, F5) skip a pitcher if `days_since_last_appearance > 7 AND
pitcher_id in fetch_il_pitcher_ids()`. Previously the check was purely day-count
based, which incorrectly blocked healthy starters mid-rotation (scheduled rest,
rain delay, etc.). `fetch_il_pitcher_ids()` in `mlb_core/data/lineups.py` calls
`/api/v1/teams/{teamId}/roster?rosterType=injured` for all 30 teams and returns
the set of pitcher MLBAM IDs currently on IL. Fails open (returns empty set) on
network error.

**MLB Stats API injured roster can lag activations by 1-2 days.**
`/roster?rosterType=injured` may still list a pitcher who was recently activated.
The 7-day threshold mitigates this -- a pitcher on normal 5-day rotation never
hits the threshold regardless of roster API lag. Real 10-day IL stints always
produce gaps >= 10 days.

**K/NRFI/F5 IL skip simplified to gap > 15d (2026-05-23).** The original
dual-condition (gap > 7d AND on IL API) failed in two ways: (1) MLB API lags
activations 1-2 days, causing healthy starters to be skipped; (2) the
`historical` filter (rows where `starter_ks` is not null) excludes today's
unplayed rows, inflating apparent gap for pitchers making their season debut
or returning from IL. Simplified to single condition: gap > 15d. Genuine IL
stints always exceed it; normal rotation never does.

**`fetch_il_pitcher_ids()` throws in Cloud Run causing `NameError: _il_ids`
(2026-05-21).** Works locally but fails in Cloud Run cold start. Fixed: wrapped
in try/except that fails open (empty set) in `run_nrfi.py` and `run_k.py`. The
fail-open behavior matches the original design intent.

**F5 `home_game_date`/`away_game_date` must be reset to `run_date` after pulling
historical row (2026-05-23).** `_build_today_feature_rows` takes `match.iloc[-1]`
which carries old dates from the last time that team pair played. `_starter_stale()`
then sees a 290+ day gap and skips every game. Fixed: both columns overwritten
with `run_date` immediately after the historical row is loaded.

**F5 IL-return (2026-05-20): `home_game_date` / `away_game_date` now in F5
feature CSV.** `game_date` added to `BASE_COLS` in `_apply_joins` in
`build_f5_features.py`. Columns land as `home_game_date` and `away_game_date`
in `model_features.csv`. `run_f5.py` skips any game where either starter's last
appearance is >10 days before `run_date`.

**`game_date` vs `run_date` in runners.** The exposure prefetch call inside
`_build_predictions()` must use `run_date` (the parameter passed to the function),
not `game_date` (which is a DataFrame column name, not a local variable at that
scope). Using `game_date` causes `NameError: name 'game_date' is not defined` at
runtime. Tests don't catch this because `_build_predictions()` is not called in
the test suite.

**`game_date` was logged in UTC before 2026-05-17.** Bets logged at 22:00 UTC
(5pm CT) on May 16 got `game_date=2026-05-17` because UTC date had rolled over.
Fixed in `main.py` by using `date.today(_CT).isoformat()` (CT timezone). ~12 bets
in the DB have wrong dates -- not worth fixing in paper mode.

**Paper mode flag removed from Discord notifications (2026-05-17).** The
paper_tag was removed from bet headlines. All bets treated as cash going forward.
The `paper` column still exists in the DB for historical reference.

**`market_prob` in NRFI bets stores the book fair probability, not the model
probability.** The AUC script must use `model_prob` column for NRFI, not
`market_prob`. For other systems (HR, K, F5, OUTS) both columns are similar.

**HR bets store full team names in `away_team`/`home_team`** (e.g. "Red Sox",
"Braves") while all other systems store 3-letter abbrevs (e.g. "BOS", "ATL").
Root cause: HR runner gets teams from feature CSV which uses SGO medium names.
Frontend works around this with `TEAM_ABBREV` lookup map in `picks-table.tsx`
and `results/page.tsx`. Proper fix: normalize team names in `run_hr.py` at
log time. Backlog item.

**F01 -- HR vig formula centralised.** `mlb_core/odds/utils.py`: added
`devig_unilateral(market_prob, vig_pct=0.07)`. `mlb/runners/run_hr.py`: replaced
hardcoded `market_prob / 1.07` with `devig_unilateral`.

**F02 -- SQL injection in /dashboard and /reset-bets.** `main.py`:
`system_filter` now whitelist-validated and passed as a bound parameter.
`/reset-and-run` and `/reset-bets` now require `X-API-Key` auth.

**F03 -- Retractable roof always set to is_outdoor=1.** `mlb/runners/run_hr.py` line
460: `1 if roof == "open" else 1` -> `1 if roof in ("open","retractable") else 0`.

**F04 -- F5 calibrator applied without boundary check.** `mlb/runners/run_f5.py`:
added `X_min_`/`X_max_` range guard matching the NRFI runner pattern.

**F05 -- `_norm` defined three times in settle_bets.py.** Hoisted to module
level. All three inline copies removed.

**F10 -- Morning/evening bet deduplication.** `mlb_core/tracking/bet_tracker.py`
`is_duplicate`: new `kelly_triggered` parameter. Non-triggered morning prediction
no longer blocks a triggered evening bet on the same market. Triggered bet still
blocks a second triggered bet.

**F11 -- Settlement fetched game results serially.** `mlb/runners/settle_bets.py`:
`ThreadPoolExecutor(max_workers=8)` parallelises `fetch_game_result` calls.
15-game slate: ~7.5s -> ~1s.

**F12 -- `post_pitch_clock` added to builders but not to explicit feature lists.**
`mlb/training/retrain_nrfi_v17.py` `HALFINN_FEATURES`: added `"post_pitch_clock"`.
`mlb/training/retrain_k_v1.py` `K_FEATURES`: added `"post_pitch_clock"`.

**F14 -- HR name matching: exact-only misses accent variants and suffixes.**
`mlb/runners/run_hr.py`: added `difflib.get_close_matches` fuzzy fallback at cutoff=0.85.

**SB's live candidate rows carry stale `catcher_*`/`pitcher_sb_allowed` columns
from the batter's LAST game -- must be reset before re-joining today's real
opponent, or a pandas merge produces silent `_x`/`_y` collisions instead of
fresh values.** Found live 2026-08-20 testing `run_sb.py`'s
`_build_today_feature_rows` against a real slate: each candidate row starts as
the batter's latest historical snapshot from `model_features.csv`, which still
has whatever `catcher_pop_2b_sba` etc. were true against a PAST opposing
catcher. `join_catcher_aux()` is a pandas merge, so leaving those stale
columns in place produced `catcher_pop_2b_sba_x`/`_y` instead of a clean
overwrite. Fix: drop every `catcher_*` column before calling `join_catcher_aux()`
again for today's real opponent. `pitcher_sb_allowed`/`pitcher_cs_allowed` have
the same latent staleness but don't collide (they're set via plain dict-key
assignment, matching `run_batter_hits.py`'s pitcher-join pattern) -- they were
still explicitly reset to NaN before the lookup so a failed bref match
correctly shows "unknown" instead of silently keeping a prior opponent's value.
**Any new batter-prop runner that re-joins a THIRD player-entity (not just the
batter + one opponent) at score time needs this same reset, not just the
historical builder's version of the join.**

### 15.7 Settlement and boxscore

**`game_result.py`'s `team.get("pitchers", [None])[0]` crashes with IndexError
on a real (rare) boxscore where `"pitchers"` is present but an EMPTY list, not
absent.** `.get(key, default)` only falls back to `default` when `key` itself
is missing -- an empty list is a valid value that `.get()` returns as-is, and
`[0]` on it raises. Found 2026-08-20 when this crashed the SB historical
backfill mid-run on a real historical game_pk (not a synthetic edge case).
Fixed to `(team.get("pitchers") or [None])[0]`, which catches both the
missing-key and the empty-list case. **This function is shared by every
settler** (`fetch_game_result()` is called once per game_pk and cached across
all systems in a settle run) -- the same crash could in principle have hit a
real nightly `/settle` run on any system, not just this backfill; it just
happened to surface here first because the backfill touches ~4 years of real
game_pks in one pass instead of one day's slate.

**HR settlement uses MLB Stats API boxscore (not Statcast).** `_settle_hr`
calls the MLB Stats API per game_pk. If the game is not Final, the bet is
skipped (retried tomorrow). If Final: player not in starting lineup -> void
(DK voids non-starters); starter with HR -> win; starter without HR -> loss.
No Statcast dependency for HR settlement.

**K/OUTS settlement voids bets when pitcher not in boxscore.** If a pitcher is
scratched before throwing a pitch, they won't appear in the MLB Stats API
boxscore. The settler now voids the bet (result='void', profit=0) rather than
leaving it pending indefinitely. This matches DK rules: pitcher must throw at
least one pitch for props to grade.

**F06 -- K/OUTS push grading on integer lines.** `mlb/runners/settle_bets.py`:
whole-number line now logs a `WARNING` instead of silently grading push.
DK uses half-point K/OUTS lines so a whole-number line signals a parsing error.

### 15.8 CLV and line movement

**F07 -- CLV arithmetic used vig-inclusive probabilities.**
`mlb_core/tracking/bet_tracker.py` `write_closing_line`: CLV now computes on
fair (no-vig) probs. Props use `devig_unilateral`; two-sided markets use raw
implied as approximation pending complementary-side capture in
`capture_closing_lines.py`.

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
added all four columns to both SELECTs in `mlb/runners/public_api.py`.

**`mlb-capture-closing` scheduler job uses OIDC but `/capture-closing` has
no auth check.** OIDC token is sent but ignored -- route is open. Job was
returning `status: {}` (empty, meaning success) but CLV was 0 because the
route was crashing inside due to the bugs above, not auth rejection.

**`morning_odds` will be NULL for bets placed before E10 deploy (2026-05-21).**
The column exists but historical bets have no morning snapshot to reference.
`line_move_pct` will also be NULL for these. Only bets placed after the deploy
will have both fields populated. CLV and line movement analysis should filter
to `morning_odds IS NOT NULL`.

### 15.9 Cloud Run Jobs

**Cloud Run Jobs executed via `python3 -m module` require `if __name__ == "__main__":` blocks.**
When a Cloud Run Job runs `--command python3 --args="-m" --args="mlb.runners.build_game_features"`,
Python imports the module and exits 0 silently if there is no `__main__` block.
No error, no stack trace, no payload written -- the job shows COMPLETE: 1/1 but
did nothing. All six build runners needed this block added. Training scripts
(`retrain_*.py`, `calibrate_*.py`) are invoked as scripts and already had
`__main__` blocks. Check any new runner you add as a Cloud Run Job.

**Cloud Run Job creation requires `--set-cloudsql-instances` not `--add-cloudsql-instances`.**
The service also requires
`--service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com`
-- without it the job uses the default compute SA which lacks Secret Manager access.
See RUNBOOKS.md for the full working command.

**`--args` flag takes repeated values, not comma-separated.**
`--args="-m,mlb.runners.build_game_features"` fails with "expected one argument".
Correct form: `--args="-m" --args="mlb.runners.build_game_features"`.

**Jobs created before the image is confirmed good will have the wrong image path.**
If you create a job then immediately hit an image NOT_FOUND on execute, use
`gcloud run jobs update JOB_NAME --image gcr.io/...` to fix it without deleting
and recreating.

**`mlb_core/registry.py` is the single source of truth for system config.**
`monitor_ops.py`, `monitor_performance.py`, and `discord.py` all derive their
system lists and icons from the registry via dict comprehensions. `main.py`
still has hardcoded system lists (VALID_SYSTEMS, builders dict, etc.) --
migration is deferred. When adding a new system, update both `registry.py`
(required) AND `main.py` (until the migration is done).

**`gcloud run jobs update --set-env-vars` REPLACES the entire env var list; use `--update-env-vars` to merge.**
Found 2026-09-02: `mlb-tweet-recap` had no `TWEET_MODE` env var despite both
CONTEXT.md and RUNBOOKS.md documenting `TWEET_MODE=recap` for it --
`tweet_drafter.py` defaulted to `"picks"`, so the job silently ran the wrong
code path (and still exited 0, since `picks` mode's early-return-on-empty
made it look like a healthy no-op) every day for at least the prior 8+ days.
Root cause: RUNBOOKS.md's own "one-shot Cloud Shell update" snippet used
`--set-env-vars` for what was meant to be a partial update of two unrelated
vars (`BEEZY_API_URL`/`BEEZY_SITE_URL`), silently dropping `TWEET_MODE`
every time it ran. Same "full replace vs. merge" flag trap as
`--set-cloudsql-instances`/`--add-cloudsql-instances` and
`--set-secrets`/`--update-secrets` elsewhere in this doc. See
`docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md`.
Also note: the Cloud Scheduler HTTP target for a Job trigger carries no body
at all in this repo's setup -- `TWEET_MODE` (like any Job env var) can ONLY
come from the Job resource itself, never from the scheduler.

**`gcloud scheduler jobs update http` uses `--attempt-deadline`, NOT `--deadline`.**
`--deadline` is not a valid flag and gcloud will reject it:
`ERROR: unrecognized arguments: --deadline (did you mean '--attempt-deadline'?)`.
Correct command fragment for updating the build job deadline:
```bash
gcloud scheduler jobs update http mlb-build-all-features \
  --location us-central1 \
  --uri "https://run.googleapis.com/v2/projects/concrete-crow-445205-m4/locations/us-central1/jobs/mlb-build-all-features:run" \
  --http-method POST \
  --oauth-service-account-email scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline 1800s \
  --message-body "{}"
```
Run this from Cloud Shell. Verify with
`gcloud scheduler jobs describe mlb-build-all-features --location us-central1`.
**NOTE:** Cloud Scheduler `--attempt-deadline` is capped at 1800s (30 min). This is NOT the
job execution timeout -- the Run API call returns a LongRunningOperation immediately (async),
so the scheduler only needs ~60s to get the HTTP 200 back. The job itself runs to completion
independently under the 3600s task timeout set on the Cloud Run Job itself.

### 15.10 Frontend (Tailwind v4, Clerk v7, Next.js)

**The core rule: use pure inline styles for everything in beezy-vip.** Do not
use Tailwind utility classes for colors, borders, backgrounds, spacing, or
layout. Tailwind v4 with `@tailwindcss/postcss` does not reliably scan and
generate CSS for utility classes in this monorepo subdirectory setup.

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

**Inline `style={{ display }}` overrides CSS class `display` rules.** If a
component has both `className="some-class"` (where the class sets
`display: none`) and an inline `style={{ display: 'flex' }}`, the inline style
wins due to CSS specificity. This is the root cause of the mobile nav CTA
showing on desktop. Fix: remove `display` from the inline style and add
`!important` to the CSS class's base rule (`display: none !important`) so even
future inline styles can't accidentally override it.

**Clerk v7 (`@clerk/nextjs`) API changes vs v5/v6:**
- `SignedIn` and `SignedOut` are NOT exported from `@clerk/nextjs` for use in
  `'use client'` files. Use `const { isSignedIn } = useUser()` with conditional
  rendering instead.
- `afterSignOutUrl` was removed from `<UserButton>` props. Set it on
  `<ClerkProvider afterSignOutUrl="/" signInUrl="/login" signUpUrl="/signup">`
  in `layout.tsx` instead.
- Server components use `currentUser()` from `@clerk/nextjs/server` -- works
  independently of ClerkProvider.

**Other Next.js gotchas:**
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

**`playerSlug()` hyphen bug (fixed 2026-05-25).** The strip regex `[^\wÀ-ɏ]`
also strips hyphens, so hyphen->_ conversion must run BEFORE the regex or
hyphens disappear entirely. Affected: Ha-Seong Kim, Isiah Kiner-Falefa,
Kai-Wei Teng, Sawyer Gipson-Long. `player_map.json` keys and
`process_headshots.py` `slug()` were normalized simultaneously.

---

## 16. Backlogs

_Last updated: 2026-09-02 00:39 CST_

Three independent backlogs share this section: model remediation (T-series),
engineering (E-series), and frontend UX (F-series from the Mongoose audit).
Work top-to-bottom within each tier. Mark items `[x]` when verified; completed
items move to §16.4 Archive.

**Rule:** No system crosses the paper -> live gate until every P0 task is `[x]`.

Conventions:
- Start a task -> note it in the session handoff as "in progress"
- Finish a task -> mark `[x]`, add commit hash or date, update `_Last updated` above
- Blocked task -> add a `> Blocked: <reason>` line under it
- New tasks found during work -> add to the appropriate priority tier with the
  next ID in sequence
- Scope change to existing task -> edit in place, note the change date inline

### 16.1 Model remediation (T-series)

_Source: institutional quant audit of the full codebase, 2026-05-19._

#### P0 -- Deployment blockers (all complete)
T01-T09 are complete and moved to §16.4 Archive. Open P0 items: none.

#### P1 -- Required before scaling

##### T12 -- Live lineup integration
- **Depends on:** T06 (feature contract reconciled first)
- **File:** `mlb/runners/run_nrfi.py` around `_build_today_feature_rows` lines 89-165
- **Change:** Call MLB Stats API for posted lineups. Map starter ID -> top-3
  batter IDs. Compute `top3_batter_*` rolling stats from the historical batter
  features table. Flag each bet with `lineup_confidence: 'posted' | 'estimated'`
  in the bets table.
- **Also:** Update `deploy/add_snapshot_schedulers.sh` to shift the afternoon
  snapshot closer to lineup-posting time (2-3 hours before first pitch).
- **Why:** Live runner currently uses stale historical features for all
  lineup-dependent signals. Morning run has zero lineup data.
- **Acceptance:** Afternoon runner logs `posted_lineup` for >= 80% of bets.
  Morning run accepts `estimated_lineup` and marks bets accordingly.
- [ ] Done

#### P2 -- Institutional baseline

##### T17 -- Tighten paper -> live promotion criteria
- **Depends on:** T08 (CLV tracking must be live, already done)
- **File:** `CONTEXT.md` §6 "Paper -> live criteria"
- **Replace with:**
  1. >= 200 settled bets per system
  2. Mean CLV >= +2% with t-stat > 2 over >= 100 bets
  3. Season ROI > 0% (HR: > -5% allowed if CLV positive)
  4. Calibration: hit rate within 3 pct points of avg model probability (was 5)
  5. No system down more than 50 units at paper stakes
  6. PSI for all top-10 features < 0.25 over the eval window
- **Acceptance:** `monitor_performance.py` enforces criteria 2-4 programmatically
  and blocks the Discord "ready for live" alert until all pass.
- [ ] Done

##### T18 -- Nested hyperparameter tuning
- **Files:** Each `retrain_*.py`
- **Change:** Add Optuna-based search. Inner CV on train slice (3-fold
  time-series). Outer evaluation on OOS year. Search space:
  `max_depth in [2,4]`, `learning_rate in [0.01, 0.1]`,
  `min_child_weight in [5, 50]`, `reg_alpha`, `reg_lambda`, `gamma`. Hardcoded
  `XGB_PARAMS` dicts become defaults only.
- **Why:** Current params are undocumented guesses or notebook-era defaults.
- **Acceptance:** Each retrain logs tuned params. `model_meta_*.json` includes
  `tuned_params` and `optuna_n_trials`.
- [ ] Done (E09 wrote the tuner; needs to be invoked per system)

##### T19 -- Pitcher IL-return / debut handling
- **Files:** All `build_*_features.py`
- **Change:** Compute `days_since_last_start` correctly across IL gaps. If gap
  > 25 days, set `coming_off_il = True` and reset rolling window accumulation
  from that start forward (don't average across the gap). Add `coming_off_il`
  to feature lists.
- **Why:** IL returnees have stale rolling stats and incorrect short_rest flags.
- **Acceptance:** Spot-check 10 known 2025 IL returns -- all correctly flagged.
  `coming_off_il` carries non-zero feature importance.
- [ ] Done

### 16.2 Engineering (E-series)

_Source: ongoing engineering work, 2026-05-21._
_All items are engineering, not data gaps. 5 years of training data (25k+ rows)
is sufficient. Live bet sample (51-110 per system) grows daily and is not the
bottleneck._

#### Immediate

##### E05 -- NRFI 2026 drift investigation
- **Why:** Walk-forward CV shows AUC degrading: 2024=0.5985, 2025=0.5876,
  2026=0.5394. Weather fix recovered live AUC from 0.500 but the year-over-year
  decay needs investigation. Pitch clock, opener usage, lineup construction all
  shifted.
- **Approach:** Compare feature distributions year-by-year. Add opener usage
  flag, pitch count efficiency, chase rate vs 2023 baseline.
- **Acceptance:** 2026 fold AUC >= 0.560 after feature additions + retrain.
- [ ] Done

##### E07 -- Verify mlb-capture-closing and mlb-refresh-data scheduler jobs are firing
- **Why:** mlb-refresh-data had Last attempt: None causing the weather gap.
  Same pattern likely affects mlb-capture-closing (explaining clv_n=0).
- **Check:** `gcloud scheduler jobs describe mlb-refresh-data --location=us-central1`
- **Fix if URI wrong:** `gcloud scheduler jobs update http mlb-refresh-data --location=us-central1 --uri=https://mlb-betting-628109313129.us-central1.run.app/refresh-data`
- [ ] Done

#### Short term

##### E08 -- NRFI sub-model ensemble
- Gated: only start after NRFI live AUC >= 0.54 with n >= 200 bets.
- Split pitcher dominance / lineup quality / park+weather into sub-models.
- Stacking layer: logistic regression combining three sub-model outputs.
- [ ] Done

#### Medium term

##### E11 -- Cross-system Kelly coordination
- Global daily exposure cap: total stake across all systems <= 10% of bankroll
  per day. Only needed before going live. Paper mode single-system caps are
  sufficient now.
- [ ] Done

##### E13 -- Migrate main.py hardcoded system lists to registry
- **Why:** main.py has VALID_SYSTEMS, builders dict, _run_system switch,
  build_features_handler, dashboard list, reset-and-run list all hardcoded.
  Adding a new system requires editing 6+ places in main.py after updating the
  registry. Long-term: one registry entry = fully wired.
- **Files:** `main.py` -- replace VALID_SYSTEMS, builders, _run_system with
  registry lookups. Dynamic import via `importlib.import_module(cfg.builder_module)`.
- **Blocked:** main.py has complex dynamic imports and the Flask route structure
  needs a separate pass to avoid breaking the service. Do not rush this.
- [ ] Done

##### E14 -- Migrate tune_hyperparams.py SYSTEM_CONFIG to registry
- **Why:** `tune_hyperparams.py` has its own SYSTEM_CONFIG dict with
  target/objective/metric/output fields that duplicate
  `mlb_core/registry.py` SystemConfig tune_* fields. Already kept in sync
  manually -- divergence is a matter of when, not if.
- **Files:** `mlb/training/tune_hyperparams.py` -- replace SYSTEM_CONFIG dict with
  `{name: get_system(name) for name in active_systems()}` and read
  `cfg.tune_target` etc.
- [ ] Done

##### E15 -- BATTER_HITS statcast usecols optimization
- **Why:** `build_batter_hits_features.py` loads the full statcast_master.csv
  (~80 cols) to compute contact rate, BABIP, launch metrics. Currently requires
  8Gi/4CPU. Profiling may show that only 20-30 cols are actually used -- `usecols`
  could halve memory, allowing a smaller Job.
- **Action:** Add `_STATCAST_COLS` set; run build job with 4Gi to verify it stays
  under memory. Only worth doing if daily build time or cost is a concern.
- [x] Done 2026-06-01 -- 18 cols identified, `_STATCAST_COLS` added, job downsized to 2CPU/4Gi

##### E16 -- GAME Pro v1 Optuna tuning (post-gate)
- **Why:** best_iteration=15 on first retrain indicates heavy regularization
  with 5 seasons of data. Optuna nested CV will find better
  max_depth/min_child_weight/reg_alpha balance.
- **Blocked:** Do not run until GAME clears 200-bet gate (LOG_ONLY gate). Tuning
  on a model that may have calibration issues is wasted compute.
- **Action:** `python -m mlb.training.tune_hyperparams --system GAME --n-trials 50`
  then `mlb-retrain-game-v1 -> mlb-calibrate-game`.
- [ ] Done

##### E17 -- BATTER_HITS nb_alpha calibration (post-gate)
- **Why:** nb_alpha=0.01 (clamped to minimum) on first retrain means the model
  uses Poisson CDF for bet sizing. Re-fit nb_alpha from 200+ settled predictions
  vs actual hit counts to check whether underdispersion assumption holds in
  live data.
- **Blocked:** Need 200 settled BATTER_HITS bets first.
- **Action:** Compute `var/mean` on settled bets, update nb_alpha in model_meta,
  re-calibrate.
- [ ] Done

### 16.3 Frontend UX (F-series)

_Added 2026-05-25. Informed by Mongoose Bets UI/UX audit._
_Frontend lives at `beezy-vip/` (Next.js 16, React 19, Tailwind CSS v4, Recharts)._

Mongoose Bets (mongoosebets.com) is the reference product: same category, more
polished execution. Their core philosophy: "Surface actionable betting edges
immediately" -- decision-first, not data-first. Their UI compresses statistical
complexity into emotionally legible signals (composite score, tier badges, glow
intensity). The backlog below closes that gap in priority order.

#### Key files (read these first at the start of any frontend session)

| File | Purpose |
|---|---|
| `beezy-vip/app/globals.css` | Design tokens (CSS vars), responsive grid classes, animations |
| `beezy-vip/lib/tokens.ts` | System colors (SYSTEM_COLOR), pill styles (SYSTEM_PILL), team abbrevs, pickLabel() |
| `beezy-vip/lib/types.ts` | Bet, SystemStats TypeScript interfaces |
| `beezy-vip/app/cheat-sheet/cheat-sheet-client.tsx` | Main mobile-optimized picks card (393 lines) |
| `beezy-vip/components/picks/picks-table.tsx` | Desktop table + mobile cards for picks/results |
| `beezy-vip/components/picks/filter-bar.tsx` | Hamburger toggle + chip filter rows for /picks |
| `beezy-vip/app/results/results-client.tsx` | Results page: charts, system chips, bets table |
| `beezy-vip/app/dashboard/picks/page.tsx` | Authenticated picks dashboard (today's picks + Kelly) |
| `beezy-vip/components/layout/nav.tsx` | Sticky nav, hamburger mobile menu |
| `beezy-vip/components/landing/models-grid.tsx` | System cards on landing page |
| `beezy-vip/components/ui/primitives.tsx` | SystemBadge, StatCard, ResultPill, PnL, LiveDot |

#### Sprint 0 -- Bugs (fix immediately)

##### F-B1 -- Dashboard shows `Game {game_pk}` instead of matchup
File: `beezy-vip/app/dashboard/picks/page.tsx:72` (pending table) and `:112`
(settled table). `away_team` / `home_team` exist on the pick object but are not
rendered. Fix: replace `Game {pick.game_pk}` with
`{pick.away_team} @ {pick.home_team}`. Same fix needed on both grids.
- [ ] Done

##### F-B2 -- User bankroll never applied to Kelly calculation
File: `beezy-vip/app/dashboard/picks/page.tsx:16,61`.
`const bankroll = (user?.publicMetadata?.bankroll as number) ?? DEFAULT_BANKROLL`
is read correctly at line 23, but `kellyStake()` at line 61 still passes
`DEFAULT_BANKROLL` (1000) instead of `bankroll`. Fix: pass `bankroll` to
`kellyStake()`.
- [ ] Done

##### F-B3 -- Results table shows bucketed edge ranges instead of exact values
File: `beezy-vip/app/results/results-client.tsx:506`.
Renders `"10%+"` / `"5-10%"` / `"0-5%"` / `"<0%"` when `bet.edge` is an exact
float. Fix: replace with
`bet.edge != null ? (bet.edge * 100).toFixed(1) + '%' : '--'`.
- [ ] Done

#### Sprint 1 -- Design system tokens

Mongoose uses rounded cards, subtle elevation, and emissive glows. Beezy uses
square corners, flat borders, and no elevation. All three gaps have one-line
fixes in globals.css that cascade everywhere.

##### F-1.1 -- Add border radius scale to globals.css
Add to `:root` in `beezy-vip/app/globals.css`:
```css
--radius-sm: 6px;
--radius:    10px;
--radius-lg: 14px;
```
Apply `border-radius: var(--radius)` to:
- Pick cards in `cheat-sheet-client.tsx:72`
- Bet cards in `picks-table.tsx:121`
- Stat cards in `components/ui/primitives.tsx` (StatCard component)
- The outer cheat sheet wrapper at `cheat-sheet-client.tsx:237`
- System badges everywhere (currently `borderRadius: '2px'`, change to `var(--radius-sm)`)
- [ ] Done

##### F-1.2 -- Add card elevation shadow tokens to globals.css
Add to `:root`:
```css
--shadow-card: 0 1px 3px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.04);
--shadow-elevated: 0 4px 12px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.06);
```
Apply `box-shadow: var(--shadow-card)` to all card surfaces. The outer cheat
sheet card at `cheat-sheet-client.tsx:242` already has a green glow shadow --
keep that AND add the base elevation.
- [ ] Done

##### F-1.3 -- Add hover elevation to interactive cards
Any card with an `href` or `onClick` should have:
```css
transition: transform .15s ease, box-shadow .15s ease;
```
On hover: `transform: translateY(-1px); box-shadow: var(--shadow-elevated)`.
Files: `picks-table.tsx:121`, `models-grid.tsx:68`, `cheat-sheet-client.tsx:72`.
- [ ] Done

##### F-1.4 -- Add confidence tier color tokens
Add to `:root` in `globals.css` AND export from `lib/tokens.ts`:
```css
--strong: #22c55e;   /* Beezy Score 65+ */
--lean:   #facc15;   /* Beezy Score 40-64 */
--watch:  #94a3b8;   /* Beezy Score <40 */
```
These are separate from system colors. System colors identify WHAT a pick is.
Confidence colors show HOW MUCH to trust it.
- [ ] Done

##### F-1.5 -- Emissive glow keyed to confidence tier (not system)
Per pick card, apply a box shadow based on the pick's Beezy Score tier:
```css
/* Strong (65+) */
box-shadow: 0 0 0 1px rgba(34,197,94,.25), 0 0 20px rgba(34,197,94,.10);
/* Lean (40-64) */
box-shadow: 0 0 0 1px rgba(250,204,21,.25), 0 0 20px rgba(250,204,21,.08);
/* Watch / no score */
box-shadow: var(--shadow-card);
```
Apply in `cheat-sheet-client.tsx:72` on the pick card outer div. Move the
current static green glow (on the wrapper at line 242) inside to per-card.
- [ ] Done

#### Sprint 2 -- Composite Beezy Score (most important product gap)

Mongoose's `77 STRONG PLAY` / `61 LEAN PLAY` pattern is a single composite score
(0-100) that abstracts edge %, Kelly trigger, odds value, and model vs. market
gap into one emotionally legible signal. This is the #1 reason Mongoose feels
more accessible than Beezy to non-quant bettors.

##### F-2.1 -- Create `beezy-vip/lib/beezy-score.ts`
```typescript
import type { Bet } from './types'

export function beezyscore(bet: Bet): number {
  const edgePoints = Math.min(((bet.edge ?? 0) * 100) * 4, 40)
  const kellyPoints = bet.kelly_triggered ? 20 : 10
  const oddsPoints = (bet.odds ?? -200) > -130 ? 20
                   : (bet.odds ?? -200) > -180 ? 12 : 6
  const probGap = ((bet.model_prob ?? 0) - (bet.market_prob ?? 0)) * 100
  const probPoints = Math.min(Math.max(probGap * 2, 0), 20)
  return Math.round(Math.max(0, Math.min(100, edgePoints + kellyPoints + oddsPoints + probPoints)))
}

export type ScoreTier = 'strong' | 'lean' | 'watch'

export function scoreTier(score: number): ScoreTier {
  if (score >= 65) return 'strong'
  if (score >= 40) return 'lean'
  return 'watch'
}

export const TIER_COLOR: Record<ScoreTier, string> = {
  strong: '#22c55e',
  lean:   '#facc15',
  watch:  '#94a3b8',
}

export const TIER_LABEL: Record<ScoreTier, string> = {
  strong: 'STRONG PLAY',
  lean:   'LEAN PLAY',
  watch:  'WATCH',
}
```
Tune the weights by backtesting against `results` -- the formula is a starting
point.
- [ ] Done

##### F-2.2 -- Add ScoreBadge component to `components/ui/primitives.tsx`
Component renders score number (large, tier color) above tier label badge.
- [ ] Done

##### F-2.3 -- Wire ScoreBadge into cheat sheet pick cards
`beezy-vip/app/cheat-sheet/cheat-sheet-client.tsx:188-200`. Replace current
`EDGE` side panel with two-row panel: score number + STRONG/LEAN/WATCH badge.
Keep edge value as smaller secondary label. Apply tier glow (F-1.5) to card border.
- [ ] Done

##### F-2.4 -- Wire ScoreBadge into dashboard pending picks
`beezy-vip/app/dashboard/picks/page.tsx:54-55`. Add `Score` column as FIRST
column. Sort picks by score descending by default.
- [ ] Done

##### F-2.5 -- Wire score onto landing recent picks table
`beezy-vip/components/landing/recent-picks-table.tsx`. Add score + tier badge
column. This is social proof for visitors.
- [ ] Done

#### Sprint 4+5 -- Today view + Visualization (remaining items)

##### F-7.3 -- Better empty state on cheat sheet
`beezy-vip/app/cheat-sheet/cheat-sheet-client.tsx:322`. Current empty state is
a dead end. Replace with yesterday's top 3 settled results + win/loss summary
+ expected next update time. Requires passing yesterday's picks as a prop from
the server component.
- [ ] Done

##### F-7.4 -- Share button full integration on cheat sheet
`beezy-vip/app/cheat-sheet/cheat-sheet-client.tsx:54`. Share button skeleton
shipped in Sprint 4+5 with Web Share API + download fallback. Full integration
calls `/api/og/picks-card` with the pick's ID to generate the OG card image.
This is the social flywheel -- make Discord screenshots one tap.
- [ ] Done

#### Sprint order recommendation

Sprint 0 (bugs F-B1, F-B2, F-B3) -- 1-2 days. Trust-breaking; fix immediately.
Sprint 1 (design tokens F-1.1 through F-1.5) -- 1 week. Changes every surface.
Sprint 2 (Beezy Score F-2.1 through F-2.5) -- 1 week. Closes the philosophical
gap with Mongoose -- the single change that makes the product feel like a
recommendation engine rather than a data dump.

The fastest path to closing the gap with Mongoose: **Sprints 0-2**.

### 16.4 Completed (archive)

#### Model remediation (T-series)

- **T01** Fix Kelly formula -- `mlb_core/odds/utils.py`. [x] 2026-05-19
- **T02** Remove `implied_win_pct` from K_FEATURES. [x] 2026-05-19
- **T03** Remove market-derived features from HR. [x] 2026-05-19
- **T04** Fix NRFI isotonic calibrator leakage -- train-only fit. [x] 2026-05-19
- **T05** Fix calibrator fit in HR, F5, K calibration scripts. [x] 2026-05-19
- **T06** Reconcile `top3_batter_*` feature contract. [x] 2026-05-19
- **T07** Fix in-sample innings-window scalar fit -- walk-forward by year. [x] 2026-05-19
- **T08** Add CLV tracking (closing_odds, closing_implied_prob, clv_pct columns + capture_closing_lines.py + monitor_performance). [x] 2026-05-19
- **T09** Port K leakage check to NRFI. [x] 2026-05-19
- **T10** Add fold-by-fold dispersion to retrain output (cv_folds, cv_mean_auc, cv_std_auc, cv_auc_ci_lo/hi in model_meta). [x] 2026-05-19
- **T11** Migrate HR and F5 training out of notebooks (`retrain_hr_v6.py`, `retrain_f5_v5.py`). [x] 2026-05-19
- **T13** Add regime indicator feature (`post_pitch_clock`). [x] 2026-05-19
- **T14** Add PSI drift monitoring (`mlb/runners/monitor_drift.py`, Monday cron). [x] 2026-05-19
- **T15** Per-book performance breakdown in monitor_performance.py. [x] 2026-05-19
- **T16** Add F5 bullpen features (`bullpen_xfip_L30`, `bullpen_k_pct_L30`, `bullpen_xwoba_L30`). [x] 2026-05-19
- **T20** Odds math test coverage (`tests/test_odds_math.py`). [x] 2026-05-19

#### Engineering (E-series)

- **E01** Fix K fair probability calculation -- closed (not a bug; historical pre-T01 edge values inflating avg_edge).
- **E02** F5 CV loop C03 leak -- `retrain_f5_v5.py` CV loop now carves val from train. [x] 2026-05-21
- **E03** CLV pipeline bugs -- two rounds of fixes (2026-05-21, 2026-05-22). bt_upper rename, real MLB_DB_URL, SGO team name lookup, extractor list vs dict, public_api CLV columns. CLV capturing from 2026-05-22 evening bets onward. [x] 2026-05-22
- **E04** Build OUTS as proper regression model (`mlb/training/retrain_outs_v1.py`, NegBin count, Cloud Run Job `mlb-retrain-outs-v1`). [x] 2026-05-21
- **E06** Fix deploy script traffic routing (`--traffic=100` + missing Discord webhook secrets). [x] 2026-05-21
- **E09** Hyperparameter tuning via Optuna -- script written (`mlb/training/tune_hyperparams.py`). [x] 2026-05-21. Per-system invocation tracked separately under T18.
- **E10** Pre-game line movement feature (`bets.morning_odds`, `bets.line_move_pct`, `mlb_core/odds/line_movement.py`, all 4 runners load morning snapshot). [x] 2026-05-21
- **E12** BATTER_HITS first-run sequence (build + retrain + calibrate Cloud Run Jobs, all wired into main.py). [x] 2026-05-24

#### Post-audit bug fixes (F-series, 2026-05-19)

Beyond the T01-T20 backlog. All complete.

- **F01** HR vig formula centralised (`devig_unilateral`).
- **F02** SQL injection in /dashboard and /reset-bets (whitelist + bound params + X-API-Key auth).
- **F03** Retractable roof always set to is_outdoor=1.
- **F04** F5 calibrator applied without boundary check.
- **F05** `_norm` defined three times in settle_bets.py.
- **F06** K/OUTS push grading on integer lines.
- **F07** CLV arithmetic used vig-inclusive probabilities.
- **F08** Missing endpoints for T08/T14 scripts (/capture-closing, /monitor-drift).
- **F09** SGO snapshot staleness -- `check_snapshot_freshness` + runner aborts.
- **F10** Morning/evening bet deduplication via kelly_triggered parameter.
- **F11** Settlement parallelised via ThreadPoolExecutor.
- **F12** `post_pitch_clock` added to explicit feature lists.
- **F13** `ump_tight_zone` thresholds use `expanding().quantile()`.
- **F14** HR name matching: difflib fuzzy fallback.
- **F15** `build_batter_rolling` / `build_pitcher_features` accept `run_date`.
- **F16** Weather fetch has 4-attempt exponential backoff.
- **F17** Settlement grading test coverage (35 test cases in `tests/test_settlement.py`).

#### Frontend UX (F-series, 2026-05-25)

- **Sprint 3 -- Mobile shell.** `components/layout/bottom-nav.tsx` (NEW), `app/layout.tsx` ClerkProvider wrapper + BottomNav render, `app/globals.css` `.mobile-only` rules + body padding, `components/layout/nav.tsx` mobile collapse to logo + auth only.
- **Sprints 4+5 -- Today view + Visualization.** `app/cheat-sheet/cheat-sheet-client.tsx` per-card expand + summary row + chip filter tabs + edge magnitude coloring + share button + empty state. `app/cheat-sheet/page.tsx` fetches yesterday's settled picks. `components/today/slate-strip.tsx` (NEW). `app/dashboard/picks/page.tsx` ProbBar replaces 3 plain-text columns. `components/landing/system-sparkline.tsx` (NEW). `components/landing/models-grid.tsx` per-system sparklines. `app/results/results-client.tsx` edge chart filtered. `lib/betting-api.ts` `apiGetSparklineBySystem`.
- **Sprints 6+7 -- Table UX + Polish.** `components/picks/picks-table.tsx` clickable sort headers + pagination + notes as bullets. `app/results/results-client.tsx` clickable sort headers + pagination + per-system counts on chips + date filter via useSearchParams. `components/picks/date-bar.tsx` (NEW). `components/layout/live-ticker.tsx` pinned label. `components/landing/hero.tsx` primary CTA changed. `app/page.tsx` DiscordCTA removed.

#### Dell 1996 design unification (2026-06-10)

Full site-wide replacement of the terminal-palette color set with Dell 1996 catalog tints.
~30 files edited across two sessions. Key commits: `4559b28` (palette sweep), `46f69db` (font + hero + nav polish).

- **Palette sweep.** All `#10b981` -> sage `#b3bd95`, `#ef4444` -> salmon `#d77a7a`, `#3b82f6` -> sky `#9ab6c8`, `#f59e0b` -> peach/yellow, `#71717a` -> muted `#888890`. All `0.5px solid` borders -> `1px solid`. All `borderRadius: var(--radius-sm)` -> `0`. Across: all page routes, picks/results tables, filter bar, date bar, cheat sheet, slate, CLV tracker, tools (Kelly, edge finder, odds calc, bet tracker, slate), models, learn, landing (hero, sparkline, models grid), auth pages, dashboard, error/not-found pages, copy-bet button, bankroll input, checkout button, footer.
- **Font fix.** `.times` CSS class changed from `'Times New Roman'`-first to `Georgia`-first (`Georgia, 'Times New Roman', Times, serif`). Georgia is a universally available system font -- eliminates Vercel edge rendering failures. Same fix applied to footer nav links. `app/globals.css` duplicate `.article-body code` rule cleaned up; stray `#10b981` removed; pre `border-radius: 4px` -> `0`.
- **Hero panel.** `components/landing/hero.tsx` background changed from loud red `#c91525` to dark green `#0e1f0d`.
- **Nav centering + logo placeholder.** `components/layout/nav.tsx` logo section gets `flex: 1` and a 28px dashed placeholder box (replace with final `<img>` when mark is ready). Right section gets `flex: 1, justifyContent: flex-end` so nav links are visually centered between equal-weight flanks.
- **Pitcher headshot resilience.** `cheat-sheet-client.tsx` `<img>` for headshots now has `onError` to silently hide broken URLs instead of showing a broken-image icon.

---

## 17. Pointers to other docs

- `RUNBOOKS.md` -- common manual actions (gcloud/curl fragments), Claude Code workflow, social media pipeline
- `handoffs/` -- dated session handoff files for point-in-time state (open bugs, in-flight migrations, session findings)
- `ipynb_CONTEXT` -- modeling theory + per-notebook summaries
- `deploy/RETRAIN_NOTES.md` -- retrain pipeline runbook + rollback
- The notebooks (`*.ipynb`) -- canonical modeling logic

---

## 18. When to update this file

- Adding/removing a system -> §1, §2, §3
- Changing a contract -> §5
- New market or bet type -> §5 (bet type table + settlement table) + §10 (DK grading rules)
- New gotcha -> §15 (place in the right subsection)
- New infra -> §7
- Changes to file layout -> §2
- Performance monitor threshold change -> §11
- Ops monitor check change -> §12
- Scheduler job added/removed -> §9 (monitor_ops' health check needs no update -- it enumerates the API directly, no allowlist)
- SGO API change -> §8
- DK house rule change -> §10
- Discord server change -> §14
- Frontend page/route/contract change -> §13
- New backlog item -> §16 (T/E/F series as appropriate)
- Backlog item completed -> §16.4 Archive

**Don't put point-in-time state here.** That belongs in `handoffs/`.
**Don't put runbook fragments here.** That belongs in `RUNBOOKS.md`.

---

## 19. NBA (data-only, pre-modeling)

Basketball expansion, **data-collection-first**. Source: SportsBlaze
(`https://cache.sportsblaze.com`, no auth). There is **no NBA model, odds feed,
or betting runner yet** -- this is raw box-score ingest only. Odds sourcing is a
separate, later effort. Full detail lives in `nba/README.md`; plan + decisions in
`handoffs/scope_nba_expansion_2026-06-14.md`; API quirks in
`docs/solutions/integration-issues/sportsblaze-nba.md`.

- **Code:** `nba/` package (additive; reuses `mlb_core.storage`; MLB untouched).
  `nba/data/{sportsblaze,flatten,masters,backfill,refresh,kaggle_ingest}.py`;
  `nba/odds/` -- odds layer (reuses `mlb_core.odds.utils` for edge/Kelly/de-vig):
  `parlayapi.py`+`parlay_extract.py`+`accumulator.py` (**ParlayAPI = chosen provider**;
  sport-agnostic; banks live props/game-lines forward to `OddsAccum/{sport}/` since no
  historical-props API exists); `theoddsapi.py`+`extract.py`+`snapshot.py` (The Odds API
  alternate). Accumulator Job via `deploy/setup_parlay_accumulator.sh`. Full pipeline
  plan (features/models/edge/parlay/backtest, mostly DEFERRED) in `nba/BLUEPRINT.md`.
- **Data lake:** shared bucket under `NBA/` prefix -- `raw/boxscores/{date}.json`
  (idempotent cache), `games_master.csv`, `team_boxscores_master.csv`,
  `player_boxscores_master.csv`, `last_refresh.json`. 8 seasons available
  (2018-19..2025-26); 7 backfilled (2019-25): 9,186 games / 18,372 team /
  315,331 player rows.
- **Nightly ingest:** Cloud Run Job `nba-refresh-data` + Cloud Scheduler
  `nba-refresh-data` (daily 13:00 UTC, OAuth + Run API trigger, see §9). Runs
  year-round; no-ops on empty days (offseason self-healing). Provisioned by
  `deploy/setup_nba_refresh.sh`. **Not** wired into `monitor_ops` yet (MLB-keyed).
- **Deep history (Kaggle):** Cloud Run Job `nba-kaggle-ingest`
  (`nba/data/kaggle_ingest.py`) mirrors the eoinamoore historical NBA dataset
  (stats.nba.com lineage: games/team/player box + play-by-play + advanced stats,
  1947-today, **no odds**) into `NBA/stats_nba/raw/`. Slow overnight batch;
  kagglehub auth via Secret Manager `kaggle-username`/`kaggle-key`. Provisioned by
  `deploy/setup_nba_kaggle_ingest.sh` (16Gi). Live-reproducible advanced-feature
  source matching the historical training parquet.

When NBA gains odds + a model it graduates to the full "adding a new system"
checklist (§6); until then keep it out of the MLB registry/monitors.
