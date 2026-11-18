---
title: Bankroll Challenge launch and rollback runbook (order of steps, flags, who can do what)
module: contest
tags: [contest, runbook, deploy, scheduler, clerk, flags, rollback]
problem_type: convention
category: conventions
date: 2026-11-18
---

## State as of 2026-11-18

Merged (PRs 337, 338), Cloud Run deployed (`mlb-betting-00340-n4p`), Vercel deployed. EVERYTHING IS OFF: `CONTEST_ENABLED` is not set anywhere, so
`/contest*`, `/api/contest/*` and the Flask routes answer 404 / `{"error":{"code":"OFF"}}`. No contest row exists in the production database.

## Turning it on (one command, held until the owner says go)

`START=1 PROJECT_ID=concrete-crow-445205-m4 CONTEST_SLUG=<slug> CONTEST_INVITE=<code> bash deploy/setup_contest_scheduler.sh`

1. Cloud Run `CONTEST_ENABLED=1` (new revision, traffic to latest); 2. the `contest-setup` scheduler job runs once and calls `POST /contest-setup` (creates the
contest with default rules and the invite code; never edits an existing contest); 3. Vercel production `CONTEST_ENABLED=1`, `CONTEST_SLUG=<slug>` and a redeploy;
4. `contest-refresh` (every 20 min) and `contest-halftime` (every minute, a no-op while the halftime rule is off) resume.
`PAUSE=1` reverses it. Dry-run against fake gcloud / vercel only; the Vercel half is untested. Needs `vercel link` in `beezy-vip` and the vercel CLI logged in.

## Switches

| switch | where | default |
|---|---|---|
| `CONTEST_ENABLED` | Cloud Run + Vercel env | off (0 / false / off / no / empty) |
| `CONTEST_SLUG` | Vercel env | unset = the page shows SAMPLE data |
| `CONTEST_NOTIFY` + `CONTEST_DISCORD_WEBHOOK` | Cloud Run env | off |
| `CONTEST_HALFTIME_ENABLED` + the contest rule `halftime_enabled` (organiser console) | both needed | off |
| contest rules `cashout_enabled`, `alt_lines_enabled`, `sgp_enabled`, `blackjack_enabled` | organiser console | on |

Local review without Clerk: `AUTH_DISABLED=1 CONTEST_DEV_USER=<name> [CONTEST_DEV_ADMIN=1]` (ignored on a Vercel production deployment).

## People

- A participant joins with an invite code (needs a verified Clerk email); the join mirrors `privateMetadata.contest.roles.<slug> = "participant"`.
- An organiser is a Clerk user whose private metadata has `contest.roles.<slug> = "admin"` (set it in the Clerk dashboard or with
  `clerk api -X PATCH /users/<id>/metadata -d '{"private_metadata":{"contest":{"roles":{"<slug>":"admin"}}}}'`); they get `/contest/admin`.
- CLI: `scripts/contest_admin.py create|invite|refresh|status|rename|adjust` against `MLB_DB_URL`.

## Rollback

Soft: `PAUSE=1 ...` (jobs paused, flags off). Hard: `gcloud run services update-traffic mlb-betting --to-revisions=mlb-betting-00360-zil=100`.
The new tables are additive (`contest_*`); nothing else reads them, so leaving them in place is harmless.

## Rules in force (owner, 2026-11-17)

Start $5,000; minimum bet $50 (1% of the start), also a round robin's total; no maximum beyond the balance; teaser placeholders kept; picks hidden until
kickoff; bankrupt = out (the $1 blackjack chip is the floor while the lifeline is on); bragging rights only, no prizes; ESPN and Kalshi feeds approved.
