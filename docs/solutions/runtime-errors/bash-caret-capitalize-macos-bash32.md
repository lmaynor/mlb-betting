---
title: "deploy/*.sh scripts fail on macOS with 'bad substitution' -- bash 4+ syntax on bash 3.2"
module: deploy/setup_build_all_features.sh, deploy/setup_model_jobs.sh, deploy/setup_kalshi_alert_job.sh, deploy/setup_kalshi_capture.sh, deploy/setup_backfill_bettingpros.sh, deploy/setup_walkforward_batch.sh, deploy/add_snapshot_schedulers.sh
tags: [bash, macos, deploy, portability, cloud-run-jobs]
problem_type: runtime_error
category: runtime-errors
date: 2026-08-22
---

## Problem

Running any of several `deploy/*.sh` scripts directly from this Mac (rather
than from Cloud Shell, the documented normal flow) fails immediately with:

```
./deploy/setup_build_all_features.sh: line 50: ${action^} job: $JOB (image: $IMAGE): bad substitution
```

## Root cause

`${action^}` is bash's parameter-expansion syntax for capitalizing the first
letter of a variable -- introduced in **bash 4.0** (2009). macOS ships
**bash 3.2.57** as `/bin/bash` (and as whatever `/usr/bin/env bash` resolves
to) and has for years, because Apple stopped upgrading bash after GPLv3
(3.2 is the last GPLv2 release). Cloud Shell runs a modern Linux bash
(5.x), where this syntax works fine -- which is exactly why RUNBOOKS.md's
documented deploy flow ("Deployment happens from Cloud Shell") never hit
this: nobody had run these scripts from a Mac shell before.

7 scripts had the identical pattern (all a purely cosmetic
`echo "${action^} job: ..."` / `echo "${action^} $name ..."` log line, no
functional effect on the actual `gcloud` command that follows):
`setup_build_all_features.sh`, `setup_model_jobs.sh`,
`setup_kalshi_alert_job.sh`, `setup_kalshi_capture.sh`,
`setup_backfill_bettingpros.sh`, `setup_walkforward_batch.sh`,
`add_snapshot_schedulers.sh`.

## Fix

Since the capitalization was purely cosmetic (an "update"/"Update" vs
"create"/"Create" log-line prefix, no functional impact), the fix was to
just drop it rather than introduce a `tr`-based portable replacement:

```bash
# Before (bash 4+ only):
echo "${action^} job: $JOB (image: $IMAGE)"
# After (portable):
echo "${action} job: $JOB (image: $IMAGE)"
```

Ran `bash -n` against every script in `deploy/` afterward to confirm no
other bash4+-only constructs (`${var,,}`, `${var^^}`) remain.

## Prevention

If a new `deploy/*.sh` script needs to run from this Mac (not just Cloud
Shell) -- as happened 2026-08-22 deploying the SB system, where the deploy
was done end-to-end from this local machine rather than Cloud Shell -- run
`bash -n script.sh` first, and treat `${var^}`, `${var^^}`, `${var,}`,
`${var,,}` as red flags requiring a portable rewrite (`tr` or a small
helper) before relying on this Mac's default bash. `bash --version` here
reports 3.2.57; anything requiring 4.0+ syntax needs this same check first.
