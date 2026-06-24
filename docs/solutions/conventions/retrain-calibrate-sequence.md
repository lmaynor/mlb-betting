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

## Second calibrator layer: the PREDICTION calibrator (mlb-fit-calibrators)

There are TWO calibrators per system, and the retrain sequence above only refreshes the
first one:

1. **Training-time isotonic calibrator** (`mlb-calibrate-*` -> `*/models/isotonic_calibrator_*.pkl`)
   -- fit on the model's OOS output range. Covered by the sequence above.
2. **Prediction calibrator** (`mlb-fit-calibrators` -> `Calibration/{system}_prediction_calibrator.pkl`)
   -- fit against REALIZED BET OUTCOMES, applied PRE-edge by `mlb_core.risk.calibration.apply`.
   This is the layer that corrects live overconfidence and that gates the `EDGE_CAP`.

**A retrain invalidates BOTH.** The prediction calibrator maps the OLD model's
probabilities to realized outcomes; after a retrain it is calibrating predictions from
a model that no longer exists. Observed 2026-06-24: NRFI was retrained 2026-06-22 but
every `Calibration/*_prediction_calibrator.pkl` was still dated 2026-06-11, so live
NRFI ran with a stale prediction calibrator (and the edge cap silently mis-fired).

**After any retrain, also run `mlb-fit-calibrators`** (re-fits all prediction calibrators
from the full settled-bets table). Schedule it weekly regardless of retrains, since it
also drifts as bet volume grows.

Coverage note: `mlb_core.risk.calibration.apply` must be called by EVERY scoring runner
or that system bypasses both calibration and the `EDGE_CAP`. As of 2026-06-24 all ten
systems are wired: K/OUTS/PITCHER_ER (run_k), 1IOU (run_nrfi), F5/F1H (run_f5),
GAME (run_game), HR (run_hr), BATTER_HITS (run_batter_hits). PITCHER_ER and F1H were
added 2026-06-24 -- they had been silently uncalibrated and uncapped.

## Why This Matters

Calibrators fit on the train slice (70%) give adequate output range coverage. Refitting on full df would leak val/test into calibration -- do not change calibrate scripts to fit on full df.

## When to Apply

After any of: full retrain, feature_means patch in model meta, bug fix that changes model output values, new features added to the model.
