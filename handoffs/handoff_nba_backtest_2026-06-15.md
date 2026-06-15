# Handoff -- NBA moneyline backtest (go/no-go) 2026-06-15

Offline backtest of an NBA game-winner model vs the market, to decide whether to
productionize NBA game moneyline. **Verdict: NO.** Pivot to player props.

## Setup
- Training data: `full_dataset_clean.parquet` (21,336 games, 2007-2025; 168 `fs_*`/
  `elo_*` engineered features; target `home_team_won`). Now in GCS at
  `NBA/training/full_dataset_clean.parquet`.
- Model: HistGradientBoosting, trained on games < 2022-07-01, tested 2022-25.
- Odds: NbaBetExplorer historical decimal moneyline (avg/closing), 2022-23..2024-25,
  joined on (date, unordered team pair); 99.7% coverage (3,648 games). Home/away
  odds assigned by team identity; orientation verified (market AUC 0.74 vs outcome).

## Results
- **Market AUC 0.739 > model AUC 0.703** -- the de-vigged line out-predicts the model.
- **Flat 1u backtest on the +edge side LOSES at every threshold:** ROI -7.7% (all),
  -6.9% (5% edge), -5.8% (8% edge). Worse than bet-favorite-always (-3.2%). ROI does
  NOT improve with bigger edges -> adverse selection (biggest "edges" = biggest model
  errors; winner's curse, same pattern as MLB /edge-analysis).
- **Incremental value (held-out 2024-25, n=1,195):** model+market logistic blend AUC
  0.7559 vs market-only 0.7563 (identical). Blend weights: model **+0.062**, market
  **+0.983**. The model carries essentially no information the market lacks.

## Why
The market prices same-day injury / lineup / rest / load-management news that the
parquet's pre-game stat features cannot see. On NBA moneyline -- the most efficient
basketball market -- that gap is decisive.

## Decision
1. **Do NOT build NBA game-moneyline.** Demoted in `nba/BLUEPRINT.md`.
2. **Pivot NBA modeling to player props** (softer markets; transfers our MLB
   BATTER_HITS/TB prop experience). Props MUST clear their own backtest first.
3. **Infra is reusable** -- SportsBlaze, Kaggle ingest, `nba/odds/`, the join
   pipeline all carry over.

## Blockers for the props path
- Need **historical player-prop odds** for a props backtest. nba_gambling's
  `player_props`/`props_comparison` CSVs are shallow (~Jan-Jun 2026 only). Options:
  run The Odds API forward to accumulate (slow; 500/mo free tier), buy historical
  prop data, or scrape more depth.
- Need a **prop projection model**: Kaggle player box (`NBA/stats_nba/`) + the
  projection feature spec in `nba/BLUEPRINT.md` (last5/10/20 rolling, opp def rank,
  rest/B2B, etc.) -> NegBin CDF for P(over line) (as MLB BATTER_HITS).

## Repro
`/tmp/nba_backtest.py` + `/tmp/nba_blend.py` (this session). Deterministic
(random_state=0, time-based splits). Re-runnable against the local parquet +
NbaBetExplorer CSVs.
