---
title: "gsutil -m cp on macOS (Homebrew gcloud, Python 3.14) hangs forever at 0 bytes; gcloud storage cp does the same copy in seconds"
module: local data pulls (scripts, ad-hoc analysis)
tags: [gsutil, gcloud-storage, macos, python-3-14, multiprocessing, hang]
problem_type: runtime_error
category: runtime-errors
date: 2026-10-16
---

## Problem

`gsutil -m cp "gs://<bucket>/NHL/shots/*.csv" <dir>/` (and the same for `-m` with parquet partitions) started, printed nothing,
created the directories, and sat at 0 bytes for 10+ minutes with the worker processes idle (`ps` showed the parent plus two
children, ~0.00s CPU). Killing it and retrying reproduced it. A non-`-m` gsutil copy started by another session days earlier was
still alive in the process list. Another trap in the same command: an UNQUOTED `gs://.../*.csv` glob is expanded by zsh
(`no matches found`) before gsutil sees it -- always quote it.

## Fix

Use `gcloud storage cp` (no multiprocessing layer): 49 MB of masters in 8 s, 118 MB of shots and 121 MB of BettingPros parquet in
under a minute, 38 MB/s. Quote the wildcard. Run long pulls from a script file (`bash pull_data.sh`) rather than a one-liner so a
timeout does not orphan half a copy.

Observed on: macOS (Darwin 25.6), Homebrew `google-cloud-sdk` running gsutil under Python 3.14.7. The root cause was NOT
isolated (multiprocessing start-method change is the likely suspect); the workaround is what is verified.
