---
title: "ParlayAPI NHL/NBA prop payloads: one-sided Yes quotes, team-total junk, name variants, and per-market billing"
module: nba/odds/parlay_extract.py, nba/odds/accumulator.py, ev/props.py
tags: [parlayapi, nhl, nba, props, odds-banking, name-resolution]
problem_type: integration_issue
category: integration-issues
date: 2026-10-14
---

## Problem

The MLB-shaped prop extractor silently mangled real NHL/NBA ParlayAPI payloads (probed live 2026-10-14, NHL event
Canadiens @ Capitals, NBA slate).

## What the payloads actually look like

- **Anytime markets are one-sided "Yes" prices**, and the `point` is inconsistent across books: Novig sends `0.5`
  (with `1.5` / `2.5` rungs for 2+/3+ goals), FanDuel sends `0.0`, Bovada sends no point at all. All mean "1 or more". The MLB
  extractor required BOTH sides and a point, so these rows were dropped entirely.
- **`player_goals` contains team-total rows**: `name: "Over Washington Capitals"`, `description: "Washington Capitals"`,
  `point: 6.5`. Without a filter a team total lands in the player table as a 6.5-goal skater.
- **Books spell players differently**: `Alex Ovechkin` (FanDuel/Novig), `Alex Ovechkin (WSH)` (Bovada, team suffix), and
  `Alexander Ovechkin` (seen in a book's output). Grouped on the raw string, `n_books` never exceeded 1.
- **Goalie saves arrive as `player_saves`**, although the catalog (`GET /v1/markets`) lists `player_total_saves`. Both are mapped.
  The market is often empty early in the day (goalies unconfirmed) and **is still charged**: credits are billed per
  (event x requested market), not per market returned.
- **DFS / pick'em apps** (underdog, sleeper, prizepicks, dabble, betr, fliff) quote ~-112/-112 on every line: they carry
  the NUMBER, not a price. They must never enter a best-book price or a fair-probability median.
- `GET /v1/props/markets` does not exist (404); the catalog is `GET /v1/markets` -> `props_by_sport`.
- NHL: 9 prop markets; NBA: 21 (incl. `*_alternate` ladders). Real pre-game coverage is thin: ~3-4 real two-sided books.

## Fix

`parlay_extract.flatten_parlay_props(..., include_one_sided=True)` (accumulator only; MLB's default is unchanged): skips rows whose
description is a team, splits the `(WSH)` suffix into `team_hint`, normalises a one-sided `Yes` point of `None`/`0.0` to `0.5`
and keeps the original in `point_raw`. The accumulator drops DFS books from the best-book CSV (the raw JSON keeps everything).
`ev/props.parse_event_props` resolves players to league ids so spellings merge.

## Prevention

Pin new sports against a real captured payload (tests/fixtures/odds/nhl_props_sample.json) before trusting an extractor, and
read `x-requests-last` after any new market list: a key the book does not carry still costs a credit.
