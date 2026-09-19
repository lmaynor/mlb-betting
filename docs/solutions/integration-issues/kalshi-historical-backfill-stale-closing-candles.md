---
title: Kalshi historical closing-candle backfill can produce stale/unrepresentative prices, manufacturing a fake -30% "edge"
module: mlb/analysis/kalshi_history.py (closing_candle), mlb/analysis/kalshi_vs_books.py (scan_kalshi_side)
tags: [kalshi, odds, backtest, closing-line, candlesticks, stale-quote, false-edge]
problem_type: integration_issue
category: integration-issues
date: 2026-09-19
---

## Problem

A first-pass backtest of `scan_kalshi_side()` (is buying Kalshi's own game_ml/nrfi_ou
contract +EV vs the real book pack's devigged consensus -- the mirror of the existing
book-vs-Kalshi scanner) over Kalshi's full available history (2026-05-17..today) showed
a strongly negative, statistically significant result: ROI -32.8%, t-stat -4.56, n=970.
That looked like real, damning evidence the strategy loses money -- but ~20% of the
flagged rows had Kalshi asking 2 cents for a side the real book consensus had as a
55-64% FAVORITE (edge/ev_pct in the 2500-3100% range), which is not a plausible real
price for a liquid, efficient two-sided moneyline market.

## Symptoms

- `implied_prob` (Kalshi's own ask) pinned at exactly 0.02 for the flagged extreme
  rows, paired with `book_fair` of 0.55-0.64 for the SAME selection -- both directions
  of the pair (e.g. HOME=0.02, AWAY=0.99) internally consistent (sums to ~1.01), so
  this isn't a HOME/AWAY selection-mapping bug -- it's a real stored Kalshi print, just
  an implausible one.
- Confirmed NOT a snapshot-timing mismatch (e.g. comparing Kalshi's price at/near actual
  game resolution against a genuinely pregame book consensus): manually checked one
  flagged game (SF@MIL, 2026-06-04) -- Kalshi's row is timestamped 21:35 UTC, a good
  ~2.5h *before* the book pack's own 23:30 UTC closing snapshot that same evening, both
  clearly pregame.
- Filtering the extreme (implied_prob<0.05 or >0.95) rows out still left a significant
  negative result on the same window (ROI -23.2%, t=-3.02, n=783) -- the extreme rows
  weren't the whole story.
- Re-running the identical scan+validate bounded to ONLY the live forward-capture window
  (2026-08-10..today, i.e. excluding the 2026-05-17..07-22 historical-backfill window
  entirely) on the same two markets came back ROI -8.2%, **t-stat -0.645 (n=441) -- not
  statistically significant**. Night-and-day difference from the same analysis on the
  historical-backfill-heavy window.

## Root cause

`kalshi_history.py`'s `closing_candle()` (the historical backfill, covering
2026-05-17..07-22 for game_ml/nrfi_ou/game_total/game_rl/f5_ml) fetches the last ~24h
of hourly candles before a market's `close_time` and returns the LAST one with a valid
two-sided quote (`yes_bid>0 and yes_ask>0`), scanning backward. For an MLB team's own
"will X win" contract, real two-sided trading activity/liquidity is not guaranteed to
be continuous through the whole pregame window -- if the book goes quiet for hours after
some early, thin activity, "the last valid 2-sided candle in the lookback" can land on
a stale, unrepresentative print from early in the day rather than a genuine
closing-consensus price. Kalshi's own bid/ask SIZE and volume/open-interest are captured
in the raw ingestion (`mlb_core.odds.kalshi.prices()`) but are NOT persisted into the
odds_history schema (`_rows_for_market()` only keeps price fields) -- so there is no way,
from odds_history alone, to tell a real liquid print from an old resting order nobody
would actually transact against at size. The live forward-capture path
(`kalshi_to_history.py`) doesn't have this specific failure mode as badly, presumably
because it samples the CURRENT top-of-book repeatedly through the day rather than
searching backward through a candle history for whatever the last valid print happened
to be.

## Fix

No code fix applied -- this is a data-quality characteristic of the historical backfill
to work around, not a bug to patch. When backtesting `scan_kalshi_side()` (or anything
else built on Kalshi historical data for these markets):
- Prefer `--since 2026-08-10` (after the 2026-07-24..08-09 live-capture gap) over
  including the 2026-05-17..07-22 closing-candle-backfill window, unless the analysis
  can otherwise account for stale/thin prints.
- Don't trust a single extreme (near-0 or near-1) Kalshi implied_prob at face value on
  a game-level moneyline without corroborating it (there is no liquidity/size signal in
  odds_history to lean on).
- If a bet-sizing / live-execution use of Kalshi's own price is ever built (this repo
  does not currently place orders anywhere), it would need REAL order-book depth at
  decision time, not a value out of odds_history.

## Related

- `docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md` -- the CLV/
  significance-not-ROI-alone rule that generalizes this: a huge headline ROI number is
  itself a reason for suspicion, not confidence, until corroborated.
- `docs/solutions/integration-issues/bettingpros-per-book-line-collapse.md` -- same
  shape of finding (a stable, large "edge" that was actually an ingest/data artifact),
  different root cause.
- `mlb/analysis/kalshi_vs_books.py` module docstring, "THE MIRROR" section.
