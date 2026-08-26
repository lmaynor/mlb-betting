# Handoff -- 2026-08-20 -- EV alert profitability tracking, Discord double group-by, PITCHER_ER/F1H Discord-posting bug fix

Picked up from `handoffs/handoff_2026-08-19_dedup_hr_odds_and_threshold_submarkets.md`.
User-driven session, three rounds of follow-up messages across the same
day: (1) logging/profitability of the +EV alert pager + a Discord embed
cleanup/grouping request + a reported bug, (2) "merge this to main and
deploy it, kalshi too", (3) "include EV in the daily recap, apply the
sportsbook grouping to the other systems' regular pings too, trim a
sentence from the EV embed." All resolved.

**UPDATE (round 2):** kalshi_alert.py wired into the same `system="EV"`
pool (was deliberately deferred in round 1 -- see the now-superseded
bullet below), branch merged to `main`, deployed. See "Follow-up: kalshi
wiring + merge + deploy" for exactly what changed and how the deploy was
verified.

**UPDATE (round 3):** EV now renders in the `#daily-recap` embed;
`post_bets()` (every OTHER system's regular pick pings) picked up the
same sportsbook double group-by the +EV pagers already had; trimmed one
description sentence from the +EV embed. See "Follow-up #2" near the end.
On its own branch, pushed, not yet merged/deployed as of this writing.

The rest of this handoff below is round 1's original writeup, left as-is
for the reasoning trail rather than rewritten in place.

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
- Did NOT run `deploy/deploy_service.sh` and did NOT deploy to Cloud Run
  in THIS first pass -- the user's original message asked to "commit and
  push," not deploy; these are live-Discord-posting and live-settlement
  code paths, so treating a deploy as a separate, explicitly-confirmed
  step felt like the right level of caution given the size of this
  change. **Superseded same day**: the user's follow-up message
  explicitly asked to merge + deploy -- see "Follow-up" section below for
  that (and for how "did it actually deploy" got verified despite this
  machine's HTTP requests to the service never actually reaching Cloud
  Run at all).

## Loose threads

- ~~Not deployed~~ -- **superseded, see the follow-up section below**:
  merged to `main` and deployed same day (revision `mlb-betting-00289-6g8`).
- ~~kalshi_alert.py deferred~~ -- **superseded**: wired in the same
  follow-up, see below.
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

## Follow-up (same day): kalshi wiring + merge + deploy

User's next message: "Merge this to main and deploy it kalshi too."

**kalshi_alert.py now pools into the same `system="EV"`** the first pass
built for fast_alert_loop.py -- the "deliberately deferred" bullet above
is superseded. Both pagers' posted alerts settle through the identical
`settle_bets._settle_ev()` dispatcher. This took more than copy-pasting
the logging call, because kalshi_alert.py scans markets fast_alert_loop
never does (`nrfi_ou`, `game_ml`, `f5_ml`, plus `game_total`/`game_rl`
which have no settler at all) and three of those needed real changes:

- `fast_alert_loop._ev_bet_type()` extended: `nrfi_ou` -> bare
  `"NRFI"`/`"YRFI"` (OVER 0.5 = a run scored = YRFI), `game_ml` ->
  `"GAME_{SIDE}"`, `f5_ml` -> bare `"{SIDE}"` (F5's own bet_type has no
  prefix at all). `game_total`/`game_rl` explicitly return `None` --
  carried in `odds_history` (`bettingpros_to_parquet`'s `system=""`
  entries) but genuinely have no settler to grade them against.
- **Found a real bug in my own first-pass design before it ever shipped**:
  the "_{book}" suffix convention that already worked fine for HR/K/OUTS/
  BATTER_TB/BATTER_HITS/PITCHER_ER (all fixed-position-prefix parsers that
  ignore trailing tokens) breaks NRFI's exact-string match, F5's
  exact-string match, and the innings-window settler's
  `rsplit("_", 1)` -- appending a book suffix to `"NRFI"` or `"HOME"`
  means those settlers' equality checks never fire. Fixed by adding
  `settle_bets._strip_ev_book_suffix()`: strip the suffix back off before
  handing those three families their pending rows, rather than touching
  their parsing (which also grades the real, live NRFI/F5/GAME systems'
  own bets -- safer to leave alone). Caught this via tracing the existing
  settler code before writing tests, not via a failing test -- worth
  tracing exact-match vs prefix-match parsing explicitly next time this
  convention gets extended to a new market shape, rather than assuming
  the suffix trick generalizes.
- kalshi_alert.py's own `_log_ev_bets()` is a separate small adapter (not
  a reuse of fast_alert_loop's), since its row shape differs
  (`ev_pct`/`p_true`/`cons_impl`, not `ev`/`consensus_fair`/`decimal`) --
  it does reuse `_ev_bet_type`/`_EV_BET_DB`/`_EV_STAKE_UNIT` from
  fast_alert_loop.

**Also fixed, before deploying**: my own first-pass CONTEXT.md edit had
made the `_Last updated:` header a 3-line sentence. `deploy/
deploy_service.sh` auto-stamps that line every deploy via
`re.sub(r"_Last updated:.*?_", ...)` -- non-DOTALL, so it only matches
within a single line. This is *exactly* how the original garbled fragment
I fixed earlier this session (`_TB/BATTER_HITS -- see s5/s6...`) got stuck
there permanently: some past edit appended text after the closing `_`
on the same line, and every subsequent auto-stamp since has replaced only
the `_..._` portion, silently leaving that trailing fragment untouched
forever. Reverted my line to a bare `_Last updated: 2026-08-20_` with no
trailing content, so the auto-stamp mechanism works correctly on every
future deploy instead of accumulating more stale text. If this line ever
needs commentary again, it belongs in a handoff (or right below the
line, outside the `_..._` span), not appended inside/after it.

Tests: `tests/test_fast_alert_loop_ev.py` gained 6 more cases (nrfi_ou/
game_ml/f5_ml/game_total/game_rl bet_type mapping); `tests/
test_settlement.py`'s `TestSettleEv` gained 6 more (NRFI/GAME/F5
dispatch + the suffix-stripping + a two-books-collide-to-one-row check);
new `tests/test_kalshi_alert_ev.py` (6 tests, including one that verifies
the cross-pager pooling/dedup claim above -- fast_alert_loop logs a quote
first, kalshi_alert independently finds the identical one, second logging
call must hit the dedup key and log zero new rows). Full suite: 609
passed (was 589 after the first pass; +20 new, 0 broken).

**Merge + deploy**: merged (`--no-ff`) to `main` locally, 609/609 tests
green on `main` post-merge, pushed. Ran `deploy/deploy_service.sh` (with
`.venv_audit/bin` prepended to PATH so its bare `python3`/`pytest` calls
resolve to a real environment -- the system `python3` has none of
xgboost/sklearn/sqlalchemy/pytest installed, would fail the script's own
test gate immediately otherwise; see "environment note" below). Build
succeeded, tests passed inside the script too, deployed. New revision
**`mlb-betting-00289-6g8`**, 100% traffic.

**Verification hit a real environmental wall, worth recording**: every
attempt to `curl` the live service from this machine -- the plain
`*.run.app` URL, a `gcloud run services proxy` local tunnel, even the
custom domain `api.beezy.fyi` -- returned an identical Google-branded
"404 (Not Found)!!1" page. The giveaway: `curl -v`'s TLS certificate for
the direct HTTPS attempt was issued by `ca.deloitte-ame-wps.goskope.com`
(Netskope, Deloitte's corporate CASB/DLP proxy), not Google -- this
machine's outbound HTTPS is being intercepted, and apparently blocked for
this exact hostname pattern regardless of which domain fronts it,
including through gcloud's own tunnel. **None of these curl attempts ever
reached Cloud Run at all** -- confirmed by reading Cloud Logging
(`gcloud logging read`, a `*.googleapis.com` control-plane call, unaffected
by the interception) for the new revision: zero entries for any of my
request timestamps. So don't trust a 404 from this machine against this
service as meaning anything about the deploy -- verify via `gcloud`
control-plane calls instead. What Cloud Logging DID show, real and
credible:
- Clean startup: gunicorn booted, "Default STARTUP TCP probe succeeded",
  zero errors, for revision `mlb-betting-00289-6g8` specifically.
- **Real production traffic already succeeding on it**: two organic 200s
  within the first ~10 minutes (`GET https://api.beezy.fyi/api/public/
  picks/today` from a `python-requests` client, `GET .../picks/recent`
  from a `node` client -- the actual beezy.fyi frontend/other consumers,
  not me).

That's real, credible evidence the deploy is live and healthy. What it
does NOT confirm: the specific NEW code paths from this session (EV
alert logging, the Discord embed changes, the PITCHER_ER/F1H gate fix)
haven't been exercised by a real scheduled run yet as of this writing --
those are background-job behaviors (fast_alert_loop/kalshi_alert/settle/
run), not something an HTTP health check would touch anyway, and I did
NOT force-trigger any of them (posting to live Discord channels / staking
real paper bets on demand felt like the wrong thing to do just to
self-verify). They'll exercise naturally on each job's own next scheduled
tick per CONTEXT.md s4/s9. If something looks off in `#daily-picks` or
`#performance` after that, or `BetTracker(db, system="EV").summary()`
comes back empty for today, come back to this revision first.

**Environment note for future sessions**: this machine's plain `python3`/
`pip3` (Homebrew, externally-managed) has NONE of the project's
dependencies installed and refuses plain `pip install` (PEP 668). There's
a pre-existing `.venv_audit/` in the repo root (from the 2026-08-16 audit
session, per its own docstring in `tests/conftest.py`) with everything
needed (pytest, xgboost, sklearn, sqlalchemy, pg8000, scipy) for Python
3.14, since production's pinned versions (numpy==1.26.4 etc.) have no
3.14 wheels. Prepend `.venv_audit/bin` to PATH when running tests or
`deploy_service.sh` directly on this machine, e.g.:
`PATH="$(pwd)/.venv_audit/bin:$PATH" bash deploy/deploy_service.sh`.

## Follow-up #2 (same day): EV in the recap, book-grouping for all systems, trim the EV embed copy

User's next message, after the merge+deploy above: "Please include EV
system as a system in the daily recap. Also include the sportsbook group
by criteria for the regular Discord pings on other systems. Also for the
EV alerts, take out the sentence that says soft book price lagging."
Three small, independent Discord-formatting changes, all in
`mlb_core/notify/discord.py` (plus one line in `fast_alert_loop.py`):

1. **EV now renders in `#daily-recap`.** `post_all_systems_summary()`'s
   render loop walks `CANONICAL_ORDER + _EXTRA_RECAP_SYSTEMS` (a new,
   deliberately LOCAL list in `discord.py`, currently `["EV"]`) instead of
   just `CANONICAL_ORDER` -- keeps the registry itself (and
   `monitor_performance.py`'s gate loop that also reads it) completely
   untouched, per the scope boundary from the first pass. Minor thing
   found while doing this: the recap's top-line "Combined paper P&L"
   total had ALREADY been silently including EV's contribution before
   this fix (it sums over every key in the passed-in stats dict, not just
   `CANONICAL_ORDER`) with no field shown for it -- this closes that quiet
   mismatch rather than creating a new one.
2. **`post_bets()` (the regular per-system #daily-picks pings -- HR, NRFI,
   F5, K, OUTS, BATTER_TB, BATTER_HITS, GAME, PITCHER_ER) now uses the
   same sportsbook double group-by** the +EV pagers' embeds already had:
   one field per book (books ordered by their own best edge), bets within
   a book ordered by edge. New `_grouped_bet_fields()` is a direct parallel
   of `fast_alert_loop._grouped_fields()`. One shared function, so every
   system that calls `post_bets()` picked this up automatically -- no
   per-system changes needed.
3. **Removed the "Soft-book price lagging..." sentence** from
   `fast_alert_loop.notify()`'s embed entirely (dropped the `description`
   key, not just blanked it) -- user said it wasn't adding anything.
   Left `kalshi_alert.py`'s own (different) description sentence alone;
   it wasn't mentioned and is a distinct piece of text.

Tests: `tests/test_discord_notify_grouping.py` (new, 12 tests covering
`_grouped_bet_fields`/`post_bets` and the recap's EV inclusion) + one
more in `tests/test_fast_alert_loop_ev.py` (embed has no `description`
key at all). Full suite: 635 passed (was 609). `compileall` clean.

Committed on branch `feat/discord-recap-ev-and-book-grouping-2026-08-20`,
pushed. **NOT merged to main, NOT deployed** -- this message only asked
to "commit and push," mirroring the same two-step pattern as the first
round (commit+push, then a separate explicit ask to merge+deploy).

## Where things stand

First round (**EV tracking + PITCHER_ER/F1H fix**): merged to `main`,
deployed, live on revision `mlb-betting-00289-6g8`, confirmed via Cloud
Logging (not an HTTP smoke test -- this machine's own requests to the
service never reach Cloud Run at all, see above).

Second round (**recap EV inclusion + book-grouping for post_bets() +
trimmed EV embed copy**): on branch
`feat/discord-recap-ev-and-book-grouping-2026-08-20`, pushed, NOT yet
merged or deployed. 635 Python tests passing.

Nothing left pending from either round except the deliberately-scoped-out
items already listed above (`capture_closing_lines.py` CLV for EV rows;
the +9.2% ROI figure is still a one-off pull worth re-checking in a few
weeks once real settlement accumulates through the new tracking).
