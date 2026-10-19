---
title: "BettingPros anytime-TD offers are ONE offer per GAME (a selection per player): the generic parser mislabels every row"
module: mlb_core/odds/bettingpros_props.py, ev/bettingpros_events.py
tags: [bettingpros, nfl, anytime-td, parser, backfill, data-corruption]
problem_type: integration_issue
category: integration-issues
date: 2026-10-17
---

## Problem

`bpp.build_prop_rows` assumes the player-prop shape: one offer per PLAYER, `participants[0]` is the player, the two selections are
`over` / `under`. The NFL `touchdown-scored` (anytime TD) market has a different shape (verified live 2026-10-17, 12 offers for 12 games):

- ONE offer per GAME. `participants` lists every player (plus the two team-defence rows, `position: "DST"`), `selections` has one entry
  per player with an EMPTY `selection`, `label` = the player's name and `participant` = his id.
- BettingPros stamps the line `1` ("1+ touchdowns"). ParlayAPI's one-sided Yes ladders use `0.5`.

Consequences:

1. **The historical crawl is corrupt for this market.** Every row of a game carries `player` = the game's FIRST participant and `side` =
   the lowercased name of the player the row is really about (e.g. `player: "Case Keenum"`, `side: "tank bigsby"`). Checked on
   `Odds/bettingpros_nfl/touchdown-scored/2025-12-14.parquet`, `2026-09-28`, `2026-10-05`: one `player` per game, many `side` values.
   `ev.bettingpros_live.rows_to_quotes` silently drops them all (side is not over/under), so the live adapter produced ZERO anytime-TD quotes.
2. Left as a `line 1` quote it would also be read as "P(2 or more TDs)": `ev.daily_projections.p_over('anytime', mu, line)` treats `line >= 1` as a
   count threshold and only `line < 1` as "at least one".

## Fix (live path only)

`ev.bettingpros_events.build_per_game_player_rows` reads participant -> selection -> books, skips DST rows, emits `side='over'` at point `0.5`.
`PER_GAME_PLAYER_MARKETS` names the markets that need it (`nfl: anytime_td`). Test: `tests/test_bettingpros_events.py::
test_per_game_player_market_anytime_td_is_parsed_as_one_sided_yes_at_half_point` pins the recorded shape.

## NOT fixed: the backfill

`bpp.build_prop_rows` and the `bp-backfill-nfl` output are unchanged (the crawl is running; a fix needs the image rebuilt and the partitions
re-crawled). The data is recoverable without a re-crawl for the player name: `side` holds it lowercased (accent/case lost); everything else on the
row (book, odds, line) is right. Do not use `touchdown-scored` partitions for anything until that is done. Any other market whose offer has
`selection == ""` has the same problem.
