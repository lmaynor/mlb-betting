---
title: nrfi_ou selection labels never matched between Kalshi (NRFI/YRFI) and real books (YES/NO) -- zero joined rows, silently
module: mlb/analysis/kalshi_vs_books.py (_prep, _NRFI_SELECTION_MAP)
tags: [kalshi, nrfi, odds-history, selection-label, join, silent-failure]
problem_type: integration_issue
category: integration-issues
date: 2026-09-19
---

## Problem

`kalshi_vs_books.py` joins Kalshi's own nrfi_ou quotes against real books' nrfi_ou
quotes on `(market, game_pk, player_id, line, selection)`. For nrfi_ou specifically,
this produced **zero matched rows at any EV threshold** (checked with `min_ev=-1.0`,
i.e. "show literally everything"), in both `scan()` (the original book-vs-Kalshi
direction, live since 2026-08-16) and the new `scan_kalshi_side()` mirror. This looked
at first like "an efficient, tight market that never diverges enough to flag" --
plausible, since nrfi_ou is documented repo-wide (this module's own docstring, the
LIQUID set, `kalshi_to_history.py`'s docstring) as one of the deep/trustworthy Kalshi
markets. It was not that -- the join was silently matching nothing at all.

## Symptoms

- `scan_kalshi_side(['nrfi_ou'], ..., min_ev=-1.0)` returns an empty frame despite
  real coverage on both sides (confirmed 1088 Kalshi rows + 9164 book rows in a single
  40-day window).
- No error, no warning -- an inner merge on a key that never matches just produces an
  empty result, which is indistinguishable from "no divergence exists" without
  checking the raw values on both sides.

## Root cause

Kalshi's own ingestion (`kalshi_to_history.py`/`mlb_core/odds/kalshi.py`, kind="rfi")
labels its two sides `"YRFI"`/`"NRFI"` -- the same named convention every live bet
and settlement in this repo uses (CONTEXT.md's bet_type table: `"NRFI"`, `"YRFI"`).
Real-book ingestion (BettingPros historical + the live ParlayAPI-forward feed, both
checked) instead frames `run_in_1st_inning` as a plain yes/no proposition and labels
its two sides `"YES"`/`"NO"`, with no numeric `line` on either side (confirmed:
`line` is `None`/NaN for both Kalshi and book rows -- the line was never the issue).
`"YES"` and `"NRFI"` never equal each other as strings, so `_JOIN`'s `selection`
component never matches for this one market. Every other market this file handles
(hr_yn/k_ou/game_ml/...) happens to use a consistent OVER/UNDER or team-code
vocabulary on both sides, so this specific mismatch is unique to nrfi_ou.

## Fix

`_NRFI_SELECTION_MAP = {"YES": "YRFI", "NO": "NRFI"}` applied in `_prep()` to
non-Kalshi nrfi_ou rows only (Kalshi's own rows are already correctly labeled).
Semantic direction confirmed via the SGO extractor's own docstring
(`sgo.extract_nrfi_odds`: "under 0.5 = NRFI, over 0.5 = YRFI") -- "YES, a run scores"
is unambiguously YRFI, "NO" is NRFI. Fixes both `scan()` and `scan_kalshi_side()`
since both go through the shared `_prep()`. Verified: after the fix, nrfi_ou produces
real matched rows (86 flagged, 84 settled) in the same window that previously
returned zero.

**Important paired gotcha**: fixing this join surfaced Kalshi's own stale-price issue
(see `kalshi-historical-backfill-stale-closing-candles.md`) on nrfi_ou too -- an
un-sanity-checked first read of the newly-joining data showed a wild +879% ROI, which
was itself an artifact (19 of 102 flagged rows had Kalshi pricing NRFI at 1 cent,
i.e. "1% chance of a scoreless 1st inning"). Fixing a silent join failure and getting
a plausible number back are two separate steps -- do both before trusting a result.

## Related

- `docs/solutions/integration-issues/kalshi-historical-backfill-stale-closing-candles.md`
  -- the companion finding from the same investigation; fixing this join is what
  exposed the other bug's presence on nrfi_ou specifically.
- `mlb_core/odds/sgo.py::extract_nrfi_odds` -- the semantic reference for which side
  is which.
