# First-inning run-sim spike -- DECISION (2026-06-11)

Branch: `spike/first-inning-runsim`. Scope: `scope_first_inning_runsim_2026-06-10.md`.
Eval executed as Cloud Run Job `mlb-spike-runsim-eval-h8t8f` on the v18 OOS test
slice (n=3843 games). Report: `NRFI_Pro_System/experimental/runsim_v1/eval_report.json`.

## VERDICT: FAIL -- do NOT wire into production. Do NOT rebuild F5/F1H/GAME as counts.

The count:poisson half-inning model loses to the existing v18 binary ensemble on
the SAME held-out test set, across every gate criterion except monotonicity.

| Metric (OOS, n=3843) | New count model | v18 binary ensemble | Gate |
|---|---|---|---|
| AUC for P(YRFI) | **0.535** | **0.624** | >= 0.55 -> FAIL |
| Brier skill vs base rate | **-0.046** | **+0.046** | > 0 -> FAIL |
| Max bin cal_err | 0.393* | 0.149* | < 0.05 -> FAIL (both) |
| Reliability monotone | yes | yes | pass |

*Both max-cal-err values come from sparse tail bins; the substantive bins differ:
the count model is overconfident throughout (cal_err -0.09 to -0.17), while v18 is
well-calibrated in its populated bins (+0.014, +0.013, +0.005, -0.026).

Gate cause of failure: **discrimination first, calibration second.** Even perfectly
recalibrated, a 0.535-AUC model cannot beat a 0.624-AUC model -- calibration is
monotonic and does not change AUC. Collapsing each half to a single Poisson lambda
and composing under independence discards signal that the 3-sub-model + stacker
ensemble captures. The integer run target is also thin (most half-innings are 0-1
runs), so the count objective has little to learn beyond the binary.

## The bigger finding (overturns the premise of the spike)

The spike was motivated by F5/NRFI showing AUC ~0.50 and cal_err -0.20 to -0.26.
**That was a selection-bias artifact of the `bets` table, not a property of the model.**

On the full OOS holdout the v18 binary ensemble is actually GOOD:
- AUC **0.624** (not ~0.50)
- Brier skill **+0.046** (positive -- beats the naive base rate)
- Well-calibrated across populated bins (errors of 1-2 pct points)

The ~0.50 AUC and severe miscalibration we see in `/model-health` and the gate come
from measuring on the censored, anti-market subsample of games the model actually
bet -- exactly the bias flagged before the spike. The binary models are NOT broken
at discrimination. **The problem lives in the bet-selection / edge layer, not the
model.** A 0.62-AUC, well-calibrated model that still loses money on its chosen bets
is a min_edge / vig / market-efficiency problem.

## Recommendations

1. **Abandon the count-rebuild path for game/inning binaries.** The paradigm-transfer
   hypothesis (count models beat binary for NRFI) is not supported. Negative result,
   cleanly documented.
2. **Pivot the investigation to bet selection.** Why does a 0.62-AUC, calibrated model
   lose on the bets it places? Likely: it bets where it most disagrees with an
   efficient market (the disagreement is usually the model being wrong at the margin),
   and/or vig swamps a thin edge. Examine min_edge sizing, de-vig assumptions, and CLV
   on the selected bets vs the full slate.
3. **Gate implication (important):** bet-sample `auc_model` and bet-sample `cal_err`
   are selection-biased health signals -- OUTS shows bet-sample AUC 0.43 while earning
   +11% ROI. The suppression gate should lean on ROI (ground truth) and CLV, and should
   NOT auto-suppress a profitable system on bet-sample AUC/calibration. See the
   profitability-guard proposal in this session's gate work.

## Disposition
- Keep the spike branch + experimental artifacts for the record. Do NOT merge.
- Tear down the throwaway jobs:
  `gcloud run jobs delete mlb-spike-runsim-train mlb-spike-runsim-eval --region=us-central1 --quiet`
- Production NRFI continues on v18 binary, unchanged (it was never touched).
