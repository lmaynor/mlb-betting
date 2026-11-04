---
title: BestBook carries college basketball (cbb) game lines only, no player props, and no team-name mapping exists
module: ev.bestbook_events
tags: [bestbook, cbb, ncaab, game-lines]
problem_type: integration-issue
category: integration-issues
date: 2026-11-03
---

## What the feed carries

Probed live 2026-11-03: the slate has `slate:cbb` (27 games over two days), game keys `cbb:YYYY-MM-DD-AWAY-HOME`, ~12-14 venues per game
(dk, fan, mgm, bov, thescore, kalshi, polymarket, ...). Per-game snapshots hold section `lines` only (ml / spread / total ladders, team totals, `ptie`);
**no player-prop sections**, even on a marquee game (MAN@UK). Props for cbb cannot come from BestBook.

## Decisions

- `bb-live-cbb` (hourly :50, Nov-Apr) runs the same crawl; it writes `Odds/bestbook_live/cbb/game_lines_latest.json` (events stay empty).
- Team identity is BestBook's own code (`bbe.bb_team_code`): ~360 D1 teams, no engine, no ParlayAPI name map. Codes run to 4+ letters, so the game-key regex allows 2-6.
- Not done: the Game Lines board (`ev.game_odds_board`) still has only nba / nhl / nfl. Putting cbb on it needs a ParlayAPI `basketball_ncaab` mapping or a BestBook-only board, a code -> display-name table, and a `GAME_ODDS_SPORTS` / `public_api` entry.
- Games already started are skipped (15 of 18 on the 11-03 slate), so the first run of the day shows few games.
