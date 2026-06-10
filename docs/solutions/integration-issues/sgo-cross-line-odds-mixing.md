---
title: SGO _best_book_odds_int() mixed odds across different lines, poisoning BATTER props
module: mlb_core/odds/sgo.py, runners/run_batter_hits.py, runners/run_batter_tb.py
tags: [sgo, odds, batter, batter-hits, batter-tb, extractor, poisoned-bets]
problem_type: logic_error
category: integration-issues
date: 2026-06-10
---

## Problem

`_best_book_odds_int()` selected the highest American odds value across all books regardless of which line (e.g. u0.5 vs u1.5) each book was offering. This caused massive fake edge on batter prop unders.

## Symptoms

- BATTER_HITS and BATTER_TB unders showed inflated model edge
- 82 `BATTER_HITS_UNDER_1.5` bets logged between 2026-05-25 and 2026-06-10 with fake paper P&L (~+$933)
- Bets had to be deleted from the DB manually

## Root Cause

Within a single SGO odd_id (e.g. `batting_hits-PLAYERID-game-ou-under`), different books post different lines. DK may have u0.5 at -200 while BetMGM has u1.5 at +200. The old `_best_book_odds_int()` picked BetMGM's +200 odds for what DK reported as a 1.5-line bet — a meaningless comparison across different markets.

## Solution

`_best_book_odds_for_line(entry, target_line)` now restricts odds selection to books whose `overUnder` matches the canonical line (from DK or the highest-priority onshore book).

`_dk_line_float` also updated to iterate `ONSHORE_BOOKS_PRIORITY` (DK first) instead of an unordered set so the canonical line always comes from the most liquid book.

## Prevention

Any new prop extractor that calls `_best_book_odds_int()` for O/U props: use `_best_book_odds_for_line(entry, target_line)` instead. The target line must be anchored from a single reference book before comparing prices across books.

Note: `BATTER_HITS_UNDER_0.5` bets with positive odds are NOT poisoned — under 0.5 hits is the hitless prop and positive odds are correct for that market.
