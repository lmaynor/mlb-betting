---
title: pd.read_sql with pg8000 requires SQLAlchemy text() wrapper for named params
module: mlb_core/tracking/bet_tracker.py, runners
tags: [postgres, pg8000, sqlalchemy, pandas]
problem_type: runtime_error
category: runtime-errors
date: 2026-05-14
---

## Problem

`pd.read_sql()` with a raw string containing `:param` syntax crashes with `syntax error at or near ":"` when using pg8000 as the Postgres driver.

## Symptoms

```
sqlalchemy.exc.ProgrammingError: syntax error at or near ":"
```

## Root Cause

pg8000 does not interpret SQLAlchemy named parameter syntax (`:param`) in raw strings. The string is passed verbatim to the Postgres wire protocol, which rejects the colon.

## Solution

Always wrap the query in `text()` from `sqlalchemy`:

```python
from sqlalchemy import text

# Wrong -- crashes with pg8000
df = pd.read_sql("SELECT * FROM bets WHERE system = :sys", conn, params={"sys": s})

# Correct
df = pd.read_sql(text("SELECT * FROM bets WHERE system = :sys"), conn, params={"sys": s})
```

Also applies to `CURRENT_DATE` comparisons: `game_date` is stored as `TEXT`. Comparing to `CURRENT_DATE` (a Postgres `date` type) raises `operator does not exist: text = date`. Always cast: `game_date = CURRENT_DATE::text`, or pass `date.today().isoformat()` as a named param.

## Prevention

Any new `pd.read_sql()` call with named parameters: wrap in `text()`. Never use `CURRENT_DATE` directly in parameterized queries against the `bets` table.
