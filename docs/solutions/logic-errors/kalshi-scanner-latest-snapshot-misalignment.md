---
title: kalshi_vs_books paired each source's LATEST snapshot independently -- in-play Kalshi prices were matched to pre-game book prices
module: mlb/analysis/kalshi_vs_books.py (_drop_inplay, _gap_filter, _real_books, scan, scan_kalshi_side)
tags: [kalshi, odds-history, snapshot-alignment, in-play, false-edge, backtest]
problem_type: logic_error
category: logic-errors
date: 2026-10-03
---

## Problem

`scan()` and `scan_kalshi_side()` reduced each source to its latest snapshot per quote
(`_prep`: sort by snapshot_ts, `drop_duplicates(keep="last")`) and then joined the two. The two
sources are captured on different cadences and BOTH keep capturing after first pitch. So the
"latest" Kalshi print could be an in-play price (a team leads big -> 0.96) while the books' latest
was their last pre-game price. Result: manufactured edges in every market, and every backtest/
validation built on this scan was contaminated.

A second, smaller defect: the synthetic `consensus` row (an aggregate the ingestion adds) and the
`open` tag were treated as real books -- counted in `n_books`, included in the median, and (in
`scan()`) offered as a bettable quote.

## Symptoms

Real history 2026-09-25..10-03, `scan(["game_ml","game_rl"], min_ev=-1)`:

| market | rows >30% EV before | after |
|---|---|---|
| game_ml | 360 / 1,342 (26.8%) | 3 / 958 (0.3%) |
| game_rl | 447 / 1,528 (29.3%) | 33 / 985 (3.4%) |

Extremes occurred at a similar rate in game_ml, where the join was never broken -- the signature
of a scanner-wide cause rather than a per-market mapping bug (the run-line join bug,
kalshi-run-line-semantics-mismatch.md, was a separate defect found first).

## Root cause

Independent "latest per source" selection with no notion of game time. odds_history has no
first-pitch column, but every Kalshi row's `event_id` is the event ticker, whose tail encodes the
scheduled first pitch in EASTERN time (`<YY><MON><DD><HHMM>`).

## Fix

1. `_drop_inplay(odds)` -- before `_prep` chooses "latest", drop every snapshot at/after first
   pitch for each game_pk (first pitch from the Kalshi `event_id` via the new shared
   `mlb_core.odds.kalshi.event_start_utc`). Skipped for `--closing`. A game with no Kalshi row has
   nothing to join and is left alone.
2. `_gap_filter(...)` -- after matching, drop pairs whose Kalshi and book snapshots are further
   apart than `--max-gap-min` (default 60). The mirror's consensus carries `cons_ts` (latest
   contributing book snapshot) for this.
3. `_real_books(d)` -- exclude `consensus` / `open` from the book pack in both directions.

## Consequences

- **Re-run, aligned (2026-10-03; `--side kalshi --markets game_ml,nrfi_ou --since 2026-08-10 --until
  2026-10-03 --min-ev 0.03 --validate`):** flagged quotes fell from 646 to **73** (~90% of the old
  flags were misalignment artifacts; game_ml 550 -> 6). Gross ROI +3.5% (t=0.26), NET of Kalshi's
  fee +1.5% (t=0.12) -- not significant. Quotes passing the executor's own gates (net EV 3-10%
  after a 0.5% haircut): n=8, ROI -12.4%, noise. Still gross of slippage/depth (Kalshi book size is
  not in odds_history). Conclusion: at the 8x/day snapshot cadence Kalshi lag edges are RARE
  (~1.4 flags/day across both markets, ~0.15/day through the gates) and unproven.
- Any Kalshi-vs-books figure produced before 2026-10-03 used misaligned pairs: the 2026-09-19
  mirror backtest (-2.6%, t=-0.237, n=518), the 2026-10-02 re-run (-0.1%, n=646), and the
  kalshi_alert pager's alert quality. Re-run before relying on them (results recorded in
  docs/designs/kalshi_auto_execution.md).
- Live same-day use was less affected (the latest snapshots are all pre-game) but still benefited
  from the `consensus` exclusion and the gap guard.

## Prevention

When pairing two time series, pair them AT a common time (or reject pairs that are not within a
tolerance) -- never take "latest" of each independently. And define the population (pre-game only)
before aggregating, not after.
