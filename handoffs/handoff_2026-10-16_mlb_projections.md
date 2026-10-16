# Handoff -- 2026-10-16 -- MLB projections (backend persist + API + board)

Branch `feat/mlb-projections`, stacked on `worktree-picks-redesign` (PR #67). NOT deployed. Python 1938 + 6 new tests pass; jest 257 pass; `tsc` clean.

## What changed
- **bets.proj_mean** (new nullable column): the model's expected value for the stat, in the market's unit. Written by run_k (K: mean Ks; OUTS: IP * 3 outs),
  run_batter_tb (`lambda_tb`), run_batter_hits (`lambda_hits`), run_sb (`lambda_sb`) and the PITCHER_ER Gamma proxy (`mu_er`).
  Migration: `_MIGRATE_PROJ_MEAN_SQL` in `BetTracker._init_db` AND `ensure_proj_mean_column()` from `_make_engine` and every public reader.
- **Public API**: `get_today_picks` / `get_picks` / `get_recent_settled` return `proj_mean` (K / OUTS rows logged earlier are back-filled from `proj_k`,
  which is not returned); `/picks/today` now also returns `book`.
- **Frontend**: `/picks/mlb/projections` board (`lib/mlb-projections.ts`, `components/picks/mlb-projections-board.tsx`), `MlbTabs` (Picks | Projections),
  `Bet.proj_mean`. The board is a gated surface (player board, `lib/access.ts`) and ends with the player systems footer.

## Deploy order (backend FIRST)
1. Deploy the Cloud Run service (`deploy/deploy_service.sh`): the API now SELECTs `proj_mean`; `ensure_proj_mean_column` adds it on first read.
2. Rebuild job images / re-provision so the runners write it (RUNBOOKS s7a / s8: `build_all_images.sh` retags prod :latest -- read its blast radius).
3. Then merge the frontend. Until the next model run, today's board has only K / OUTS rows (back-filled); TB / hits / SB / ER fill in after.

## Read before trusting it
- A projection is the model's EXPECTED VALUE (calibrated lambda), not a point forecast; it is the same number the over/under probability is priced from.
- No L5 / L10 hit rates for MLB (not computed). HR, NRFI, F5, GAME have no expected count and are not on the board.
- Only K / OUTS have history (back-filled); nothing else has proj_mean before 2026-10-16, so no accuracy record yet. Next step if wanted: log every projection daily
  (not just the logged side) and grade MAE/CRPS by stat -- the transparency record.
- The board shows the side the runner logged (the one with the larger edge), one row per player and market; alt lines and 2+/3+ thresholds are excluded.
