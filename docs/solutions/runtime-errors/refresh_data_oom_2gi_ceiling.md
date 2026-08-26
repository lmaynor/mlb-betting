---
module: deploy
tags: [cloud-run, memory, oom, monitor-ops, scheduler]
problem_type: runtime-error
---

# `/refresh-data` (and `/build-features`) intermittently OOM-killed at the 2Gi service memory ceiling

## Symptom

`mlb-refresh-data` (and occasionally other heavy endpoints on the same
service) shows up in the daily `monitor_ops` ops-alert as:

    `mlb-refresh-data` last run failed (code=14, at=<timestamp>)

`code=14` = UNAVAILABLE. The Cloud Scheduler job itself looks fine
(`state: ENABLED`, a recent `lastAttemptTime`) -- the failure is inside the
Cloud Run **service** handling the request, not the scheduler.

Diagnostic that nails it -- pull the actual Cloud Run system log (not just
the app's own logger output) around the failure timestamp:

    gcloud logging read '
    resource.type="cloud_run_revision"
    resource.labels.service_name="mlb-betting"
    severity>=ERROR
    timestamp>="<window start>" timestamp<="<window end>"
    ' --format="value(timestamp,severity,textPayload)"

Look for:

    While handling this request, the container instance was found to be
    using too much memory and was terminated.
    Memory limit of 2048 MiB exceeded with <N> MiB used.

## Root cause

`/refresh-data` does a LOT in one request/process: weather + umpire +
scoring + Statcast masters, six Savant leaderboards (in-season), and (since
2026-08-19) the four AuxData sources (FanGraphs pitching, swing-take, team
schedule, manager hooks) -- see CONTEXT.md s4 "Loop A". `/build-features`
similarly runs multiple systems' feature builders sequentially in one
request. Both load full-history CSVs (Statcast alone is 946k+ pitch rows
and growing every day of the season) into memory.

The Cloud Run **service** (`mlb-betting`) was sized at `2Gi` -- a limit set
long before this many systems/datasets existed. Memory usage has been
creeping up all season (observed range 2051-2330 MiB against the 2048 MiB
ceiling over 2026-07-28 through 2026-08-25) and `/refresh-data`'s own
latency grew from ~140-170s to ~200-230s over the same window -- consistent
with organically growing data volume, not a sudden leak. Whichever request
happens to land on the larger side of that variance gets OOM-killed; Cloud
Run returns 503, which Cloud Scheduler's HTTP target records as
`status.code=14`.

This is why the *count* of daily `monitor_ops` failures fluctuates
(1-3/day) rather than being constant: on top of the deterministic scheduler
IAM bug (see
[scheduler-permission-denied-on-new-jobs.md](scheduler-permission-denied-on-new-jobs.md)),
this OOM fires roughly every few days whenever that day's data volume pushes
a request over the ceiling -- confirmed via `gcloud logging read` across
2026-07-28, 08-01, 08-02, 08-06, 08-07, 08-13, 08-14, 08-16, 08-19, 08-22,
08-25 (11 occurrences in 29 days, ~40% of `/refresh-data`'s twice-daily
executions in that window).

## Fix

Bump the Cloud Run **service**'s memory limit (Google's own error message
suggests exactly this). Applied 2026-08-26: `2Gi -> 4Gi` (kept `cpu=2`,
memory was the only resource cited in the error).

    gcloud run services update mlb-betting --region=us-central1 \
      --memory=4Gi --cpu=2

Also baked explicitly into `deploy/deploy_service.sh`'s
`gcloud run services update` call (it previously didn't pass `--memory` at
all, relying on whatever the live revision happened to already have --
tribal-knowledge config, the same anti-pattern flagged elsewhere in this
repo's history) so the setting survives the next full deploy instead of
silently drifting back to a default.

Verified the fix by manually firing `mlb-refresh-data`
(`gcloud scheduler jobs run mlb-refresh-data`) against the new revision and
confirming a clean 200 rather than another 503 -- raising the limit without
re-triggering a real request would only be a theory, not a verified fix.

## Prevention / detection

- `monitor_ops`'s scheduler check surfaces this (code=14), but only as a
  bare code with no memory number attached -- for the actual MiB figures
  you still have to go to `gcloud logging read` / Cloud Logging directly.
  Consider having `monitor_ops` (or a lighter weekly job) grep for
  `"Memory limit of"` itself and fold the observed high-water-mark into the
  alert text, so a growing trend is visible from the Discord alert alone
  rather than requiring a manual log pull to notice the creep.
- If this recurs after the 4Gi bump, check whether data volume has simply
  kept growing (expected, in-season) vs. an actual leak -- the tell is
  whether latency/memory keeps climbing indefinitely within a SINGLE
  request (leak) or just tracks total row counts season-over-season
  (capacity). Re-run the `gcloud logging read "Memory limit of"` query
  above over a longer window before reaching for another memory bump.
- `/build-features` shares the same service and the same ceiling and was
  seen OOMing too (2026-08-17/18), though those instances lined up with ad
  hoc heavy backfill/reprocess calls from other sessions rather than the
  routine 14:30 UTC `mlb-build-all-features` schedule -- worth re-checking
  after the 4Gi bump if a *routine* `/build-features` 503 shows up in
  `monitor_ops` specifically (as opposed to `/refresh-data`).
