---
title: "New Cloud Run Jobs never fire: scheduler-invoker needs roles/run.invoker ON EACH JOB (scheduler status code 7)"
module: deploy/setup_*.sh
tags: [cloud-scheduler, cloud-run-jobs, iam, permission-denied, provisioning]
problem_type: runtime_error
category: runtime-errors
date: 2026-10-14
---

## Problem

After provisioning 11 new jobs on 2026-10-14 (NFL mirror, NHL refresh, NBA crosswalk, props alerts/grade, odds banking), every
scheduler entry for them showed `status.code: 7` (PERMISSION_DENIED) on its first firing, and the job never ran. Nothing alerts on
this loudly: the scheduler entry is `ENABLED`, the job exists, a manual `gcloud run jobs execute` works (it uses YOUR credentials).
`nhl-refresh-data` was denied at 12:30 UTC and the nightly NHL refresh did not run.

## Root cause

The scheduler calls the Cloud Run Jobs API (`.../jobs/{name}:run`) with an OAuth token for `scheduler-invoker@`. That service
account must hold `roles/run.invoker` ON THE JOB. The role is per job, not inherited: the jobs that worked (`nba-refresh-data`,
`mlb-build-all-features`) had a binding; none of the new ones did, because the provisioning scripts created the job and the scheduler but
never granted it. The older scripts that already had the grant carried a comment that this "bit us for 3 jobs in a row" -- it has
recurred since.

## Diagnose

```bash
gcloud scheduler jobs describe <name> --location=us-central1 --format='yaml(status,lastAttemptTime)'   # code: 7
gcloud run jobs get-iam-policy <job> --region=us-central1                                              # no roles/run.invoker
```

## Fix

```bash
gcloud run jobs add-iam-policy-binding <job> --region=us-central1 --member=serviceAccount:scheduler-invoker@<project>.iam.gserviceaccount.com --role=roles/run.invoker
gcloud scheduler jobs run <scheduler-name> --location=us-central1      # proves the real path (a manual `jobs execute` does not)
```

Every `deploy/setup_*.sh` that creates a job-trigger scheduler now grants the role before creating it (idempotent), and
`tests/test_deploy_scripts_iam.py` fails any script that creates one without it.

## Prevention

After provisioning any job with a schedule, trigger the SCHEDULER (not the job) once and confirm `status.code` is empty and an
execution started. `monitor_ops` enumerates scheduler status codes, so this also surfaces in #ops-alerts the next morning.
