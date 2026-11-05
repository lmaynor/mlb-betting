---
title: "NHL shift charts: duplicate shift rows flip a game's PP/PK split, and the NHL already publishes EV/PP/SH time on ice"
module: nhl/data/toi_splits.py, nhl/props/toi_feat.py
tags: [nhl, nhl-api, shiftcharts, time-on-ice, power-play, penalty-kill, rate-limit]
problem_type: integration_issue
category: integration-issues
date: 2026-11-05
---

## Two endpoints on api.nhle.com/stats/rest/en (no auth, same per-IP rate limit as api-web.nhle.com)

1. `shiftcharts?cayenneExp=gameId=<id>` -- one row per shift (`typeCode` 517) with `period`, `startTime`/`endTime` as game-clock
   `MM:SS`, `playerId`, `teamId`; goalies have shifts too (one per period, split when pulled); `typeCode` 505 rows are goal events
   (`duration` null) and must be dropped. About 17 KB gzipped per game. One request per game.
2. `skater/timeonice?isGame=true&limit=-1&cayenneExp=gameDate>="A" and gameDate<="B"` -- the NHL's own per-game, per-skater
   `evTimeOnIce`, `ppTimeOnIce`, `shTimeOnIce`, `timeOnIce` (seconds; ev + pp + sh == total on every row checked). The query is
   **capped at 10,000 rows** and `start=` paging stops working past it (observed: `total` reports 10000, `limit=10000&start=0&sort=gameId`
   returned 100 rows and every page from `start=10000` on was empty), so use date windows: 14 days is about 5-6k rows. A whole season is ~25 requests, all of 2019-2026 is ~150.

## The trap in the shift chart

The chart can list the **same shift twice** (identical player, period, start and end). Seen live in game 2024020302 (a 57 s shift
listed twice): the team then has six skaters on the ice for 57 s, a per-second skater-count timeline reads it as a power play for the
other side, and a large part of the game's PP/PK seconds come out wrong (in that game, ~680 skater-seconds of phantom power play) while the totals still look plausible. `toi_splits.merge_overlaps` unions
overlapping shifts per player. The official TOI report and the box score total do not carry the duplicate.

Also: a sixth skater can be on the ice for 20 s before a too-many-men call. Raw on-ice counts call that a power play; the penalty
ledger does not. `toi_splits.anchor_to_pbp` pins every stretch between two play-by-play events that record the same `situationCode`
strength to that strength (the same effective-skater rule as `nhl.data.powerplay`: a pulled goalie's extra attacker is not a skater
edge). On 37 real games this took the share of players within 5 s of the NHL's published PP split from 96.5% to 100%.

## What is already free

Play-by-play for every ingested game is mirrored at `NHL/raw/pbp/<id>.json.gz`, so the builder only needs the shift chart
(`--pbp-from-gcs`). Numbers and the full-backfill estimate: `docs/analysis/nhl_toi_splits_2026-11-05.md`.
