---
title: Positive backtest ROI with CLV~0 = beating a soft historical line, not a real edge
module: mlb/analysis/backtest_market.py, mlb/analysis/walkforward.py, mlb/analysis/gen_preds.py
tags: [backtest, roi, clv, leakage, walk-forward, odds_history, bettingpros, edge]
problem_type: logic_error
category: logic-errors
date: 2026-06-30
---

## Problem

A model-vs-line backtest reports strong ROI (+10..+22%) and it looks like a real
betting edge. It is not. Two independent failure modes produce fake ROI, and both
are invisible if you only look at ROI:

1. **In-sample leakage.** `gen_preds` scores with the *production* model + calibrator,
   which were trained on data spanning the backtest window. A "train/test split" on
   game_date is NOT a holdout -- both slices are inside the model's training set. The
   tell: train ROI ≈ test ROI (e.g. HITS +14.65% train / +14.53% test).
2. **Soft-historical-line artifact.** The historical odds in `odds_history` are mostly
   BettingPros *daily* scrapes (stale/soft). A model looks +EV against a soft line, but
   by the time a sharp market closes the price has corrected -- so it was never a price
   you could actually get. The tell: **CLV ≈ 0 / slightly negative**, and **all profit
   concentrated in the extreme (10%+) model-edge bucket** while the 2-10% buckets are
   flat/negative (no monotonic edge ladder = no real ranking skill).

## Symptoms

- `backtest_market` shows +10..+22% ROI but `clv%` is ~-0.2..-0.5% in every bucket.
- ROI lives entirely in the `10%+` edge bucket; `2-4%`, `4-6%`, `6-10%` are ~0 or
  negative.
- In-sample train ROI ≈ test ROI (should differ if test were a real holdout).
- `walkforward` (true OOS) ROI drops toward the CLV-implied ~0 and buckets stay
  incoherent (e.g. K/OUTS/HITS/TB all -> +6.7..+10.8% OOS, CLV~0, only 10%+ pays).

## Root cause

ROI is measured against the *recorded historical line* (soft BettingPros). CLV is
measured against the *closing line* (the real market). When they disagree, the ROI is
an artifact of the odds source being soft, not evidence of edge. A genuine edge beats
the close -> positive CLV. None of the 6 MLB systems did (2026-06-30 sweep).

## Fix / How to read it correctly

- **CLV is the go/no-go gate, not ROI.** ROI and CLV must AGREE. Positive ROI + CLV≤0
  = soft-line beating, reject.
- **Require a monotonic edge ladder.** Real edge => ROI rises smoothly with model edge.
  "Only the 10%+ bucket pays" = artifact.
- **Use `walkforward` (train pre-cutoff, score post-cutoff) for the real answer.** The
  plain `backtest_market` with `--split` is in-sample when the production model was
  trained across the window.
- **CLV is only as trustworthy as snapshot density.** Historical BettingPros ≈ 1
  snapshot/day => entry≈close => CLV weakly informative on the historical window; the
  ROI train->test *collapse* is the stronger leakage signal there. Dense intraday CLV
  requires the forward 8x/day ParlayAPI snapshots.

## Related

- `handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md` -- full sweep + verdict.
- `roadmap_2026-06-28_model_improvement.md` -- NRFI concept-drift precedent (live AUC
  0.498) that motivated edge-bucket-ROI over global AUC.
- Pivot: open->close line-movement / CLV capture is where edge (if any) lives.
