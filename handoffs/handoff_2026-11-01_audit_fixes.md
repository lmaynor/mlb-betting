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

## Decisions / open items for the owner
1. **Budgets:** NBA 32k / NHL 24k were cut from 45k / 40k without measuring real burn (total committed 91.5k of 100k). Re-tune after a month.
2. **BestBook ToS:** schedulers are created paused and the runner refuses without `BESTBOOK_ENABLED=1`. Decide before enabling.
3. **Re-run provisioning** to take effect: `deploy/setup_props_alerts.sh`, `deploy/setup_nfl_sweep_schedule.sh`, `deploy/setup_bestbook_live.sh` (paused), `deploy/setup_odds_banking.sh`. None were executed here.
4. **NFL sweep** is still outside the shared ledger (`ev/sweep_nfl_props.py` default `--max-credits 9000` per run; first cycle's spend is not counted). Only `setup_nfl_sweep_schedule.sh`'s 150/firing caps it.
5. **#daily-recap** still renders MLB systems only; winter results settle but do not appear in the embed (`mlb_core/notify/discord.py`, `sport='mlb'` stats).
6. A scheduler for `nba.odds.accumulator --kind game_lines` (basketball_nba, icehockey_nhl) must exist or the game engines refuse a >6 h old pointer and the payload goes stale.
7. `SEASON_CARRYOVER = 0.5` (Kalman profile carryover) is a guess; NBA rebounds/turnovers are NaN for 2019-24 so the replay effectively uses 2025 only.
8. Not done: server-side stripping of the hidden outlier book name (outliers are computed client-side; the lock is not a security boundary); virtualisation / further split of `odds-client.tsx` (~3400 lines); NHL history loader NaN guard (`float(x or default)`); Python 'TD PROXY' label for 2-leg rows; real-launcher check of maskable icons; `gcloud` description-field format for the pause stamp unverified against real gcloud.
9. `weather_multiplier` temperature step at 32F is still a step (wind is now continuous); coefficients unvalidated.

## Verified
Full pytest, `tsc --noEmit`, full jest (one 9,000-row timing test in `odds-screen.test.ts` can fail under CPU load; passes alone 3/3). Nothing run against live GCS, gcloud, ParlayAPI or a browser.
