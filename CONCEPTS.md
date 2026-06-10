# Concepts

Shared domain vocabulary for the mlb-betting project. Glossary only -- not a spec or catch-all. For authoritative behavioral contracts, see CONTEXT.md. For past bugs and gotchas, see `docs/solutions/`.

---

## Betting systems

### System
One betting model targeting one market. Identified by a string key (`NRFI`, `HR`, `F5`, `K`, `OUTS`, `BATTER_TB`, `BATTER_HITS`, `GAME`, `F1H`, `PITCHER_ER`). Registered in `mlb_core/registry.py` which drives monitor, discord, and settlement loops. `main.py` has parallel hardcoded lists that must also be updated when adding a new system.

### Paper mode
All systems run in paper mode until each clears a 200-settled-bet gate with positive ROI, calibrated hit rate, and positive CLV. Paper mode logs bets to the DB with real Kelly stakes for sizing, but does not execute real wagers. `LOG_ONLY = True` in runners indicates the system is paper-only.

### kelly_triggered
Boolean on each logged bet row. `True` means the prediction cleared `min_edge` and received a non-zero stake. `False` rows have `stake=0` and are logged for model monitoring only. `BetTracker.summary()` counts only `kelly_triggered=True` rows for P&L stats.

### Kelly fraction
The fraction of the Kelly criterion stake actually wagered. All systems currently use 50% Kelly (half-Kelly) to account for model uncertainty. `kelly_stake()` in `mlb_core/odds/utils.py`.

### Exposure cap
Per-game, per-system risk limit enforced by `mlb_core/risk/exposure.py`. `prefetch_exposure()` + `apply_cap()` prevent multiple bets in the same game from exceeding the configured bankroll fraction. Cross-system exposure is independent (NRFI and HR on the same game do not compound each other's cap).

### Build sentinel
`{system_prefix}/data/last_build.json` in GCS. Written by each feature builder on success. `monitor_ops` checks freshness at 15:20 UTC. `check_build_sentinel()` in `mlb_core/storage.py` is called by runners at scoring time to abort on stale features.

---

## Odds and markets

### SGO (Sports Game Odds)
The third-party odds API (`api.sportsgameodds.com`). Client in `mlb_core/odds/sgo.py`. Amateur tier: 10 req/min, 2,500 entities/month. Snapshots stored in `Odds/sgo/latest.json` on GCS; runners always read from this snapshot, never call SGO directly.

### NRFI / YRFI
No Run First Inning / Yes Run First Inning. The binary outcome for whether any run scores in the top+bottom of inning 1. The NRFI system bets the O/U market and the 3-way 1st inning ML (AWAY/HOME/DRAW). Model is `xgb_halfinn_v18.json` predicting P(YRFI).

### F5
First 5 innings moneyline. Bets on which team leads after 5 innings. Model is `xgb_f5_v5.json`. Dependency: F5 feature builder reads NRFI's `pitcher_start_features.csv`, so NRFI must build before F5.

### CLV (Closing Line Value)
The difference between the odds at which a bet was placed and the closing line. Positive CLV indicates the bet was placed at better odds than the market settled on -- the primary indicator of long-term edge. `clv_pct` in the `bets` table. Captured by `capture_closing_lines.py` at midnight.

### Implied probability / fair probability
Implied prob = 1 / decimal_odds (includes book's vig). Fair prob = implied prob with vig removed (zero-sum). `american_to_implied_prob()` and `remove_vig()` in `mlb_core/odds/utils.py`. CLV arithmetic uses fair probs.

### ONSHORE_BOOKS
The set of legal US sportsbooks used for best-odds selection: `{draftkings, fanduel, caesars, betmgm, espnbet, thescore, pointsbet}`. `_best_book_odds_for_line()` in `sgo.py` picks the best price among these for the canonical line (anchored from the highest-priority book, DK first).

---

## Models and features

### XGBoost booster
The trained gradient-boosted tree model. Stored as `xgb_{system}_v{N}.json` in GCS. Loaded via `xgb.Booster(); booster.load_model(path)`. Always use `iteration_range=(0, ntree_limit)` pattern for prediction; do not pass `None`.

### model_meta
JSON file paired with each booster: `model_meta_{system}_v{N}.json`. Contains at minimum: `version`, `features` (list), `feature_means` (dict), `best_iteration`, `auc_oos`. **Never hardcode a feature list in a runner** -- always load from meta.

### feature_means
The training-set mean for each feature, stored in `model_meta`. XGBoost uses these to impute NaN values at scoring time. If `feature_means` contains wrong values (e.g. from a training bug), the imputation is incorrect and model output degrades for all rows with missing features.

### Isotonic calibrator
A monotone post-hoc probability calibrator (sklearn `IsotonicRegression`) fit on OOS model outputs. Maps raw XGBoost scores to calibrated probabilities. Always refit after any model artifact change. NRFI calibrator is fit on YRFI scores -- apply it to `model_yrfi_prob`, not `model_nrfi_prob`.

### NegBin (Negative Binomial)
The distribution used for count-outcome props (K strikeouts, batter hits, batter total bases, pitcher outs). Parameterized by `lambda` (expected count) and `nb_alpha` (overdispersion). `CDF(line | lambda, alpha)` gives the probability of the count being under/over the line. `nb_alpha` is stored in `model_meta`.

### lambda
In the context of K/BATTER_HITS/BATTER_TB/OUTS runners: the expected count output from the XGBoost Poisson regressor, calibrated by an isotonic lambda calibrator. Not a Python lambda function.

### PSI (Population Stability Index)
Feature drift metric. PSI > 0.25 on a top-10 feature triggers a `monitor_drift` alert. Computed by `monitor_drift.py` weekly using `feature_dists` percentiles stored in model meta.

---

## Data sources

### Statcast master
`Statcast/statcast_master.csv` in GCS. One row per plate appearance (PA-level, not pitch-level). ~963k rows, 2021-present. Columns like `pitch_number` do not exist. `len(group)` counts PAs, not pitches.

### Scoring master
`Scoring/scoring_master.csv`. Per-(game_pk, inning, half) run counts. Authoritative source for all run-based targets. Updated nightly by `mlb-refresh-data`.

### Savant leaderboards
Six Baseball Savant leaderboard datasets: `exit_velocity_barrels`, `expected_statistics`, `pitch_arsenals`, `sprint_speed`, `bat_tracking`, `batter_arsenal_stats`. Fetched by `savant_leaderboards.py`. Note: `pitch_arsenals` uses `pitcher` as the MLBAM ID column (not `player_id` like the others).

### AuxData
`AuxData/` in GCS. B-Ref pitching stats (`bref_pitching_master.csv`), swing/take run values (`swing_take_master.csv` -- batter MLBAM IDs only), team schedule features (`team_schedule_master.csv`), manager hook tendencies (`manager_hooks_master.csv`). Joined via `mlb_core/data/aux_joins.py`.

---

## Infrastructure

### GCS bucket
`concrete-crow-445205-m4-mlb-data`. All data and model artifacts. Access via `mlb_core/storage.py` wrappers (`read_csv`, `write_csv`, `read_bytes`, `write_bytes`, `exists`). Never call the GCS client directly from runners.

### Cloud Run service
`mlb-betting`. Flask + gunicorn, max 1 instance, timeout=3600s. Handles `/run`, `/settle`, `/build-features`, `/snapshot-odds`, etc. Image: `gcr.io/concrete-crow-445205-m4/mlb-betting:latest`.

### Cloud Run Jobs
Separate job executions for feature builds (`mlb-build-all-features`), retrains, and calibrations. Same Docker image as the service. Jobs need `if __name__ == "__main__":` blocks and `--args` repeated flags (not comma-separated).

### Secret Manager
Holds: `mlb-db-url`, `mlb-gcs-bucket`, `sgo-api-key` (version 3 is current), `discord-webhook-url`, and others. Read via `mlb_core/config.py`. Never read env vars directly with `os.environ["DB_URL"]` -- the var is `MLB_DB_URL`.

### Beezy.FYI
The public frontend at `beezy.fyi`. Next.js 16 / React 19, deployed on Vercel. Read-only -- connects to Cloud Run public API, never to Cloud SQL directly. Dell 1996 design system: no Tailwind utility classes for layout/color, pure inline styles only.
