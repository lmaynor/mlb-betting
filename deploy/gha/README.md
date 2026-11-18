# GitHub Actions staging (OPS-15)

`an-daily.yml` in this folder is the production workflow that replaces the 14
`an-daily-{league}` Cloud Run jobs + Cloud Scheduler definitions with GitHub
Actions scheduled runs (saving ~$4.40/mo; Actions minutes are free on this
public repo).

It is staged here, NOT in `.github/workflows/`, for one reason: the GitHub App
integration the agent tooling uses does not hold the Workflows permission, so
every automated write to `.github/workflows/*` returns 403. A human with repo
access has to put the file in place. One step:

1. Create `.github/workflows/an-daily.yml` on `main` with exactly the contents
   of `deploy/gha/an-daily.yml` (GitHub web UI: Add file -> Create new file,
   name it `.github/workflows/an-daily.yml`, paste, commit to main). Or move
   this file there in any local clone and push.

Everything else is already done (Nov 16, 2026):

- Workload Identity Federation is live in project concrete-crow-445205-m4:
  pool `github-actions`, provider `github`, attribute condition pinning
  `repository == lmaynor/mlb-betting`; `mlb-betting-sa` (the same service
  account the Cloud Run jobs run as) grants `roles/iam.workloadIdentityUser`
  to that repo's workflows only. No stored service-account key anywhere.
- Equivalence was proven before cutover: the banking script ran under both
  configurations (Cloud Run env vs Actions env) against scratch buckets for
  NBA and NHL over 2026-11-13..15. NBA: 171/171 raw payloads byte-identical,
  171/171 parquets content-identical. NHL: 60/60 partitions, identical keys,
  rows and totals; 59/60 byte-identical, the one difference traced to an
  upstream Action Network response that arrived with a blank player-identity
  block in one fetch (the Actions-config copy was the more complete one).

After the file lands on main:

1. Dispatch the workflow manually for nba and nhl against the production
   bucket (defaults) and confirm both complete and bank normally.
2. Pause (do not delete) the 14 `an-daily-*` Cloud Scheduler jobs, after
   archiving their full definitions to the bucket + a local copy (definitions
   carry API-key headers; never commit them).
3. The Cloud Run jobs themselves stay in place, idle, as the rollback path.

Timing note: GitHub cron is UTC-only. The crons reproduce the 06:00-06:52 CT
stagger during CST and land one hour later CT during CDT; the banking is
lookback-based and idempotent, so jitter, the seasonal hour, or a dropped run
self-heals on the next run.
