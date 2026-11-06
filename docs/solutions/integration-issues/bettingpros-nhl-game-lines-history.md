---
title: "BettingPros serves NHL game lines (moneyline / puck line / total) from 2021-01; the gotchas that matter when scoring against them"
module: mlb_core/odds/bettingpros_games.py, nhl/game/market_eval.py
tags: [bettingpros, nhl, game-lines, backfill, puck-line, closing-line, team-codes, ARI, PST, last-change-stamp]
problem_type: integration_issue
category: integration-issues
date: 2026-11-05
---

## Problem

"NHL has no historical game-line odds" (game-line harness, 2026-11-04) was true of the lake, not of the provider. The prop crawl
(`mlb.runners.backfill_bettingpros_props`) selects `category == "player-props"` only, so game markets were never asked for.

## What works

`/v3/markets?sport=NHL`, category `game-odds`, period `game`: `moneyline` 193, `total` 194, `puck-line` 195 (resolve live; ids differ per
sport). `/events` has NHL events back to at least 2019 but `/offers` returns nothing before **2021-01** (probed 2020-02, 2020-03, 2019-11:
events, zero offers; 2020-08: no events; 2021-01-20 and 2021-07-05: full quotes). One offer per event per market, so a slate is one page
(the 10-offer cap does not bite) and a day costs 4 requests. `mlb_core/odds/bettingpros_games.py` banks them, resumable and idempotent,
to `Odds/bettingpros_nhl_games/{moneyline,spread,total}/{date}.parquet` (`spread` = the puck line; same schema as the NBA
`Odds/bettingpros_nba_games`).

## Gotchas

- **One snapshot per book per event, stamped with its last CHANGE.** `updated` is UTC, like `scheduled`. On NHL the median last change is
  hours before the drop and none is after it beyond a few minutes, so unlike NBA there is no in-play contamination to speak of, but a quiet
  line can carry a stamp a day old. The close is "last quote with `updated <= scheduled + 10 min`"; report staleness, do not silently
  filter on it (`nhl.game.market_eval --fresh-hours` is the sensitivity).
- **Moneyline `line` is a meaningless 1** in the raw offers; the crawler stores NaN. The away side of the puck line is stored at `-home_line`,
  so re-key it before pairing.
- **The opening line is ONE quote per side**, `book_id` = the book that posted it (usually DraftKings) and `updated` = its creation time. A
  two-sided open exists only where both sides name the same book and the stamps agree.
- **Playoffs are `season_type == "PST"`**, regular season `REG`. A `POST` filter silently drops every playoff game.
- **Team codes:** `LA / NJ / SJ / TB` (Kalshi style) vs the schedule's `LAK / NJD / SJS / TBL`; Arizona is `ARI` through 2023-24 and Utah `UTA`
  after (`nhl.props.history.canon_team`, dated). BettingPros `scheduled` equals the NHL schedule's `start_utc` to the minute, so the join can
  be exact on (home, away, nearest start).
- **Arizona is one-sided in BettingPros' moneyline and puck-line offers** (2020-21 to 2023-24): the offer carries a single team selection
  (the opponent's), so ~300 Coyotes games have no ML / puck-line pair at any book (totals are fine). `build_game_rows` labels a lone
  selection by comparing it with the event's `home`; do not "fix" that by guessing the missing side.
- **`ARI` is not in `nhl.config.TEAMS`**, so the deployed game engine raises `Unknown team code` pricing a Coyotes game and silently skips
  them in the Kalman update. Not fixed here (it changes production pricing); `market_eval --ari-as-uta` replays them as UTA.
- **Totals exclude the shootout, the final score includes it.** Use `home_goals_ex_so + away_goals_ex_so` for totals; the moneyline and
  puck-line use the final score (a shootout win is a 1-goal margin).
- Writing 1,400 partitions with `storage.write_parquet` took ~8 s per object (a new GCS client per call); `bettingpros_games.write_day`
  reuses one client.
