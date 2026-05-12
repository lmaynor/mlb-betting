# Retrain Pipeline — Runbook

Lightweight retrain infrastructure for model artifacts that live in GCS. Pattern matches the `Odds/sgo/latest.json` setup: a stable "latest" key the runtime reads, plus timestamped archives for inspection / rollback.

## Current jobs

| Job name | What it does | Image | Trigger |
|---|---|---|---|
| `mlb-retrain-f5-meta` | Reads F5 `model_features.csv`, computes per-feature means, patches `model_meta_f5_v5.json` | Same as `mlb-betting` service | Manual only |

No models are actually retrained yet — this job only patches metadata. Real retraining (NRFI / F5 / K) will follow the same pattern as new scripts in `training/`.

## One-time setup

```bash
chmod +x deploy/setup_retrain_job.sh
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_retrain_job.sh
```

Idempotent — safe to run repeatedly. Updates the job if it already exists.

## Manual trigger

```bash
gcloud run jobs execute mlb-retrain-f5-meta \
  --region=us-central1 \
  --project=concrete-crow-445205-m4 \
  --wait
```

`--wait` blocks until the job completes and prints the result. Drop it for fire-and-forget.

## Inspect what happened

```bash
# Last few executions
gcloud run jobs executions list --job=mlb-retrain-f5-meta --region=us-central1

# Logs from a specific execution
gcloud run jobs executions logs read <EXECUTION_NAME> --region=us-central1
```

## GCS layout the job touches

```
gs://concrete-crow-445205-m4-mlb-data/
  F5_Pro_System/
    models/
      model_meta_f5_v5.json                                # the "latest" pointer
      archive/
        model_meta_f5_v5.20260512_214530.json              # archived snapshots
        model_meta_f5_v5.20260612_140012.json
        ...
```

The runtime reads only `model_meta_f5_v5.json`. The `archive/` directory accumulates timestamped backups, one per job run. Storage cost is negligible (each file is ~5 KB).

## Roll back

If a retrain produces a meta that breaks predictions, copy an archive over the latest pointer:

```bash
# List archives
gcloud storage ls gs://concrete-crow-445205-m4-mlb-data/F5_Pro_System/models/archive/

# Restore a specific one
gcloud storage cp \
  gs://concrete-crow-445205-m4-mlb-data/F5_Pro_System/models/archive/model_meta_f5_v5.20260512_214530.json \
  gs://concrete-crow-445205-m4-mlb-data/F5_Pro_System/models/model_meta_f5_v5.json
```

## How to add a new retrain script

1. Create `training/retrain_<system>_<thing>.py` with a `run()` that returns a result dict and a `main()` that calls `sys.exit`.
2. Use the same GCS read/write pattern: read from a stable key, write to both a timestamped archive AND overwrite the latest pointer.
3. Copy `setup_retrain_job.sh` to `setup_retrain_<system>_<thing>_job.sh`, change `JOB_NAME` and the `--args` to point at the new module.
4. Run the setup script with `PROJECT_ID=... ./deploy/setup_<...>_job.sh`.

## Cost

Per execution: ~2 GB image pull + ~30s of 2 vCPU / 2 GB run time. Under $0.01 per run. At a monthly cadence, annual cost is well under $1.

## What's NOT in this pipeline yet

- Real model retraining (only meta patching). NRFI / F5 / K full retrains are open action items.
- Scheduled triggers. Cloud Scheduler entry for a monthly cron will be added once the first real retrain script is built and trusted.
- Email/Discord notifications on failure. Job failures show in Cloud Run console logs; not automated yet.
