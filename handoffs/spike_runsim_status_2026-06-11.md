# First-inning run-sim spike -- build status (2026-06-11)

Branch: `spike/first-inning-runsim` (off main; do NOT merge -- scope s3).
Scope: `handoffs/scope_first_inning_runsim_2026-06-10.md`.

## What is built (Phases 0-3 scaffolding, code-complete)

| File | Purpose |
|---|---|
| `training/spike_runsim_nrfi_v1.py` | TRAIN: count:poisson half-inning model + nb_alpha fit. Re-derives integer `runs_against_i1` target from scoring_master (production builder untouched). Writes to `NRFI_Pro_System/experimental/runsim_v1/`. |
| `training/spike_runsim_nrfi_eval.py` | EVAL: composes game-level P(YRFI) via NegBin P(0) product; scores v18 ensemble baseline on the SAME OOS test slice; prints PASS/FAIL vs scope gate; writes `eval_report.json`. |
| `deploy/setup_spike_runsim.sh` | Builds a SEPARATE spike image (never the prod image, no service revision) + throwaway Cloud Run Jobs. |

### Phase 0 recon findings (confirmed)
- The production builder (`build_nrfi_features.py:209-221`) computes the integer
  1st-inning runs-against but **binarizes to `yrfi` and drops the integer**. So the
  feature CSV carries only binary `yrfi`. The spike re-derives the integer target
  itself from `scoring_master` using the identical join -- production builder is
  NOT modified.
- Half mapping: `pitcher_is_home==1` faces AWAY batters (scoring half "top"); else "bot".
- v18 is a 3-sub-model ensemble + logistic stacker. The eval imports
  `_load_v18_ensemble` / `_score_v18` / the isotonic calibrator from `run_nrfi.py`
  to score the baseline exactly as production does (apples-to-apples).

### Design decisions
- Feature set = v18 PITCHER+LINEUP+CONTEXT verbatim (scope: no new features).
- Temporal split = v18 exact: `test_idx=int(n*0.7)`, `val_idx=int(test_idx*7/8)`.
- `nb_alpha` fit on train+val residuals ONLY; test slice never seen (leakage guard).
- Composition assumes independence of the two halves (documented assumption, scope s2).
- Pure-math core (NegBin P(0), composition, AUC/Brier-skill/reliability) unit-validated
  locally; full pipeline needs GCS so it runs as a Cloud Run Job.

## How to run (Cloud Shell)

```bash
git checkout spike/first-inning-runsim
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_spike_runsim.sh
gcloud run jobs execute mlb-spike-runsim-train --region=us-central1 --wait
gcloud run jobs execute mlb-spike-runsim-eval  --region=us-central1 --wait
gsutil cat gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/experimental/runsim_v1/eval_report.json
```

## Decision gate (scope s1 -- ALL must hold on OOS test for the NEW model)
1. AUC for P(YRFI) >= 0.55
2. |hit_rate - mean_pred| < 0.05 in each of 5 bins
3. Brier skill > 0 vs YRFI base-rate naive
4. Reliability curve monotone

The eval prints PASS/FAIL and the per-bin reliability table; `eval_report.json`
has the full numbers for both the new model and the v18 baseline.

## Not yet done
- Execution of the two jobs (needs GCS; user runs in Cloud Shell).
- Phase 4 writeup with the actual comparison table + reliability plot -- author after
  the eval runs. If PASS, append a production-wiring scope (do NOT implement in spike).
- Backtest (scope s1b) is SUPPLEMENTARY and sample-gated; not built in this pass --
  the statistical gate (1-4) is the real decision and does not need odds history.
