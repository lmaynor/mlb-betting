# Roadmap -- 2026-06-28 -- Model improvement program (3 tracks)

A staged program to fix "the model is not performing well enough." Spawned from a
question: would the **ParaMonte** library (a parallel Monte Carlo MCMC sampler)
help? Short answer below, then the plan that actually addresses the diagnosis.

This is a multi-session program. Track A is the highest-leverage work and uses NO
new dependencies. Track C is a cheap tooling spike. Track B is the real modeling
upgrade and depends on A's diagnosis + C's tooling decision.

---

## 0. Verdict on ParaMonte (do NOT adopt)

ParaMonte is a serial/parallel Monte Carlo library whose flagship is ParaDRAM, an
adaptive Metropolis-Hastings MCMC posterior sampler (Fortran core, Python/C/MATLAB
bindings; came out of astrophysics). It samples arbitrary likelihoods in parallel.

It is aimed at the wrong layer of our problem:
- It is NOT a classifier/regressor and does not replace XGBoost.
- It does nothing for our actual root causes (feature signal loss + train-vs-live
  drift; see section 1).
- The one place the *concept* helps -- Bayesian uncertainty quantification to fight
  overconfidence -- is far better served by NumPyro / PyMC / cmdstanpy: idiomatic
  Python, pandas-native, trivial pip install, huge community. ParaMonte's edge
  (parallel MPI sampling of expensive Fortran likelihoods, zero external deps)
  buys us nothing at logistic-regression scale and costs a Fortran/CMake/MPI
  integration into a Cloud Run image whose build speed is already a pain point.

Track C still spikes ParaMonte vs NumPyro head-to-head so the decision is
evidence-based, not asserted. Expectation: confirms NumPyro/PyMC.

---

## 1. Diagnosis (why the model underperforms) -- from our own data

Source: handoff_2026-06-24_calibration_coverage.md + code read 2026-06-28.

1. **Weak/absent LIVE discrimination (the real wound).** NRFI scores OOS AUC 0.589
   but LIVE AUC 0.498 -- worse than a coin flip. 1IOU, F5, F1H sit at AUC ~0.50.
   NRFI sub-model live signal: lineup 0.589 (only real signal), pitcher 0.526
   (stump, best_iteration=7), context 0.504 (dead). Walk-forward CV shows
   year-over-year decay: 2024=0.599 -> 2025=0.588 -> 2026=0.539. This is
   **concept drift / regime change** (pitch-clock era), NOT undertraining.

2. **Overconfidence at the tails -> adverse selection.** The largest apparent
   "edges" are dominated by model error. Quantified in calibration.py: the >=20%
   edge bucket has model ~0.77 but wins ~0.46.

3. **Process gaps (largely already fixed 06-24).** Two systems bypassed the
   calibration + EDGE_CAP layer (PITCHER_ER, F1H) -- fixed. Calibrators went 13
   days stale -- weekly refit scheduler added. No-edge systems (1IOU, F5, F1H)
   paused via registry.force_gate -- bleed contained.

What is ALREADY in place (do not rebuild):
- `mlb_core/risk/calibration.py` -- pre-edge isotonic calibration + `EDGE_CAP=0.20`
  hard skip on residual overconfident edges. The worst bleed is already capped.
- `mlb_core/risk/gates.py` -- dynamic suppression + registry.force_gate override.
- `mlb/runners/monitor_performance.py` -- computes `mean_clv` + `clv_tstat`
  (`_clv_stats`), but gate suppression is deliberately ROI-ONLY (author argues ROI
  is the only unbiased signal; CLV depends on closing-line capture coverage).
- `mlb/runners/monitor_drift.py` -- weekly PSI feature-drift monitor.

---

## 2. Environment constraint (read this before executing)

The local Mac checkout is **edit-only**: no xgboost, no numpyro/pymc/jax, no
paramonte, no pg8000/sqlalchemy, no gcloud, no .env, no local data. Python is 3.14
(bleeding edge; jax/pymc wheels may be absent). Per the repo's whole operating
model, runs happen in **Cloud Shell / Cloud Run against Cloud SQL + GCS**. So this
program = write review-ready code + run diagnostics/retrains/backtests in Cloud
Shell. Code-only steps below are doable locally; data-dependent steps are flagged
[CLOUD].

---

## 3. Sequencing

```
Phase 1  Track A  Stop bleed (done) + DIAGNOSE drift     <- start here, no new deps
Phase 2  Track C  Sampler spike -> tooling decision       <- cheap, gates Track B
Phase 3  Track B  Hierarchical Bayesian NRFI + backtest   <- the real upgrade
```

Rationale: the lost money is in #1 (drift) and #2 (overconfidence). A diagnoses and
tells us whether NRFI is salvageable by retrain/recency-weighting or needs a
structurally different model. C decides the Bayesian tooling. B builds it.

---

## TRACK A -- Diagnose drift + tighten discipline (Phase 1)

### A2 (FIRST, highest value): Fix + run the NRFI drift diagnostic
**Bug found 2026-06-28:** `monitor_drift.py` cannot monitor live NRFI.
- `SYSTEM_CONFIG["1IOU"]` points at `model_meta_v17.json`, but the runner loads the
  **v18 ensemble** (preferred; v17 is fallback only). See run_nrfi.py `_load_v18_ensemble`.
- v18 meta has NO top-level `feature_means`/`feature_stds` -- they are nested under
  `sub_models.{pitcher,lineup,context}` (retrain_nrfi_v18.py step 8). The monitor
  reads top-level `feature_stds` -> gets `{}` -> returns `no_feature_stds` -> checks
  nothing. So NRFI drift monitoring has been silently dead.

Steps:
1. [code] Repoint `monitor_drift.SYSTEM_CONFIG["1IOU"]` to v18 meta and flatten the
   nested per-sub-model `feature_means`/`feature_stds` (union across pitcher/lineup/
   context) so PSI can run. Add F5 v5 / others if their meta is also stale.
2. [code] PSI alone measures covariate shift; NRFI's failure is concept drift (the
   feature->target relationship decayed while marginals may look stable). Extend the
   diagnostic to also compute **live per-sub-model AUC on settled bets** vs the OOS
   sub-model AUCs in meta -- this is the signal that actually moved.
3. [CLOUD] Run `/monitor-drift` for NRFI; pull `/edge-analysis` + bet-level data via
   Cloud SQL proxy; confirm which features/sub-models drifted.
4. Decision output: is NRFI salvageable via (a) recency-weighted retrain, (b)
   lineup-only model (handoff's suggestion -- lineup is the only live signal), or
   (c) a structurally different model (feeds Track B)?

### A3 (refinement, lower priority): edge-bucket sizing taper
`EDGE_CAP=0.20` already HARD-skips the worst bucket, so this is a refinement, not a
wound. If A2/edge-analysis data supports it: replace the cliff with a monotone taper
that down-weights stake as edge grows between `min_edge` and `EDGE_CAP` (large
"edges" are adverse-selection signals, not opportunities). Code + tests. Gate on data.

### A4: elevate CLV as a primary scorecard (respect the ROI-only reasoning)
CLV machinery exists (`_clv_stats`) but does not drive the gate by design. Do NOT
bulldoze that. Instead: surface `mean_clv` + `clv_tstat` as a first-class verdict
line in `/model-health` and the weekly digest, and make CLV a gate INPUT only where
closing-line coverage is sufficient (T17 direction: mean CLV >= +2% with t-stat > 2
over >=100 bets). Code + tests.

---

## TRACK C -- Sampler spike + tooling decision (Phase 2)

### C1: ParaMonte vs NumPyro head-to-head (time-boxed ~1 day)
Scratch venv (NOT the Cloud Run image). Toy likelihood: Beta-Binomial posterior for
a team/pitcher NRFI rate from N recent starts. Implement once in ParaMonte (ParaDRAM)
and once in NumPyro (NUTS). Compare: install footprint, lines of glue code, samples/s,
ergonomics with pandas. Output a one-page decision doc. Expectation: NumPyro wins;
this makes it evidence-based. Pure local/scratch work (mind Python 3.14 wheel gaps --
may need a 3.11/3.12 venv).

---

## TRACK B -- Hierarchical Bayesian NRFI (Phase 3, the real upgrade)

### B1: partial-pooling Bayesian NRFI model
Build a hierarchical model (chosen sampler from C1) for P(NRFI) that:
- Partial-pools across pitchers/teams so thin-data starters shrink toward
  population means (kills fake small-sample "edges").
- Emits a POSTERIOR over the probability, not a point estimate -> a credible-interval
  WIDTH usable for uncertainty-aware Kelly sizing (bet less when uncertain; directly
  attacks the overconfidence in diagnosis #2).
- Recency-weights / regime-aware (informed by A2): down-weight pre-pitch-clock
  seasons given the 2024->2026 decay.
Mirror the `retrain_*.py` pattern (load model_features.csv, time split, write
artifacts + meta to GCS). [CLOUD] for fit.

### B2: backtest vs XGBoost v18, decide
Walk-forward backtest on held-out weeks. Score on **CLV + calibration (Brier)**,
not just ROI. Compare uncertainty-aware sizing vs current. Productionize only if it
beats the incumbent on the low-variance metrics; otherwise shelve with notes.

---

## 4. Task map (TaskCreate ids this session)

- #1 roadmap doc (this file)
- #2 A1 verify calibration fix held [CLOUD, read-only]
- #3 A2 NRFI drift diagnostic (start with the v17->v18 monitor_drift bug)
- #4 A3 edge-bucket sizing taper (gated on data)
- #5 A4 CLV primary scorecard
- #6 C1 sampler spike + decision
- #7 B1 hierarchical Bayesian NRFI
- #8 B2 backtest + productionize decision

## 5. Immediate next action
Fix the `monitor_drift.py` v17->v18 meta + nested-stds bug (A2 step 1) -- pure code,
locally doable, and it is the prerequisite for diagnosing the #1 root cause.
