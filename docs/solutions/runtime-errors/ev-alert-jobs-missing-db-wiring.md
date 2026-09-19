---
title: mlb-fast-alert / mlb-kalshi-alert had no MLB_DB_URL -- every EV bet silently discarded since launch
module: deploy, bet_tracker.py, fast_alert_loop.py, kalshi_alert.py
tags: [cloud-run, jobs, gcloud, cloudsql, db-url, bet-tracker, ev-alerts, data-loss]
problem_type: runtime_error
category: runtime-errors
date: 2026-09-18
---

## Problem

`mlb-fast-alert` and `mlb-kalshi-alert` (the two Cloud Run Jobs behind the
intraday +EV Discord pagers) had **no `MLB_DB_URL` secret and no
`--set-cloudsql-instances` binding** since the EV bet tracking feature
shipped (2026-08-20). `mlb_core/tracking/bet_tracker.py::_make_engine()`
silently falls back to an ephemeral local SQLite file
(`EV_Alerts/data/ev_bets.db`, inside the job's own container filesystem)
whenever `MLB_DB_URL` is empty -- no exception, no warning. Every single
"successfully logged" EV alert since launch was written to that file and
discarded the instant the container exited.

## Symptoms

- Real Cloud Run Job logs show a completely convincing success trail, as
  recent as the day this was found:
  ```
  2026-09-18T23:10:12Z  EV: 1/1 posted alerts logged to bets table (system=EV)
  2026-09-18T23:10:12Z  [EV] Bet #1 logged: BATTER_TB_UNDER_0.5_fanatics Alex Call | edge: +6.4%
  ```
- But a direct query against the real production Postgres DB
  (`SELECT system, COUNT(*) FROM bets GROUP BY system`) returns **zero** rows
  for `system='EV'`, while every other system is present in force (HR=8083,
  BATTER_HITS=3282, K=2836, ...).
- `gcloud run jobs describe mlb-fast-alert` / `mlb-kalshi-alert` (env +
  annotations) show `MLB_GCS_BUCKET`, `DISCORD_WEBHOOK_URL`,
  `DISCORD_WEBHOOK_ALERTS` and the job's own `FAL_*`/`KALSHI_ALERT_*` env
  vars -- no `MLB_DB_URL`, no `run.googleapis.com/cloudsql-instances`
  annotation at all.
- The scheduler jobs (`mlb-fast-alert-loop`, `mlb-fast-alert-night`,
  `mlb-kalshi-alert-*`) all show `ENABLED` with a healthy `lastAttemptTime`
  every day -- nothing in the scheduler layer signals a problem, because
  nothing is failing.

## Root Cause

`deploy/setup_fast_alert.sh` and `deploy/setup_kalshi_alert_job.sh` -- the
checked-in, idempotent provisioning scripts for these two jobs -- never
included `MLB_DB_URL` in their `--set-secrets` list, nor
`--set-cloudsql-instances`. Both scripts' own header comments explicitly
documented this as deliberate: *"GCS only (no DB)"* -- true when the scripts
were first written (both pagers were originally pure GCS-state, Discord-only
loops), but the 2026-08-20 EV bet tracking feature added a real Postgres
write (`_log_ev_bets()` -> `BetTracker.log_bet()`) to both pagers without
anyone updating either provisioning script.

Since the gap lived in the checked-in script itself (not just a one-off hand
edit), every idempotent re-run of `setup_fast_alert.sh` /
`setup_kalshi_alert_job.sh` since 2026-08-20 reproduced the same missing
wiring. This is the same failure class as two prior incidents in this repo:
a Cloud Run Job silently missing standard wiring
(`docs/solutions/integration-issues/parlayapi-credit-exhaustion-zombie-jobs-mislabeled-sgo.md`'s
missing `run.invoker` IAM, `docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md`'s
missing `TWEET_MODE`) -- a job whose provisioning script was written before
some later feature needed new wiring, and never revisited when that feature
landed.

`mlb_core/tracking/bet_tracker.py::_make_engine()`'s fallback exists on
purpose, for real local/offline dev use:
```python
def _make_engine(db_path: str) -> sa.Engine:
    url = DB_URL or ""
    if url:
        return sa.create_engine(url)
    sqlite_path = Path(db_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return sa.create_engine(f"sqlite:///{sqlite_path}")
```
`DB_URL = os.environ.get("MLB_DB_URL", "")` (`mlb_core/config.py`) -- an
empty string is falsy, so a missing env var is silently indistinguishable
from "run locally against sqlite on purpose." Every other production runner
gets `MLB_DB_URL` from its own provisioning script (e.g.
`deploy/setup_fit_calibrators.sh`, which reads the bets table for the exact
same reason and correctly wires both flags), so this fallback had simply
never fired in production before these two jobs.

## Fix

1. `deploy/setup_fast_alert.sh` / `deploy/setup_kalshi_alert_job.sh`: added
   `MLB_DB_URL=mlb-db-url:latest` to `--set-secrets` and
   `--set-cloudsql-instances="${PROJECT_ID}:${REGION}:mlb-betting-db"`,
   matching `deploy/setup_fit_calibrators.sh`'s pattern exactly. Corrected
   the stale "GCS only (no DB)" header comments on both scripts.
2. Re-ran both scripts against the live jobs (`gcloud run jobs update`, no
   new image needed -- job config only).
3. Verified with a real manual `gcloud run jobs execute` on each job,
   confirmed a new `system='EV'` row landed in the real Postgres `bets`
   table.
4. Recovered the lost history: `scripts/backfill_ev_history.py` walks
   `Alerts/{day}/log.parquet` + `notified.parquet` (fast_alert) and
   `kalshi_log.parquet` + `kalshi_notified.parquet` (kalshi) -- both durably
   written to GCS the whole time, unaffected by this bug -- inner-joins each
   day's full-detail log against its true-posted-keys file to recover
   exactly what was actually alerted (not every candidate merely scanned),
   and re-inserts via the real `BetTracker.log_bet()` (safe to re-run: hits
   the production dedup index). See that script's own docstring for detail.

## Prevention

**Whenever a runner is given a new persistent side effect (a DB write, a new
external API call, a new required secret), check whether every Cloud Run Job
that executes that code path has the matching provisioning-script wiring --
don't assume an existing job "already has everything it needs" just because
it's been running successfully.** "Running successfully" and "actually
persisting its output" are different claims; `BetTracker`'s fail-open sqlite
fallback (a reasonable default for local dev) means a missing `MLB_DB_URL`
produces zero errors, zero warnings, and a completely convincing success log
line -- the only way this was caught was a direct query against the real
production table, not by reading logs or trusting a "logged N/N" message at
face value. When a Cloud Run Job's own provisioning script contains a
comment asserting a scope limitation ("GCS only", "read-only", "no DB") --
treat that comment as a claim to re-verify, not a fact, any time that job's
underlying code changes.
