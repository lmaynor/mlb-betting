---
title: tweet_drafter recap prompt filtered on date.today() -- can never match a settled bet
module: tweet_drafter.py
tags: [tweet-drafter, recap, settle, date-filter, timezone]
problem_type: logic_error
category: logic-errors
date: 2026-09-02
---

## Problem

`build_recap_prompt()` in `tweet_drafter.py` filtered settled bets with
`p.get("game_date") == str(date.today())`. This can never match at the time
the recap job actually runs, so the function always returned `None` and the
job always hit "No settled bets today -- skipping" -- independent of, and in
addition to, the `TWEET_MODE` bug (see
`docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md`).
Fixing `TWEET_MODE` alone would not have made this job actually work.

## Symptoms

- Even with `TWEET_MODE=recap` set and real settled bets in the DB, the
  recap job logs `No settled bets today -- skipping.` and exits 0.

## Root Cause

`/settle` runs at 09:00 UTC daily and always settles **yesterday's** slate --
confirmed live via `settle_bets` logs on 2026-09-01:
`settle: starting for settle_date=2026-08-31`. `mlb-tweet-recap` runs an hour
later, at 10:00 UTC. By then, `date.today()` inside the container (UTC, no
`TZ` override) has already rolled to the *next* calendar day relative to
every bet `/settle` just processed -- so `game_date == str(date.today())` is
comparing "yesterday" to "today" and is false by construction, every single
day, regardless of timezone edge cases.

`get_recent_settled()` (`mlb/runners/public_api.py`) queries
`ORDER BY game_date DESC, created_at DESC LIMIT :limit`, so the maximum
`game_date` present in the returned rows already IS "the most recently
completed, fully-settled slate" -- which is what a 5am-ET recap tweet
actually means by "today's results" in normal usage.

## Fix

Filter on `recap_date = max(p["game_date"] for p in settled if p.get("game_date"))`
instead of `str(date.today())`, and use `recap_date` (not `date.today()`) in
the prompt's display text too. This is also strictly more robust than a
hardcoded "yesterday" (`date.today() - timedelta(days=1)`): if a stale
pending bet from 2+ days ago settles late on retry, or there were no games
"yesterday" (off day / All-Star break), taking the max date actually present
in the settled batch degrades gracefully instead of hardcoding an assumption
about the settle cadence.

## Prevention

Any job that reads "recently settled" data and wants to describe "the latest
completed day" should derive that day FROM the data (max/most-recent date
present), not from `date.today()` at the reader's own execution time --
especially for a batch job that intentionally runs shortly after a
UTC-day-crossing nightly settlement step. This is the same class of bug as
the `game_date` UTC-vs-CT logging bug already documented in CONTEXT.md s15.6
("`game_date` was logged in UTC before 2026-05-17") -- date-boundary
assumptions across a job that runs close to local midnight are a recurring
sharp edge in this codebase.
