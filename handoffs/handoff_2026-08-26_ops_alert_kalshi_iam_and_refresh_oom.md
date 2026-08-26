# Handoff -- 2026-08-26 -- Ops-alert diligence pass: 2 real production bugs found, fixed, verified live

Picked up from `handoffs/handoff_2026-08-25_ev_discord_merge_deploy_and_ci_timezone_fix.md`
on the user's request to check the `#ops-alerts` / `monitor_ops` Discord alert
history and patch whatever's been causing it to fire "pretty frequently."

## TL;DR

`monitor_ops` had posted an ops-alert on **every single day for at least the
past 14 days** (checked via `gcloud logging read`, 2026-08-12 through
2026-08-25, 1-3 failures/day, never zero). Root-caused to **two independent,
unrelated bugs**, both fixed and independently verified against live
production, merged to `main` (`f09efea`):

1. **`mlb-kalshi-capture-closing` (Cloud Run Job) had an empty IAM policy --
   zero successful executions in the 34 days since it was created
   (2026-07-23).** `scheduler-invoker` was never granted `roles/run.invoker`
   on it, unlike its sibling `mlb-kalshi-capture`. This is a third
   recurrence of the documented
   `docs/solutions/runtime-errors/scheduler-permission-denied-on-new-jobs.md`
   failure class -- the 2026-08-10 fix for the sibling job was a one-off
   manual `add-iam-policy-binding`, not a re-run of the idempotent setup
   script, so this job was missed. This alone explains the "at least one
   failure every day" baseline: it fires deterministically at 23:05 UTC and
   monitor_ops runs ~15:20 UTC the next day, always downstream of the
   previous night's failure.
2. **`/refresh-data` intermittently OOM-killed at the Cloud Run service's
   2Gi memory ceiling** -- confirmed via `"Memory limit of 2048 MiB
   exceeded with N MiB used"` in Cloud Logging, ~11 times over the past 29
   days (~40% of its twice-daily runs) as statcast/feature data has grown
   all season (latency crept from ~140-170s to ~200-230s over the same
   window). This explains the occasional 2nd/3rd daily failure on top of
   (1).

## What was actually done (all live, all verified -- not just "should fix
this now")

**Bug 1 fix:**
- `gcloud run jobs add-iam-policy-binding mlb-kalshi-capture-closing
  --region=us-central1 --member=serviceAccount:scheduler-invoker@... 
  --role=roles/run.invoker`
- Verified end-to-end, not just "no more 403": manually fired
  `gcloud scheduler jobs run mlb-kalshi-2305`, confirmed a terminal
  `Completed: True` execution (`mlb-kalshi-capture-closing-w8dcd`,
  1m59.6s) AND a real new object landing in
  `Odds/kalshi/raw/2026-08-26/snapshot_0358.json`.
- **Audited all 16 unique scheduler-invoked Cloud Run Jobs in the project**
  for the same missing-binding pattern (loop over every
  `gcloud scheduler jobs list` entry whose target is a
  `run.googleapis.com/.../jobs/{name}:run` URI, check
  `gcloud run jobs get-iam-policy` on each unique job name). No other
  instances found -- this was the only one.
- Updated `docs/solutions/runtime-errors/scheduler-permission-denied-on-new-jobs.md`
  with this as a documented third recurrence, including the audit
  one-liner so the next person (or session) can re-run the same check in
  30 seconds instead of re-discovering it.

**Bug 2 fix:**
- `gcloud run services update mlb-betting --region=us-central1 --memory=4Gi --cpu=2`
  (was 2Gi/2cpu) -- live now on revision `mlb-betting-00292-grc`.
- Baked `--memory=4Gi --cpu=2` explicitly into `deploy/deploy_service.sh`'s
  `gcloud run services update` call (it previously passed neither flag,
  relying on whatever the live revision already happened to have --
  exactly the "config only lives in current GCP state, not in a script"
  anti-pattern this repo has been bitten by before). This is now the
  documented source of truth so it survives the next full deploy.
- Verified by manually firing `gcloud scheduler jobs run mlb-refresh-data`
  against the new revision and confirming a clean **200, 176.6s latency**
  (previously: crashes at 503 in 51-98s, OOM message in Cloud Logging;
  clean runs at 130-230s). No memory-limit error logged for this run.
- New doc:
  `docs/solutions/runtime-errors/refresh_data_oom_2gi_ceiling.md`
  (symptom/root-cause/fix/prevention, matching this repo's convention),
  including a note that `/build-features` shares the same ceiling and was
  seen OOMing too on 2026-08-17/18 (from ad hoc heavy backfill calls, not
  the routine schedule) -- worth re-checking if a *routine* `/build-features`
  503 shows up post-bump.

**Final state, confirmed clean right now:**

```
$ python3 -c "from mlb.runners.monitor_ops import run; print(run())"
  (run locally against live GCS/Scheduler with MLB_GCS_BUCKET/GCP_PROJECT
   set -- see below for why this works despite this laptop's usual
   Netskope interception of the Cloud Run *service* URL)
{
  "_check_schedulers": [],
  "_check_odds_history_freshness": [],
  "_check_data_masters": [],
  "_check_snapshot_freshness": [],
  "_check_build_sentinels": [],
  "_check_stuck_bets": [],   # see caveat below -- not independently verified
  "_check_feature_freshness": [],
  "_check_model_artifacts": []
}
```

Not literally a call to the live `/monitor-ops` endpoint (this laptop's
requests to the Cloud Run service/`api.beezy.fyi` still don't reach it, per
the 2026-08-20/25 handoffs' standing environment note) -- instead, imported
`mlb.runners.monitor_ops` locally and called its check functions directly
with `MLB_GCS_BUCKET`/`GCP_PROJECT`/`SCHEDULER_LOCATION` set. This works
because those functions talk to `storage.googleapis.com` /
`cloudscheduler.googleapis.com` directly via the `google-cloud-*` SDKs
(same class of call `gcloud` itself makes, which already worked all
session), not to the Cloud Run service's own URL -- so it isn't affected by
whatever Netskope is intercepting.

**Caveat -- `_check_stuck_bets` (pending-bets-over-3-days) was NOT
independently re-verified against the real production DB.** It no-ops
locally (falls back to a scratch SQLite with no `bets` table, catches the
resulting exception, logs a warning, returns `[]`) because no
`MLB_DB_URL`/Cloud SQL Proxy was set up in this session -- the memory note
about a prior session having psql+proxy access set up was for that
session's shell, not this one, and re-establishing it wasn't done here
since neither root cause found implicated stuck bets, and there's no log
evidence either way that it ever contributed to a failure count. If
`monitor_ops` alerts again soon and neither bug above explains it, this is
the next thing to check for real (set up `cloud-sql-proxy`, point
`MLB_DB_URL` at `localhost`, re-run `_check_stuck_bets()` for real).

## Also flagged, not fixed here (spawned as a separate background task)

CONTEXT.md documents several daily job times that no longer match live
Cloud Scheduler cron config (drifted after being written; the "Full daily
schedule" table itself is accurate, only narrative text elsewhere
disagrees with it):
- `mlb-refresh-data`: docs say 08:00 UTC in one place, live is 14:00 UTC.
- `mlb-build-all-features`: docs say 12:00 UTC in one place, live is 14:30 UTC.
- `mlb-monitor-ops`: docs say 12:50 UTC (CONTEXT.md, 2 places) or 13:15 UTC
  (CONTEXT.md once, `monitor_ops.py`'s own docstring once), live is
  15:20 UTC.

Docs-only, no behavior implications -- spawned as `task_a4a1f4b0` with exact
line numbers and verified-correct values rather than fixed inline, to keep
this session's diff focused on the actual bugs.

## Verification

- `pytest tests/ -q` (via `.venv_audit/`) -- 635/635 passed, before and
  after this session's changes (bash script + 2 new/edited markdown docs
  only -- no Python touched).
- `bash -n deploy/deploy_service.sh` -- syntax clean.
- Both fixes verified against LIVE production (not just "should work now"):
  real Cloud Run Job execution + real GCS object for bug 1; real HTTP
  200/176.6s Cloud Run request + scheduler status clearing for bug 2; a
  final full local re-run of every GCS/scheduler-based `monitor_ops` check
  showing all-clean.
- Branch `fix/ops-alert-kalshi-iam-and-refresh-oom-2026-08-26`, merged to
  `main` locally (gh still not authenticated in this repo/session -- same
  as every prior session's note on this), pushed (`f09efea`).

## Where things stand

`main` has both fixes. Live Cloud Run service is on revision
`mlb-betting-00292-grc` (4Gi/2cpu) -- this was a config-only
`services update`, not a full `deploy_service.sh` run, so the image itself
is unchanged from `mlb-betting-00291-4f9` (2026-08-25's EV/Discord +
timezone-fix deploy) -- only resource limits changed. `mlb-kalshi-capture-closing`
now has the same IAM binding as its 15 sibling scheduler-invoked jobs, all
of which were audited clean. Next `monitor_ops` run (today ~15:20 UTC, if
not already past by the time this is read) should be the first clean one
in at least 15 days -- worth a quick look at `#ops-alerts` to confirm no
alert posts, as the actual real-world confirmation beyond this session's
own direct checks.
