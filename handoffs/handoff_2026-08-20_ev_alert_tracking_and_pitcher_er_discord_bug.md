# Handoff -- 2026-08-20 -- EV alert profitability tracking, Discord double group-by, PITCHER_ER/F1H Discord-posting bug fix

Picked up from `handoffs/handoff_2026-08-19_dedup_hr_odds_and_threshold_submarkets.md`.
User-driven session: four separate asks bundled into one message (logging/
profitability of the +EV alert pager, a Discord embed cleanup, a Discord
grouping request, and a reported bug). All four resolved. **Code is
committed on branch `fix/ev-alert-tracking-and-pitcher-er-bug-2026-08-20`,
pushed to origin, tests green (589/589) -- NOT yet merged to main or
deployed.** This session did not touch prod.

## TL;DR

- **Confirmed + fixed a real, live bug**: PITCHER_ER (in `run_k.py`) and,
  found along the way, the identical bug in F1H (in `run_f5.py`) were
  posting EVERY scored prediction to `#daily-picks` -- including
  negative-edge, zero-stake ones -- because their Discord-bound row lists
  were appended unconditionally, missing the `if kelly_triggered:` gate
  every other runner has. This is what the user saw as a negative-edge
  Cade Cavalli PITCHER_ER pick on 2026-08-19. See docs/solutions/
  logic-errors/log-only-runner-must-gate-discord-rows-on-kelly-triggered.md
  for the full writeup. Both are log-only systems, so post-fix they now
  correctly post nothing (matching GAME/BATTER_HITS's existing behavior)
  until each graduates off log-only.
- **fast_alert_loop's Discord embed**: removed the "Lineup events" field
  (raw game_pks, not actionable on its own -- hot-game badge/sort logic is
  untouched); replaced the one-field-per-alert layout with a double
  group-by -- one field per sportsbook (books ordered by their best EV),
  alerts within a book ordered by EV.
- **EV notifications ARE being logged** (confirmed, they were already):
  `Alerts/{day}/log.parquet` + `notified.parquet` (fast_alert_loop) and
  `odds_alert.py`'s own CLV-style `resolved.parquet` scorecard (posts to
  `#performance`). But that scorecard resolves against a LATER quote, not
  a real settled outcome -- its own docstring flags true ROI settlement as
  unbuilt. Built that: every alert actually posted now ALSO logs into the
  `bets` table (`system="EV"`, flat stake) and settles nightly via a new
  `settle_bets._settle_ev()` that delegates to the exact settler each
  alert's underlying market already uses. Deliberately kept OUT of
  `mlb_core.registry.SYSTEMS`/`CANONICAL_ORDER` -- see "why EV isn't a
  registry system" below.
- **Ran a one-off real-outcome settlement** of ~14 days of already-posted
  alerts (2026-08-06..19) against the actual MLB Stats API results (not
  just CLV) to give the user an actual profitability read while this was
  being built. Result: **+9.2% ROI, 54.2% hit rate across ~1500 decided
  bets, positive in every market**, EXCLUDING a ~1 week window
  (2026-08-10..17) where Kalshi was still incorrectly showing up as a
  bettable "book" in this same outlier scan (pre-dates the 2026-08-17
  finding-C4.1 fix that added Kalshi to `backtest_market.OFFSHORE`) --
  including that window doesn't change the sign, ROI is a bit lower
  (+6.6%) because ~55% of it is Kalshi rows with a much higher void rate.
  This was a throwaway local script (MLB Stats API is public, no prod
  creds touched) -- NOT committed, NOT automated. The new EV
  logging/settlement above is what makes this an ongoing, queryable
  number instead of a one-time pull.

## Why EV isn't a registry system

Deliberately did NOT add `"EV"` to `mlb_core.registry.SYSTEMS` or
`CANONICAL_ORDER`. That registry is model-system-shaped (feature_csv,
model_artifact, build_sentinel, `expected_hit_rate`) and
`monitor_performance.py`'s `CANONICAL_ORDER` loop drives the LIVE
suppression-gate + Discord performance-alert machinery off it -- none of
which is designed for (or safe to run against) a book-vs-consensus outlier
feed with no model probability, no AUC, no calibration. Consequences,
spelled out in CONTEXT.md's new "EV bet tracking" subsection (s5) so a
future session doesn't "helpfully" wire it in without re-deriving this:

- EV settles nightly (`settle_bets.SYSTEM_MAP`/`ALL_SYSTEMS` both include
  it) and is queryable (`BetTracker(db, system="EV").summary()`), but does
  **not** appear in the `#daily-recap` embed and is **not** covered by
  `monitor_performance.py`/`monitor_ops.py`.
- `capture_closing_lines.py` was not wired to capture closing lines for EV
  rows -- `closing_odds`/`clv_pct` stay NULL on them. Not done this
  session; a reasonable, low-risk follow-up.
- `mlb.runners.kalshi_alert` (the sibling pager, same strategy, Kalshi
  anchor instead of Pinnacle-consensus) was NOT wired into `system="EV"`
  logging this session -- scoped out to keep this change to one file's
  blast radius. Same mechanism would apply directly if picked up later
  (its alerted `book` is a real bettable book already, unlike Kalshi
  itself which is excluded from what gets flagged).

## What actually changed (files)

- `mlb/runners/run_k.py` -- PITCHER_ER Discord-gate fix.
- `mlb/runners/run_f5.py` -- F1H Discord-gate fix (same bug, found while
  fixing the above).
- `mlb/runners/fast_alert_loop.py` -- Lineup Events field removed;
  `_grouped_fields()` double group-by; `_ev_bet_type()` +
  `_log_ev_bets()` (new EV bet tracking); `notify()` signature dropped the
  now-unused `notes` param.
- `mlb/runners/settle_bets.py` -- new `_settle_ev()`; `"EV"` added to
  `SYSTEM_MAP` and `ALL_SYSTEMS` (see scope-boundary note above for why
  NOT `CANONICAL_ORDER`).
- `CONTEXT.md` -- Discord posting contract gained an explicit RULE (gate
  Discord rows on `kelly_triggered`), bet_type table gained an EV row, new
  "EV bet tracking" subsection (s5), settlement sources table gained an EV
  row, repo-map entry for `fast_alert_loop.py` updated. Also fixed an
  unrelated pre-existing garbled sentence in the file's own header
  timestamp line (a leftover fragment from some earlier edit, not
  something this session caused -- flagging since CONTEXT.md is supposed
  to be the clean source of truth).
- `docs/solutions/logic-errors/log-only-runner-must-gate-discord-rows-on-kelly-triggered.md`
  -- new.
- Tests: `tests/test_fast_alert_loop_ev.py` (new, 20 tests: `_ev_bet_type`,
  `_log_ev_bets`, `_grouped_fields`/`notify` embed shape) and
  `tests/test_settlement.py` gained a `TestSettleEv` class (9 tests). Full
  suite: 589 passed (was 560 as of the last handoff; +29 new, 0 broken).
  `tests/test_fast_alert_loop_dedup.py` (pre-existing) gained one stub line
  -- see "a hermeticity catch" below, it's worth reading if you touch
  `fast_alert_loop.run()` again.

## A hermeticity catch worth knowing about

The pre-existing `test_fast_alert_loop_dedup.py` calls `fal.run()`
end-to-end. The first full-suite run after wiring in `_log_ev_bets()`
silently created a REAL `EV_Alerts/data/ev_bets.db` file in the repo
working directory -- that test never mocked the new DB write, so it
executed for real against `_EV_BET_DB`'s default relative path. Caught it
via `git status` showing an untracked file after the test run, not
because any test failed (the call is wrapped in try/except in `run()`
specifically so a logging hiccup never breaks the pager, which also meant
it never breaks a test that doesn't check for it). Fixed by stubbing
`fal._log_ev_bets` in that test's `stub_environment` fixture. Worth
flagging because the failure mode -- a test silently touching the real
filesystem, or a real database if `MLB_DB_URL` happened to be set in
whatever ran the suite -- produces no red test output at all; only
`git status` after the fact caught it here.

## Verification performed

- `pytest tests/ -q` -- 589/589 passed, via the pre-existing
  `.venv_audit/` test environment (Python 3.14, matches
  `tests/conftest.py`'s documented rationale for why this repo keeps an ad
  hoc test venv alongside prod's pinned Python 3.11 requirements.txt).
- `python3 -m compileall mlb mlb_core main.py` -- clean.
- Did NOT run `deploy/deploy_service.sh` and did NOT deploy to Cloud Run --
  the user asked to "commit and push," not deploy; these are live-Discord-
  posting and live-settlement code paths, so treating a deploy as a
  separate, explicitly-confirmed step felt like the right level of
  caution given the size of this change. **Next session (or this user):
  review the diff, then deploy.**

## Loose threads

- **Not deployed.** Everything above is on a branch, pushed, not merged.
  `run_k.py`/`run_f5.py`'s fix in particular should go out promptly --
  it's the exact bug the user reported.
- **kalshi_alert.py** could get the identical `system="EV"` logging
  treatment (or a distinct `system="EV_KALSHI"` if the two anchors'
  results shouldn't be pooled) -- deliberately deferred, see above.
- **capture_closing_lines.py** doesn't capture CLV for EV rows yet.
- **The +9.2% ROI figure is one ad hoc pull, ~14 days, ~1500 decided
  bets.** Promising, not proof -- this project's own convention elsewhere
  is a 200-settled-bet gate / significant-CLV bar before calling something
  a real edge (see `backtest_market.py`'s `verdict()` and the CLV
  significance bar in `mlb_core.risk.clv.clv_verdict`). Worth re-running
  `BetTracker(db, system="EV").summary()` in a few weeks once real
  settlement has accumulated, rather than trusting the one-off number.
- **Yesterday's (2026-08-18) already-corrupted HR bets** from the prior
  handoff are still uncorrected (unrelated to this session, just still
  open).

## Where things stand

Branch `fix/ev-alert-tracking-and-pitcher-er-bug-2026-08-20`, pushed to
origin. 589 Python tests passing. Not merged, not deployed. `main` itself
is untouched and still at the commit from the last handoff.
