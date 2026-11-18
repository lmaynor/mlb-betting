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

---

## Bankroll Challenge (play-money contest)

A private, invite-only contest, separate from the betting systems above: no real money, no models, bragging rights only. Contract and decisions: CONTEXT.md section 28, `docs/designs/bowl-bankroll-contest-scope.md`, runbook in `docs/solutions/conventions/contest-launch-and-rollback-runbook.md`.

### Bankroll (contest)
Balance plus stakes still at risk. Starts at $5,000, bust = out. The balance is a cached sum of an append-only ledger (`contest_ledger`); `store.reconcile` must return `[]`. Money is integer cents everywhere.

### Hidden picks
A player's picks are invisible to everyone else until each game kicks off. Enforced on the server by `store.visible_bets`; the UI only renders what the API sends. A halftime bet stays hidden until its window closes.

### Slip kinds
Straight, parlay, teaser (spreads / totals moved by points, fixed payout table), round robin (every k-leg parlay of N picks, up to 11, each its own bet under one group id), and same-game parlay.

### Alt line
The same game at a different half-point number, priced by the contest from the main line's de-vigged two-sided price through the margin model plus a margin. Spreads and totals only; never inside a teaser or a same-game parlay.

### Margin model
`contest/margin_model.py`: a discretised normal for the final margin (or total), refit so it reproduces the board's de-vigged probability, with key-number weights on football spreads. Prices alt lines and moves a held line to the current one for cash out. Sigmas come from closing lines (football 15.0 / 15.5, basketball 11.1 / 17.2). UNVALIDATED.

### Key number
A final football margin that occurs far more often than a normal curve says (3 is about 3.8x its neighbours, 7 about 3.0x). Moving a line across one is worth more than the same distance elsewhere. The out-of-sample test could not show the weights help; they are kept so buying through the 3 is not free.

### Same-game parlay (SGP)
Two legs on one game (a spread or the moneyline with the total) priced as a PAIR with a Gaussian copula at the measured correlation of the two events (football +/-0.17 for spread x total, basketball +/-0.02), then a margin. The ticket price is quoted by the server and stored on the bet (`contest_bet.ticket_price`). No cash out.

### Cash out
Selling an open pregame straight or parlay back: payout x P(every leg still wins now) x (1 - 5%), rounded down. Can exceed the stake when the line moved your way. `VALUE_CHANGED` if the board moved since the quote.

### Lifeline (blackjack)
A hidden blackjack table that opens only while the bankroll is under $100. Server-held deck, 3:2 blackjack, winnings capped so the bankroll never rises above $100 through it. A player is eliminated only when they cannot afford the $1 chip.

### Halftime window
A short window, while ESPN reports halftime, when straight bets are taken on the full game at Kalshi-derived prices (mid plus a margin, markets wider than 6 cents not offered). Opens after the score is unchanged across two polls and a post-halftime quote exists; closes on a score change, a stale quote, 8 minutes, or leaving halftime. OFF by default; needs a contest rule and `CONTEST_HALFTIME_ENABLED`.

### Organiser
A Clerk user with `privateMetadata.contest.roles.<slug> = "admin"`: gets `/contest/admin` (rename, adjust, void, settle by hand, invites, open / close, feature switches; every action is logged in `contest_admin_log`). A participant's role is a mirror of their `contest_entry` row; the database is the authority.

