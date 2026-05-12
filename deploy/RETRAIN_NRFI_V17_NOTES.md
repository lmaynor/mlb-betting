# NRFI v17 Retrain Runbook

Companion to `deploy/RETRAIN_NOTES.md` (F5 meta patcher). This documents the
NRFI v17 full retrain pipeline.

## What it does

`training/retrain_nrfi_v17.py` mirrors `NRFI_Pro_Complete_v17.ipynb` Sections
8 + 8b:

1. **Load** `NRFI_Pro_System/data/model_features.csv` from GCS.
2. **OOS eval (Section 8)**: time-based 80/20 split, XGBoost with early
   stopping (50 rounds, max 800), captures `auc_oos`, `brier_oos`,
   `logloss_oos`, `best_iteration`.
3. **Full retrain (Section 8b)**: trains on 100% of data for
   `best_iteration` rounds. This is the production booster.
4. **Compute `feature_means`** per-feature from the full training set, for
   the runner's NaN-fill at predict time.
5. **Write to GCS** with archive + latest pointer pattern:
   - `NRFI_Pro_System/models/xgb_halfinn_v17.json`         (latest)
   - `NRFI_Pro_System/models/model_meta_v17.json`          (latest)
   - `NRFI_Pro_System/models/archive/xgb_halfinn_v17.{ts}.json`
   - `NRFI_Pro_System/models/archive/model_meta_v17.{ts}.json`

## Difference from F5 retrain

- **F5 retrain** (`retrain_f5_meta.py`): patches `feature_means` into an
  existing meta. Booster is unchanged.
- **NRFI v17 retrain** (`retrain_nrfi_v17.py`): produces both the booster
  and the meta from scratch. Used to bootstrap the v17 model (no
  artifact existed previously) and for periodic retrains.

## Provisioning the Cloud Run Job

One-time setup:

```bash
chmod +x deploy/setup_retrain_nrfi_v17.sh
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_retrain_nrfi_v17.sh
```

Idempotent — re-running updates the job in place.

## Triggering

```bash
gcloud run jobs execute mlb-retrain-nrfi-v17 \
  --region=us-central1 --project=concrete-crow-445205-m4 --wait
```

Expected runtime: **2-5 minutes** on the 2Gi/2cpu Job config (full retrain
on ~31K rows, ~42 features, ~500 boosted rounds). Job timeout is 1800s.

## Sanity check: expected metrics

From notebook v17 with current features (April 2026 data):

| Metric              | Expected      | Acceptable range |
|---------------------|---------------|------------------|
| `auc_oos`           | ~0.587-0.59   | > 0.55           |
| `brier_oos`         | ~0.20         | < 0.22           |
| `best_iteration`    | ~400-500      | 200-800          |
| `mean_pred`         | ~0.28-0.29    | within 0.02 of `mean_actual` |
| Training rows       | ~25,000+      | grows over time  |

If `auc_oos < 0.55` or `best_iteration < 100`, **don't trust the model —
investigate before promoting**. The latest pointer is already overwritten
by then; roll back per below.

The overfit gap (train AUC − test AUC ≈ 0.09 in v17) is known and
documented in `ipynb_CONTEXT`. The notebook's Section 14 rigor assessment
provides a more comprehensive check if you want to validate offline first.

## Verifying the artifacts after a run

```bash
# Latest pointers exist
gsutil ls gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/

# Inspect meta
gsutil cat gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/model_meta_v17.json | jq '.'

# Confirm features count matches HALFINN_FEATURES (42 in v17)
gsutil cat gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/model_meta_v17.json \
  | jq '.features | length'

# Confirm feature_means coverage
gsutil cat gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/model_meta_v17.json \
  | jq '.feature_means | length'
```

## Rollback procedure

If a retrain produces a bad model, roll back by copying a prior archive
over the latest pointer. Archives are timestamped (UTC):

```bash
# List archives, newest first
gsutil ls -l gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/archive/ \
  | sort -k2 -r

# Copy a prior archive over latest
TS="20260512_143000"  # the timestamp you want to restore
gsutil cp \
  gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/archive/xgb_halfinn_v17.${TS}.json \
  gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/xgb_halfinn_v17.json

gsutil cp \
  gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/archive/model_meta_v17.${TS}.json \
  gs://${MLB_GCS_BUCKET}/NRFI_Pro_System/models/model_meta_v17.json
```

The runner reads only the latest pointer, so the next `/run` will pick up
the restored model. No service restart needed.

## When to re-run

- **After NRFI feature schema changes** (new features added to
  `HALFINN_FEATURES`, or a builder rewrite that changes column semantics)
- **Periodically** (monthly?) to incorporate new game outcomes into the
  training set as the season progresses
- **After fixing a feature-builder bug** that would have changed historical
  feature values

The Job has no Cloud Scheduler trigger by default — triggered manually
only. Add a Scheduler if you want automated retrains.

## Gotchas

- **Notebook contract is duplicated** in `training/retrain_nrfi_v17.py`
  (`HALFINN_FEATURES`, `XGB_PARAMS`, `NUM_BOOST_ROUND`,
  `EARLY_STOPPING_ROUNDS`). If you change the notebook's training cell
  (cell `v17_02296`), mirror the change in the Python module **and** flag
  it in the next handoff. There is no automated check that they agree.
- **`best_iteration` is stored as a count, not an index.** The notebook's
  `int(booster.best_iteration) + 1` convention is preserved here. The
  runner uses it as a count via `iteration_range=(0, ntree)`. Don't
  "fix" the +1 — it's load-bearing.
- **Calibrator is absent by design.** The notebook explicitly bypasses
  isotonic calibration (worsened Brier by 0.0005 in testing). The runner
  handles missing calibrator gracefully. If you want to re-introduce
  calibration, fit it in a separate job and write to
  `NRFI_Pro_System/models/isotonic_calibrator_v17.pkl`.
- **Image must be rebuilt** before this Job picks up code changes to
  `training/retrain_nrfi_v17.py`. Cloud Run Jobs pin to `:latest` on each
  execution, so a `gcloud builds submit` is enough — no Job redeploy
  needed (the F5 retrain notes already document this).
