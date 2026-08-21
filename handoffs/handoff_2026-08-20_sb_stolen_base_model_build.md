# Handoff -- 2026-08-20 -- SB (stolen base) model: full build

Picked up from `handoffs/scope_stolen_base_model_2026-08-20.md` (the same-day
scoping doc, itself following on from a live ParlayAPI probe that resolved
its biggest open risk). User asked to build the entire system end to end --
data pipeline, odds, model, training, backtest, live runner, frontend,
Discord -- not just plan it. **Code is complete, checked out on branch
`feat/stolen-base-model-2026-08-20`, but still entirely UNCOMMITTED working-
tree changes (29 modified + 12 new files, `git status --porcelain` for the
full list) -- not committed, not pushed, NOT merged to main, NOT deployed.**
This session did not touch prod infrastructure (no Cloud Run Jobs created/
run, no schedulers provisioned) -- GCS writes were made (real historical
masters backfilled, see below), which is additive/safe and required to have
anything to train against.

## TL;DR

- All 16 planned phases complete: data plumbing, odds extraction, settlement,
  feature builder, system registration, model training + calibration,
  walk-forward backtest, live runner, `main.py` wiring, frontend, Discord/
  monitoring (registry-driven, needed no code), deployment scripts (prepped,
  not run).
- **Real historical backfills completed, not synthetic**: 195,679 batter-game
  rows / 9,331 games (2023-03-30 -> 2026-08-19) of real stolen-base/
  caught-stealing outcomes from MLB Stats API boxscores (statcast cannot see
  these events at all -- confirmed live, see the scope doc), plus catcher
  identity for 9,336 games (92.3% with both sides resolved) and catcher pop
  time/arm strength for 2015-2026 (946 catcher-seasons).
- **Model trained on real data**: NegBin count regressor, 22 features, walk-
  forward CV stable across 2024/2025/2026 folds (MAE 0.11-0.13, R² 0.045-0.047
  -- low in absolute terms but that's normal for this class of prop model,
  matches OUTS v1's R²=0.042), OOS calibration bias +0.0018 (essentially
  unbiased raw). `nb_alpha` hit the top of its clip range (0.50) -- stolen
  bases are a very rare, heavily zero-inflated count; worth revisiting the
  clip ceiling if a future retrain shows the same saturation.
- **3 real bugs found and fixed via live testing, not theoretical review**:
  a pre-existing crash in shared `game_result.py` (empty pitcher list --
  could in principle have hit any system's real nightly settlement, not
  just this backfill), a market-mapping gap in `parlayapi_to_history.py`
  (caught by the existing test suite), and a column-collision bug in
  `run_sb.py`'s live scoring path (stale per-opponent catcher columns
  colliding with a fresh join -- would have silently fed the model garbage
  catcher data every single day in production had it shipped unfixed).
- **Backtest verdict: NO_EDGE.** 341 real historical bets across 19 rolling
  monthly OOS windows (2024-04 -> 2026-08): ROI -3.05% to -3.13%, CLV
  -1.63%. Evaluated on CLV per this repo's own rule
  (`docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md`),
  not ROI alone -- here they **agree** (both negative), which is the clean
  "no real edge" case, not the "positive ROI / negative CLV" soft-line
  mirage the rule was written to catch. Same bucket as K/OUTS/BATTER_HITS.
  `LOG_ONLY = True` stays as shipped; this is a reason to keep it, not a
  reason the build failed -- the plumbing is what was asked for, and now
  there's a real, honest answer instead of a guess.
- Two out-of-scope issues found by the frontend sub-agent and spawned as
  separate background-task chips (not fixed here, deliberately): a stale
  `CONTEXT.md` §13 color-palette description (the frontend was redesigned off
  Dell-1996 on 2026-06-28, per `[[project_terminal_redesign_2026-06-28]]`
  memory -- §13 was apparently never updated to match, a pre-existing gap
  unrelated to this session), and a hardcoded K/OUTS-only branch in
  `app/models/[slug]/page.tsx` that renders a misleading "OOS AUC: 0.000" for
  any other system including the brand-new SB.

## Why this took as long as it did

Two real historical backfills (~9,500 games each, one via 3 MLB Stats API
calls/game for box scores, one via 1 call/game for lineups) had to run before
there was anything real to train on -- statcast_master.csv cannot supply
stolen-base outcomes at all (see the scope doc's Statcast finding), so this
wasn't optional plumbing, it was the actual training-label source. Ran both
concurrently in the background while writing the rest of the system (odds
extractor, settlement, registry, runner, frontend dispatch) so the wall-clock
cost wasn't purely serial.

## What actually changed (files)

### New files
- `mlb_core/data/sb_boxscore.py` -- batter-game SB/CS labels from MLB Stats
  API boxscores (backfill + nightly-refresh functions, mirrors
  `mlb_core.data.scoring`'s checkpoint pattern).
- `scripts/backfill_sb_boxscore.py`, `scripts/backfill_catcher_identity.py` --
  one-time historical backfill drivers (already run once this session; safe
  to re-run, both are resumable/idempotent).
- `mlb/runners/build_sb_features.py`, `mlb/runners/run_sb.py`.
- `mlb/systems/SB_Pro_System/config_sb.py` (+ `__init__.py`).
- `mlb/training/retrain_sb_v1.py`, `mlb/training/calibrate_sb_v1.py`.
- `tests/test_game_result.py` (new regression coverage for the empty-pitchers
  crash + the team/caught_stealing additive fields).
- `handoffs/scope_stolen_base_model_2026-08-20.md` (same-day scoping doc,
  updated in place with the live ParlayAPI probe results).

### Modified files
- `mlb_core/data/game_result.py` -- added `team`, `caught_stealing` per
  batter/pitcher (additive); fixed the empty-pitchers-list crash.
- `mlb_core/data/lineups.py` -- new `get_starting_catchers()` +
  `catcher_backfill_gcs()`.
- `mlb_core/data/auxiliary_features.py` -- new catcher pop-time source
  (source 5), wired into the nightly loop.
- `mlb_core/data/aux_joins.py` -- new `join_catcher_aux()` (first 3-way join
  in this codebase).
- `mlb_core/odds/sgo.py` -- new `extract_stolen_base_odds()` (dual-path, NOT
  a call to the shared `_extract_player_ou_props()`) + `extract_stolen_base_alt_line_odds()`.
- `mlb_core/odds/parlay_adapter.py`, `nba/config.py` -- `player_stolen_bases`
  market wired in (confirmed live).
- `mlb/runners/settle_bets.py` -- SB added to `STAT_MAP`/`SYSTEM_MAP`/
  `BATTER_PROP_SYSTEMS`/`ALL_SYSTEMS`/the EV bet_type-sniffing prefix list.
- `mlb_core/registry.py`, `mlb_core/schemas.py`, `mlb_core/rationale.py` --
  SB entries (see scope doc for why `log_only=True` is set here even though
  the field isn't itself gate-enforcing -- `run_sb.py`'s own module flag is
  what actually gates).
- `mlb/runners/monitor_drift.py`, `mlb/training/tune_hyperparams.py` -- SB
  entries (both hand-maintained, NOT auto-derived from the registry --
  documented gotcha that's bitten 3 prior systems).
- `mlb/analysis/gen_preds.py`, `mlb/analysis/walkforward.py`,
  `mlb/analysis/parlayapi_to_history.py` -- SB wired into the backtest/
  analytics tooling. The `walkforward.py` fix was a real gap the test suite
  and a live run both caught independently (see Bugs below).
- `main.py` -- `VALID_SYSTEMS`, `DEFAULT_RUN_SYSTEMS`,
  `DEFAULT_FEATURE_BUILD_SYSTEMS`, `_run_system`, `build_features_handler`,
  `build_all_features_handler`'s builders dict, `/reset-and-run`'s
  `runner_map`, `/dashboard`'s systems list + `_VALID_SYSTEMS_DASH`. Verified
  `/model-health` and `/edge-analysis` need no changes -- both derive their
  system list from whatever `system` values exist in the `bets` table.
- `deploy/setup_model_jobs.sh`, `deploy/setup_build_all_features.sh`,
  `deploy/setup_betting_schedulers.sh` -- SB added to the relevant job/chain/
  body definitions. **Not executed** -- these provision real Cloud Run
  Jobs/schedulers and were deliberately left as a prep-only step.
- `CONTEXT.md` -- s1 systems table, s2 repo layout, s3 GCS layout, s5 bet
  type/settlement/SGO-coverage tables, s8 odds providers, s15 gotchas (all
  three bugs below).
- 6 files under `beezy-vip/` (frontend wiring -- see the sub-agent's report
  for exact diffs; tokens.ts, pick-systems.ts, model-specs.ts,
  picks-table.tsx, models-grid.tsx, globals.css).

## Bugs found and fixed (all via live testing against real data, not review)

1. **`game_result.py:160`, `team.get("pitchers", [None])[0]` IndexError on a
   real historical game whose boxscore has `"pitchers": []`** (empty list,
   not a missing key -- `.get()`'s default only covers the missing-key case).
   Crashed the SB boxscore backfill mid-run at ~4,500/9,478 games. Fixed to
   `(team.get("pitchers") or [None])[0]`. This function is shared by every
   settler (`fetch_game_result()`, called once per game_pk, cached across all
   systems in a settle run) -- flagging that a real nightly `/settle` run
   could in principle have hit this same crash on any system, it just hadn't
   yet. New regression test in `tests/test_game_result.py`.
2. **`parlayapi_to_history.py`'s `PARLAY_TO_HISTORY` dict had no
   `"stolen_bases"` entry**, caught immediately by the pre-existing
   `tests/test_parlayapi_to_history.py::test_all_parlay_markets_mapped` the
   moment `player_stolen_bases` was added to `PARLAY_PROP_MARKETS`. Fixed:
   `"stolen_bases": ("steals_ou", "SB")` -- deliberately the same market code
   BettingPros' own historical backfill already uses, so a backtest sees one
   unified history regardless of which provider sourced a given date.
3. **`run_sb.py`'s live candidate rows carried stale `catcher_*` columns
   from the batter's LAST game**, colliding with `join_catcher_aux()`'s
   pandas merge (`catcher_pop_2b_sba_x`/`_y` instead of a clean value) when
   re-joining TODAY's actual opponent. Found by literally running
   `_build_today_feature_rows` against today's real slate before assuming it
   worked. Fixed by dropping every `catcher_*` column before the join;
   `pitcher_sb_allowed`/`pitcher_cs_allowed` had the identical latent
   staleness (silently kept a prior opponent's value on a failed bref match)
   and were reset to NaN defensively even though they don't collide (plain
   dict-key assignment, not a merge). Full writeup in CONTEXT.md s15.6.
4. **`walkforward.py`'s `_resolve_contract()` had a hardcoded tuple of
   `*_FEATURES` attribute names to look for** (`K_FEATURES`,
   `BATTER_HITS_FEATURES`, etc.) that didn't include `SB_FEATURES` --
   `retrain_sb_v1.py` imports it correctly, but the walk-forward backtest
   tooling couldn't find it under an unlisted name. One-line fix.

## Verification performed

- `pytest tests/ -q` -- 622/622 passed (up from 589 at the last unrelated
  handoff; includes the new `test_game_result.py` file and extended
  `test_sgo_extractors.py`/`test_settlement.py` coverage for SB).
- `python3 -m compileall mlb mlb_core main.py nba scripts` -- clean.
- Feature builder, training, and calibration all run against the REAL
  backfilled data (not a synthetic smoke test) -- see the numbers above and
  the Appendix.
- Live-tested `run_sb.py`'s feature-row assembly against today's actual slate
  (2026-08-20, 9 real games, 135 real candidate rows) -- this is what caught
  bug #3 above.
- Did NOT run `deploy/deploy_service.sh` and did NOT deploy to Cloud Run --
  out of scope for this session per the task's own deployment-scripts note
  (prep only, explicit go-ahead required before executing).

## Loose threads / next session

- **Not merged, not deployed, not pushed.** Review the diff, then decide:
  merge to main, deploy the service, provision the 2 new Cloud Run Jobs
  (`mlb-retrain-sb-v1`, `mlb-calibrate-sb`) and the updated
  `mlb-build-all-features`/betting-scheduler bodies, via the already-updated
  `deploy/setup_*.sh` scripts.
- **200-settled-bet paper gate** -- `run_sb.py` ships `LOG_ONLY = True`.
  Flip only after real paper-mode volume + a clean CLV read, same bar as
  every other system.
- ~~Nightly refresh not wired for the two new backfilled masters~~ --
  **fixed same session**. `sb_nightly_gcs()` and the new
  `catcher_identity_nightly_gcs()` (added to `mlb_core/data/lineups.py`) are
  both now called from `/refresh-data` alongside `scoring_nightly_gcs()`.
  Live-smoke-tested against real yesterday's data (2026-08-19, 15 games) --
  both correctly no-op/update idempotently.
- **Optuna tuning wired but not run** (`tune_hyperparams.py`'s SB entry is
  ready; `mlb-retrain-sb-v1` shipped with default hyperparameters, matching
  how every other system's FIRST retrain also shipped untuned -- see T18
  backlog, still open for older systems too).
- **The two frontend follow-ups spawned as separate task chips** (stale
  `CONTEXT.md` §13 color docs; `app/models/[slug]/page.tsx`'s K/OUTS-only
  metric branch) -- not fixed here, deliberately out of scope.
- **`caughtStealing` is now pulled but `pitcher_cs_allowed`'s real predictive
  value is unverified** -- it's in the feature list and joined correctly, but
  no feature-importance/ablation pass was run to confirm it's pulling its
  weight versus `pitcher_sb_allowed` alone.
- **Backtest verdict: NO_EDGE** (ROI -3.05%, CLV -1.63%, 341 bets/19
  windows -- both agree, just not the direction anyone wants). See Appendix
  for the full breakdown. This is a reason to keep `LOG_ONLY`, not a reason
  to not ship the plumbing -- same posture as every other system that
  backtested flat.

## Appendix -- real numbers from this session

**Historical backfill:**
- SB boxscore: 195,679 batter-game rows / 9,331 games, 2023-03-30 -> 2026-08-19.
  13,297 real stolen bases, 3,637 real caught-stealing events.
- Catcher identity: 9,336 games, 8,620 (92.3%) with both sides' catcher resolved.
- Catcher pop time/arm strength: 946 catcher-seasons, 2015-2026 (real trend
  visible: league pop_2b_sba mean fell from 2.021s in 2015 to 1.945s in
  2026 -- catchers have gotten measurably faster, a good sanity check that
  this is genuine data).

**Feature build** (`SB_Pro_System/data/model_features.csv`, 181,216 rows):
- `sb_per_game_L20` coverage 97.3%.
- `sprint_speed_ft_sec`: 179,249 exact-year matches, 1,545 prior-year
  fallback, 422 league-median-filled.
- `pitcher_sb_allowed`/`pitcher_cs_allowed` (B-Ref, season-level): 14.9% NaN.
- `catcher_pop_2b_sba` (the new catcher join): 6.1% NaN -- once the catcher
  identity backfill completed (was 100% NaN in an earlier partial-data test
  run before the backfill finished).

**Training** (`retrain_sb_v1`, 22 features, `count:poisson`):
- Base rate: mean 0.0688 SB/batter-game, 6.28% of batter-games have >= 1 SB
  (a real, sane MLB base rate -- direct sanity check the target is correct).
- Walk-forward CV: 2024 MAE=0.1265/R²=0.047, 2025 MAE=0.1232/R²=0.047, 2026
  MAE=0.1144/R²=0.045 -- stable across years, no degradation.
- OOS (train <2026, test 2026): MAE=0.1152, RMSE=0.2571, R²=0.0451,
  cal=+0.0018, best_iteration=315.
- No leakage suspects.
- `nb_alpha=0.50` -- hit the top of the `[0.01, 0.50]` clip range. Stolen
  bases are heavily zero-inflated/overdispersed; worth checking whether the
  clip ceiling itself is the binding constraint on a future retrain with more
  data.

**Calibration** (`calibrate_sb_v1`, OOS split from 2025-09-24):
- Raw bias +0.0017, calibrated bias +0.0012 -- the model was already close to
  unbiased before calibration; isotonic gives a small additional MAE
  improvement (0.1146 -> 0.1135).

**Hyperparameter tuning** (`tune_hyperparams --system SB --n-trials 50`, run
after the NO_EDGE verdict below at user request, per house precedent that
tuning hasn't flipped K/OUTS/BATTER_HITS out of NO_EDGE either):
- Had to `pip install optuna` into `.venv_audit` first -- not actually
  installed despite the script assuming it is.
- Best trial: `max_depth` 4->3, `learning_rate` 0.03->0.0896,
  `min_child_weight` 20->5, `subsample` 0.8->0.654, `colsample_bytree`
  0.8->0.969, `reg_alpha` 1.0->0.0657, `reg_lambda` 3.0->1.831, `gamma`
  0.5->0.0056. Saved to GCS (`SB_Pro_System/models/sb_tuned_params.json`)
  and promoted into `retrain_sb_v1.py`'s static `XGB_PARAMS` (the tuning
  script's own "drop-in" convention) -- **note this promotion was required
  for the params to actually reach the backtest**: `walkforward.py` reads a
  retrain module's `XGB_PARAMS` as a static attribute, it never calls
  `run()`, so the GCS pickup logic inside `run()` alone would have been
  silently bypassed by the rolling backtest.
- Retrain with tuned params: OOS MAE 0.1152->0.1149, R^2 0.0451->0.0456,
  best_iteration 315->101 -- essentially unchanged predictive ceiling, just
  a more efficient fit (fewer rounds for the same result). `nb_alpha` still
  hit 0.50 (the zero-inflation finding is a property of the data, not the
  untuned hyperparameters). Calibration moved similarly (calibrated MAE
  0.1143 vs 0.1135, noise-level).

**Backtest** (`walkforward --system SB --rolling --start 2024-04-01 --end
2026-08-19 --min-books 2 --select consensus`, edge>=10%, real historical
odds from `odds_history` market=`steals_ou`):

- 29 monthly windows attempted; 10 errored with `bad split: holdout=0`
  (2024-10 -> 2025-02 and 2025-10 -> 2026-02, symmetric both years) --
  **not a bug**, this is the Nov-Feb MLB offseason plus thin/absent
  postseason SB-prop coverage in the historical odds feed, so those windows
  have zero real games/odds to score against. The other systems' WF_SYS
  backtests have the same seasonal gap; unrelated to SB specifically.
- **POOLED 341 bets across the 19 valid windows:**
  - ROI (consensus, no-soft-line baseline): **-3.05%** (z=-0.8)
  - ROI (best/soft-line, your actual strategy): **-3.13%** (z=-0.9)
  - win-rate 74.8%, avg n_books 35.2
  - **CLV: -1.63%** (adverse-selection check -- negative means the closing
    line moved away from these bets on average)
- By book depth: `n_books<=2` (n=36, thin markets) shows roi_cons=+0.60%/
  clv=-0.28% (near flat); `n_books>=4` (n=263, the vast majority) shows
  roi_cons=-5.31%/clv=-2.12% (worse than pooled). Tightening the book-count
  gate does not rescue the result -- it makes it worse, so this isn't a
  "not enough books" artifact.
- By entry odds: 94% of all bets (321/341) are heavily-juiced UNDER 0.5
  favorites (dec<1.5, i.e. betting that the player does NOT steal, which is
  correct most of the time in real MLB) at hit-rate 77.6% but ROI -3.27% --
  the vig on a short price eats the edge from the high win rate. Classic
  "right side of history math wrong side of the bet" pattern, not a
  data/label issue.

**Verdict (pre-tuning): NO_EDGE.** Applying this repo's rule from
`docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md` --
CLV must agree with ROI for a result to count as real, not a soft-line
artifact -- ROI (-3.05% to -3.13%) and CLV (-1.63%) **agree**, just not in
the direction anyone wants: both say no edge, consistently, rather than
the "positive ROI / negative CLV" mismatch that would flag a soft-line
mirage. This slots into the exact same bucket as K/OUTS/BATTER_HITS from
the 2026-07-23 profit review: no capturable model-vs-line edge on this
prop.

**Re-run after tuning, same command:** 328 bets across the same 19 windows
(down from 341 -- the tuned model's marginally different lambdas move a
handful of bets across the 10% edge threshold). ROI (consensus) -0.17%
(z=-0.0), ROI (best) -0.10% (z=-0.0) -- both now statistically
indistinguishable from breakeven, a real improvement over -3.05%/-3.13%.
**But CLV is -1.63%, identical to the pre-tuning run to two decimal
places**, and the larger/cleaner `n_books>=4` sub-sample (n=255, most of
the pool) stayed solidly negative in both runs (-5.31% pre-tuning,
-2.98% post-tuning). Per this repo's own rule, CLV is the go/no-go gate
specifically *because* it's lower-variance than ROI (this repo's
`mlb_core/risk/clv.py` docstring: "CLV converges far faster") -- a pooled
ROI drifting toward zero on a ~4%-different bet set, while CLV holds
exactly steady and negative, is the ROI number being noisy, not the edge
picture actually changing. The one bucket that swung hardest --
longshots, `dec>3.5`, n=9 -- flipped to ROI +59.44% alongside CLV
*worsening* to -10.45%: textbook small-n soft-line-artifact shape, not
signal.

**Verdict (post-tuning): still NO_EDGE.** Tuning improved the model's raw
fit efficiency (fewer boosting rounds for the same ceiling) and happened
to land on a slightly friendlier historical bet sample, but did not move
the CLV signal at all and did not create a real, tradeable edge.
**Recommendation unchanged: leave `LOG_ONLY = True` as shipped. Do not
graduate to live capital.** The tuned hyperparameters are still worth
keeping as the new default (strictly better model fit, no downside), which
is why they were promoted into `retrain_sb_v1.py` regardless of the
verdict. Let the 200-settled-bet paper gate accumulate real same-season
data before revisiting -- both backtest samples here (~330-340 bets,
mostly 2024) are thin enough that a materially different real-money
verdict after a full paper season wouldn't be shocking, just not something
to bet ahead of.
