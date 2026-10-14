---
title: "Historical NHL SOG quote DB: game_date is the SNAPSHOT date, one line per player per book, no close"
module: nhl/props/history.py, nhl/props/study.py
tags: [nhl, sog, props, backtest, odds-history, join, clv]
problem_type: logic_error
category: logic-errors
date: 2026-10-14
---

## Problem

`nhl_data.db` (the `nhl-sog-predictor` bundle, `nhl_player_props`, 96,090 SOG quotes 2026-01-01..2026-03-23) joins to outcomes
wrongly if taken at face value:

1. **`game_date` is the snapshot date, not the game date.** 99 of 488 events differ (a snapshot taken the evening before an
   afternoon game, or a UTC-date artifact). Joining outcomes on `game_date + home + away` left 127 of 487 events unmatched
   (part of that is the Utah naming below) and could pair an event with the wrong day for a team that plays on consecutive days.
   With the ET-date fix and the Utah mapping, 486 of 487 events with final results matched (the last day has no results yet).
   Fix: game date = ET date of `commence_time`.
2. **`Utah Mammoth` appears as a full name where an abbreviation belongs** (17 events). Map to `UTA`.
3. **822 quotes (0.9%) were captured 0-5 minutes AFTER the scheduled start.** Drop (`mins_to_start < 0`); they are live prices.
4. **The table PK is `(event_id, bookmaker, player_name, over_under)`**: one line per player per book. Alternate ladders
   were overwritten, so "which line did the book post" is the last write, not the main line. Pair Over/Under per
   (event, book, player, line); 2 orphan Under rows exist.
5. **One snapshot per quote, ~184 min pre-start. There is no closing line**, so no CLV. Under the repo rule (CLV, not ROI, is
   the go/no-go) this dataset can reject a model or call it "promising", but can never promote one.
6. Names: the DB's `player_game_stats.player_name` is abbreviated ("A. Laferriere"); resolve quote names with a full-name
   crosswalk (`mp_player_game.player_name` + `NHL/players_master.csv`) **inside the event's two teams**. Two real hazards: "Elias
   Pettersson" (two VAN players, ambiguous -> dropped, as the shared resolver intends) and accented names ("Stuetzle"-style)
   that are simply absent from MoneyPuck.

## Related live-feed note

In the one real ParlayAPI NHL event captured as a fixture (`tests/fixtures/odds/nhl_props_sample.json`) `player_shots_on_goal` is
a two-sided Over/Under only from `underdog` (a DFS app, excluded from consensus); `bovada` and `novig` send one-sided `Yes`
ladders (an Over at each point). So a live SOG consensus of >= 3 two-sided REAL books may be rare -- check the banked
`OddsAccum/icehockey_nhl/` snapshots before assuming the 8-9 two-sided books the historical DB has.

## Prevention

`nhl.props.history` encodes all of the above and `tests/test_nhl_props_history.py` pins them on a synthetic DB. Report n, voids
and unresolved names with any result built on this DB.
