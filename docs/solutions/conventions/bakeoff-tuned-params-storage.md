---
title: Bake-off tuning output must never land at the production tune_hyperparams.py key
module: mlb/analysis/bakeoff_persist.py, mlb/analysis/bakeoff_tuning.py, mlb/training/tune_hyperparams.py
tags: [bakeoff, tuning, optuna, gcs, storage, tuned-params, model-artifact]
problem_type: convention
category: conventions
date: 2026-08-11
---

## Context

`model_bakeoff.py` / `hr_model_bakeoff.py`'s `xgb_optuna` candidate
(`mlb.analysis.bakeoff_tuning.tune_system_walkforward`) runs a real, walk-forward-safe
Optuna search per system and needs somewhere to write the tuned hyperparameters +
search metadata for that run.

`mlb.training.tune_hyperparams.py` already has a per-system GCS key for exactly this
shape of payload -- `SYSTEM_CONFIG[system]["gcs_output"]`, e.g.
`HR_Pro/models/hr_tuned_params.json` -- but that key is not a generic "tuned params"
scratch space. It is read by `load_tuned_params(system)`, which several PRODUCTION
retrain scripts call.

## Guidance

**Never write a bake-off (or any other exploratory/analysis) run's tuned params to
`tune_hyperparams.SYSTEM_CONFIG[system]["gcs_output"]`.** Always use the separate,
run-scoped path: `mlb.analysis.bakeoff_persist.write_tuning(prefix, system, params, meta)`
-> `Analysis/bakeoff/runs/{run_id}/tuning/{system}_tuned.json`.

```python
# WRONG -- reachable by the next real production retrain
storage.write_bytes(json.dumps(result).encode(), "HR_Pro/models/hr_tuned_params.json")

# RIGHT -- confined to this bake-off run's own namespace
bakeoff_persist.write_tuning(persist_prefix, "HR", tuned_params, tune_meta)
```

## Why This Matters

`retrain_batter_hits_v1.py`, `retrain_batter_tb_v1.py`, and `retrain_game_v1.py` each
call `tune_hyperparams.load_tuned_params(SYSTEM)` inside `run()` and will silently
`XGB_PARAMS = tuned["xgb_params"]` on the **next real Cloud Run retrain** if any JSON
exists at that system's `gcs_output` key -- with no distinct alert that a tuned
(rather than hardcoded) param set was used. A one-off exploratory bake-off search --
run on a much smaller pre-cutoff slice, un-vetted, optimizing plain AUC/MAE rather
than anything betting-related -- would silently become the live production model's
hyperparameters on the next scheduled `mlb-retrain-*` job.

`retrain_k_v1.py`, `retrain_outs_v1.py`, and `retrain_hr_v6.py` do not have this hook
today, but the convention should hold uniformly: it is a matter of when, not if, the
hook gets added there too (mirroring the other three).

## When to Apply

Whenever writing tuning/search output from ANY analysis or exploratory script, not
just this bake-off. A parameter set has not earned a path back into
`tune_hyperparams.load_tuned_params()`'s key until it has been through the real
retrain + calibrate + paper-mode gate sequence (CONTEXT.md Sec6, "Paper -> live
criteria" / T17) -- not merely because it happened to be produced by
`optuna.create_study()`.
