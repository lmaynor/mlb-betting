# Game-line harness: NBA and NFL engines vs the closing lines (2026-11-04)

Code only, nothing deployed, nothing written to GCS or the bets table. Everything here is UNVALIDATED research output.

## What was built
- `mlb_core/eval/market_scoring.py`: sport-agnostic scoring of a model against a de-vigged market on the same events. Paired log loss
  (market minus model, so positive = model better) with DATE-CLUSTERED standard errors, Brier skill, ECE, the logit blend weight `c`
  on (model - market) with cluster-robust SE, edge buckets (win-rate minus implied, ROI at median and best price, rank-correlation
  monotonicity), the NO-MODEL line-shopping control (both sides of everything), and same-line open-to-close price CLV when open
  prices are supplied.
- `nfl/game/market_eval.py`: replays `nfl/game` (Kalman ratings + PBP features + key-number matrix) game by game from 2018, scoring 2021-2025
  against the nflverse schedule closing spread / total / moneyline and their prices.
- `nba/game/market_eval.py`: runs `nba.game.evaluate.evaluate_walk_forward` and scores it against BettingPros closing quotes
  (fresh books only, >= 3 books on the same main line, Consensus pseudo-book excluded, PHO/NOR/UTH mapped).
- `nhl/game/market_eval.py`: refuses to run without an odds table (see "NHL").

Reproduce:

    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025
    gcloud storage cp -r gs://<bucket>/Odds/bettingpros_nba_games/{moneyline,spread,total} <dir>   # reads are ~10 s each from a laptop
    GCS_BUCKET=<bucket> python -m nba.game.market_eval --local-dir <dir>

## Result: neither engine beats the closing line
| | n | log-loss gain vs market (z) | blend c on (model - market) (z) | edge buckets |
|---|---|---|---|---|
| NFL moneyline | 1355 | -0.040 (-5.8) | -0.04 (-0.4) | gap rho -0.40, not rising |
| NFL spread | 1326 | -0.034 (-4.0) | +0.14 (+1.7) | rho -0.60 |
| NFL total | 1348 | -0.063 (-6.3) | -0.03 (-0.3) | rho +0.60, not monotone |
| NBA moneyline | 1272 | -0.021 (-2.7) | +0.22 (+1.5) | rho -0.40 |
| NBA spread | 1243 | -0.035 (-4.1) | +0.02 (+0.2) | rho -0.60 |
| NBA total | 1218 | -0.027 (-3.5) | +0.05 (+0.4) | rho -0.10 |

- NFL point forecasts are worse than the line in EVERY season 2021-2025: margin RMSE 13.27 vs 12.63 overall, total RMSE 13.80 vs 13.08, and the
  engine's total is 0.55 to 4.05 points ABOVE the line every season (+2.45 pooled). Its probabilities are overconfident (ECE 0.10 spread,
  0.13 total vs 0.006 / 0.015 for the market) and 49-66% of games land in the 8%+ "edge" bucket (ML 49%, spread 56%, total 66%), which is the winner's-curse shape.
- No blend weight is significantly positive on any market, so there is no sign the engines know something the line does not.
- Spread and total markets are priced at the market's own line, so `p_mkt` is ~0.5 and the market's information is the line itself;
  the blend's market weight `a` is not identified there (ignore it); `c` and the log-loss gain are the meaningful numbers.
- The line-shopping control matters on NBA: betting BOTH sides of every game earns -6.2% at the median price and -2.0% at the best price
  (shopping alone is worth +4.2% on moneylines). Any NBA ROI quoted at the best book must be read against that, e.g. the model's 0-2% moneyline
  bucket at +16.5% best-price ROI is mostly shopping plus noise (its win-minus-implied gap is not monotone in edge). NFL has one price per game
  in nflverse, so there is no shopping effect to subtract there.
- CLV: not computed. BettingPros stores single-side opening quotes, so an open-to-close price pair on the same line is not available;
  `clv_price` is implemented and tested for tables that carry both sides. Forward CLV for picks comes from the PROPS ledger and the banked feeds.

## Limits (read before quoting any number)
- **NBA covers 2025-26 only.** `build_team_game_stats_from_raw` keeps 2,858 of 18,586 team-games: the 2019-24 team box has no rebounds or
  turnovers (`nan_stats` = 14,844 dropped), so the merged engine cannot be replayed on 2021-2024. The hoopR/ESPN mirror carries those columns and
  is the way to widen it (follow-up). The unmerged research models on branch `feat/nba-game-models` (`nba/games`) cover 2021-2025 and reached the
  same NO EDGE verdict on a different feature set (`docs/analysis/nba_game_models_2026-10-20.md` on that branch).
- NBA closing quotes are BettingPros' last quote per book (quotes older than 4 h before tip dropped); NFL lines are nflverse's closing lines with
  an undocumented source book. Neither is Pinnacle.
- Single replay per sport, parameters as shipped; nothing was tuned on these seasons and nothing here is an adoption decision.
- I did not check which seasons the NFL engine's hand-set constants were chosen on; if any of 2021-2025, these numbers are generous to it.

## A production bug found on the way
`nfl.game.pbp_features.aggregate_team_game_pbp` took `.mean()` of nflverse's `play_clock`, which is a STRING column, and raised on real data.
`fit_kalman_and_pbp` (the NFL game board's history fit) calls it unguarded, so the live NFL game-line engine would fail rather than price.
Fixed with a numeric coercion and a regression test (`tests/test_game_market_eval.py`). I did not check what the deployed board currently serves.

## NHL
No historical NHL game-line odds exist here: `Odds/bettingpros_nhl/` is player props only, the ParlayAPI game-line archive is ~168 h deep and
nearly empty for NHL, and BettingPros never quotes Pinnacle. `nhl/game/market_eval.py` therefore exits with an explanation unless given an events
table, and demands >= 200 events. The way to get one is to bank `OddsAccum/icehockey_nhl` game-line snapshots forward (they exist) and score
once a season of closes has accumulated; paying for a historical closing-odds archive is the alternative (a decision, not made here).

## Not done / next
1. Widen NBA to 2021-2025 by feeding the engine hoopR/ESPN team boxes (needs the four-factor columns mapped).
2. Forward CLV on engine picks (needs picks logged with timestamps; none exist for game lines).
3. Leakage garble test for the replay loops (scramble results on/after date t, predictions at t unchanged). The loops predict before they update, but
   that property is not pinned by a test here.
