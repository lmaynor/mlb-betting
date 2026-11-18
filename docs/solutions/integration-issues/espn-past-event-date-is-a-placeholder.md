---
title: ESPN scoreboard returns a placeholder event.date (1993-01-31) on most past events, so never order history by it
module: scripts.contest_history_pull
tags: [espn, scoreboard, history, dates, contest]
problem_type: integration-issue
category: integration-issues
date: 2026-11-17
---

## What happened

The first football history pull for the contest research stored `event["date"]` as each game's date. On most past events ESPN returns
`"1993-01-31T23:30Z"` there (a few carry the real kickoff), so the date-ordered 60/40 train / test split in
`scripts/contest_sportsbook_research.py` was ordering games by a meaningless value. Every out-of-sample number from that run was invalid.

## Rule

Stamp each game with the day you QUERIED (`dates=YYYYMMDD`) and order by that. `scripts/contest_history_pull.py` now writes `day` and the
research script sorts on `day`, falling back to `date` only for old caches. A cache pulled before 2026-11-17 has no `day`; re-pull it.

## How to spot it

`sorted(set(r["date"][:4] for r in rows))` containing `1993`, or a date range printed as `1993-01-31 .. 2025-12-..`.
