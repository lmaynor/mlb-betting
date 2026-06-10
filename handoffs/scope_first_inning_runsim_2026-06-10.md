# Scope + requirements -- First-inning run-distribution spike (NRFI/YRFI)

_Authored 2026-06-10 by Opus 4.8 for handoff to Sonnet. This is a VALIDATION SPIKE, not a
production rollout. Read CONTEXT.md and this file in full before writing code._

---

## 0. One-paragraph thesis

The systems that beat the market (BATTER_HITS, K, OUTS) are all `count:poisson` rate models
scored by integrating a Negative-Binomial distribution against the offered O/U line. The systems
that fail (F5, NRFI, F1H, GAME) are `binary:logistic` classifiers that emit a single point
probability for a high-variance game/inning outcome. The failing models show AUC ~0.50 and
calibration error of -0.20 to -0.26 (model says 65-75%, actual ~48%), which the Kelly edge filter
then amplifies by selecting exactly the most-overconfident tail.

Hypothesis: NRFI is not a "team binary" problem -- it is an aggregation of two half-inning run
counts, each a pitcher-vs-top-of-order matchup, which is the *same* count paradigm that already
works. Rebuilding NRFI bottom-up (model runs-per-half-inning as a count, compose to a game-level
NRFI probability via the count distribution) should restore discrimination AND calibration.

This spike tests that hypothesis on the smallest, lowest-lineup-risk unit (the 1st inning) before
committing to rebuilding F5/F1H/GAME the same way. Negative results are valuable -- document them.

---

## 1. Success criteria (the decision gate)

Evaluate the new model against the current `binary:logistic` v18 baseline on the SAME out-of-sample
holdout (do not re-split; reuse the v18 70/10/20 temporal split so the comparison is apples-to-apples).

Ship-it gate -- ALL must hold on the OOS test slice:
1. OOS AUC for P(YRFI) >= 0.55 (current production: 0.498-0.547).
2. Calibration error |hit_rate - mean_pred| < 0.05 in each of 5 probability bins (current: -0.26).
3. Brier skill score > 0 vs the YRFI base-rate naive model (current: negative).
4. Reliability curve monotone and near-diagonal (plot it; attach to the writeup).

The statistical gate (1-4) is the REAL gate and does not depend on odds history. The backtest
below is supplementary and is itself gated on sample size.

If the gate fails, STOP, write up why (was it discrimination, calibration, or data?), and do NOT
wire anything into production.

### 1b. Backtest (supplementary, sample-size gated)

We only have ~1 month of captured odds, so a paper-ROI backtest is underpowered and must not be
treated as pass/fail. Build the logic but GATE it on datapoint count:

Odds source -- use the SGO snapshot archive, NOT the `bets` table (the bets table only contains
games the OLD model liked, a biased sample). For each date in the OOS test slice:
- `load_snapshot("Odds/sgo/{date}/snapshot_{HHMM}.json")` (mlb_core/odds/sgo.py:281), prefer the
  pregame/closing snapshot of the day.
- `extract_nrfi_odds(events)` (sgo.py:406) to get NRFI/YRFI O/U lines + best-book odds per game.
- Join to `scoring_master` for the realized 1st-inning outcome.

Then let the NEW model pick its own bets across all games with archived odds (compute edge vs the
de-vigged market prob, apply the system's `min_edge`, simulate flat or Kelly paper stakes), and
report n, hit rate, ROI.

Datapoint gate (let `BACKTEST_N` = number of settled simulated bets the new model would place):
- `BACKTEST_N >= 100`  -> report ROI as a supporting signal.
- `30 <= BACKTEST_N < 100` -> report but label "UNDERPOWERED -- directional only".
- `BACKTEST_N < 30`    -> SKIP the ROI number entirely; state "insufficient odds history for
  backtest (n=<X>); decision rests on statistical gate 1-4". Do not compute a misleading ROI.

Make the threshold a module constant `MIN_BACKTEST_N = 100` (with the 30-floor) so it is easy to
raise as more odds accumulate. The decision gate in section 1 is unchanged regardless of backtest
outcome.

---

## 2. Modeling design (primary: Design A -- half-inning count, composed)

### The target
`scoring_master.csv` is long format, one row per (game_pk, inning, half), columns:
`inning, half ("top"=away batting / "bot"=home batting), runs, hits, errors, lob`.
The 1st-inning run count for a half is `runs` where `inning==1`.

`runners/build_nrfi_features.py` ALREADY:
- aggregates inning=1 statcast to one row per (pitcher, game_pk) (the half-inning that pitcher throws),
- joins the opposing-half run total as `_runs_against` from scoring_master (lines ~212-221),
- derives a binary `yrfi` target, lineup handedness (`lineup_pct_L`), and `platoon_edge`.

So each existing feature row already corresponds to ONE half-inning with a known integer run count.

### The model
- Train an XGBoost `count:poisson` regressor on **runs allowed in inning 1 by that pitcher**
  (the integer count, not the binary). Reuse `_runs_against` as the target -- confirm it is the
  raw integer count and NOT already binarized; if the builder only emits binary `yrfi`, add a
  `runs_against_i1` integer column to the builder (see section 4).
- Fit an over-dispersion parameter `nb_alpha` on the training residuals exactly as
  `training/retrain_k_v1.py` / `retrain_batter_hits_v1.py` do (MLB run counts over-disperse vs
  Poisson; see CONTEXT.md C07). Store `nb_alpha` in model meta.
- Reuse the feature set the v18 builder already produces. Do NOT invent new features for the spike
  -- the point is to isolate the paradigm change (count vs binary), not confound it with new inputs.

### Composition to game-level NRFI
For each game there are two relevant half-innings: away batting (top) vs home starter, and home
batting (bot) vs away starter. Predict lambda for each half, then:

```
P(half scores 0) = NegBin_pmf(0; mu=lambda_half, alpha=nb_alpha)   # = (1/(1+alpha*lambda))^(1/alpha)
P(NRFI) = P(top scores 0) * P(bot scores 0)                        # independence assumption
P(YRFI) = 1 - P(NRFI)
```

The independence assumption across the two halves is reasonable for inning 1 and matches the
existing runner, which already composes `1 - (1-p_home)*(1-p_away)` (run_nrfi.py:365). Document it
as an assumption; a later iteration can test a correlation term.

Reuse the NegBin pmf/cdf helpers already written in `runners/run_batter_hits.py:45-70`
(`_negbin_p_over` / `_negbin_p_under`) and the Monte Carlo pattern in `runners/run_k.py:319`
(`_simulate_outs_model`). Do not re-derive the distribution math.

### Why not Design B (full PA-level Markov sim)
A plate-appearance transition simulator is more accurate but is a multi-week build and overkill for
a go/no-go spike. If Design A validates, Design B becomes a follow-on for F5/full-game. Note this in
the writeup but do not build it now.

---

## 3. Version control + artifact isolation (READ THIS -- non-negotiable)

The production NRFI system MUST keep running on v17/v18 binary throughout the spike. Nothing in this
spike may touch the live scoring path, the registry `active` flags, the scheduler, or any production
GCS pointer until the gate passes and a separate production-wiring task is approved.

### Git
- Branch off `main`: `git checkout -b spike/first-inning-runsim`. Do all work there.
- Small, frequent, single-purpose commits. ASCII-only in all source (CONTEXT.md s6 -- no em-dash,
  no Unicode arrows; this breaks `str.replace`).
- Co-author trailer on commits per repo convention.
- Do NOT merge to main. Open a draft PR when the gate is evaluated so the writeup + reliability
  plots are reviewable. Final merge decision is the user's.

### GCS artifacts -- new namespace, never overwrite
- Production artifacts that must NOT be touched:
  `NRFI_Pro_System/models/xgb_halfinn_v17.json`, `model_meta_v17.json`,
  `isotonic_calibrator_v18.pkl`.
- Write all spike artifacts under an experimental prefix:
  `NRFI_Pro_System/experimental/runsim_v1/` (booster, meta with `nb_alpha`, reliability data).
- No "latest" pointer updates. No archive rotation of production files.
- The spike training script reads the existing feature CSV read-only; if it needs an added column
  (`runs_against_i1`), write the augmented training frame to
  `NRFI_Pro_System/experimental/runsim_v1/train_frame.csv`, NOT over the production
  `model_features.csv`.

### Code placement
- New training script: `training/spike_runsim_nrfi_v1.py` (self-contained; mirror the structure of
  `training/retrain_nrfi_v18.py` for split logic and `retrain_k_v1.py` for the count objective +
  nb_alpha fit + leakage guard).
- New evaluation script or notebook: `training/spike_runsim_nrfi_eval.py` (computes the section-1
  metrics for BOTH the new model and the v18 baseline on the shared holdout, emits a comparison
  table + reliability plot).
- If `build_nrfi_features.py` needs the integer-count column added, gate it so production behavior
  is unchanged (always emit the existing columns; ADD `runs_against_i1` alongside -- additive only,
  never rename/remove). XGBoost tolerates the extra column; the v18 model ignores it.
- Do NOT edit `runners/run_nrfi.py`, `mlb_core/registry.py`, `main.py`, the scheduler, or
  `monitor_*` in this spike. Those are production-wiring concerns for the follow-on task only.

### Leakage guard
Replicate the temporal/leakage discipline from `retrain_k_v1.py`:
- All rolling features must be as-of the game date (the existing builder already enforces this; do
  not bypass it).
- Use the v18 temporal 70/10/20 split (train / val for early stopping / test). The test slice is
  never seen during training, nb_alpha fit, or threshold selection (CONTEXT.md C03).
- Confirm the count target `runs_against_i1` comes from scoring_master and contains no post-first-
  inning information.

---

## 4. Step-by-step plan for Sonnet

Phase 0 -- recon (read-only, no commits):
- Read CONTEXT.md fully, then this file, then: `runners/build_nrfi_features.py`,
  `training/retrain_nrfi_v18.py`, `training/retrain_k_v1.py`, `runners/run_k.py` (NegBin sim),
  `runners/run_batter_hits.py` (NegBin pmf/cdf), `mlb_core/data/scoring.py`, `mlb_core/schemas.py`.
- Confirm: does the feature row carry the raw integer 1st-inning runs-against, or only binary
  `yrfi`? This determines whether Phase 1 needs a builder change.

Phase 1 -- training data:
- Ensure an integer `runs_against_i1` target exists (add additively to the builder if missing, per
  section 3). Write the experimental train frame to the experimental prefix.

Phase 2 -- model:
- `training/spike_runsim_nrfi_v1.py`: count:poisson booster + nb_alpha fit, v18 split, leakage
  guard, save to experimental prefix.

Phase 3 -- compose + evaluate:
- `training/spike_runsim_nrfi_eval.py`: predict lambda per half-inning on the OOS test slice,
  compose P(YRFI) per game, compute section-1 metrics for the new model AND load the v18 booster to
  compute the same metrics on the identical holdout. Emit comparison table + reliability plot.

Phase 4 -- decision:
- Write `handoffs/handoff_<date>.md` with: the comparison table, reliability plot path, the
  independence-assumption note, and a clear PASS/FAIL against the section-1 gate.
- If PASS: append a "production wiring scope" section (new system vs replace-NRFI-internals;
  registry entry, runner changes, retrain/calibrate Cloud Run Jobs, scheduler, monitor wiring per
  CONTEXT.md s6 "adding a new system" checklist). Do NOT implement it in this spike.
- If FAIL: document which gate failed and the most likely cause.
- Open a draft PR from `spike/first-inning-runsim`. Do not merge.

---

## 5. Constraints, gotchas, and known traps

- ASCII-only source (CONTEXT.md s6). Patch scripts must use `base = os.path.expanduser("~/mlb-betting")`,
  never hardcode paths (s6 "read before write").
- nb_alpha must be fit and stored in meta; the runner/eval falls back to Poisson if absent, which
  will under-state variance and bias P(0) -- so a missing nb_alpha silently degrades the spike.
  Verify it is non-zero and stored (CONTEXT.md C07).
- The current production NRFI is the WORST-calibrated system; do not "validate" the new model by
  comparing to NRFI's live odds-based ROI. Use the statistical gate (AUC/calibration/Brier) on the
  held-out target -- that is the honest test and does not depend on thin odds history.
- Do NOT remove `platoon_edge` from features (CONTEXT.md C01: removing it dropped OOS AUC 0.579 ->
  0.531). Keep the v18 feature set intact.
- scoring_master `half` is "top"/"bot" and "top" = AWAY batting (run_nrfi.py:195, build_nrfi
  comment ~195). Map halves to the correct pitcher when composing the two-half product.
- Independence across halves is an assumption, not a fact -- flag it; do not silently treat the
  result as exact.
- Deploy script gates on tests (`./deploy/deploy_service.sh` runs compileall + pytest). The spike
  does NOT deploy, but any new training script must at minimum import cleanly
  (`python3 -m compileall training/spike_runsim_nrfi_v1.py`).

---

## 6. Out of scope for this spike (explicitly)
- Any change to live scoring, the registry, main.py, schedulers, or monitors.
- Rebuilding F5 / F1H / GAME (those follow only if NRFI validates).
- A full plate-appearance Markov simulator (Design B).
- New features beyond what build_nrfi_features.py already emits.
- The model-health `signal` classifier fix and the rolling-performance suppression gate (separate
  task discussed with the user; not part of this spike).

---

## 7. Reference index (confirmed-present files)
- Target source / schema: `mlb_core/data/scoring.py`, `mlb_core/schemas.py` ("scoring_master").
- Existing NRFI features + runs-against join: `runners/build_nrfi_features.py` (~lines 50-221, 300-366).
- v18 binary baseline (split, feature groups): `training/retrain_nrfi_v18.py`,
  `training/calibrate_nrfi_v18.py`.
- count:poisson + nb_alpha + leakage guard reference: `training/retrain_k_v1.py`,
  `training/retrain_batter_hits_v1.py`.
- NegBin pmf/cdf + Monte Carlo reference: `runners/run_batter_hits.py:45-70`, `runners/run_k.py:319`.
- Live NRFI composition (`1-(1-p_home)*(1-p_away)`) + calibrator range-clip pattern:
  `runners/run_nrfi.py:365-387`.
- "Adding a new system" checklist (for the post-validation wiring scope only): CONTEXT.md s6.
