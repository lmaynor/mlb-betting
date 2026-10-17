---
title: "The projections payload was ordered priced-first across ALL days, so any cut (MAX_ROWS or the API limit) dropped near-game rows before far ones"
module: ev/daily_projections.py, mlb/runners/public_api.py, main.py
tags: [projections, api, limit, ordering, payload-size, frontend]
problem_type: logic_error
category: logic-errors
date: 2026-10-17
---

## Problem

`rows = (line_rows + rotation_rows)[:MAX_ROWS]` with line rows sorted by score and rotation rows by game: priced rows of ANY day came before
every rotation row, and the API then applied `rows[:limit]` (the Next proxy sends no `limit`, so the route default of 300 applied; the cap was 700).
With a 7-day window (NFL, and NBA once the horizon was raised) a cut therefore dropped a whole near day's rotation rows to keep far days' priced
rows, and the day strip showed days with no rows at all. Rotation filler at 6 per team per stat was ~108 rows per NBA game: 40 opening-week games
would have blown any cap on the first night.

## Fix

- `order_and_cut`: sort by (Central game day, priced first, display order) and cut at `MAX_ROWS` (2400): the farthest games go first.
- `ROTATION_PER_TEAM` is per sport (nfl 5, nhl 4, nba 4) and the team's PRICED players count toward the quota, so a covered team gets no filler.
- The API serves rows in that order and `limit` now goes to 1200 (default still 300). New `day=YYYY-MM-DD` returns one Central game day, so a
  client no longer needs a large limit to see a given day; `games` lists every game with its row counts even when a cut removed its rows.

## For the frontend

Request `day=` (or `limit=1200`) rather than relying on the default 300; `rows` are no longer "best score first" across the whole window.
