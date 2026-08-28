# Handoff -- 2026-08-27 -- ParlayAPI credit exhaustion mislabeled as "SGO error"

Picked up from a user report: "getting SGO error that no events found... no
credits left?" Ran a live test rather than guessing.

## TL;DR

Not an SGO problem. ParlayAPI (the live `ODDS_PRIMARY`, not SGO) hit its real
account-side monthly cap -- confirmed by calling it directly with the
production key:

    403 {"detail": {"error": "OUT_OF_USAGE_CREDITS",
         "message": "Monthly credit limit reached (20,000 for starter tier).
                      Credits reset at 2026-09-01T00:00:00Z.", ...}}

SGO itself was healthy the whole time (856/2,500 monthly entities, 34%).

Root cause of the credit blowout: two Cloud Scheduler jobs
(`parlay-accum-mlb-game-lines`, `parlay-accum-mlb-props`, every 4h) that
`CONTEXT.md` claimed were "retired" were actually still `ENABLED`, calling
the same ParlayAPI account outside `snapshot_odds.py`'s own tracked ledger
-- which is why the internal tracker (8,640/mo) never saw the real spend
(20,000/mo) coming. Full root-cause writeup:
`docs/solutions/integration-issues/parlayapi-credit-exhaustion-zombie-jobs-mislabeled-sgo.md`.

The "SGO" label itself was also just wrong: `main.py`'s `/snapshot-odds`
error handler hardcoded `post_error("SGO", ...)` for every failure
regardless of which provider actually failed -- a leftover from before the
2026-06-29 ParlayAPI migration.

## What was done (all live/deployed)

1. **Paused both zombie jobs** (`gcloud scheduler jobs pause
   parlay-accum-mlb-game-lines` / `parlay-accum-mlb-props`,
   `--location=us-central1`). They are `PAUSED`, not deleted -- a future
   session should decide whether to delete them outright now that
   `snapshot_odds.py` covers the same ground, or leave them paused
   indefinitely as a safety margin. Nothing else depends on them running.
2. **No `ODDS_PRIMARY` or cadence change.** Riding out the rest of August on
   the existing automatic SGO-inning-fallback (`_gather_parlay()` already
   falls back to a full SGO pull on the 4 daily `include_sgo=True` windows
   when the ParlayAPI merge comes back empty). SGO has ample headroom for the
   ~4 remaining days. **No action needed when ParlayAPI's credits reset
   2026-09-01T00:00:00Z** -- `snapshot_odds.py` will simply start succeeding
   again on its own; nothing is coded to require a manual flip back.
3. **Code fixes**, committed+merged to `main` locally (`5013686` fix +
   `4049eb2` merge + `5ff8d6b` CONTEXT.md stamp), pushed, built, and
   deployed to Cloud Run revision `mlb-betting-00293-jzd` (100% traffic):
   - `mlb/runners/snapshot_odds.py`: `run()` now returns `"provider"` on
     every status, not just `"ok"`.
   - `main.py`: `/snapshot-odds`'s Discord error alert now labels with the
     actual provider (`result["provider"]`, uppercased) instead of a
     hardcoded `"SGO"`.
   - `nba/odds/parlayapi.py`: the 401/403 handler now reads the response
     body and logs `"OUT OF CREDITS: <message>"` when ParlayAPI's own error
     code is `OUT_OF_USAGE_CREDITS`, instead of always logging
     `"check PARLAY_API_KEY"`.
   - `CONTEXT.md` (s3): corrected the stale "parlay-accum-mlb-* are retired"
     claim.

## Verification

- `nba/odds/parlayapi.py`'s new branch exercised directly against the real
  captured `OUT_OF_USAGE_CREDITS` body plus 3 edge cases (genuine 403,
  non-JSON body, plain 401) via a monkeypatched session -- all correct, no
  exceptions.
- Full `pytest tests/` run in an ad hoc local venv (this laptop's system
  Python is 3.14, which can't build `pyarrow==17.0.0` from source -- the
  real Docker image is `python:3.11-slim`, unaffected): **626 passed, 5
  skipped, 4 failed** -- all 4 failures are the same pre-existing, purely
  local-environment `pyarrow` gap (`test_parquet_twin.py` x2,
  `test_fast_alert_loop_dedup.py`, `test_odds_alert_discord.py`), unrelated
  to this diff and files this session never touched.
- **Live production re-verification**, post-deploy: manually re-triggered
  `mlb-snapshot-2125` against the new revision. Confirmed via
  `gcloud logging read` the exact fix working:
  `ERROR nba.odds.parlayapi — parlayapi OUT OF CREDITS: Monthly credit limit
  reached (20,000 for starter tier). Credits reset at
  2026-09-01T00:00:00Z...` (previously: `parlayapi auth error 403 -- check
  PARLAY_API_KEY`). The Discord alert this triggered should read `PARLAY
  Error`, not `SGO Error` (not independently eyeballed in Discord itself,
  but the label comes directly from `result["provider"]`, which this same
  code path now guarantees is `"parlay"`).

## Open items for a future session

- Zombie jobs are paused, not deleted -- decide whether to delete them for
  good.
- No monitoring currently distinguishes "ParlayAPI credits exhausted" from
  other snapshot failure modes at the `monitor_ops` level -- worth a
  dedicated alert if this class of failure recurs after a plan upgrade or
  a future credit-pool change.
- Real ParlayAPI spend vs. our internal ledger should be reconciled once
  credits reset 2026-09-01, to confirm the zombie jobs were really the
  entire gap and nothing else is drawing on the account untracked.
