---
title: "BettingPros live crawl: ET-day date filter, how far ahead books price, team-code gaps, and measured cycle cost"
module: ev/bettingpros_events.py, mlb/runners/bettingpros_live_props.py
tags: [bettingpros, live-lines, nba, nhl, nfl, team-codes, pacing, coverage]
problem_type: integration_issue
category: integration-issues
date: 2026-10-17
---

## What was measured (read-only probes, 2026-10-17 01:00-01:10 CT, real slates)

- **`/v3/events?date=` is an ET calendar day.** A Monday 20:15 ET game (stamped `2026-10-20 00:15:00` UTC) comes back under Monday's date. Events
  are listed ~7 days out, but **offers exist only ~2-4 days ahead**: NHL 2 days (14 of 31 listed games priced), NBA ~3 (13 of 36), NFL ~3 (13 of
  14; Thursday's game not yet). A far-future event returns an empty offers page, so crawl ALL event ids per market in one request set instead of
  per day.
- **Offers need `limit=10`** (hard cap), so a slate is ~100-250 requests. Measured with the repo's pacing (0.8 s +-40% between pages, 1.0 s between
  markets): NHL 121 requests / 137-145 s, NFL 75 / 61-98 s, NBA 99 / 99-102 s. Responses take ~0.4 s each.
- **Early-season books are thin**: the 2026-10-20 NBA opener had DraftKings, FanDuel (partial), Caesars, BetRivers, Fanatics, theScore, Novig and
  Kalshi; most NBA rows had ONE two-sided book (263 of 404). NFL 5-13 books per prop, NHL 1-8.
- **Team codes**: BettingPros NBA writes `UTH` / `NOR` where the project uses `UTA` / `NOP` (`canon_team` cannot read them; 4 of 36 events were
  dropped as unknown teams until `BP_TEAM_FIX`). NFL (`JAC`, `LA`) and NHL (`LA`, `NJ`, `SJ`, `TB`) resolve through `canon_team` as they are.
- **Agreement with ParlayAPI** (NFL, same game, same time): of 330 props both feeds priced, 278 had the same consensus line; median |delta
  P(over)| 0.2 pp, p90 1.0 pp; the median book count was 6 in both.
- BettingPros book ids that are the same book under two names are mapped once (`ev.bettingpros_live.LIVE_BOOKS`): `PartyCasino` (27) is a BetMGM
  skin and `SugarHouse` a BetRivers clone, both left out so a book is never counted twice; `Fliff` is a DFS-style book here (excluded, as ParlayAPI's
  `DFS_BOOKS` does).

## Rules that follow

1. Never publish a crawl that found games but priced none because every market failed (`publishable`): that would blank the last good file.
2. The board unions BettingPros with ParlayAPI per (book, stat) and prefers the strictly fresher feed; see `ev.daily_projections.union_events`.
3. The `date` filter being an ET day means "today" must be computed in ET, not UTC, or the evening games of the previous ET day leak in/out.
