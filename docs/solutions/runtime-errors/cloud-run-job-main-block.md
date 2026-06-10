---
title: Cloud Run Jobs exit silently without __main__ block
module: runners, training
tags: [cloud-run, jobs, gcloud, silent-failure]
problem_type: runtime_error
category: runtime-errors
date: 2026-05-24
---

## Problem

A Cloud Run Job shows `COMPLETE: 1/1` but wrote no output — no GCS files, no DB rows, no Discord messages. No error, no stack trace.

## Symptoms

- Job execution shows success in Cloud Console
- `last_build.json` sentinel not updated
- `monitor_ops` alerts the next day about stale features

## Root Cause

When a Cloud Run Job runs `--command python3 --args="-m" --args="runners.build_game_features"`, Python imports the module and exits 0 immediately if there is no `if __name__ == "__main__":` block. The module-level code does not run — only top-level definitions (imports, functions, classes) execute.

## Solution

Every runner or training script invoked as a Cloud Run Job must have:

```python
if __name__ == "__main__":
    run()
```

Training scripts (`retrain_*.py`, `calibrate_*.py`) are invoked as scripts and already have `__main__` blocks. Build runners (`build_*_features.py`) needed this added explicitly.

## Prevention

When adding a new runner as a Cloud Run Job:
1. Confirm the file has `if __name__ == "__main__": run()` or equivalent
2. Test locally with `python3 -m runners.build_myfeatures` and verify output is written
3. Check `last_build.json` sentinel is updated after the job runs
