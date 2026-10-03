# Handoff -- 2026-10-02 -- Audit of the 2026-09-18/19 pushes (EV DB wiring/Kelly, CI, t-stat, Kalshi mirror) + 09-04 refactor

Scope: commits aa7db45..e84d887 (primary) and the 2026-09-04 refactor merge
820d208 (secondary, delegated to two read-only auditors). Baseline 715 tests passed;
tests proved little -- the bugs below all lived behind green tests.

## Verdicts
- **CI fix (98e7aa4):** verified -- GitHub Actions green on the last 4 commits.
- **09-04 refactor (retrain/calibrate dedup, O/U scoring dedup, dead-code removal,
  team-id/season consolidation, bakeoff dedup):** NO regressions found. Retrain/
  calibrate scripts A/B'd against pre-refactor code on synthetic data: byte-identical
  boosters, metas, calibrator pickles, GCS write order. ou_bets.py differential-tested
  over 20k random cases: zero mismatches. All 162 modules import; every first-party
  import name and every deploy `-m` path resolves.
- **EV tracking + Kalshi mirror:** 5 real bugs (below), all fixed in the working tree.

## Bugs found and fixed (uncommitted)
1. **HR "Under 0.5" graded as "1+ HR wins"** (`fast_alert_loop._ev_bet_type`). 129 real
   posted alerts: graded as the settler does = -$10,122; correct = +$1,040 (flat $100).
   This was the true cause of the 09-18 handoff's "model_prob ~0.99 HR rows" / negative-
   bankroll Kelly blowup, so "Kelly underperforms flat" and +0.26% / -0.88% are unreliable.
2. **nrfi_ou YRFI/NRFI inverted** (same function): assumed OVER/UNDER, real labels are
   YES/NO, and e84d887 normalizes to YRFI/NRFI -> every YRFI alert logged as NRFI.
   LATENT: zero nrfi_ou kalshi alerts exist yet; would start the moment e84d887 deploys.
3. **Backfill lost player names for ~53% of rows** (3,261/6,166): took `player_name` from
   `log.parquet`, which `odds_alert.py` also writes (unnamed, keep="last"). Unnamed ->
   `"AWAY @ HOME"` -> void + dedup collisions. Explains most of the "37-52% void rate"
   the 09-18 handoff attributed to DK non-starter rules. Live path now re-resolves too.
4. **Kalshi mirror nrfi_ou ground truth** (`_realized_outcomes`): NRFI model_features is one
   row per starter; last-row-wins mislabeled 18% of flagged quotes. Re-run (n=646):
   nrfi_ou -7.7% -> +16.7% (t=1.43, n=96); overall -3.7% -> -0.1%. Still not significant.
5. `_kalshi_book_fair` counted NaN-fair_prob rows in n_books (now `count`); kalshi_vs_books
   `--help` crashed (bare `%` in argparse help); `main.py` had 2 unguarded `se > 0` t-stat
   sites the 09-19 "fix" missed (now `> 1e-9`).
Also hardened: NRFI v18 `_leakage_check` wrapped in try/except (warning-only by design;
an exception would otherwise abort the weekly production retrain).

Tests: 734 pass (+19). The 13 new regression tests were verified to FAIL on the old code.
Doc: docs/solutions/logic-errors/ev-bet-type-ignores-side-and-line-misgrades.md; CONTEXT.md s5.

## NOT done -- needs the owner's decision
- **Nothing is committed, pushed or deployed.** The deployed image is from 2026-09-04: none
  of the 09-18/19 code (kelly_pct in live EV rows, mirror scan, nrfi join fix) is live; only
  the Cloud Run job wiring was patched in place. Deploying e84d887 WITHOUT fix #2 would start
  logging inverted NRFI/YRFI rows.
- **Production `bets` data remediation (writes to prod Postgres, not attempted):** system='EV'
  rows from the backfill and live HR UNDER rows are wrong. Suggested: delete the backfilled
  rows (and any live hr_yn UNDER rows) and re-run the fixed `scripts/backfill_ev_history.py`,
  then recompute `mlb.analysis.ev_kelly_bankroll`. Then re-evaluate the EV conclusions --
  and the 0.05 cap in ev_kelly_bankroll.py was masking bug #1, not an outlier_scan bug.
- **Persistence of live EV rows is unverified:** the jobs log "N/N logged" (same line that
  lied for a month); confirm with `SELECT count(*) FROM bets WHERE system='EV' AND
  created_at > '2026-09-18'` (needs Cloud SQL proxy).

## Lower-priority risks noted by the auditors (not changed)
- tune_hyperparams.py HR target fix makes the HR tuner run, but its feature filter doesn't
  exclude same-game aggregates (hr_per_fb_num, barrel_game, ...) retrain_hr_v6 denylists ->
  would report leaked AUC. Nothing consumes hr_tuned_params.json yet.
- K/OUTS live-odds gate is now active and K/OUTS extractors price at the canonical line only
  (old: best price across lines) -> CLV before/after 2026-09-04 isn't like-for-like.
- NRFI leakage check baseline isn't like-for-like (spurious warnings possible).
- Calibrator "OOS" slices are in-sample for the full-data booster (pre-existing; OOS
  calibration error is optimistic).
- `_retrain_common.py` / `_calibrate_common.py` have no direct tests.
- Stale references to deleted nrfi v17 scripts in CONTEXT.md and a live v17 fallback in run_nrfi.py.
- odds_alert.py and fast_alert_loop.py both write Alerts/{day}/log.parquet with keep="last"
  dedup -- a design hazard (root cause of #3).


## OUTCOME (2026-10-03) -- deployed, remediated, merged
- Merged to main (26f4190, 4745f63). Service deployed (rev mlb-betting-00303-h4m); mlb-fast-alert /
  mlb-kalshi-alert re-pointed at the new image (wiring intact; healthy executions since).
- **Persistence: CONFIRMED.** Live EV rows do land in Postgres (7,274 rows before cleanup, new rows every
  day through 10-03). The old "N/N logged" line was telling the truth post-09-18.
- **Remediation** via one-off Cloud Run Job `mlb-ev-remediate` (`mlb/analysis/ev_remediate.py`; modes
  --stats / --settle / --apply; provisioned by deploy/setup_ev_remediate_job.sh). Backup table
  `bets_ev_backup_20261003` holds the 7,274 ORIGINAL rows (undo = INSERT back). Then deleted system='EV',
  rebuilt 7,583 rows from the GCS alert trail with the fixed code, graded with the real settlers.
  - unnamed ("AWAY @ HOME") rows: 2,318 -> 0.  void rate: 37% -> 8% (605/7,583; 11 are null-game_pk voids).
  - All rows now carry kelly_pct; HR rows 512 -> 352 (HR Under / alt-line alerts no longer logged).
  - A SECOND bug surfaced and was fixed: _settle_ev did int(NaN) on null-game_pk rows (crashed the
    first grading run; would have crashed nightly settle on any such pending row) -> now void.
- **NEW real numbers (replace the 09-18 ones):** 7,546 graded (3,487 W / 3,454 L / 605 void, 37 pending),
  hit 50.2%. Flat $100: staked $754,600 (void stake included in denominator), P&L +$57,028, ROI +7.56%.
  Kelly @ $1,000 bankroll, 5%/bet cap: +$13,304, ROI +11.91%, ending $14,304.
  CAVEATS before believing this: (a) paper P&L at the flagged price at flag time -- no slippage, limits,
  or line-already-moved; quote-survival (mlb.analysis.quote_survival) is the right reality check;
  (b) the same prop flagged at 2-3 books counts as 2-3 correlated bets, inflating n and any t-stat;
  (c) some flagged books (novig, prophetx, fliff, partycasino, ...) may not be bettable for the user;
  (d) not yet broken down per market / de-duplicated per prop. Treat as "promising, unverified".
- Remaining: delete the one-off job when done (gcloud run jobs delete mlb-ev-remediate); the backup table
  can be dropped once satisfied; per-market + de-duplicated breakdown not yet done.
