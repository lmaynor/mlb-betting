---
title: "BettingPros history: the strict pregame flag drops the CLOSING quotes, and four NHL team codes do not match the NHL API"
module: mlb_core/odds/bettingpros_loader.py, nhl/props/bp_eval.py, nhl/props/history.py
tags: [bettingpros, nhl, pregame, updated_max, team-codes, coverage, consensus, join]
problem_type: integration_issue
category: integration-issues
date: 2026-10-16
---

## Problem

Joining the historical BettingPros NHL consensus (`consensus_from_long`) to games silently loses about half the rows, and the
half it keeps is the WRONG half.

1. **`pregame == (updated_max <= scheduled)` is too strict.** Books re-post or suspend a prop within seconds of the puck drop, so
   the last-change stamp of the LAST pre-game quote is routinely 0-5 minutes AFTER the scheduled start. On goalie saves
   (2022-26, 7,520 consensus rows) 46% of rows were flagged `pregame == False`; the stamp gap is centred on -1 minute with a 95th
   percentile of +5 minutes and only 0.2% of rows beyond +10 minutes (those are in-play lines: level and outcome differ). In the
   2025-26 season nearly every row is "post-game" because the crawl captured the market right at the start (2025-26: 6 of ~2,740 scored props
   survived the strict filter). `docs/analysis/bettingpros_model_vs_market_2026-10-15.md` saw the same 28-36% loss and listed a
   grace window as "not tested".
2. **The survivors are not neutral.** The strict-filter rows are the older, hours-stale snapshots, concentrated in 2023-24; the
   dropped rows are the true closing lines. Scoring only the survivors mixes a season effect with a quote-timing effect.
3. **Four team codes differ.** BettingPros writes `LA / NJ / SJ / TB` (and `FA` for no team) where the NHL API writes
   `LAK / NJD / SJS / TBL`; Arizona (`ARI`, until 2023-24) became Utah (`UTA`) and the two never coexist. `history.TEAM_FIX` only
   mapped Utah, so a join through it cannot match any game involving those four clubs (~24% of games involve at least one of them). The earlier
   analysis reported 17% `no_game` on SOG; its pipeline was not re-run here, so that is the likely cause, not a verified one. With
   `bp_eval.canon()` applied, `no_game` is 0 on saves / goals / points / assists (2022-26).

## Fix

- `nhl.props.bp_eval.apply_slack(cons, slack_min=10)` re-labels `pregame` as `updated_max - scheduled <= 10 min`. Saves coverage went
  from 52% to 98% of consensus rows (14 postgame drops left). Both stamps are UTC (checked: the gap distribution is centred on -1 min;
  a timezone error would be hours).
- `nhl.props.bp_eval.canon()` maps both the BettingPros side and the masters side to one code set (adds ARI -> UTA);
  `history.TEAM_FIX` gained `LA/NJ/SJ/TB` (a test pins the two maps equal).
- Report EVERY BettingPros result per NHL season, never only pooled: league shot volume fell ~12% across 2022-25, goalie saves per
  start went 25.7 -> 22.9, and the books' lines lagged that fall in 2023-24 (realized over-rate 45%) and caught up in 2025 (51.5%).

## How to apply

Any new consumer of `bettingpros_loader` output should (a) use a slack window, not the strict flag, (b) normalise team codes on both
sides, (c) print the coverage record and treat a `no_game` / `postgame_quote` count above a few percent as a bug, not a feature.
