---
module: deploy
tags: [cloud-run-jobs, cloud-scheduler, iam, silent-failure]
problem_type: runtime-error
---

# Schedulers on new Cloud Run Jobs fail every firing with PERMISSION_DENIED

## Symptom

A newly provisioned Cloud Run Job never runs on schedule. The Cloud Scheduler
job shows `state: ENABLED` and a recent `lastAttemptTime`, so everything LOOKS
healthy -- but `gcloud run jobs executions list` shows zero scheduler-invoked
executions (only manual ones, `RUN BY <your account>`). Downstream, whatever
data the job banks silently stops accumulating.

Diagnostic that nails it:

    gcloud scheduler jobs describe <sched-job> --location=us-central1 \
      --format="value(state,status.code,lastAttemptTime)"

`status.code: 7` = PERMISSION_DENIED.

## Root cause

The `scheduler-invoker` service account needs `roles/run.invoker` **on each
Cloud Run Job** it triggers (job-level IAM, not project-level). Our
`deploy/setup_*.sh` scripts created the job + scheduler but never granted the
binding, so every firing 403'd. This bit three jobs in a row
(mlb-track-bettingpros, mlb-fast-alert, mlb-weekly-survival) -- the tracker
lost 2 days of unrecoverable intraday odds snapshots (2026-07-03/04) before
anyone noticed, because a denied scheduler firing alerts NOTHING.

## Fix

    gcloud run jobs add-iam-policy-binding <job> --region=us-central1 \
      --member=serviceAccount:scheduler-invoker@<project>.iam.gserviceaccount.com \
      --role=roles/run.invoker

All `deploy/setup_*.sh` job-provisioning scripts now apply this binding right
after `gcloud run jobs create/update` (2026-07-05). Copy that block into any
new setup script.

## Prevention / detection

- After provisioning ANY new scheduled job, verify the first scheduled firing
  actually produced an execution (`executions list`, RUN BY should be the SA).
- `status.code` on the scheduler describe is the fast tell; the scheduler UI
  showing ENABLED means nothing about whether invocations succeed.
- monitor_ops checks GCS freshness of the SGO snapshot and feature builds but
  NOT odds_history partitions -- consider adding a freshness check for
  `Odds/history/market=k_ou` partition dates if the tracker matters long-term.
