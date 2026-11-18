---
title: deploy_service.sh ships ALL of main, repoints traffic to latest, and aborts on two macOS-only test failures
module: deploy.deploy_service
tags: [cloud-run, deploy, traffic, tests, macos, contest]
problem_type: runtime-error
category: runtime-errors
date: 2026-11-18
---

## What the script really does

`deploy/deploy_service.sh` stamps CONTEXT.md and COMMITS AND PUSHES it on the CURRENT branch (so run it from a clean checkout of merged main, never with
unrelated work uncommitted), runs `pytest tests/ -q`, builds `gcr.io/.../mlb-betting:latest`, updates the service and runs `update-traffic --to-latest`.
It therefore ships EVERY commit on main, not just your PR. Before the first contest deploy, ~30 other backend PRs had merged since the live revision
(00360, a `mlb-betting-feeds` image built 2026-11-04). Check `git log --first-parent <live build date>..origin/main -- mlb ev nba nhl nfl main.py` and ask.

## Traffic

`gcloud run services describe` lists tagged revisions first in `status.traffic`; read `spec.traffic` for the 100% entry. After the script,
`spec.traffic` is `latestRevision: true` (it was a named revision before), so a rollback is `gcloud run services update-traffic mlb-betting
--to-revisions=<previous>=100`. The previous revision before the contest deploy was `mlb-betting-00360-zil`; the new one `mlb-betting-00340-n4p`
(revision numbers are not monotonic across image changes).

## The test gate

`tests/test_decisions_claim_packaging.py::test_package_evidence_directory_pointer_attaches_listing` and `..._tiny_budget_forces_truncation_marker` FAIL on
macOS (also on an untouched local main checkout) and pass in GitHub CI on Linux (probably the `/var` -> `/private/var` tmp symlink). The gate aborts the deploy.
`SKIP_TESTS=1 I_KNOW_TESTS_ARE_SKIPPED=1` is the sanctioned override and is refused on a dirty main; use it only when CI is green on the merged commit and
the failures are known and unrelated. The right fix is a separate PR on those two tests.

## Smoke test

`/healthz` returns Google's own 404 page on Cloud Run (the path is reserved): it proves nothing. Use an app route (`/robots.txt` 200, a public API route 401)
and the revision's logs ("Booting worker", "STARTUP TCP probe succeeded").
