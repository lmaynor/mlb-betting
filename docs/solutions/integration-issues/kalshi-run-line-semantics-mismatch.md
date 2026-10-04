---
title: Kalshi run-line contracts and sportsbook run lines are different propositions -- the raw (selection, line) join compared unlike bets
module: mlb/analysis/kalshi_vs_books.py (_add_join_keys, _match_books, _kalshi_book_fair, _kalshi_ev)
tags: [kalshi, run-line, game_rl, join-key, false-edge, odds-history]
problem_type: integration_issue
category: integration-issues
date: 2026-10-03
---

## Problem

`kalshi_vs_books` (both `scan()` and the `scan_kalshi_side()` mirror, and therefore the
`kalshi_alert` pager, which includes `game_rl` by default) joined Kalshi quotes to sportsbook
quotes on raw `(market, game_pk, player, line, selection)`. For the run line that key identifies
**different bets** on the two sides.

## Root cause

- **Kalshi** (`KXMLBSPREAD`, `kalshi_to_history` kind `spread`) quotes "TEAM wins by MORE THAN N":
  `selection=TEAM`, `line=+N` (1.5, 2.5, 3.5). Verified against the live market text: *"San Diego
  wins by over 1.5 runs?"*, `floor_strike 1.5`.
- **Sportsbooks** quote the standard run line per side: `HOME -1.5` and `AWAY +1.5` (plus alts).
  "AWAY +1.5" covers when away wins OR loses by one -- it is the **complement** of "HOME wins by
  more than 1.5", not "AWAY wins by more than 1.5".

Real rows, SD@MIL (game_pk 849830, 2026-10-03):

| source | selection | line | fair | meaning |
|---|---|---|---|---|
| Kalshi | HOME | 1.5 | 0.455 | HOME wins by >1.5 |
| Kalshi | AWAY | 1.5 | 0.235 | AWAY wins by >1.5 |
| books  | HOME | -1.5 | 0.466 | HOME wins by >1.5 (same bet as Kalshi HOME/1.5) |
| books  | AWAY | +1.5 | 0.534 | NOT(HOME wins by >1.5) |

The old join matched Kalshi AWAY/+1.5 (0.235) to the book AWAY/+1.5 (0.534) -- unlike bets -- and
never matched Kalshi HOME/+1.5 to the book's HOME/-1.5, which *is* the same bet.

## Symptoms

- Mirror scan (`--side kalshi`) on game_rl, 2026-09-25..10-03, 65 flagged rows: **median EV +112%**,
  53/65 rows above +30% (a Kalshi ask of 0.03 "vs" a book fair of 0.56).
- Books-side scan: only half the quotes joined (858 rows) because the complement side of every pair
  could not match anything.

## Fix

Compute explicit join keys once in `_prep` (`_add_join_keys`): `_jsel`/`_jline` = the proposition a
quote prices ("T wins by more than N"), `_no` = the quote is the COMPLEMENT of it.
`book line < 0 -> (S, |N|)`; `book line > 0 -> (opp(S), N)` with `_no=True`. Kalshi rows already
carry the canonical form. Then:

- `scan()` joins on `_JK`, groups the consensus by `_JK + _no` (a quote never shares a median with
  its complement), and flips `p_true` to `1 - Kalshi mid` for `_no` rows (the bet is NOT-X).
- `_kalshi_book_fair` flips complement quotes' devigged fair prob onto the same proposition before
  taking the median.
- Whole-number run lines (a push is possible) are a different proposition and are dropped, not guessed.
- Display columns (`selection`, `line`) stay the quote's own; only the join identity changes.
- Every other market maps to itself (`_jsel == selection`, `_jline == _line`, `_no == False`), so
  game_ml / nrfi_ou / totals / props behave exactly as before. Helpers default the new columns when
  handed hand-built frames.

## Verification

Same real history (2026-09-25..10-03), old vs fixed code: mirror median EV **+112% -> -3.5%**,
rows >+30% EV 53 -> 19; books-side rows 858 -> 1,716 (the complement side now joins). 9 new
regression tests in `tests/test_kalshi_vs_books.py::TestRunLineJoin` use the exact rows above.

## The residual extremes were a separate bug -- now fixed too

After the join fix, ~27-29% of rows in a wide historical window still showed >30% EV in EVERY market
(game_ml included), because the scanner paired each source's latest snapshot independently
(in-play Kalshi vs pre-game books) and treated the synthetic `consensus` row as a book. Fixed the
same day: see docs/solutions/logic-errors/kalshi-scanner-latest-snapshot-misalignment.md
(game_ml 26.8% -> 0.3%, game_rl 29.3% -> 3.4% extreme rows).

## Prevention

When joining two sources on a "line", confirm what the line MEANS in each source (cover vs margin
threshold, sign convention, whole vs half) against the market's own rules text -- not just that the
numbers look alike. The same trap applies to any future Kalshi spread/total mapping (the
`mlb.execution.kalshi` mapper deliberately does not map `KXMLBSPREAD` for this reason).
