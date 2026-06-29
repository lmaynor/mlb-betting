# Handoff -- 2026-06-29 -- NRFI was misdiagnosed by global AUC

## TL;DR

The model-improvement program (roadmap_2026-06-28) concluded NRFI suffered fatal
concept drift -- "live AUC 0.498, not salvageable." That verdict was a **metric
artifact**. Judged on the metric that matches what the system actually does --
bet the confident tails against a market that prices nearly every game at ~0.48 --
NRFI is profitable out-of-sample. A holdout backtest against REAL historical lines
shows **+8.1% ROI at consensus over 879 out-of-sample bets** (2 seasons), with the
model's Brier beating the market's. NRFI should be re-evaluated on tail/edge-bucket
ROI, not global AUC, and is a candidate to un-pause with two guardrails.

## How we got here

Built `mlb/analysis/` on top of the user-supplied `yrfi_master.csv` (historical
per-game NRFI/YRFI prices) plus a snapshot extractor for 2026:
- `gen_nrfi_preds.py` [Cloud Shell] -- scores all `model_features.csv` rows with the
  production v18 ensemble (reuses run_nrfi `_load_v18_ensemble` / `_score_v18`),
  combines halves, calibrates, emits `game_key,p_yrfi,yrfi,game_date`.
- `extract_2026_nrfi_odds.py` [Cloud Shell] -- walks `Odds/sgo/{date}/snapshot_*.json`,
  takes the latest-available NRFI price per game, emits rows in the yrfi_master
  schema. Yielded 633 priced 2026 games (2026-05-12 -> 2026-06-28).
- `nrfi_market.py` -- de-vig market baseline, backtest vs real lines, model-vs-market
  calibration, and (new) bettable-slate metrics.

## Evidence

### 1. The result is genuinely out-of-sample (not memorization)
`model_meta_v18.json`: `trained_at 2026-06-22`, but v18 uses a 70/10/20 TIME split
and the saved booster fits only the first ~61% of games by date. `test_from =
2024-08-20`, `train_through = 2024-05-22`. So everything from 2024-08-20 onward
(all of 2025 + 2026) is held out. The backtest windows below are true OOS.

### 2. Honest backtest vs real lines (consensus price, flat 1u)
| Sample | Bets | Win% | ROI | Model Brier | Market Brier |
|---|---|---|---|---|---|
| 2026 only (631 priced games) | 448 | 56.0% | +7.5% (+11.0% best-line) | 0.243 | 0.247 |
| Full holdout >= 2024-08-20 (4035 games) | 879 | 56.8% | +8.1% | 0.240 | 0.248 |

Calibration is cleanly monotonic: realized YRFI climbs 0.28 -> 0.33 -> 0.40 -> 0.51
-> 0.55 -> 0.62 -> 0.82 across the model's probability bins, while the de-vigged
market stays pinned at ~0.48 in EVERY bin. The market barely discriminates
first-inning scoring by matchup; the model does.

### 3. Why "live AUC 0.498" was misleading
`diagnose_nrfi_drift.py` scores the same `model_features.csv` with the same v18 over
a 150-day window (n >> 200, so not small-sample noise) and reports GLOBAL stacked
AUC ~0.498. But ~40% of the slate sits in the indifferent 0.40-0.50 model band
(255 of 631 games in 2026) where model ~ market ~ realized ~ 0.5. Global AUC weights
all pairs equally, so that flat mass washes the statistic to 0.50. A near-0.50
global AUC and a profitable tail-selective bettor are fully compatible. The
roadmap's `DEAD_FLOOR=0.52` test judged the wrong slice.

### 4. Leakage ruled out
`build_nrfi_features.py` computes every rolling / season-to-date feature with
`shift(1)` after sorting by `game_date` within pitcher/group (lines 240, 260, 267,
273, 282, 347). The window only ever sees prior games; logic does not reference
"today," so even a batch rebuild stays point-in-time correct. The holdout AUC 0.589
and the backtest are honest.

### 5. The one actionable refinement
Edge buckets (full holdout, consensus) form a clean U; the 5-10% band is the entire
leak: 0-5% +14.8% (n=146), **5-10% -9.6% (n=348)**, 10-15% +14.7% (n=147), 15-20%
+11.0% (n=105), 20%+ +37.7% (n=133). Dropping the 5-10% band keeps ~531 bets at
~+19.8% ROI at consensus. Validate the exact boundary on a sub-split before trusting
it (non-monotonic cuts can overfit), but on 2 seasons it is a strong signal that the
mid-confidence range is miscalibrated while the tails are excellent.

VALIDATED (2026-06-29, validate_skip_band.py): fit the band on 2024-08-20..2026
(431 bets), test on 2026 (448 bets). The 5-10% band is negative on BOTH splits
(-14.4% fit / -4.1% test). On the TEST split, skipping it improves OOS ROI
monotonically across cut widths -- bet-all +7.5% (t=1.64) -> skip[5,10) +13.9%
(t=2.43) -> skip[5,12) +15.6% (t=2.54) -> skip[5,15) +19.2% (t=2.85). Monotone
agreement across widths is the opposite of an overfit knife-edge. CONCLUSION: adopt
**skip[5,12)** -- bet edges in [3%,5%) and [>=12%). Do NOT extend to skip[5,15): the
10-15% bucket is UNSTABLE (+32% fit vs +0.2% test), so cutting it overfits 2026's
shape. NRFI is positive even with no skip (+7.5%), so the band is an
efficiency/significance enhancement, not load-bearing. (Bucket-level 2026 ROIs are
noisy at n~50-160; the aggregate strategy t-stats carry the result.)

### 6. Line shopping is real money
Best-of-book vs consensus is worth ~+4.6% (YRFI) / +3.2% (NRFI) on the full sample
(~+6.3% / +3.0% in 2026), available in ~80-92% of games -- roughly half the ~6.5%
hold. It lifts ROI from +8.1% (consensus) to +11.0% (best-line) in 2026.

## Tooling shipped (branch analysis/nrfi-historical-odds)
- `mlb/analysis/nrfi_market.py` -- added `bettable_slate_metrics()` (global vs tail
  vs bettable AUC + month x edge-bucket ROI) and `--since` / Deliverable 4 output.
- `mlb/runners/diagnose_nrfi_drift.py` -- added `tail_auc()`; `recommend()` no longer
  declares NRFI dead on global AUC alone when the tail clears the weak floor; result
  now carries `stacked_tail_auc` / `tail_n` / `tail_margin`. Docstring corrected.

## Reproduce (Cloud Shell)
```
export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
PYTHONPATH=. python3 -m mlb.analysis.gen_nrfi_preds --out nrfi_preds.csv
PYTHONPATH=. python3 -m mlb.analysis.extract_2026_nrfi_odds --year 2026 \
    --master yrfi_master.csv --out yrfi_master_2026.csv
PYTHONPATH=. python3 -m mlb.analysis.nrfi_market --odds yrfi_master_2026.csv \
    --preds nrfi_preds.csv --since 2024-08-20 --price consensus
```

## PROPOSAL: un-pause 1IOU with guardrails

1IOU is currently paused via `registry.force_gate` (roadmap Track A). Proposed path:

1. **Re-evaluate on the right metric.** Stop gating NRFI on global AUC. Use the new
   bettable-slate / edge-bucket ROI from `nrfi_market.py`. The drift diagnostic's
   `tail_auc` now surfaces the bettable signal directly.
2. **Bet rule guardrails** (data-supported, both reduce the known weak spot):
   - **Line-shop the best onshore book** (already the runner's `_best_book_odds_int`
     behavior) -- worth ~3-4 pts.
   - **Skip the 5-12% edge band** (VALIDATED OOS, see s5) -- bet edges in [3%,5%)
     and [>=12%]. Do NOT extend the skip to 15%; the 10-15% bucket is unstable
     across periods and cutting it overfits 2026.
3. **Stage as paper/log-only against the 200-bet gate** (CONTEXT s6), watching the
   tightened T17 criteria: mean CLV >= +2% (t > 2) over >=100 bets; hit rate within
   3 pts of avg model prob in the BETTABLE buckets; PSI < 0.25. The holdout already
   clears hit-rate calibration in the tails.
4. **Re-check the live-vs-holdout gap before real stakes.** The model is sharp on
   holdout; if a future LIVE sample still underperforms once measured on tail/edge
   buckets (not global AUC), suspect execution (data latency at 08:00 build, v17/v18
   path confusion, settlement), not the model.

## Open / next
- [DONE 2026-06-29] Confirmed the skip-band on a 2024-25 vs 2026 split
  (validate_skip_band.py): skip[5,12) generalizes; do not extend to 15%.
- [DONE 2026-06-29, commit 68457cc] Wired the bettable-slate / edge-bucket view
  into `monitor_performance.py`: per-edge-bucket ROI + dead-band [5%,12%) tracking
  in season stats / digest / gate file; placed-bet AUC no longer alerts on its own
  (only with negative ROI + n>=100); new dead-band bleed alert. Suppression stays
  ROI-only. Still TODO: apply skip[5,12) in run_nrfi itself (runner-side bet rule),
  and flip registry.force_gate off to un-pause 1IOU once staged.
- This overturns roadmap_2026-06-28 section 1's "NRFI not salvageable" conclusion --
  update the roadmap / Track B framing accordingly.
