---
title: BettingPros O/U ingest collapsed per-book lines (0.5 vs 1.5 mixed into one row)
module: mlb_core/odds/bettingpros.py (_rows_player_ou, _rows_two_sided_game, _rows_team_total)
tags: [bettingpros, odds, over-under, total-bases, player-props, line, ingest, corruption]
problem_type: integration_issue
category: integration-issues
date: 2026-07-01
---

## Problem

For player O/U props, BettingPros serves DIFFERENT main lines per book for the same
player: e.g. FanDuel (book id 10) posts Total Bases 1.5 while bet365/HardRock/etc post
0.5 (confirmed live: 112 of 277 players on 2026-07-01). The ingest built ONE CSV row
per player with a single `Line` (defaulted to "0.5", set only from the OVER selection)
and `_sel_odds` took `book["lines"][0]` -- so a book's 1.5-line price landed in a row
labeled 0.5 (and vice versa). Downstream, `bettingpros_to_parquet` tags every book in
the row with that one line -> odds_history rows where the price does not match the line.

## Symptoms

- odds_history: for one player/line, some books quote OVER as a heavy favorite and
  others as a heavy dog -- internally-consistent MIRROR images (e.g. bet365 OVER -185
  vs FanDuel OVER +205). Both prices are individually SANE -- for DIFFERENT lines
  (OVER 0.5 ~ -185 favorite; OVER 1.5 ~ +205 dog for a weak hitter).
- Backtests show a huge, stable "edge" (BATTER_TB ~+30% ROI) that is actually the
  max-edge line-shop betting a favorite-priced-as-dog mislabeled row. CLV stays
  negative (-6 to -12%) the whole time -- the tell it is not a real edge.
- ~29-40% of prop rows affected; profit concentrates on lines 0.5 and 1.5.

## Root cause

`_rows_player_ou` (and `_rows_two_sided_game` totals, `_rows_team_total`) assumed one
line per player and jammed all books into a single row, discarding each book's own
`lines[].line`. It is NOT a swap and NOT a stale line -- it is a per-book line collapse.

## Fix

`_sel_by_line(sel)` returns `{line: {book_col: odds}}` keyed by EACH quote's actual
line; `_rows_by_line(base, selections, sides, out)` emits ONE row per distinct line,
pairing each book's over/under of the SAME line. All three O/U builders use it. yesno
(NRFI) has no line and is unchanged. Verified with a synthetic offer (FanDuel 1.5 +
bet365 0.5) -> two clean rows, no cross-line mixing.

Note: the OLD CSVs already lost per-book line info (collapsed), so the store must be
REPAIRED by re-fetching: re-run the BettingPros backfill (fixed parser) then
`bettingpros_to_parquet`. Until then, backtests must gate on cross-book agreement
(`backtest_market --min-books 4 --max-spread 0.10`), which drops the mixed rows.

## Related

- `handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md`
- `docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md` (the CLV-not-
  ROI rule that first flagged the "edge" as an artifact; this is the mechanism behind it)
- Diagnostic: `mlb/analysis/diagnose_bettingpros_ou.py`
