---
title: An unanchored `data/` in .gcloudignore removed the mlb_core/nfl/nhl/nba `data` PACKAGES from the Cloud Build image
module: .gcloudignore
tags: [gcloud, cloud-build, gcloudignore, deploy, modulenotfounderror]
problem_type: runtime-error
category: runtime-errors
date: 2026-11-01
---

## Symptom

The image built on 2026-11-01 started (the service passed its startup probe) but `daily-projections` died with
`ModuleNotFoundError: No module named 'mlb_core.data'`, and `/api/public/projections` returned 500 for the ~1 minute the revision took traffic.

## Cause

`.gcloudignore` uses gitignore semantics. The line `data/` (added to keep local data dumps out of the build context) matches a directory named
`data` at ANY depth, so `mlb_core/data`, `nfl/data`, `nhl/data` and `nba/data` (real Python packages) were left out of the upload.

## Fix / rule

Anchor repo-root-only patterns: `/data/`, `/.claude/`. `tests/test_gcloudignore_keeps_packages.py` runs `git check-ignore --no-index` with
`.gcloudignore` as the excludes file over every package `__init__.py` under mlb_core/mlb/nba/nfl/nhl/ev and fails if any is ignored.

Deploy habit that would have caught it earlier: canary the service (`--no-traffic --tag=canary`) AND execute one job that imports the data layer
(`gcloud run jobs execute daily-projections --wait`) BEFORE shifting traffic or retagging `mlb-betting:latest`. A 401 from the canary only proves the
router is up.
