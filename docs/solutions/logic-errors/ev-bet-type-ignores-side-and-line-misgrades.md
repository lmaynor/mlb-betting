---
title: EV alert bet_type mapping ignored side/line -- HR "Under 0.5" graded as "1+ HR wins", NRFI/YRFI labels inverted, backfill lost player names
module: mlb/runners/fast_alert_loop.py (_ev_bet_type, _log_ev_bets), scripts/backfill_ev_history.py, mlb/analysis/kalshi_vs_books.py (_realized_outcomes)
tags: [ev, settlement, bet-type, hr-yn, nrfi, kalshi, backfill, silent-failure]
problem_type: logic_error
category: logic-errors
date: 2026-10-02
---

## Problem

`system="EV"` rows are graded by delegating to the real per-market settlers
(`settle_bets._settle_ev`), keyed off a `bet_type` string built by
`fast_alert_loop._ev_bet_type(market, selection, line, book)`. Three defects in
or around that mapping produced wrong grades with no error anywhere:

1. **`hr_yn` ignored `selection` and `line`.** Everything became `"HR_{book}"`, and
   `_settle_hr` grades every `HR*` bet as "win iff the batter homered". The scanner
   emits every `(line, selection)` for `hr_yn`, so "Under 0.5 HR" (no HR; ~13% of the
   1,549 posted hr_yn alerts, 129 non-Kalshi) was graded as the opposite bet.
   Replayed against real boxscores: graded as the settler would, those 129 alerts lose
   -$10,122 on flat $100; graded correctly they make +$1,040 (a ~$11k distortion).
   The "implausible model_prob ~0.99 at -2000..-2800" cluster that blew up the
   Kelly-bankroll report was these rows (P(under 1.5 HR) really is ~0.99) -- not a
   `consensus_fair` bug, so the 0.05 stake cap in `ev_kelly_bankroll.py` was
   treating a symptom, and the "Kelly underperforms flat" conclusion was an artifact.
2. **`nrfi_ou` assumed OVER/UNDER.** Real book rows are `YES`/`NO`
   (`mlb_core/odds/bettingpros.py`: `run_in_1st_inning` is kind `"yesno"`), and
   `kalshi_vs_books._prep` (2026-09-19) normalizes them to `YRFI`/`NRFI`. The mapping
   was `"YRFI" if sel == "OVER" else "NRFI"`, so every YES/YRFI alert became NRFI.
   Latent until the join fix deployed (before it, nrfi_ou produced zero rows).
3. **`backfill_ev_history.py` took `player_name` from `log.parquet`.** `odds_alert.py`
   also appends (unnamed) rows to that file and its `keep="last"` dedup can overwrite
   the named `fast_alert_loop` row. ~53% of recoverable rows (3,261 / 6,166) had no
   name -> `player="AWAY @ HOME"` -> every settler voids it (and unnamed rows in one
   game collide on the `(system, game_date, game_pk, player, bet_type)` dedup key).
   This, not DK non-starter rules, is most of the "37-52% void rate" the 2026-09-18
   handoff reported.

Related: `kalshi_vs_books._realized_outcomes("nrfi_ou")` read `yrfi` from the NRFI
`model_features.csv` as if game-level; it is one row per STARTER (that pitcher's own
1st-inning runs), so `dict(zip(...))` kept whichever row was last and mislabeled
~20% of games NRFI. Game-level YRFI = `groupby(game_pk).yrfi.max()`.

## Why tests didn't catch it

The tests asserted the *assumed* convention (`test_nrfi_over_is_yrfi`) and never
passed `UNDER` for `hr_yn`, `YES`/`YRFI` for `nrfi_ou`, or an unnamed row through the
backfill. The suite passed 715/715 while all of the above was wrong.

## Fix

- `_ev_bet_type`: `hr_yn` only maps OVER/YES at line <= 0.5 (anything else -> `None`,
  i.e. not logged -- no settler exists for it); `nrfi_ou` accepts YES/OVER/YRFI vs
  NO/UNDER/NRFI, unknown -> `None`.
- `_log_ev_bets` and `recover_fast_alert` re-resolve a missing name from `player_id`.
- `_realized_outcomes` aggregates to game level.
- Regression tests for each (verified to fail on the old code).

## Prevention

- Any `bet_type` that is handed to an existing settler must encode **everything that
  settler needs to grade the bet correctly**. If the settler can't grade a side/line,
  return `None` rather than approximating.
- When tests mirror a mapping's own assumption they prove nothing -- add a case from
  REAL data (what selections/lines does the scanner actually emit?) before trusting it.
- A table with a per-entity grain (starter, batter) is not a per-game label table.
  Check the grain before `dict(zip(game_pk, label))`.
- Data already written to `bets` under the old mapping was wrong; remediated 2026-10-03 via
  `mlb/analysis/ev_remediate.py` (backup table `bets_ev_backup_20261003`). A one-off prod data fix
  belongs in a Cloud Run Job with a read-only `--stats` default, a never-overwritten backup, and a
  `--settle` re-entry mode -- the first run crashed in grading AFTER inserting, so re-entry mattered.
- `_settle_ev` must never `int()` a NULL game_pk (void it) -- one such row aborts the whole settle run.
