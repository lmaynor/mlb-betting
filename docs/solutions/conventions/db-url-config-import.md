---
title: Always import DB_URL from mlb_core.config, never os.environ["DB_URL"]
module: mlb_core/config.py, main.py, runners
tags: [database, config, env-vars, keyerror]
problem_type: convention
category: conventions
date: 2026-05-14
---

## Context

The environment variable holding the database DSN is named `MLB_DB_URL`, not `DB_URL`. Reading the wrong name causes a `KeyError` at runtime in Cloud Run.

## Guidance

Always use:
```python
from mlb_core.config import DB_URL
```

Never use:
```python
url = os.environ["DB_URL"]  # KeyError -- the var is MLB_DB_URL
```

`mlb_core/config.py` reads `MLB_DB_URL` correctly and exposes it as `DB_URL`. The indirection exists because:
1. Secret Manager injects it as `MLB_DB_URL`
2. `mlb_core.config` normalizes the name for all callers

## When to Apply

Any new route, runner, or script that needs to connect to Postgres. Check any copy-pasted Cloud Run route code for direct `os.environ` reads.
