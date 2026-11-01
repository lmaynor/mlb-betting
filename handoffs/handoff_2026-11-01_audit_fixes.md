# Handoff -- 2026-11-01 -- audit of the last 20 commits + fixes

Branch `claude/recent-commits-audit-4a4e44`. **Backend DEPLOYED 2026-11-01 (see the Deployed section at the end); Vercel frontend NOT deployed.** Audit covered 6835c26..2408d6a (see CONTEXT.md s22 for the contract-level summary).

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

## Deployed 2026-11-01 (backend, from commit 1f61be1, unmerged branch)
- One image (digest `sha256:def3f762...`) tagged onto `mlb-betting:latest`, `mlb-betting-feeds:latest`, `mlb-betting-ev:shadow`, `mlb-betting-bp-crawl:latest`; service revision `mlb-betting-00349-xev` at 100% (previous good: `-00332-tq7`).
- Re-run: `setup_bestbook_live.sh` (ungated, schedulers enabled; first `bb-live-*` runs were previously blocked by the missing run.invoker), `setup_nfl_sweep_schedule.sh`, `setup_props_alerts.sh`, `setup_odds_banking.sh`.
- INCIDENT (self-inflicted, ~1.5 min): the first build of this session dropped `mlb_core/data` (unanchored `data/` in `.gcloudignore`); the service took 100% on it for a few minutes and ~6 `/api/public/projections` requests returned 500 until rolled back; two `daily-projections` executions failed. Fixed + regression test + `docs/solutions/runtime-errors/gcloudignore-unanchored-data-dropped-packages.md`.
- Verified live: `daily-projections` writes `EVSweep/{nba,nhl}_games/odds_board_latest.json`; BestBook (16 books incl. novig/polymarket/thescore) is a third source for NBA; the NFL sweep records its credits in `OddsAccum/americanfootball_nfl_sweep/_credits/2026-11.json`.
- NOT verified: the authenticated `/api/public/games/odds` response from the service (the canary answered 401 without the site key; the board file itself was read from GCS), the Vercel UI, the recap embed (next `mlb-settle` run).

## Price-board findings from real banked data (items 1-2)
1. **The feed carries MAIN lines only**: one spread and one total per book, no alternate ladders. Rows split only where books' main lines differ by a half point (e.g. totals 229.5/230/230.5). Alternates would need `alternate_spreads`/`alternate_totals` markets (unverified that ParlayAPI serves them for game odds; ~1 credit per market per slate call).
2. **Stale cutoff**: the game_lines schedulers fire 12:35, 17:35, 21:35, 22:35, 01:00, 03:35 CT. The 12:35 -> 17:35 gap is 300 min, longer than `STALE_MINUTES = 270`, so the badge flips for ~30 min on afternoons; the 03:35 -> 12:35 overnight gap is expected. Cheap fix is cadence, not the cutoff: a game_lines snapshot is only 3 credits.
3. **Real bad-feed case found and fixed**: betmgm quoted Philadelphia -185 / Utah +150 where eleven books had Utah -180 / Philadelphia +150 (a fake ~28% arb). `_drop_flipped_books` drops a book whose favourite is the opposite of the consensus (>= 4 books, > 0.15 off the median); count in `summary.flipped_quotes_dropped`.
4. 4 of 8 NBA and 1 of 6 NHL banked events are one-book stubs with no `commence_time`; they are skipped (warning in the log).
