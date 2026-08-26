# Handoff -- 2026-08-25 -- EV/Discord work merged+deployed; a real CI timezone-boundary bug found+fixed

Picked up from `handoffs/handoff_2026-08-20_ev_alert_tracking_and_pitcher_er_discord_bug.md`
(that file has the full technical writeup of the EV alert tracking system,
the PITCHER_ER/F1H Discord bug, and the sportsbook double group-by --
this handoff only records what happened to close it out, plus one new,
unrelated finding).

## TL;DR

- **Round 2's branch (`feat/discord-recap-ev-and-book-grouping-2026-08-20`)
  merged to `main` and deployed.** EV now renders in `#daily-recap`;
  `post_bets()` (every regular per-system pick ping) picked up the
  sportsbook double group-by; the "Soft-book price lagging..." sentence
  is gone from the +EV embed. Live on revision **`mlb-betting-00291-4f9`**,
  100% traffic, verified via Cloud Logging (this machine's own HTTP
  requests to the service still don't reach Cloud Run at all -- see the
  2026-08-20 handoff's environment note, still true, verified again the
  same way: clean container startup + a flood of real 200-status
  `api.beezy.fyi` traffic already succeeding on the new revision).
- **Found and fixed a real, currently-red CI failure on `main`** --
  unrelated to any of this session's EV/Discord changes, surfaced by the
  timing of pushing to main in the evening (Central time). Full story
  below; short version: a test fixture's "today" and the code-under-test's
  "today" can disagree by a whole calendar day for ~5 hours every
  evening, and this session's pushes happened to land in that window.
  Fixed, merged, pushed. Test-only change -- did not require a redeploy.

## The CI timezone-boundary bug

After merging + deploying round 2, `deploy_service.sh`'s own commit (the
CONTEXT.md timestamp stamp) triggered `.github/workflows/build.yml` (runs
`pytest tests/ -v` on every push to `main`, on GitHub Actions'
`ubuntu-latest`). The user pasted a CI failure:

```
test_get_today_picks_includes_clv_columns ... assert len(picks) == 1
E   assert 0 == 1
```

Root cause, confirmed with real numbers before touching anything:
`mlb/runners/public_api.get_today_picks()` always filters
`WHERE game_date = :_today` using `_ct_today()` (US/Central -- the
timezone this whole product organizes the baseball day by). The test
fixture in `tests/test_public_api_clv_and_limit.py`, though, built its
bet's `game_date` from bare `date.today()` -- the RUNNER's system
timezone, which is UTC on GitHub Actions. Central is behind UTC, so for
roughly 5 hours every evening (~7pm-midnight Central) UTC has already
rolled to the next calendar day while Central hasn't -- during that
window the fixture logs a bet dated "tomorrow" (per UTC) while the
function queries for "today" (per Central), producing exactly the
observed 0-rows-instead-of-1. Verified the actual arithmetic: the deploy
ran at Central 22:23 (per the CONTEXT.md auto-stamp), which is UTC 03:23
the *next* day -- squarely inside the mismatch window.

This is **deterministic, not flaky** -- it fails every time CI happens to
run inside that ~5hr window and passes the rest of the day, which is why
it hadn't been noticed before (most pushes to this repo apparently don't
land in that specific window) and why it showed up now (two pushes in a
row, both in the evening).

Fix: the fixture now calls `_ct_today()` (imported straight from
`mlb.runners.public_api`, the module under test) instead of re-deriving
"today" a different way -- eliminates the whole class of disagreement
regardless of the runner's own clock/timezone, rather than just papering
over this one manifestation. Grepped the rest of `tests/` for
`date.today()`/`datetime.now().date()`/`datetime.today()` -- this was the
only instance, no other latent copies of the bug. The fixture is shared
by 3 other tests in the same file (`get_picks` calls with no date filter
at all), confirmed unaffected either way.

Branch `fix/public-api-test-timezone-boundary-2026-08-25`, merged to
`main`, pushed. Test-only change (`tests/test_public_api_clv_and_limit.py`)
-- no runtime code touched, so no redeploy was needed or done for this
part.

## Verification

- `pytest tests/ -q` -- 635/635 passed (via `.venv_audit/`, same
  environment-PATH note as the 2026-08-20 handoff), both before and after
  the timezone fix.
- `compileall` clean.
- Deploy verified via `gcloud run revisions describe` (Ready=True, fresh
  `creationTimestamp`) + `gcloud logging read` for revision
  `mlb-betting-00291-4f9` -- clean startup, and real `api.beezy.fyi`
  traffic (picks/stats endpoints, multiple systems including one labeled
  `system=SB` -- not this session's work, presumably the concurrent
  session's stolen-base model referenced in the 2026-08-20 handoff --
  already succeeding on it).
- Did NOT re-run `deploy_service.sh` for the timezone fix -- it's
  test-only, the already-deployed revision's runtime behavior is
  unaffected by it.

## Where things stand

`main` has everything from the 2026-08-20 handoff's three rounds
(EV tracking, kalshi pooling, the PITCHER_ER/F1H bug fix, the recap/
grouping/copy follow-ups) plus this session's CI fix. Live on revision
`mlb-betting-00291-4f9`. All local branches from today's work are merged;
nothing outstanding from either handoff except the items already flagged
as deliberately deferred there (`capture_closing_lines.py` CLV for EV
rows; the +9.2% ROI figure is still a one-off pull worth re-checking once
more real settlement has accumulated through the new EV tracking).
