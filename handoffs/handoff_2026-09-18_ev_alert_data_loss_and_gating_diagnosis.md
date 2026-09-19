# Handoff -- 2026-09-18 -- EV alert data-loss bug fixed+recovered; gating pattern diagnosed (not a bug)

Picked up from a user report: "a lot of no qualifying bets at 11am/5pm,
P&L barely moving for weeks, low betting volume" plus a request to add real
Kelly sizing to the EV alerts and backfill history off an assumed $1000
bankroll. Investigated both against live production (Cloud Logging, the
live `Gates/model_gates.json`, and a direct read-write query against the
real Postgres DB via a user-approved local Cloud SQL Auth Proxy tunnel).
Two independent, fully-confirmed findings came out of it, one much bigger
than the original ask -- plus four more real bugs found and fixed while
executing the recovery itself (see "Bugs found mid-recovery" below; this
session validated its own work hard before trusting any number in it).

## TL;DR

1. **The "no qualifying bets" pattern is real but is NOT a bug.** All 12
   systems are currently gated off: 4 chronically (`force_gate="on"` since
   2026-06-24/08-17, confirmed still-justified -- E05/E08 are still open),
   5 more tripped the dynamic ROI gate in just the last 3-29 days
   (HR/K/OUTS/BATTER_HITS/BATTER_TB), 2 are hardcoded log-only by design
   (SB/PITCHER_ER), and GAME has never once triggered in its lifetime (n=0).
   Real weekly bet-volume data confirms this is a genuine collapse (418
   bets/wk on 07-27 down to 13 on 09-14), not noise -- and it lines up
   almost to the day with each system's own suppress_streak. **Not fixed
   here on purpose** -- these gates are protecting real (if paper) P&L from
   models with no proven live edge; re-enabling them would need a retrain
   (E05/E08) or a deliberate gate-policy change, not a config flip. No
   stuck/ungraded settlement backlog was found anywhere (checked the exact
   query `monitor_ops._check_stuck_bets()` uses).

2. **EV alerts have never been durably saved, at all, since the feature
   launched (2026-08-20) -- FOUND, FIXED, AND HISTORICAL DATA RECOVERED,
   same session.** `mlb-fast-alert` and `mlb-kalshi-alert` (the two Cloud
   Run Jobs behind the intraday +EV pagers) had no `MLB_DB_URL` / Cloud SQL
   wiring -- baked into the checked-in provisioning scripts themselves, not
   just a hand-edit drift. `BetTracker`'s DB_URL-empty fallback silently
   wrote every "successfully logged" EV bet to an ephemeral sqlite file
   inside each job's container, gone the instant it exited. Confirmed via a
   direct query: **zero** `system='EV'` rows existed in production despite
   a month of completely convincing "N/N posted alerts logged to bets
   table" log lines. Fixed live (job wiring), recovered **6,090 real
   historical alerts** (of which 6,001+ are now graded) from the
   independently-durable `Alerts/{day}/*.parquet` GCS trail, and `kelly_pct`
   added to the live code path + backfilled historically.

## Finding 1 detail: why every run shows "no qualifying bets"

`mlb_core/notify/discord.py:post_bets()` posts "No qualifying bets today"
any time a system hands it zero rows -- every scoring run (11am/2pm/5pm/
6:35pm CT) calls this once per system.

| System | Why it's off right now |
|---|---|
| 1IOU (NRFI), F5, F1H, 1I | `force_gate="on"` since 2026-06-24 / 2026-08-17 -- AUC~0.50, no live edge. E05 (NRFI drift) / E08 (sub-model ensemble) still open in the backlog. |
| HR | Dynamic ROI gate, roi -29.3%, suppress_streak=23 (last real bet 2026-08-26) |
| K | roi -36.6%, streak=3 -- just tripped (last real bet 2026-09-15) |
| OUTS | roi -59.9%, streak=13 (last real bet 2026-09-05) |
| BATTER_HITS | roi -20.6%, streak=15 (last real bet 2026-09-03) |
| BATTER_TB | roi -33.9%, streak=29 (last real bet 2026-08-20) |
| SB, PITCHER_ER | Hardcoded `LOG_ONLY`/`PITCHER_ER_LOG_ONLY` by design, pending validation |
| GAME | Never triggered once in its lifetime (n=0, clear_streak=100) -- likely a genuinely underfit model (best_iteration=15), not pursued further this session |

Real weekly Kelly-triggered bet volume (any system), queried directly:

| Week of | Bets | P&L |
|---|---|---|
| 07-27 | 418 | -$98.53 |
| 08-17 | 341 | +$149.84 |
| 08-24 | 170 | +$18.22 |
| 08-31 | 145 | -$70.00 |
| 09-07 | 45 | -$9.57 |
| 09-14 (partial) | 13 | -$17.62 |

Recommendation left for the user: prioritize E05 (NRFI drift fix) if
reviving the 4 force-gated systems matters, and/or reconsider the dynamic
gate's hysteresis (2 consecutive clean days to recover is a high bar for a
system whose true edge is close to zero -- it trips off easily and
struggles to reset). Not changed this session -- a strategy call, not a
cleanup task.

## Finding 2 detail: the EV data-loss bug

Full root cause, symptoms, and prevention notes are in
`docs/solutions/runtime-errors/ev-alert-jobs-missing-db-wiring.md` and
CONTEXT.md s5 ("EV bet tracking") + s15.9. Short version: both jobs'
provisioning scripts (`deploy/setup_fast_alert.sh`,
`deploy/setup_kalshi_alert_job.sh`) never gained `MLB_DB_URL` /
`--set-cloudsql-instances` when the EV bet tracking feature was added on
top of them 2026-08-20 -- their own header comments still said "GCS only
(no DB)". Same failure class as two prior incidents in this repo (a
hand-provisioned Cloud Run Job missing standard wiring).

## What was done

- `deploy/setup_fast_alert.sh` / `deploy/setup_kalshi_alert_job.sh`: added
  `MLB_DB_URL=mlb-db-url:latest` + `--set-cloudsql-instances`, corrected the
  stale comments. **Applied live** -- `gcloud run jobs update` on both,
  confirmed via `gcloud run jobs describe` that both now carry the
  `run.googleapis.com/cloudsql-instances` annotation and `MLB_DB_URL`.
  (Also had to drop `DISCORD_WEBHOOK_ALERTS=discord-webhook-alerts:latest`
  from both scripts' `--set-secrets` -- that secret was never actually
  created, and referencing a nonexistent secret fails the ENTIRE `gcloud run
  jobs update` call, which is what was silently blocking every previous
  update to these two jobs, unrelated to this session's fix. Code already
  falls back to `DISCORD_WEBHOOK_URL` when unset, so this is a no-op
  behavior change; add the line back once that secret exists for real.)
- `mlb/runners/fast_alert_loop.py` / `mlb/runners/kalshi_alert.py`:
  `_log_ev_bets()` now computes `kelly_pct` via the same
  `mlb_core.odds.utils.kelly_pct()` every model system uses (fraction 0.25,
  env `EV_KELLY_FRACTION`), off the `model_prob`/`odds` already stored per
  row. `stake` stays the flat `_EV_STAKE_UNIT` -- purely informational,
  per the user's explicit call to preserve ROI%-comparability across
  alerts.
- `scripts/backfill_ev_history.py` (new): recovers historical posted alerts
  from `Alerts/{day}/log.parquet`+`notified.parquet` (fast_alert) and
  `kalshi_log.parquet`+`kalshi_notified.parquet` (kalshi) -- inner-joined
  to the TRUE posted subset per day (not every candidate merely scanned),
  reconstructs each row identically to the live `_log_ev_bets()` (same
  bet_type/kelly_pct computation), inserts via the real `BetTracker.log_bet()`
  (hits the production dedup index, safe to re-run), then grades recovered
  rows via `settle_bets._settle_ev` directly (NOT `settle_bets.run()`,
  which would have spammed `#daily-recap` with a misleading "yesterday"
  recap covering two months of history).
- `mlb/analysis/ev_kelly_bankroll.py` (new): read-only report -- rescales
  each settled EV bet's real graded profit/stake ratio to a
  `kelly_pct * bankroll` size (fixed reference bankroll, non-compounding,
  capped at `--max-pct` [default 0.05] of bankroll per bet -- see "Bugs
  found" below for why the cap is load-bearing), reports total P&L/ROI/
  ending bankroll alongside the existing flat-$100 ROI. Does not touch
  `stake`/`profit`.
- CONTEXT.md: new s15.9 gotcha, s5 EV bet tracking section updated (kelly_pct
  + the data-loss note, including a note reconciling why the 2026-08-20
  "~1500 decided bets, +9.2% ROI" retrospective figure was already hedged
  as "not yet a standing figure" -- it almost certainly wasn't backed by
  durable `bets` rows either).
- New solution doc:
  `docs/solutions/runtime-errors/ev-alert-jobs-missing-db-wiring.md`.
- 22 new tests across `tests/test_fast_alert_loop_ev.py`,
  `tests/test_kalshi_alert_ev.py`, `tests/test_backfill_ev_history.py` (new),
  `tests/test_ev_kelly_bankroll.py` (new). 688/688 passing.

## Bugs found mid-recovery (all fixed same session, before trusting any number)

Running the actual recovery surfaced four more real, independent bugs --
each one caught by refusing to trust an output that looked suspicious,
verifying against real data instead:

1. **My own first backfill attempt forgot to set `MLB_DB_URL`** and silently
   wrote ~6,090 rows to a throwaway local sqlite file
   (`EV_Alerts/data/ev_bets.db`) instead of production -- the *exact* bug
   this session was fixing, self-inflicted. Caught immediately by checking
   production row counts (still 0) before believing the "success" exit.
   Cleaned up the stray file; no production impact.
2. **Kalshi-as-a-bettable-book contamination (2026-08-10..17), already
   known/fixed elsewhere in this repo (finding C4.1,
   `backtest_market.OFFSHORE`), but not filtered by this recovery until
   caught.** A sample row showed `book="kalshi"` at +9900 American odds on a
   total-bases prop -- not a real, placeable price. Quantified before
   trusting the fix: 2026-08-12 was 380/559 (68%) such rows, 2026-08-16 was
   661/809 (82%); zero from 2026-08-17 onward (the documented fix date).
   Filtered via the same `backtest_market.OFFSHORE` denylist the live pager
   itself already uses post-fix. Net: 2,013 contaminated rows excluded from
   recovery (2,012 fast_alert + 1 kalshi).
3. **8 of 6,090 recovered rows had `game_pk IS NULL`** (a pre-existing, rare
   upstream gap in `outlier_scan.py`'s own game-matching, unrelated to this
   session). 5 were unambiguously resolvable via
   `mlb_core.data.id_resolver.resolve_game_pk()` once the row's `game_date`
   fallback (GCS-folder day, used when the source's own `game_date` was
   blank) was corrected to the real date (found by searching adjacent
   dates for the matchup). The other 3 had TWO candidate real games on
   different nearby dates for the same matchup -- genuinely ambiguous, no
   safe way to pick one -- voided per the user's explicit call ("just void
   them") rather than deleted, consistent with this repo's existing
   void-not-delete convention for unsettleable bets.
4. **`settle_bets._void_stale_nonfinal_bets()`'s age-based catch-all
   (`UNSETTLEABLE_VOID_DAYS=7`: "still pending after 7 days -> void, no
   settler ever graded it") is correct for the real DAILY `/settle` loop,
   where age reflects failed retry attempts -- but wrongly voided 5,295 of
   6,090 rows (87%!) on this backfill's first settlement pass**, because
   every row's "age" here just reflects how long ago the real game was
   played, not how many (zero) real retry attempts had failed. It ran
   BEFORE the real per-market settlers and removed nearly everything before
   `_settle_hr`/`_settle_k`/etc. ever got a chance. Caught because a 92%
   void rate was obviously implausible for real DK grading rules; confirmed
   via `game_cache` stats showing 848/858 games were genuinely Final (i.e.
   gradable) at the time of the wrongly-voided pass. Fixed by removing that
   call from `settle_recovered()` entirely (a one-time backfill has no
   "retry count" to speak of); reverted exactly the 5,295 wrongly-voided
   IDs (extracted from that run's own log) back to pending; re-ran
   settlement through the real settlers only.
5. **`ev_kelly_bankroll.py`'s first real run against the recovered history
   produced a NEGATIVE ending bankroll from a $1,000 start** (obviously
   wrong for a fractional-Kelly report). Root cause: a cluster of `HR_yn`
   rows carry an implausible `model_prob` near 0.99 (no single batter has a
   ~99% per-game HR chance) at extreme-favorite odds (-2000 to -2800) --
   very likely a pre-existing data issue in `outlier_scan.py`'s own
   `hr_yn` `consensus_fair` computation (NOT root-caused further this
   session; flagged below), producing uncapped `kelly_pct` up to 0.2185
   (21.85% of bankroll on one prop), almost all of which lost. `kelly_pct`
   itself is stored uncapped by design (`kelly_pct()`'s own docstring: "for
   signal gating"), matching every model system's convention -- but every
   model system's real STAKE also always goes through `kelly_stake()`'s own
   `max_pct` cap (typically 0.05), which this report's first version
   skipped. Fixed: added the same cap to `ev_kelly_bankroll.py` (default
   0.05, `--max-pct` to override).

## Final real numbers (after all fixes above)

- **6,090** historical EV alerts recovered and inserted (5,456 fast_alert +
  1,220 kalshi... net of a handful of legitimate cross-pager dedup
  collisions -- see `tests/test_kalshi_alert_ev.py`'s
  `test_two_pagers_same_real_bet_dedupe_to_one_row` for why that's correct,
  not a bug).
- Settled: 3,552 decided (1,720 win / 1,832 loss), 2,486 void (mostly real
  DK-rule voids -- "player not in boxscore" -- since unlike the model
  systems, this scanner doesn't gate on confirmed starting lineups; see
  void-rate-by-market breakdown investigated live, 37-52% across
  BATTER/HR/K/OUTS, all attributable to that), 52 still pending (2026-09-18
  games not yet Final -- will clear on the next nightly `/settle`).
- **Flat $100 stake (matches this repo's existing per-system ROI
  convention, i.e. includes void bets' stake in the denominator like
  `settle_bets.py`'s own `system_stats` does): $603,800 staked, +$1,593.84
  P&L, +0.26% ROI.**
- **Kelly-sized @ $1,000 bankroll, 5%/bet cap: $96,093.50 staked
  (rescaled), -$842.77 P&L, -0.88% ROI, ending bankroll $157.23** (down from
  $1,000). Kelly sizing UNDERPERFORMS flat staking here -- a real,
  substantive finding, not a bug: it means the EV alerts' own claimed edge
  SIZE isn't a reliable signal even though the raw pick quality is close to
  breakeven, echoing this repo's own documented adverse-selection pattern
  for other systems (`/edge-analysis`: "the biggest apparent edges are
  mostly model error"). Worth a real look at `outlier_scan.py`'s `hr_yn`
  probability estimation specifically (see below) before trusting Kelly
  sizing on this feed for anything real.

## Verification

- 688/688 local tests passing (`.venv_audit`).
- Real `gcloud run jobs execute` on both alert jobs post-fix; job configs
  confirmed via `gcloud run jobs describe` to carry `MLB_DB_URL` +
  `run.googleapis.com/cloudsql-instances`. (Neither manual trigger happened
  to find a live new alert to post that moment -- a legitimate "quiet
  window" outcome, not re-verified with a real end-to-end live post this
  session; the static config check plus the identical wiring pattern
  `mlb-fit-calibrators` already uses successfully is the verification here.)
- Backfill dry-run reviewed before any write (77 days of GCS history, per-day
  counts, sample rows) -- caught the Kalshi contamination this way before
  it ever touched production.
- Real backfill run: insert + settle against production Postgres via the
  Cloud SQL tunnel, all counts above independently re-queried directly
  against the DB (not just trusted from script output).

## Open items for a future session

- **`outlier_scan.py`'s `hr_yn` `consensus_fair` looks miscalibrated for at
  least a subset of rows** (model_prob ~0.99 at odds implying ~96%, for an
  individual batter HR prop -- real single-game HR probabilities essentially
  never exceed ~25-30%). Not root-caused this session (out of scope --
  found via the Kelly-bankroll blowup, worked around via a stake cap rather
  than fixed at the source). Worth a real look: is this a units/inversion
  bug, a market-mixup (team-level vs batter-level HR), or something else in
  how `outlier_scan.py` computes the cross-book median for `hr_yn`
  specifically.
- The 4 force-gated systems (1IOU/NRFI, F5, F1H, 1I) and the dynamic-gate
  hysteresis design are unchanged -- a strategy decision for the user, not
  addressed here.
- GAME's lifetime zero-volume (n=0 since inception) wasn't dug into further
  -- flagged as likely a genuinely weak/uncalibrated model (best_iteration=15
  per CONTEXT.md s15.5), not investigated as a bug this session.
- `capture_closing_lines.py` still does not capture CLV for EV rows
  (pre-existing scope gap, unrelated to this session's fix).
- `DISCORD_WEBHOOK_ALERTS`/`discord-webhook-alerts` secret still doesn't
  exist -- both alert jobs' provisioning scripts now deliberately omit it
  (see "What was done" above); create the secret and add the line back to
  both scripts if/when a dedicated alerts channel is actually wired up.
- Neither alert job's live wiring fix was confirmed via an actual real
  posted alert reaching the DB end-to-end (both manual triggers this
  session found nothing new to post) -- worth a glance at
  `SELECT COUNT(*) FROM bets WHERE system='EV' AND created_at > '2026-09-18'`
  after the jobs' next few real scheduled runs.
