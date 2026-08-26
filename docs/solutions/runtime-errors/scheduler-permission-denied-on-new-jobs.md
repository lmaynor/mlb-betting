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

**Recurrence (2026-08-10):** `mlb-kalshi-capture` (shipped 2026-07-23 on the
`kalshi` branch, `deploy/setup_kalshi_capture.sh`) shipped without the binding
too -- the 2026-07-05 fix never got copied into that script, because it was
written on a side branch that never merged main's `deploy/` changes. All 8
`mlb-kalshi-*` scheduler jobs 403'd from day one; caught via `monitor_ops`'s
17-failure alert, not via the tracker itself (same "denied firing alerts
nothing" blind spot). Fixed live via the `add-iam-policy-binding` command below
and patched into `deploy/setup_kalshi_capture.sh` directly on the `kalshi`
branch (2026-08-10).

**Recurrence (2026-08-26): the sibling job got missed during the 2026-08-10
fix itself.** `deploy/setup_kalshi_capture.sh` provisions TWO Cloud Run Jobs
in the same script (`mlb-kalshi-capture` and `mlb-kalshi-capture-closing`,
the latter for the 23:05 UTC closing-line snapshot only) and its
`_upsert_job()` helper already applies the IAM-binding fix to whichever job
it's called for. But the 2026-08-10 recurrence above was fixed with a
one-off manual `add-iam-policy-binding` targeting `mlb-kalshi-capture` by
name (not a full re-run of the script), so `mlb-kalshi-capture-closing`'s
binding was never applied. Its scheduler entry (`mlb-kalshi-2305`) likely
hadn't fired yet at the moment the 2026-08-10 fix was spot-checked, so its
`status.code` was still `-1` ("never run") rather than `7` -- invisible to
a quick look. Net effect: `mlb-kalshi-capture-closing` had an **empty IAM
policy from creation (2026-07-23) through 2026-08-26 -- 34 days, zero
successful executions ever** (`gcloud run jobs executions list` was
completely empty, not just missing recent runs), silently confirmed by
`monitor_ops`'s daily ops-alert firing at least once every single day for
that entire window without anyone connecting it to this specific job (the
Discord embed shows the bullet-list detail; the alert-posted log line
monitor_ops itself emits only shows a failure *count*, so recognizing "it's
always this one" from Cloud Logging alone takes an explicit
`_check_schedulers()` re-run, not just reading log lines). Fixed the same
way, verified this time by manually firing the scheduler
(`gcloud scheduler jobs run mlb-kalshi-2305`) and confirming both a
terminal `Completed: True` execution AND a real new object landing in
`Odds/kalshi/raw/{date}/`, not just an absence of a 403.

**Lesson reinforced: when a setup script provisions N jobs, verify the
IAM binding on ALL N after any fix, not just the one whose alert you
followed** -- ideally by re-running the whole `setup_*.sh` (idempotent by
design) rather than a targeted `add-iam-policy-binding` on one resource.
A quick repo-wide audit for this specific class (worth re-running whenever
a new scheduled Cloud Run Job ships, or whenever triaging a monitor_ops
alert that mentions PERMISSION_DENIED/code=7):

    for job in $(gcloud scheduler jobs list --location=us-central1 \
        --format="value(httpTarget.uri)" | grep -oP '(?<=/jobs/)[^:]+' | sort -u); do
      gcloud run jobs get-iam-policy "$job" --region=us-central1 \
        --format="value(bindings)" | grep -q scheduler-invoker \
        && echo "OK    $job" || echo "MISSING  $job"
    done

## Fix

    gcloud run jobs add-iam-policy-binding <job> --region=us-central1 \
      --member=serviceAccount:scheduler-invoker@<project>.iam.gserviceaccount.com \
      --role=roles/run.invoker

All `deploy/setup_*.sh` job-provisioning scripts on `main` apply this binding
right after `gcloud run jobs create/update` (2026-07-05). Copy that block into
any new setup script -- **including ones written on side/analysis branches**,
since those don't inherit fixes made to `main` after they diverged.

## Prevention / detection

- After provisioning ANY new scheduled job, verify the first scheduled firing
  actually produced an execution (`executions list`, RUN BY should be the SA).
- `status.code` on the scheduler describe is the fast tell; the scheduler UI
  showing ENABLED means nothing about whether invocations succeed.
- monitor_ops checks GCS freshness of the SGO snapshot and feature builds but
  NOT odds_history partitions -- consider adding a freshness check for
  `Odds/history/market=k_ou` partition dates if the tracker matters long-term.
- Any `deploy/setup_*.sh` written on a branch that forked from `main` before
  2026-07-05 (or before whatever the latest fix-date here is) needs this block
  copied in manually -- it will not have inherited it. Diff new setup scripts
  against `setup_track_bettingpros.sh` before first use.
