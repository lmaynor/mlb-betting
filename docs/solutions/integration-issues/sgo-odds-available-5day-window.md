---
title: SGO oddsAvailable=true returns a 5-day window, not today only
module: mlb_core/odds/sgo.py, runners/snapshot_odds.py
tags: [sgo, odds, snapshot, extractor]
problem_type: integration_error
category: integration-issues
date: 2026-05-15
---

## Problem

Calling the SGO API with `oddsAvailable=true` returns events from the next 5 days, not just today's games. Runners score bets against games that haven't been played yet or were from a different date.

## Symptoms

- Runners log bets for games days in the future
- Settlement finds no matching `game_pk` for games that have finished
- Snapshot contains more events than expected

## Root Cause

SGO interprets `oddsAvailable=true` as "any event with available odds in the coming window" — currently 5 days. It is not a "today's slate" filter.

## Solution

Always pass `startsAfter` and `startsBefore` parameters. The `et_day_window()` helper handles this:

```python
from mlb_core.odds.sgo import et_day_window

starts_after, starts_before = et_day_window(run_date)
# Returns ISO8601 strings for 00:00 and 23:59 ET on run_date
events = client.fetch_mlb_slate(run_date=run_date)  # already applies et_day_window internally
```

When writing a new extractor or snapshot call, always verify the date window is applied.

## Prevention

Any new SGO API call must use `et_day_window(run_date)`. Never rely on `oddsAvailable=true` alone to scope to today's games.
