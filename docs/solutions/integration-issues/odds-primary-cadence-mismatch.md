---
module: odds
tags: [sgo, parlayapi, odds-primary, rate-limit, cloud-scheduler]
problem_type: integration-issue
---

# ODDS_PRIMARY=sgo + the 8x/day cadence overloads SGO's amateur tier

## Symptom

All 8 `mlb-snapshot-*` scheduler jobs fail every firing with `status.code: 13`
(INTERNAL) on `gcloud scheduler jobs describe`. `/snapshot-odds` returns HTTP
500 after 8-15s latency, every window, across every Cloud Run instance
(ruling out one bad instance). App log shows:

    mlb.runners.snapshot_odds -- snapshot: gather failed (sgo): 429 Client
    Error: Too Many Requests for url: https://api.sportsgameodds.com/v2/events...

Downstream: `Odds/sgo/latest.json` goes stale (>26h), `monitor_ops` alerts.

## Root cause

`ODDS_PRIMARY` (env var on the `mlb-betting` Cloud Run service) controls which
provider `snapshot_odds.py` treats as primary -- see `CONTEXT.md` s8. The
8-snapshot/day cadence (`deploy/add_snapshot_schedulers.sh`) was designed for
`ODDS_PRIMARY=parlay`: only 4 of those 8 windows pass `include_sgo=true` and
call SGO at all (~60 entities/day, well under SGO's amateur-tier 2,500/month
cap). The other 4 are meant to carry inning markets forward with zero SGO
calls.

When `ODDS_PRIMARY=sgo`, `snapshot_odds.run()` takes the direct
`provider=sgo` path instead of `_gather_parlay()` -- **every one of the 8
windows** calls SGO directly, not just 4, and a failure on that path aborts
the whole request (no graceful degradation, unlike `_gather_parlay()`'s
try/except around its own SGO merge call). The cadence and the primary-provider
setting drifted out of sync after `ODDS_PRIMARY` was rolled back from `parlay`
to `sgo` on 2026-07-22 (ParlayAPI had "dried up" -- 2 events/pull -- per
`project_profit_review_2026-07-23` in memory) without adjusting the schedule
back down to SGO-safe volume.

**Checked and ruled out:** SGO monthly entity quota was NOT exhausted at
diagnosis time (`/v2/account/usage/` showed `current-entities: 60` against the
`2500` cap). The 429s were intermittent -- some windows on the same day
succeeded (`23:05`, `01:25`) -- consistent with a transient/burst-level
throttle rather than a hard account block. The fix does not depend on pinning
down SGO's exact throttle mechanism; it depends on not needing SGO for every
window regardless.

## Fix (2026-08-10)

1. Verified ParlayAPI had recovered (shadow run via
   `POST /snapshot-odds {"provider":"parlay","out_prefix":"Odds/sgo/_shadow"}`
   returned 11 direct + 17 merged events vs. 15 on the live SGO-only snapshot,
   credits at 69/19,500 for the month).
2. Flipped back: `gcloud run services update mlb-betting --update-env-vars=ODDS_PRIMARY=parlay`
   (no redeploy needed -- this is exactly the documented cutover switch in
   `CONTEXT.md` s8).
3. Confirmed via manual scheduler trigger + log tail:
   `provider=parlay | ... | snapshot ok | 17 events`.

## Prevention / detection

- `ODDS_PRIMARY` and the snapshot cadence are a coupled pair, not independent
  knobs. Before rolling `ODDS_PRIMARY` back to `sgo` for any reason, either
  cut the cadence back to ~4x/day or accept that every window now spends an
  SGO call -- do the entity-budget math from `CONTEXT.md` s8 again at the new
  call count.
- `monitor_ops`'s "17 failures" alert was the actual catch here (see
  `scheduler-permission-denied-on-new-jobs.md` for the other 8 of those 17).
  Its scheduler-status check doesn't distinguish "provider outage" from
  "misconfiguration" -- both look like `status.code != 0`. Pull the app log
  (`textPayload:"gather failed"`) to tell them apart before assuming either.
- Direct `provider=sgo` calls have no graceful-degradation path; `_gather_parlay`
  does (SGO failure inside the merge just skips the inning-market splice and
  keeps the ParlayAPI data). This is one more reason `parlay` should be the
  default primary and `sgo` should stay a best-effort inning fallback, not the
  other way around.
