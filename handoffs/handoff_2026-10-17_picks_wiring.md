# Handoff -- 2026-10-17 -- picks-page wiring (odds, day strip, team search, NFL headshots, systems stats)

Branch `fix/picks-wiring` in worktree `/Users/lmaynor/mlb-betting-picks-wiring`, off `origin/main` 218c707. NOT committed, NOT pushed, nothing
deployed. The main checkout was left alone (it is mid-merge on `feat/discord-bot-postmessage`, 6 conflicted files, unrelated to this work).

## Why odds and market prices were empty (diagnosed on the live API, 2026-10-16 evening CT)
1. **NHL / NBA**: lines are banked twice a day (17:30 and 22:30 UTC). The projection engine refused any snapshot older than 240 min
   ("snapshot is 271 min old (> 240); refusing stale quotes"), so from ~9:30 PM to ~12:30 PM CT every row was a no-line rotation row: no
   price, no consensus, no edge, no model + market. FIXED: lines up to 20 h old are carried and flagged `lines_stale`.
2. **NFL** (FIXED later the same day, see the end of this file): there was no scheduler. `ev-nfl-props-sweep` is a manual Cloud Run Job with a hard-coded `--until`; last run 2026-10-15 01:32 UTC,
   `reason: no NFL raw prop payloads banked for today/tomorrow`. NOT FIXED (it spends the shared ParlayAPI pool; 14,620 credits left on the
   2026-10-15 run). Until it runs on a cadence, NFL shows projections only. This is the one real blocker for NFL prices.
3. **Market prices table** is books beating the consensus (`book_outliers`). NHL/NBA sweeps ran at 22:35 and found 0 rows (a legitimate
   zero). The table still reads empty when nothing beats the market; the note under it says how old the last sweep is.
4. **MLB** is the postseason: today's predictions are 18 HR + 10 EV rows, no count markets, `proj_mean` null on all, so the MLB projection board
   is empty by construction (not a wiring bug).
5. Prod serves `empirical_ewm_v1`; main has `v2` (PR #69). Anything built on v2 fields needs the backend deployed first.

## What changed
- `ev/daily_projections.py`: `CARRY_MAX_AGE_MIN` + payload `lines_stale`; `HORIZON_HOURS` (NFL 168 h, NHL/NBA 48 h); `schedule_games` fetches
  every day of the horizon. 3 new tests (58 pass in that file).
- Day strip for NFL / NHL / NBA (`components/picks/day-scroller.tsx`, `lib/board-days.ts`) on picks, projections and system pages.
- Team-aware search (`lib/team-directory.ts`) behind every text filter; placeholder now "Player, team or city".
- NFL headshots: `scripts/build_sport_assets.py --nfl-players` -> `public/headshots/nfl_player_map.json` (923 players, 100% of the 28 rows on the
  live board resolved); `headshotUrlById('nfl', token)` validates the token; NFL price rows resolve by name on the server.
- Systems: footer columns TODAY and LIVE MAE; methodology card block LIVE BOARD ACCURACY; `lib/engine-accuracy.json` from
  `nfl.props.engine_eval --no-grid` (NFL) and the new `scripts/engine_accuracy.py` (NHL, NBA).

## Numbers (accuracy only, not edge)
Engine MAE vs trailing-5 mean, paired: NFL rec yds 25.9 vs 27.1, pass yds 66.1 vs 68.8, receptions 1.82 vs 1.89; NHL SOG 1.105 vs 1.167, saves 6.06 vs 6.39;
NBA points 5.26 vs 5.52, PRA 6.67 vs 6.94. The engine beats the baseline on every stat measured. NHL/NBA replay does NOT apply the opponent factor
(it measured ~0 for NFL) and covers seasons 2024 and 2025 of the master; NFL is the full serving replay, 2021-2025, run against main's v2 params.

## Verified
pytest full suite 2187 passed; jest 23 suites / 338 passed; `tsc --noEmit` clean; eslint clean on the changed files (one pre-existing
`set-state-in-effect` error in filter-bar.tsx is untouched). In the browser against a LOCAL STUB serving captured real payloads: day strip,
stale banner, NFL headshots (real photos load), search (chiefs 0 rows, texans / houston / jags 28 rows), footer columns, mobile 375 px no overflow.
The stale-lines and multi-day views used FIXTURE edits to the captured payloads (clearly marked in the stub); NOT verified against the deployed API.

## Needs a human
- Decide the NFL banking cadence (Wed-Mon windows) and credit budget; then provision a scheduler for `ev.sweep_nfl_props`.
- Merge, deploy backend (`deploy/deploy_service.sh`) BEFORE Vercel; the site tolerates a missing `lines_stale`.
- NHL goals / assists / blocked shots have no rotation rows (only SOG, points, saves do), so they only appear when lines are banked.
- Re-run `--nfl-players` after roster moves; re-run the accuracy replays each offseason.

## Addendum -- NFL sweep scheduled (2026-10-17, user approved; credits to be raised to 100k when the 20k pool runs out)
`deploy/setup_nfl_sweep_schedule.sh` (run once, idempotent): job `ev-nfl-props-sweep` args -> `-m ev.sweep_nfl_props --max-credits 150`, retries 0,
timeout 900; scheduler-invoker granted run.invoker on the job; 3 schedulers in America/Chicago: `-weekly` `0 */2 * * 1-6`, `-sun-am` `*/30 6-11 * * 0`,
`-sun-rest` `0 0,2,4,12-20,22 * * 0`. Probe firing 2026-10-17 04:57 UTC succeeded: 11 events, 56 credits, 245 rows, Kalshi reachable, 13,783 credits left.
- Weekdays: cheap by construction (40 h horizon). Tue-Wed nothing is inside it (1 credit/firing); the cost sits Fri night-Mon (~56-76 per firing).
  Estimate ~2.3k/week, ~10k/month. The 20k pool had 13.8k left, shared with MLB and NHL/NBA, hence the planned 100k.
- The ev image still posts "NFL props SHADOW sweep running" on every firing until it is rebuilt with the `ev/sweep_nfl_props.py` change in this PR
  (startup ping only for looping runs). Rebuild via `deploy/build_all_images.sh` (it retags prod :latest, read it first).
- Sink A / flagged alerts still dedupe through GCS `notified.json`, so frequent firings do not re-alert.
