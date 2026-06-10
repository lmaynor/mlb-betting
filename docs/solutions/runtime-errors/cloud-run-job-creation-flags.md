---
title: Cloud Run Job creation flag gotchas (--args, --set-cloudsql-instances, image registry)
module: deploy, gcloud
tags: [cloud-run, jobs, gcloud, deploy]
problem_type: runtime_error
category: runtime-errors
date: 2026-05-24
---

## Problem

Cloud Run Job creation or update fails with confusing errors, or succeeds but the job can't connect to Cloud SQL or Secret Manager.

## Symptoms

- `--args="-m,runners.build_game_features"` fails: "expected one argument"
- Job exits with Secret Manager permission denied
- Job creation succeeds but execution returns NOT_FOUND for the image

## Root Causes and Fixes

**`--args` takes repeated flags, not comma-separated values:**
```bash
# Wrong
gcloud run jobs create mlb-my-job --args="-m,runners.build_my_features"

# Correct
gcloud run jobs create mlb-my-job --args="-m" --args="runners.build_my_features"
```

**Job creation uses `--set-cloudsql-instances`, not `--add-cloudsql-instances`** (which is the service flag):
```bash
--set-cloudsql-instances=concrete-crow-445205-m4:us-central1:mlb-postgres
```

**Always specify `--service-account`** or the job uses the default compute SA which lacks Secret Manager access:
```bash
--service-account=mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com
```

**Image registry is `gcr.io`, NOT Artifact Registry:**
```bash
--image gcr.io/concrete-crow-445205-m4/mlb-betting:latest
```
The Artifact Registry path returns NOT_FOUND on execute.

**Scheduler `--attempt-deadline` max is 1800s** (not the job execution timeout). The Run API call returns a LongRunningOperation immediately (async); the scheduler only needs ~60s. The actual job timeout is set on the job with `--task-timeout`.

## Prevention

See RUNBOOKS.md for the full working `gcloud run jobs create` command. Always copy from there rather than constructing from scratch.
