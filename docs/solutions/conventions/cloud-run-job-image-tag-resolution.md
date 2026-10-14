---
title: "A Cloud Run Job resolves its image TAG at every execution -- pushing :latest silently changes every scheduled job"
module: deploy/*.sh
tags: [cloud-run-jobs, images, deploy, gcr, blast-radius]
problem_type: convention
category: conventions
date: 2026-10-14
---

## The gotcha

CONTEXT.md's deploy flow says the service "pins to a digest at deploy time", which is true of the Cloud Run SERVICE revision and
had been read as covering jobs too. It does not. Verified 2026-10-14 on `mlb-build-all-features`: the job spec says
`gcr.io/<project>/mlb-betting:latest`, while its latest execution recorded `...@sha256:96406c...` -- the digest the tag pointed to
when that execution STARTED. So **pushing `mlb-betting:latest` changes what every scheduled MLB job runs on its next execution**,
including merged-but-undeployed code.

## Convention

New feature jobs (odds banking, NHL ingest, props alerts) run from a separate image, `gcr.io/<project>/mlb-betting-feeds`
(precedent: `mlb-betting-ev:shadow`). `deploy/setup_odds_banking.sh` and `deploy/setup_props_alerts.sh` default `IMAGE` to it and
only accept the production image via an explicit `IMAGE=` override. Never build into `mlb-betting:latest` to satisfy a new
job's prerequisite; build the feeds image from a CLEAN checkout of origin/main (deploy_service.sh commits and pushes from the
working tree, so it is not safe from a shared or dirty tree).
