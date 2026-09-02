---
title: gcloud run jobs update --set-env-vars silently wipes other env vars (TWEET_MODE lost)
module: deploy, gcloud, tweet_drafter.py
tags: [cloud-run, jobs, gcloud, deploy, env-vars, tweet-drafter]
problem_type: runtime_error
category: runtime-errors
date: 2026-09-02
---

## Problem

`mlb-tweet-recap` (Cloud Run Job) had no `TWEET_MODE` env var set, even though
CONTEXT.md s9 and RUNBOOKS.md s4 both document it as `TWEET_MODE=recap`.
`tweet_drafter.py:41` defaults `TWEET_MODE` to `"picks"` when unset, so the
recap job silently ran the picks code path every day instead.

## Symptoms

- `gcloud run jobs describe mlb-tweet-recap` shows an env list identical to
  `mlb-tweet-picks` (`SITE_API_KEY`, `GEMINI_API_KEY`, `TYPEFULLY_API_KEY`,
  `DISCORD_WEBHOOK_URL`, `BEEZY_API_URL`, `BEEZY_SITE_URL`) -- no `TWEET_MODE`
  on either job.
- Real log line at run time: `[tweet_drafter] mode=picks` on a job that was
  supposed to run in recap mode.
- The job still shows `SUCCEEDED_COUNT=1` in `gcloud run jobs executions
  list` every day -- because `mode=picks` hits `get_today_picks()` at
  10:00 UTC (5am ET), long before feature builds (14:30 UTC) or scoring
  (16:00/22:00 UTC) run, so `picks_list` is always empty and the job exits 0
  via the early-return "No picks today -- skipping." A clean exit code with
  zero actual output, every day, indefinitely -- exactly the kind of failure
  `monitor_ops`/execution-history checks cannot catch, because nothing is
  failing.
- The Cloud Scheduler job (`mlb-tweet-recap-schedule`) itself carries **no
  HTTP body at all** (`gcloud scheduler jobs describe ... --format="yaml(httpTarget)"`
  shows no `body` field) -- so the "Body: TWEET_MODE=recap" column in
  CONTEXT.md s9's scheduler table was aspirational documentation, never
  actually wired. The scheduler only ever calls `.../jobs/mlb-tweet-recap:run`
  with no override; the Job's own baked-in env vars are the only thing that
  determines `MODE`.

## Root Cause

RUNBOOKS.md s4's own "one-shot Cloud Shell update after domain or tweet job
code changes" snippet does:

```bash
for JOB in mlb-tweet-picks mlb-tweet-recap; do
  gcloud run jobs update "$JOB" \
    --region "$REGION" \
    --image "$IMAGE" \
    --set-env-vars BEEZY_API_URL=https://api.beezy.fyi,BEEZY_SITE_URL=https://beezy.fyi
done
```

`--set-env-vars` **replaces the job's entire env var set**, not just the two
named keys -- this is the same "full replace vs. merge" flag class already
documented for `--set-secrets`/`--update-secrets` and
`--set-cloudsql-instances`/`--add-cloudsql-instances` elsewhere in this repo.
Any `TWEET_MODE` value previously set by hand on `mlb-tweet-recap` gets
silently dropped the next time this snippet runs (any time `tweet_drafter.py`
changes, or the domain changes) -- with no error, since the two vars it does
set succeed fine.

There is also no `deploy/setup_tweet_jobs.sh` (or similar) checked-in
provisioning script for these two jobs at all -- they were originally
hand-created, same class of gap as `mlb-build-all-features` before
`deploy/setup_build_all_features.sh` was written.

## Fix

1. Live: `gcloud run jobs update mlb-tweet-recap --region=us-central1
   --update-env-vars=TWEET_MODE=recap` (merge, not replace).
2. RUNBOOKS.md's snippet changed `--set-env-vars` -> `--update-env-vars` so
   future runs of it stop wiping `TWEET_MODE`.
3. `deploy/setup_tweet_jobs.sh` added: idempotent, checked-in provisioning
   for both jobs (image, secrets, env vars including `TWEET_MODE` per job) --
   re-run this instead of hand-crafting `gcloud run jobs update` calls.

## Prevention

**Any one-off `gcloud run jobs update`/`gcloud run services update` snippet
that only intends to touch a subset of env vars must use `--update-env-vars`,
never `--set-env-vars`.** Reserve `--set-env-vars` for the rare case where you
deliberately want to replace the entire env set (e.g. inside a real `create`-
or-`update` provisioning script that lists every var explicitly, like
`deploy/setup_tweet_jobs.sh`). When adding a new env var to an existing job by
hand for a one-off test, always re-run the job's real setup script afterward
(or add one if none exists) so the change is source-controlled and survives
the next redeploy.
