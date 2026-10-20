---
title: "The NBA BettingPros backfill is player props only -- game lines (moneyline / spread / total) needed their own crawl"
module: nba/games/bp_games.py, mlb_core/odds/bettingpros_props.py
tags: [bettingpros, nba, game-lines, backfill, moneyline, spread, totals, per-book-line, updated-utc]
problem_type: integration_issue
category: integration-issues
date: 2026-10-20
---

## Problem

"We have a full BettingPros backfill and ParlayAPI is banking new odds" was true for NBA **player props** only.
`Odds/bettingpros_nba/` holds five prop markets (points, rebounds, assists, threes, pra; 2021-10 onward) because
`bettingpros_props.resolve_markets` selects `category == "player-props"`. There were **no historical NBA game lines** anywhere in the
lake: ParlayAPI's `OddsAccum/basketball_nba/` game lines started 2026-10-16 (three preseason days) and its
`historical/*/game_lines_done.json` markers are 0-row (the 7-day closing archive hit the offseason). A moneyline / spread / totals
model had nothing to be measured against until a game-line crawl was added.

## What works

BettingPros serves NBA game lines for past dates: `/v3/markets?sport=NBA` category `game-odds`, period `game` -> `moneyline` 127,
`total` 128, `spread` 129 (ids resolved live; they differ per sport). Probed 2022-01-15 (6 books), 2024-02-10 (11), 2025-01-20 (10).
`nba/games/bp_games.py` crawls them in LONG format into `Odds/bettingpros_nba_games/{market}/{date}.parquet` (one row per
event x side x book x quoted line, plus the opening line as `is_open`), resumable, newest first.

## Gotchas

- **Do not reuse the wide MLB row builders** (`mlb_core.odds.bettingpros.build_rows`, kinds moneyline/spread/total). They keep ONE
  line per selection, so books that quote different spreads (DK -2.5, FanDuel -1.5, Caesars -1.0 on the same game) are mixed -- the
  per-book line-collapse bug (`bettingpros-per-book-line-collapse.md`). The consensus takes the modal line and pairs both sides at
  that exact line.
- The away side of a spread is quoted at `-home_line`; re-key it onto the home line before pairing or no pairs ever match.
- `updated` is **UTC** (compare to `scheduled`, also UTC). The last quote of a settled day is stamped a median +1 min after tip
  (p99 +3.4, max +15): a closing-line PROXY, not a series, so no real CLV. Drop quotes stamped > 10 min after tip (in-play).
- `scheduled` is UTC; the ET game date is the join key to ESPN/hoopR (a 8:30 pm ET tip is the next UTC day).
- The `Consensus` pseudo-book (id 0), DFS and exchange ids are excluded from the consensus (`book_kind != "book"`).
- ParlayAPI's banked CSV is a different shape (book names, `h2h|spreads|totals`, outcome = team name). `market.from_parlay` maps it
  to the same long frame; `fliff` is DFS/social, `novig`/`prophetx` exchanges, `pinnacle` is kept as a sharp reference only.
