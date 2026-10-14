---
title: "ParlayAPI has a rolling historical prop-close archive (tier-limited window), and BettingPros history is deep but Pinnacle-free"
module: nba/odds/parlay_historical.py, mlb_core/odds/bettingpros_props.py
tags: [parlayapi, bettingpros, historical, props, credits, pinnacle, nhl, nba, nfl]
problem_type: integration_issue
category: integration-issues
date: 2026-10-14
---

## Problem

`nba/BLUEPRINT.md` and `nba/odds/accumulator.py` said "no historical-props API exists at any tier", so odds were only banked
forward. Reading ParlayAPI's public `/openapi.json` showed `/v1/historical/sports/{sport}/closing-odds` (player props from
`prop_closing_lines`, incl. Pinnacle and Novig) and `/v1/historical/sports/{sport}/odds` (game lines only).

## What is true

- Depth is the account tier's `historical_hours` (`/v1/pricing`, no auth): Free 48h, **Starter 168h (ours)**, Pro 720h, Business
  2160h, Enterprise 8760h. The window rolls daily; un-pulled days are gone.
- Cost: `closing-odds` = 10 credits per call regardless of `markets=` count; `offset=` pages re-bill. `/historical/odds` costs
  10 x markets and only serves h2h/spreads/totals (prop keys cost 0 and return nothing -- see `x-markets-unservable`).
- Paging: per-leg row cap 5,000 (`x-archive-row-cap`); when truncated continue with `offset` (same params) -- `dateTo` paging
  cannot advance within a single game_date.
- Rows have no team/event ids. Pull per (sport, date), `bookmakers=` to drop DFS rows (null odds).
- NHL/NBA game-line archive is nearly empty; NFL has ~42 rows/day. Don't spend 10 credits/day/sport on it speculatively.
- `x-requests-last` / `x-requests-remaining` on every response give exact spend and the shared-pool balance.
- BettingPros: deep (NFL 2021+, NBA 2021-12+, NHL 2022-11+) but **Pinnacle is in the catalog and never quotes**; its trimmed MLB
  book map drops Novig/ProphetX/Kalshi, so these sports use `mlb_core.odds.bettingpros_props` (long format, all books).

## Prevention

`nba/odds/parlay_historical.py` meters every call (`--max-credits`, shared-pool `--floor`); re-run weekly while on Starter.
