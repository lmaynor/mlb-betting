---
title: Always run the calibrate job after any retrain -- stale calibrators cause clipping
module: training/calibrate_*.py, runners/run_*.py
tags: [calibrator, retrain, isotonic, model-artifact, sequence]
problem_type: convention
category: conventions
date: 2026-05-20
---

## Context

Isotonic calibrators are fit on the OOS model output range. If the model is updated without refitting the calibrator, the calibrator's input range no longer matches actual model output.

## Guidance

**Always run the calibrate Cloud Run Job after any model artifact change** — including full retrains, feature_means patches, and IP/scaling bug fixes.

sklearn's isotonic regression clips out-of-bounds inputs to the nearest boundary value: if model outputs shift outside the calibrator's `X_min`/`X_max`, everything maps to 0 or 1. This causes the runner to post extreme (0% or 100%) calibrated probabilities for all bets.

Manual retrain sequence per system:
```
NRFI: mlb-retrain-nrfi-v18  -> mlb-calibrate-nrfi
F5:   mlb-retrain-f5-v5     -> mlb-calibrate-f5
K:    mlb-retrain-k-v1      -> mlb-calibrate-k
HR:   mlb-retrain-hr-v6     -> mlb-calibrate-hr
OUTS: mlb-retrain-outs-v1   -> mlb-calibrate-outs (uses OUTS isotonic calibrator)
BATTER_HITS: mlb-retrain-batter-hits -> mlb-calibrate-batter-hits
GAME: mlb-retrain-game-v1   -> mlb-calibrate-game
```

The `/retrain-weekly` route fires all retrains then all calibrate jobs 30 min later via a background thread.

## Why This Matters

Calibrators fit on the train slice (70%) give adequate output range coverage. Refitting on full df would leak val/test into calibration -- do not change calibrate scripts to fit on full df.

## When to Apply

After any of: full retrain, feature_means patch in model meta, bug fix that changes model output values, new features added to the model.
