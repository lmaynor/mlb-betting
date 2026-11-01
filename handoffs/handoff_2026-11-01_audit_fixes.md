# Handoff -- 2026-11-01 -- audit of the last 20 commits + fixes

Branch `claude/recent-commits-audit-4a4e44`. Nothing deployed. Audit covered 6835c26..2408d6a (see CONTEXT.md s22 for the contract-level summary).

## Fixed (all verified by tests against REAL data shapes unless noted)
- NHL/NBA settlement voided every bet (actuals have no `name` key) -> matches league player id; unresolved players stay pending. NFL settlement uses `nfl.props.grade`.
- Winter alert bets: DB wiring (`MLB_DB_URL` + Cloud SQL) on `props-alerts-*` and `ev-nfl-props-sweep`, no -110 invention, ET game day.
- `settle_bets.run()` NameError (`original_pending_count`) -- found during the fix, not in the original audit.
- NBA/NHL game engines: loaders read real schemas/pointer files, Four-Factor signs, NHL matrix mean/OT/shootout/tau, Kalman drift + offseason decay, Kalshi code mapping, sigma semantics, UNVALIDATED labelling end to end (engine -> payload -> public_api -> UI), Official Card gate.
- `setup_bestbook_live.sh` IAM (run.invoker + token scope; IAM test now scans v1 URIs); offseason pause/resume no longer pauses `mlb-free-plays` / `mlb-monitor-ops`; resume only touches jobs the pause stamped.
- ParlayAPI 100k plan reconciled into one budget table (`nba/config.py`, `tests/test_parlay_budget.py`); ledger race fixed.
- Game-lines route tier gating; SGP honesty (assumed rho, MODEL ARTIFACT flag, fabricated seed data deleted); literal `\uXXXX` in JSX; PWA/safe-area/iPadOS; `MobileBottomSheet`; `PYTEST_CURRENT_TEST` guard removed.
- BestBook: bounded SSE client, per-game memory, deterministic main line, `BESTBOOK_ENABLED=1` gate + ToS note.

- **Game lines were unreachable (found after the first push) and moved:** NBA/NHL game lines are now the Scanner's Game Lines tab `/ev/games` (model estimate vs market, UNVALIDATED), not a mode of the Odds Terminal and not under Picks; `/picks/games` is MLB's tracked game picks only, with redirects for old links. Rationale and details in CONTEXT s22. Pre-existing since cb95579.

## Decisions / open items for the owner
1. **Budgets (owner-set 2026-11-01):** NBA 45k / NHL 40k / NFL sweep 10k = 95k live + 4.5k test budget = 99.5k, floor 500. MLB's 19.5k is dormant until Mar 20; re-balance NBA/NHL via `ODDS_BANK_BUDGET_<SPORTKEY>` before `resume_mlb_season.sh` (104.5k does not fit). Re-run `setup_odds_banking.sh` to push the new env.
2. **BestBook (owner decision 2026-11-01): LIVE, ungated, an additional prop-line source** (third feed in `ev.daily_projections.load_events`). No ToS review was
   done; kill switches `BESTBOOK_DISABLED=1` / `PAUSE=1 deploy/setup_bestbook_live.sh`. Found while checking prod: the `bb-live-*` jobs and schedulers already
   existed and were ENABLED (the "born paused" behaviour was never implemented in the script), so the feed was running all along.
3. **Re-run provisioning** to take effect: `deploy/setup_props_alerts.sh`, `deploy/setup_nfl_sweep_schedule.sh`, `deploy/setup_bestbook_live.sh` (paused), `deploy/setup_odds_banking.sh`. None were executed here.
4. **NFL sweep is now on the shared ledger** (owner decision 2026-11-01): `ev/sweep_nfl_props.py` calls `credit_ledger.allowance` (key `NFL_SWEEP_LEDGER_SPORT`, 10k cumulative cap, no linear pace) before the slate call and the event pulls and records real spend; the on-demand `refresh_single_event_consensus` is metered too. `--max-credits` still caps one firing. Takes effect when the `ev:shadow` image is rebuilt.
5. **#daily-recap now renders NFL / NBA / NHL** (a field each, "props (unvalidated)", counted in the combined P&L) next to the MLB systems: `settle_bets._winter_sport_stats`.
6. The `game_lines` accumulator schedulers exist in prod (`odds-bank-{nba,nhl}-game-lines-*`, 6/day each), verified 2026-11-01.
7. `SEASON_CARRYOVER = 0.5` (Kalman profile carryover) is a guess; NBA rebounds/turnovers are NaN for 2019-24 so the replay effectively uses 2025 only.
8. Not done: server-side stripping of the hidden outlier book name (outliers are computed client-side; the lock is not a security boundary); virtualisation / further split of `odds-client.tsx` (~3400 lines); NHL history loader NaN guard (`float(x or default)`); Python 'TD PROXY' label for 2-leg rows; real-launcher check of maskable icons; `gcloud` description-field format for the pause stamp unverified against real gcloud.
9. Game-line PRICE screen BUILT on `/ev/games?view=prices` (see CONTEXT s22); verified only against fixtures and unit tests: check it against a real banked `game_lines` snapshot after the backend deploys (do the NBA/NHL feeds carry alternate lines? is `stale` (270 min) sensible?). The `daily-projections` job image must be rebuilt for the board file to be written.
10. `weather_multiplier` temperature step at 32F is still a step (wind is now continuous); coefficients unvalidated.

## Verified
Full pytest, `tsc --noEmit`, full jest (one 9,000-row timing test in `odds-screen.test.ts` can fail under CPU load; passes alone 3/3). Nothing run against live GCS, gcloud, ParlayAPI or a browser.
