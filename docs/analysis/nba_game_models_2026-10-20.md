# NBA game models (moneyline / spread / totals) -- walk-forward vs the closing consensus, 2026-10-20

Branch `feat/nba-game-models` (worktree `../mlb-betting-nba-games`). Code-only, nothing deployed. Reproduce:

    MLB_BASE_DATA=<dir with Odds/bettingpros_nba_games> PYTHONPATH=. python -m nba.games.evaluate \
      --hoopr-dir <NBA/hoopr mirror> --tsf-csv <nba_game_data.csv> --base-data <same dir> --kind ridge|hgb --out report.md

## Verdict: NO EDGE on any of the three markets. Do not wire these to alerts, picks or the DB.

- The line out-predicts the model everywhere, every season (2021-2025): ML log-loss 0.597 vs 0.620; spread RMSE 13.60 vs 14.05; total RMSE 17.99 vs 18.70.
- Incremental information is zero or negative: ML blend weight on the model -0.04 (logit), spread coefficient on (model - market) -0.010 (se 0.047), totals -0.048 (se 0.045).
- Flat-stake ROI is negative at every edge threshold, and does not improve with bigger "edges" (winner's curse, same shape as MLB): ML -5.1% (n=6604), spread -6.3%, totals -3.7% at the median book price. Even at the BEST book price per bet (an optimistic line-shop bound) ML -1.0%, spread -5.3%, totals -3.0%.
- HistGradientBoosting is no better than ridge/logistic. This reproduces `handoffs/handoff_nba_backtest_2026-06-15.md` on 5 seasons of real multi-book lines (that run used one source's ML odds only), and adds spread and totals.
- Why: the market prices lineup / injury / rest news that team box-score features cannot see. The availability work already in the repo (`nba/props/official_availability.py`, ESPN injury bank) is the only place a game-level signal could come from; none of it is wired here.

## Inputs used / not used from the two zips
- Used: Elo with 0.75 season carryover + rolling averages (NBA-Game-Outcome-Prediction notebooks); the nba_api team game log `nba_game_data.csv` for 1983-2018 Elo warm-up (NBA-Time-Series-Forecasts).
- Not used: `processed_nba_game_data.csv` (its `Points_Per_Game` is a season-wide mean = future leakage; `TEAM_ID` zeroed), both zips' accuracy claims (65-67% with unclear splits; our out-of-sample ML accuracy is below the line's), Basketball Reference scraping (ToS), Selenium/ChromeDriver pipeline.

## Data gaps found along the way
- The "full BettingPros backfill" was player props only; game lines were crawled for this work (`docs/solutions/integration-issues/bettingpros-nba-game-lines-not-in-props-backfill.md`): 1,366 days x 3 markets, 475k quote rows, now at `gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nba_games/`.
- BettingPros spells PHX/NOP/UTA as PHO/NOR/UTH; unmapped, they silently removed 19% of games from the join (caught by a constant 80.8% per-season coverage). Fixed + tested.
- Closing proxy only: BettingPros keeps one last quote per book per day (stamped a median +1 min after tip); no timestamped series, so no CLV.
- ParlayAPI live game lines are banked (3 preseason days so far) and `nba.games.score` prices them, but the first-week output is dominated by stale ratings (13-20 pt gaps to the line), flagged `early_season`.

## Full report (ridge)

Test seasons [2021, 2022, 2023, 2024, 2025]; train from 2008 (strictly before each test season); model `ridge`. Market = median de-vigged book consensus (BettingPros last quote of day, a closing proxy).
Coverage: 6605 test games, 6604 with a market.

## Moneyline
- n=6604  log-loss: market **0.5968**, logistic model 0.6197, Phi(margin/sigma) 0.6192, home-rate 0.6870
- LOSO blend log-loss 0.5971; blend coefficients on logit: market 1.059, model -0.044
- ROI by edge threshold [0.0, 0.02, 0.04, 0.06] (median price): -5.1% +/-1.6 (n=6604) | -6.1% +/-1.8 (n=5221) | -7.2% +/-2.1 (n=4046) | -7.2% +/-2.5 (n=3048)
- ROI (best price, optimistic): -1.0% +/-1.8 (n=6604) | -2.5% +/-1.9 (n=5221) | -3.6% +/-2.2 (n=4046) | -3.6% +/-2.6 (n=3048)

## Spread
- n=6604  RMSE vs actual margin: market 13.603, model 14.048; mean(model - market margin) +0.26
- incremental coefficient on (model - market): -0.010 (se 0.047); push rate 0.010
- ROI (median price): -6.3% +/-1.2 (n=6604) | -5.5% +/-1.3 (n=5541) | -5.5% +/-1.4 (n=4507) | -5.1% +/-1.6 (n=3610)
- ROI (best price): -5.3% +/-1.2 (n=6604) | -4.6% +/-1.3 (n=5541) | -4.5% +/-1.4 (n=4507) | -4.2% +/-1.6 (n=3610)

## Totals
- n=6605  RMSE vs actual total: market 17.991, model 18.702; mean(model - market total) -0.94
- incremental coefficient on (model - market): -0.048 (se 0.045); push rate 0.007
- ROI (median price): -3.7% +/-1.2 (n=6605) | -3.3% +/-1.3 (n=5571) | -3.4% +/-1.4 (n=4566) | -3.3% +/-1.6 (n=3664)
- ROI (best price): -3.0% +/-1.2 (n=6605) | -2.7% +/-1.3 (n=5571) | -2.8% +/-1.4 (n=4566) | -2.7% +/-1.6 (n=3664)

## By season
| season | games | ml_n | ml_ll_mkt | ml_ll_model | sp_rmse_mkt | sp_rmse_model | tot_rmse_mkt | tot_rmse_model |
|---|---|---|---|---|---|---|---|---|
| 2021 | 1323 | 1323 | 0.6082 | 0.6338 | 13.7581 | 14.2225 | 17.9846 | 18.9601 |
| 2022 | 1320 | 1320 | 0.6271 | 0.6458 | 12.5552 | 12.9326 | 18.2153 | 18.9008 |
| 2023 | 1319 | 1318 | 0.5838 | 0.609 | 13.7364 | 14.1803 | 17.594 | 18.1557 |
| 2024 | 1321 | 1321 | 0.5856 | 0.6086 | 13.7844 | 14.2081 | 17.7222 | 18.4268 |
| 2025 | 1322 | 1322 | 0.5795 | 0.6013 | 14.1257 | 14.6351 | 18.4239 | 19.0497 |

## HistGradientBoosting (same protocol)

## Moneyline
- n=6604  log-loss: market **0.5968**, logistic model 0.6226, Phi(margin/sigma) 0.6206, home-rate 0.6870
- LOSO blend log-loss 0.5971; blend coefficients on logit: market 1.094, model -0.094
- ROI by edge threshold [0.0, 0.02, 0.04, 0.06] (median price): -5.9% +/-1.7 (n=6604) | -6.8% +/-1.9 (n=5278) | -7.2% +/-2.2 (n=4136) | -7.5% +/-2.5 (n=3210)
- ROI (best price, optimistic): -1.8% +/-1.8 (n=6604) | -2.7% +/-2.1 (n=5278) | -2.7% +/-2.5 (n=4136) | -3.7% +/-2.7 (n=3210)

## Spread
- n=6604  RMSE vs actual margin: market 13.603, model 14.086; mean(model - market margin) +0.27
- incremental coefficient on (model - market): -0.023 (se 0.045); push rate 0.010
- ROI (median price): -5.8% +/-1.2 (n=6604) | -5.5% +/-1.3 (n=5582) | -5.5% +/-1.4 (n=4653) | -5.3% +/-1.6 (n=3741)
- ROI (best price): -4.9% +/-1.2 (n=6604) | -4.5% +/-1.3 (n=5582) | -4.5% +/-1.4 (n=4653) | -4.3% +/-1.6 (n=3741)

## Totals
- n=6605  RMSE vs actual total: market 17.991, model 18.806; mean(model - market total) +0.30
- incremental coefficient on (model - market): -0.038 (se 0.041); push rate 0.007
- ROI (median price): -4.2% +/-1.2 (n=6605) | -4.0% +/-1.3 (n=5677) | -4.3% +/-1.4 (n=4732) | -4.2% +/-1.5 (n=3817)
- ROI (best price): -3.6% +/-1.2 (n=6605) | -3.4% +/-1.3 (n=5677) | -3.6% +/-1.4 (n=4732) | -3.6% +/-1.6 (n=3817)
