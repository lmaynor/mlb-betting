---
title: Contest tests on Postgres: a leaked connection hangs the next test's DROP TABLE, and a teardown list missing a new table leaks rows between tests
module: tests._contest_engine
tags: [postgres, pg8000, tests, teardown, contest]
problem_type: logic-error
category: logic-errors
date: 2026-11-17
---

## Symptoms (both appeared ONLY on Postgres, never on SQLite)

1. `pytest tests/test_contest_*.py` hung forever. Cause: a test helper did `engine.connect().execute(...)` without closing the connection; SQLAlchemy 2 opens a
   transaction, the idle connection held an AccessShareLock, and the NEXT test's `DROP TABLE` waited on it. Always `with engine.connect() as c:`.
2. A halftime test expected `watching` and got `closed`; a notify test expected 1 message and got 0. Cause: `tests/_contest_engine.py` `TABLES` (the
   teardown list) did not include the newly added `contest_halftime` / `contest_notice` / `contest_admin_log`, so rows survived from an earlier test.

## Rule

- Every new `contest_*` table goes into `TABLES` in `tests/_contest_engine.py` AND the drop list in `tests/test_contest_store.py::_engine`.
- All contest test files build their engine with `make_engine(tmp_path)`; set `CONTEST_TEST_PG_URL` to run the whole suite on a throwaway Postgres.
- `pgserver` (pip) starts a local Postgres on a unix socket; the pg8000 URL is
  `postgresql+pg8000://postgres@/postgres?unix_sock=<socket dir>/.s.PGSQL.5432`. Run files one at a time with `-x` if you suspect a lock.
