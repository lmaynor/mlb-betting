---
module: odds
tags: [parlayapi, credits, cloud-scheduler, discord-alerts, sgo]
problem_type: integration-issue
---

# ParlayAPI credit exhaustion reported as "SGO error"; two never-retired jobs were the real drain

## Symptom

User report: "getting SGO error that no events found." Discord `#ops-alerts`
posts `⚠️ SGO Error | Snapshot returned error: no events returned` on and off
throughout the day. `/snapshot-odds` intermittently returns
`{"status":"error","error":"no events returned","events":0}`.

## Root cause (all confirmed live, 2026-08-27)

1. **ParlayAPI's real account had hit its monthly credit cap** -- confirmed
   by calling `GET /sports/baseball_mlb/odds` directly with the production
   `parlay-api-key`:
   ```
   403 {"detail": {"error": "OUT_OF_USAGE_CREDITS",
        "message": "Monthly credit limit reached (20,000 for starter tier).
                     Credits reset at 2026-09-01T00:00:00Z.", ...}}
   ```
   This is `ODDS_PRIMARY`'s live primary provider, not SGO. SGO itself was
   healthy the whole time (`/v2/account/usage/` showed 856/2,500 monthly
   entities -- 34% used).

2. **The internal credit ledger was blind to real spend.**
   `mlb/runners/snapshot_odds.py`'s own pacing tracker
   (`OddsAccum/baseball_mlb/_credits/{month}.json`) showed only 8,640 credits
   spent -- comfortably under its own 19,500 self-imposed ceiling -- yet the
   real ParlayAPI account had already burned through all 20,000. Cause: two
   Cloud Scheduler jobs, `parlay-accum-mlb-game-lines` and
   `parlay-accum-mlb-props` (`0 */4 * * *`, created by
   `deploy/setup_parlay_schedules.sh`), were **still `ENABLED`** and calling
   `nba/odds/accumulator.py` -> the same `ParlayApiClient` / same API key --
   6x/day each, 12 calls/day combined -- entirely outside
   `snapshot_odds.py`'s ledger. `CONTEXT.md` (s3, GCS layout) asserted these
   jobs "are retired" and cited that exact setup script as if it were
   evidence of the teardown -- it isn't; it's the *creation* script, and no
   corresponding retirement was ever actually run. The doc claim was
   aspirational, never verified against the live scheduler.

3. **The Discord alert mislabels every snapshot failure as "SGO"**, regardless
   of which provider actually failed. `main.py`'s `/snapshot-odds` handler
   hardcoded `post_error("SGO", ...)` -- a leftover from before the
   2026-06-29 ParlayAPI migration (`ODDS_PRIMARY=parlay` live since
   2026-08-10). Since `parlay` is primary, every failure today was actually
   ParlayAPI, but the alert always said "SGO" -- which is exactly why the
   user's report used that name.

4. **The 403 handler couldn't tell the two 403 causes apart.**
   `nba/odds/parlayapi.py`'s `_get()` logged *any* 401/403 as
   `"parlayapi auth error %s -- check PARLAY_API_KEY"` without reading the
   response body. ParlayAPI returns 403 for a genuinely bad/revoked key AND
   for `OUT_OF_USAGE_CREDITS` -- two different problems with two different
   fixes -- so every log line pointed troubleshooting at the key, not the
   quota.

**Not fully explained:** why some snapshot windows succeeded (15:55, 20:25
UTC) between others that got 403 (12:02, 16:02, 18:55, 20:03, 21:25 UTC) on
the same day, rather than a clean before/after cutover at the moment the cap
was crossed. ParlayAPI's own account-side enforcement was not perfectly
monotonic near the threshold; not worth chasing further on our side since the
fix (stop the untracked drain, ride out the reset) doesn't depend on it.

## Fix (2026-08-27)

1. Paused both zombie jobs: `gcloud scheduler jobs pause
   parlay-accum-mlb-game-lines / parlay-accum-mlb-props --location=us-central1`.
2. `snapshot_odds.run()` now returns a `"provider"` key on every status
   (success and error paths alike), not just success.
3. `main.py`'s `/snapshot-odds` handler labels the Discord alert with the
   actual provider (`result["provider"]`, uppercased), falling back to the
   requested/env provider only if the call crashed before returning a result
   at all. A `parlay` failure now posts as a `PARLAY` alert.
4. `parlayapi.py`'s `_get()` now inspects the 401/403 response body; when
   `detail.error == "OUT_OF_USAGE_CREDITS"` it logs the real message instead
   of the generic key-check line.
5. **No `ODDS_PRIMARY` or cadence change.** SGO has ample headroom (856/2,500)
   for the ~4 remaining days of August, and `_gather_parlay()` already falls
   back to a full SGO pull automatically on the 4 daily `include_sgo=True`
   windows when the ParlayAPI merge comes back empty (see
   `docs/solutions/integration-issues/odds-primary-cadence-mismatch.md` for
   why flipping `ODDS_PRIMARY` globally without cutting cadence is the wrong
   move). Net effect until the 2026-09-01 credit reset: live odds update
   ~4x/day (via the automatic SGO fallback) instead of 8x/day; the other 4
   `include_sgo=False` windows no-op and carry the prior snapshot forward,
   by design (see that same doc for why they must not sneak in extra SGO
   calls).

## Prevention / detection

- **Don't trust a doc's "this was retired/disabled" claim without checking
  the live scheduler.** `gcloud scheduler jobs list --location=us-central1`
  takes seconds and would have caught this immediately. Same lesson as the
  Kalshi `run.invoker` IAM gap recurring three times
  (`project_ops_incident_2026-08-26` in memory) -- infra drift between what
  a doc says and what GCP actually has configured is a recurring failure
  class in this repo, not a one-off.
- **A hardcoded alert label that predates a provider migration will keep
  lying forever.** Any `post_error(...)` (or similar) call should derive its
  system label from the same variable the code actually branched on, not a
  string literal frozen at the time the alert was written.
- **A shared external API key with its own pacing ledger is only as good as
  every caller writing to that ledger.** If a second job/script/notebook
  hits the same account, either route it through the same credit-tracked
  path or retire it outright -- an untracked second consumer makes the
  pacing math meaningless without the ledger ever looking wrong on its own.
- **A 403/401 is not self-explanatory** on any API that overloads that status
  for both auth and billing/quota failures -- always parse the body before
  logging a diagnosis, or the log will actively misdirect the next person
  (as it did here).
