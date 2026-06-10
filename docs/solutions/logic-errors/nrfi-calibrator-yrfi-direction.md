---
title: NRFI calibrator is fit on YRFI probs -- applying it to NRFI probs causes a sign flip
module: training/calibrate_nrfi_v18.py, runners/run_nrfi.py
tags: [nrfi, calibrator, isotonic, probability, sign-flip]
problem_type: logic_error
category: logic-errors
date: 2026-05-20
---

## Problem

Applying the NRFI isotonic calibrator to `model_nrfi_prob` instead of `model_yrfi_prob` produces a sign flip: all games show extreme YRFI probability (near 1.0). The runner silently accepts it and bets YRFI on every game.

## Symptoms

- All NRFI games show model_yrfi_prob near 1.0
- Bet log shows YRFI signal for every game regardless of pitcher quality
- P&L rapidly negative

## Root Cause

`calibrate_nrfi_v18.py` fits isotonic regression on:
```python
iso.fit(oos_g['model_yrfi_prob'], oos_g['yrfi'])
```

The calibrator maps raw YRFI scores -> calibrated YRFI probability. The runner must:
1. Apply calibrator to `model_yrfi_prob` (raw)
2. Derive `model_nrfi_prob = 1 - calibrated_yrfi`

Applying the calibrator to `model_nrfi_prob` (which is `1 - model_yrfi_prob`) feeds the wrong domain to the isotonic function — it maps high-NRFI (low-YRFI) scores to high calibrated YRFI probability.

## Solution

```python
# Correct
raw_yrfi = model.predict(dm)  # raw YRFI score
cal_yrfi = calibrator.transform(raw_yrfi.clip(X_min, X_max))
cal_nrfi = 1.0 - cal_yrfi

# Wrong -- sign flip
cal_nrfi_WRONG = calibrator.transform((1.0 - raw_yrfi).clip(X_min, X_max))
```

## Prevention

The calibrator's fit target is documented in the retrain script's comment. When in doubt: NRFI calibrator is always fit on YRFI scores. The variable names look symmetric but are not interchangeable. Verify in `run_nrfi.py` that `model_yrfi_prob` (not `model_nrfi_prob`) is passed to `calibrator.transform`.
