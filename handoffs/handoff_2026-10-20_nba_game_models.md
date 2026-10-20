# Handoff -- 2026-10-20 -- NBA moneyline / spread / totals models

Branch `feat/nba-game-models` (worktree `../mlb-betting-nba-games`, off origin/main 13fd7d7). UNCOMMITTED. Nothing deployed, no DB/Discord/picks wiring.

## What was asked
Stand up NBA ML/spread/totals models from `NBA-Game-Outcome-Prediction-main.zip` + `NBA-Time-Series-Forecasts-main.zip`, with "full BettingPros backfill + ParlayAPI new odds".

## Result
**No edge** (details `docs/analysis/nba_game_models_2026-10-20.md`). Walk-forward 2021-25, 6,604 priced games: line beats model on every market and season; blend weight on the model ~0.
Consistent with `handoffs/handoff_nba_backtest_2026-06-15.md`. Models are infrastructure for a future test with availability data, not a betting system.

## Built
`nba/games/{bp_games,data,features,models,market,evaluate,score}.py`, `tests/test_nba_games.py` (12 pass), solutions doc, CONTEXT s19 entry.

## Corrected assumption
BettingPros backfill = NBA PLAYER PROPS ONLY. Game lines crawled this session (475k rows, 1,366 days x 3 markets, 0 errors) and uploaded to
`gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nba_games/`. ParlayAPI game lines only exist from 2026-10-16.

## Gotchas
- BP tricodes PHO/NOR/UTH (not PHX/NOP/UTA) silently dropped 19% of games; alias map in `market.BP_CODES`.
- `updated` is UTC; last quote of the day ~+1 min after tip; drop > 10 min after (in-play).
- Do not use the TSF zip's `processed_nba_game_data.csv` (season-mean leakage, zeroed TEAM_ID).
- `score.py` on a preseason/first-week slate is meaningless (stale ratings); `early_season` is True until both teams have 15 games.
- The crawl is resumable: `python -m nba.games.bp_games --start <d> --end <d>` (needs a local MLB_BASE_DATA or MLB_GCS_BUCKET); it is NOT scheduled. Run it weekly if you want in-season lines kept current, or add the game-odds markets to the live BettingPros job.

## Next (only if worth it)
1. Add pre-game availability features (`nba/props/official_availability.py`, ESPN injury bank) to the margin/total matrices and re-run `evaluate` -- the only plausible source of signal.
2. Bank ParlayAPI game lines through the season (already running twice daily) and re-evaluate with true opening-vs-closing for CLV.
3. Schedule the BettingPros game-line crawl (or fold markets 127/128/129 into `bp-live-nba`).
