---
title: BestBook game-line rows are binary yes/no per rung, and `line` is the AWAY handicap, not the rung team's
module: ev.bestbook_game_lines
tags: [bestbook, game-lines, spread, sign-convention, alternates]
problem_type: integration-issue
category: integration-issues
date: 2026-11-01
---

## What the feed carries

The per-game snapshot's `section: "lines"` rows (probed live 2026-11-01, NBA LAC@GSW and NHL OTT@ANA) hold the whole game board, ladders included:

- `match:{game}|ml|{TEAM}` -- yes = TEAM wins, no = the other team wins.
- `match:{game}|spread|full|{TEAM}|{x}` -- yes = TEAM covers its rung, no = the OTHER team covers the opposite number.
- `match:{game}|total|full|{x}` -- yes = Over x, no = Under x.
- Ignored on purpose: `periods:*` (1h / 1p ...), `teamtotal|*`, `pwin3|*` (three-way), `mltot|*`, `firstgoal`, `most|*`.
- ~17 venues per pregame game, books AND exchanges (kalshi / novig / polymarket); ~90 spread and ~100 total rungs for an NBA game.
- The same crawl as the props one fetches these rows; they were being thrown away because `stat_from_row` needs a `player`.

## The traps

1. `row["line"]` is the AWAY team's handicap (the LAC rows read -x, the GSW rows read +x for the SAME 'lays x' rung). Do not use it for the rung team. Parse the signed number from
   the `rung` label instead (`'GSW -5.5'`); the minus is a real U+2212, not an ASCII hyphen.
2. Every spread rung is a 'TEAM lays x' row; the opposite side is that row's `no`. So the board's HOME-handicap row is `home lays x` (yes = home) or `away lays x` (yes = away,
   home line = +x). Both orientations describe the same market and are folded together.
3. A started game still streams: in-play rows have exchange quotes at 1c and books with no price at all. Probe pre-game games.
4. Exchange rows carry 1c / 99c placeholders where nobody bids; a BOOK's -10000 / +3500 long-shot rung is real. Only exchange sides outside 3c-97c are dropped.
5. `kalship` and `kalshi` venue codes both canonicalise to `kalshi`: a row can carry two kalshi quotes; the first non-null side wins.

## Verification

`ev.game_odds_board` built from this feed matched ParlayAPI on the same (row, book): NBA 28/51 identical and the rest within 5%, NHL 29/35 identical, none >= 20%, no swapped sides.
