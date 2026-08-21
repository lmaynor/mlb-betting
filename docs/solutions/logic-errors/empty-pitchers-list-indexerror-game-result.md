---
title: MLB Stats API boxscore can have an empty "pitchers" list, crashing shared settlement code
module: mlb_core/data/game_result.py
tags: [game_result, boxscore, mlb_stats_api, indexerror, settlement, backfill]
problem_type: logic_error
category: logic-errors
date: 2026-08-20
---

## Problem

The historical SB (stolen base) boxscore backfill crashed mid-run with an
`IndexError: list index out of range` inside `get_game_result()`, on a real
historical game (not a malformed/test fixture). This function is shared
settlement/labeling infrastructure -- every system that keys off boxscore
data (settlement, GAME feature build, the new SB labeler) calls it.

## Root cause

```python
# BEFORE:
starter_pitcher_id = team.get("pitchers", [None])[0]
```

`dict.get(key, default)` only returns `default` when the **key is
missing**. If the boxscore JSON has `"pitchers": []` -- an empty list, which
the MLB Stats API does return for at least one confirmed real historical
game (a team that used an opener/bullpen game in an unusual way, or a
partial/suspended-game boxscore) -- `.get()` returns that empty list
as-is, and indexing `[0]` into it raises `IndexError`, not a clean
`None`.

This is the classic "default only fires on missing key" trap: the code
*looks* defensive (there's a fallback right there) but the fallback never
triggers for the actual failure mode that occurs in production data.

## Why it's easy to miss

Every other boxscore field accessed nearby uses `.get(..., {})` chained
with more `.get()` calls, which degrade gracefully (an empty dict just
means downstream `.get()` calls keep returning `None`/defaults). A list
accessed with `[0]` is the odd one out -- it's the only place in this
function where a "present but empty" value is immediately indexed rather
than checked for truthiness first, so it doesn't inherit the same
resilience as everything around it.

It also only surfaces on real historical data at scale. Unit tests and
recent games (which reliably have >=1 pitcher listed) never hit it; a
multi-year backfill across thousands of games eventually finds the one
boxscore shaped differently.

## Fix

```python
# AFTER:
_pitchers_list = team.get("pitchers") or [None]
starter_pitcher_id = _pitchers_list[0]
```

`or` catches both the missing-key case AND the empty-list case (`[]` is
falsy), so the fallback actually fires for both failure modes.

## Prevention

When adding a fallback for a `dict.get(key, default)[0]` pattern, ask
specifically: "does this default fire if the key exists but the value is
an empty container?" If not, use `dict.get(key) or default` instead.
This applies to any boxscore/API-response field that is a list you plan
to index into immediately -- `pitchers`, `battingOrder` substitutions,
`plays`, etc. -- since sparse/unusual real games are the norm at
multi-season backfill scale, not the exception.

Regression tests added: `tests/test_game_result.py::test_empty_pitchers_list_does_not_crash`.
