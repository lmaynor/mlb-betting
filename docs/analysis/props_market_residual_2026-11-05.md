# Market-residual models for NBA and NFL player props -- 2026-11-05

**UNVALIDATED research. Not wired into the serving board, the Official tier, or any job.** Code: `mlb_core/odds/props_residual.py` (model, protocol, metrics),
`nba/props/residual.py`, `nfl/props/residual.py` (table builders + CLI), `scripts/props_residual_posthoc.py`, `tests/test_props_residual.py`.
Generated tables: `props_market_residual_2026-11-05_tables_nba.md`, `..._tables_nfl.md`; raw results: `..._nba_results.json`, `..._nfl_results.json`.

## Verdict

| | NBA (5 markets, 362,777 props, 2021-2025) | NFL (9 markets, 33,434 props, 2021-2026) |
|---|---|---|
| Beats the de-vigged market out of sample with significance? | **Statistically yes, economically no.** Held-out 2025-26: d log loss -0.00055 (SE 0.00017, z -3.3) ridge, -0.00053 (SE 0.00012, z -4.5) GBDT. Brier skill +0.11%. | **No.** Held-out 2025: d log loss -0.00001 (SE 0.00042, z -0.0) ridge, -0.00014 (SE 0.00028, z -0.5) GBDT. The development folds were slightly worse than the market (+0.0014 ridge, +0.0004 GBDT, not significant). |
| Money at a price a bettor can take (median book price) | Held-out: 5,131 bets with any positive model edge, ROI +0.0% [-2.7, +2.4]; 1,049 bets at >= 2% edge, ROI -1.2% [-6.9, +4.6]. A breakeven residual under a ~6% two-way hold. | 9 bets at >= 2% edge (the model almost never clears the vig): untestable. |
| Best-price ROI net of the no-model line-shopping control | Ridge +3.05% [+1.5, +4.6] over a same-count market-only control, but the control itself returned +14.5% ROI with zero model input in 2025: the best-price column is a quote artifact (below). | Ridge +1.71% [-3.4, +7.4], GBDT -1.52% [-5.7, +3.3]: nothing. |
| Pre-registered bars cleared? | Yes (all four checks) for ridge, GBDT and the validation-gated rule | No check cleared |

Plain reading. NBA: the market's consensus is slightly mis-calibrated and slightly under-uses the engine, and a hard-regularised residual can learn that out of
sample (about 0.05% of log loss). That gain is real in the statistical sense and **does not translate into profit**: about half of it is the market's stationary
over-bias (the consensus prices the Over at 49.5% on average while 48.1-48.4% of props go over), the c-only blend on the engine gap (the earlier study's construct) does as
well or better on the held-out season, and at the median book price the bets it selects break even. NFL: a negative result; with only ~8,600 props a season the study can
only detect a gain bigger than about 0.001 log loss, and the point estimates are zero.

Do not promote either model. The one thing worth carrying forward is a per-stat recalibration of the NBA consensus (see "What this does not change").

## What was built

`p = sigmoid( logit(p_mkt) + f(x) )`, p_mkt = the median of the de-vigged two-sided reference books at the modal line (`bettingpros_loader`). The market logit is the **offset**:
f can only learn the residual (zero f = the market exactly, tested). Two learners, both hard-regularised, plus three comparison models:

| model | what it is |
|---|---|
| `ridge` | own damped-Newton logistic with an offset; per-column ridge; every feature pooled (penalty lam_main) plus per-stat intercept and per-stat slopes on the four key features (penalty lam_stat, so each stat is shrunk toward the pooled slope). Grid lam_main {1e4, 1e5, 1e6} x lam_stat {3e2, 3e3, 3e4, 3e5}. |
| `gbdt` | xgboost, `base_margin = logit(p_mkt)`, depth {1, 2}, rounds {60, 200, 500}, eta 0.02, min_child_weight 60, lambda 100, subsample 0.7 |
| `auto` | validation-gated: the better family only if it beat the market on the inner-validation season, else the market |
| `blend_c` (comparison) | the earlier construct: global intercept + a per-stat slope c on `logit(engine) - logit(market)`, nothing else |
| `calib` (comparison) | CALIBRATION ONLY: per-stat intercept + per-stat slope on `logit(p_mkt)`, no other input. How much of any gain is just undoing the market's own level / slope bias |

Features (all unit-free, market-relative; `make_features`): engine gap `logit(p_eng) - logit(p_mkt)`; standardised `(engine mean - line)/sd`; season-average mean reversion (this season to date
shrunk toward last season's mean with 10 games of prior, `(blend - line)/sd`); L10 and L5 form `(mean - line)/sd`; line movement `(close line - open line)/sd` and the open->close price shift (BettingPros
carries opening quotes: `is_open` rows, one per book, DFS and Kalshi dropped; median over books); log book count; MAD dispersion of the books' fair probabilities; the median-price hold; logit(p_mkt)
itself (the "b" of the earlier refit); NBA only: L5/season minutes ratio. Engine: NBA = `ship_noabs` (the shipped hierarchical engine with NO absence information; the `oracle` variants of
`scripts.nba_engine_vs_market` are fed the box score's own absentee list, a leak, and are never used), NFL = `nfl.models.hierarchical_live` priced at the BOOK's line as of the game with history
strictly before the week (`nfl.props.engine_eval.replay_hierarchical`; no pbp / injury context, unlike a fully loaded serving run).

**Sharp-vs-soft skew could not be a training feature.** Novig and ProphetX only quote in the BettingPros feed from the 2025 season (NBA 2025-26: Novig on 51% of props, ProphetX 14%, either 60%; NFL 2025: Novig 60%, ProphetX 57%);
Pinnacle never. There is nothing to learn the coefficient from, so it is an exploratory diagnostic on the seasons that have it (below), not part of the model.

## Protocol

* **Strictly chronological expanding window by season.** For test season s: hyper-parameters are chosen by training on seasons < s-1 and scoring season s-1 (inner validation); the final fit uses seasons < s
  and predicts s once. NBA folds: 2023 (train 105k), 2024 (188k), **2025 held out** (276k). NFL folds: 2023 (8.8k), 2024 (14.8k), **2025 held out** (21.6k), plus 2026 weeks 1-5 as a partial extra season.
* **Held-out discipline.** The CLI never touches the held-out season without `--report-holdout`; every design decision (feature set, grids, the calibration comparison, the tightened bars) was made on the
  2023-24 development folds. The held-out run was executed once per sport. The bars were tightened (calibration-only and matched-count line-shopping checks added) AFTER the development folds were seen and BEFORE the
  held-out season was scored: (1) paired log-loss z vs the market <= -2.58; (2) z vs the calibration-only model <= -1.96; (3) residual-calibration slope t >= 2 with b > 0; (4) best price, edge >= 2%, ROI above a
  no-model line-shopping control with the SAME number of bets, paired date-bootstrap CI above 0. The post-hoc script (`scripts/props_residual_posthoc.py`) ran after the held-out numbers were known and selects nothing.
* **Leakage guard.** `fit_predict_fold` / `select_hyperparameters` refuse to run if any training row is dated on or after the first test row (`assert_chronological`). `tests/test_props_residual.py` garbles every row
  dated after a day t and the labels of day t itself and checks the walk-forward predictions for t are bit-identical; a second test garbles the game log from t on and checks the form features at t do not move.
* **Metrics.** Paired log loss vs the market with date-clustered SEs (CR1, whole game dates as units); Brier skill vs the market; ECE (10 equal-count bins); the residual-calibration slope
  `(y - p_mkt) = b (p - p_mkt)` (b = 1: the model is right about its own residual); ROI by edge bucket 2-4 / 4-6 / 6-8 / 8+% at the median and the best book price; per-stat results; monotonicity of the buckets.
* **Controls so a best-quote artifact is not mistaken for alpha.** (a) the no-model line-shopping rule (the market probability alone vs the best price), per bucket and with the same number of bets as the model (top-K by its
  own edge); (b) the same for the calibration-only model; (c) "market-expected ROI" of each bet (`p_mkt x decimal - 1`: what the consensus says the bet is worth at that price): ROI minus it is the selection alpha;
  (d) always-Under / always-Over at both prices.

## NBA

Data: 383,285 consensus props (>= 3 two-sided reference books, last quote of the day), 362,777 priced by the engine as of the game and scored (void DNP props removed, one modal line per player-stat; all lines x.5, no pushes).
Seasons 2021-2025 (start year), 39k / 66k / 83k / 88k / 87k props. The consensus is one snapshot per day: **there is no CLV**.

### Log loss, calibration (paired on the same props)

| | n | market LL | ridge d (z) | GBDT d (z) | blend_c d (z) | calib d (z) | ridge vs calib | GBDT vs calib |
|---|---|---|---|---|---|---|---|---|
| development 2023-24 | 171,218 | 0.6854 | -0.00059 (-3.97) | -0.00064 (-4.30) | -0.00040 (-2.96) | -0.00035 (-2.81) | -0.00024 (-2.54) | -0.00028 (-3.09) |
| **held-out 2025** | 86,504 | 0.6873 | **-0.00055 (-3.28)** | **-0.00053 (-4.50)** | -0.00072 (-4.58) | -0.00029 (-2.30) | -0.00026 (-2.55) | -0.00023 (-2.81) |

Held-out 2025: Brier skill vs market +0.110% (SE 0.034) ridge / +0.105% (0.024) GBDT; ECE market 0.0137 -> ridge 0.0055, GBDT 0.0065; residual-calibration slope b = +0.83 (t 8.2) ridge, +1.22 (t 7.6) GBDT
(the residual is the right size and direction); the residual quintiles are monotone (model gap -3.4 / -2.0 / -1.4 / -1.0 / 0.0 pts vs realised -2.7 / -1.5 / -1.5 / -1.3 / +0.1 pts for ridge).

Per stat, held-out d log loss (z), ridge / GBDT: threes -0.00137 (-3.8) / -0.00088 (-4.6); assists -0.00048 (-2.8) / -0.00047 (-3.0); rebounds -0.00078 (-2.0) / -0.00084 (-3.7); points +0.00004 (+0.2) / -0.00021 (-1.2);
PRA -0.00027 (-1.2) / -0.00025 (-1.1). The information is in threes, assists and rebounds, as the earlier c-blend study found; points and PRA carry nothing (the earlier engine-vs-market study measured an engine blend c of about 0.02 for them).

### Why this is not an edge

1. **Half of it is recalibration, and the simple blend does as well.** Calibration-only recovers 0.00029 of the 0.00055. Post-hoc (after the held-out season was seen): the c-only blend beats ridge by 0.00017 (z 2.4) and GBDT by 0.00019
   (z 2.2) on 2025; on the development folds the feature models beat it (ridge -0.00019, z -3.5), so the extra features are not robust beyond the engine gap.
2. **Over-bias is what the calibration piece is.** The consensus prices the Over at 49.4-49.6% in every season while 47.3-48.4% of props go over (rebounds 47.7% vs 49.7%, threes 46.4% vs 48.5%). A model that leans Under captures it.
   It is stationary here, but it is a feature of this feed's consensus, not an inefficiency a bettor can trade at the median price after the vig.
3. **Median book price: breakeven.** Held-out ridge, bets at any positive model edge vs the median price: 5,131 bets, ROI +0.01% [-2.7, +2.4], market-expected -6.1%; >= 1%: 2,193 bets, +0.01% [-4.1, +3.8]; >= 2%: 1,049 bets, -1.15%
   [-6.9, +4.6]. The development folds showed +4.6% [+2.3, +7.1] at >= 2% (4,854 bets) and it did not hold. GBDT: 370 / 99 / 45 bets, no information. Always-Under at the median price: -4.0% [-4.7, -3.3].
4. **Best-price ROI is a best-quote artifact.** In 2025 the no-model rule (market probability vs the best price, edge >= 2%) returned **+14.5%** over 10,740 bets with its own market-expected ROI +16.5%; the 8+% edge bucket is +85% for
   the control and +68% for ridge. The feed has more books each year (7.5 per prop in 2025 vs 4.2 in 2021) including prop-style books whose last quote can be stale. Net of the control the model is a few points up:
   ridge +3.05% [+1.5, +4.6] over a market-only rule with the same number of bets, +1.15% [+0.1, +2.3] over the calibration-only rule; GBDT +1.55% [+0.1, +3.0] and -0.35% [-1.7, +0.8]. Edge-bucket ROI is monotone 3/3 steps,
   but the top bucket is the artifact. This is not an exploitable return without proof that those best quotes were takeable.

### Sharp-vs-soft skew (exploratory, NBA 2025-26 only)

On the 51,832 held-out props (60%) with a Novig and/or ProphetX quote, skew = logit(mean sharp fair P(over)) - logit(p_mkt) has a date-block cross-validated gain of -0.00063 (SE 0.00017, z -3.7), slope 0.93 (t 8.1), correlation 0.02 with
the realised gap. It is a single season, block-CV (later blocks help fit earlier ones: NOT chronological), and the exchanges' fair prices may simply be fresher than soft books' last quotes (staleness was not measured). Worth a forward test
once Novig history exists; not evidence of an edge.

## NFL

33,434 scored props (of 56,572 consensus rows for the nine engine-modelled markets: 727 post-game quotes, 17,451 unresolved + 75 ambiguous names, the rest lost to the replay's history requirement). Per season 4.4k / 4.5k / 6.0k /
6.8k / 8.6k / 3.2k (2026 weeks 1-5). Anytime TD has no two-sided price and rush+rec yards only exists from 2025: both excluded.

| | n | market LL | ridge d (z) | GBDT d (z) | blend_c d (z) | calib d (z) |
|---|---|---|---|---|---|---|
| development 2023-24 | 12,792 | 0.6876 | +0.00143 (+1.88) | +0.00041 (+0.69) | +0.00245 (+2.72) | +0.00330 (+3.62) |
| **held-out 2025** | 8,632 | 0.6886 | -0.00001 (-0.01) | -0.00014 (-0.50) | -0.00032 (-0.70) | +0.00030 (+0.55) |
| 2026 weeks 1-5 (partial) | 3,165 | 0.6890 | +0.00005 (+0.05) | +0.00046 (+0.95) | +0.00034 (+0.29) | -0.00065 (-0.85) |

Held-out 2025: Brier skill +0.004% (SE 0.085) ridge, +0.029% (0.056) GBDT; residual slope +0.52 (t 1.4) / +0.74 (t 1.6); no stat shows a gain beyond |z| 2 (the largest move is pass yards, +0.0024 / +0.0026, the wrong way). Best-price >= 2%:
ridge 1,652 bets +4.8% [0.0, +9.6], vs the same-count market control +1.7% [-3.4, +7.4]; GBDT 1,369 bets +1.9%, vs control -1.5% [-5.7, +3.3]. At the median price the model produced 9 (ridge) and 6 (GBDT) bets with edge >= 2%.
The calibration-only model is WORSE than the market in NFL 2023, 2024 (significantly) and 2025 (not) because the NFL over rate moves by season (45.2%, 46.9%, 50.0%, 48.7%, 48.7%, 48.6%): recalibration learned on 2021-22 does not transfer.
All pre-registered checks fail for ridge, GBDT and auto. Sharp skew (2025-26, 9,218 props with a Novig / ProphetX quote): block-CV d log loss -0.00002, z -0.07.

Power: the date-clustered SE of the paired difference is 0.0003-0.0004 on 8,600 props, so a true NFL gain below about 0.001 log loss (about 0.15% of log loss) is invisible here. This is a negative result for "any large residual",
not a proof that NFL has none.

## What was NOT verified

* **CLV, and therefore edge.** One last-quote snapshot per day; nothing here can say more than the log-loss and ROI-vs-control evidence above. Best-price ROI cannot be trusted (artifact above).
* **That these quotes were takeable.** The consensus is the last quote per book; the median price assumes you could bet any book at its close.
* **The live engines.** NBA uses `ship_noabs` (no injury report; the market quote is after injury news, so some of the "residual" is the market knowing who is out); NFL uses the hierarchical engine without pbp / injury context.
* **Identity loss in NFL** (about 31% of consensus rows do not resolve to a player-week) and the NBA join (the UTC date of `scheduled`, void DNP props dropped, 3,256 same-day duplicates dropped) select the rows; not tested for selection effects.
* **Stationarity of the book roster.** The mix of books changes every season (Betr, Fliff, Hard Rock, Sleeper, Novig, ProphetX appear only in 2024-25); n_books / dispersion / hold are therefore non-stationary features.
* **Sharp skew out of time, and Pinnacle** (never in the feed). **Alt lines** (only the modal line is used). **Pushes** (NFL integer lines removed, not repriced).
* **Deployment, serving parity and runtime** of any of this: nothing was wired in.
* The validation-gated `auto` rule is the ridge in NBA 2023-24 and the GBDT in NBA 2025 (it is a selector, not a third model); in NFL 2024 it fell back to the market (neither family beat it on 2023).

## What this does not change

* `BLEND_AB_BY_SPORT_STAT` stays empty and `PROPS_SIGNAL_ALLOWLIST` stays as is: the residual model is not a reason to add a stat to the Official tier. Its per-stat evidence repeats the allowlist's (threes / rebounds / assists
  have information; points / PRA do not).
* The only actionable observation is the NBA consensus over-bias (about -1.2 points of P(over) averaged over stats, -2 on rebounds / threes): worth a per-stat calibration if a feed of un-vigged consensus is ever used as a price, but it
  is not a bet at the median price.

## Reproduce

```
# NBA (local copies of Odds/bettingpros_nba and NBA/player_boxscores_master.csv)
PYTHONPATH=. python -m nba.props.residual build --bp-dir D/Odds/bettingpros_nba --master D/NBA/player_boxscores_master.csv --work-dir W
PYTHONPATH=. python -m nba.props.residual run --table W/residual_table.parquet --out-md dev.md                                    # development folds only
PYTHONPATH=. python -m nba.props.residual run --table W/residual_table.parquet --report-holdout --out-json r.json --out-preds p.parquet
# NFL (MLB_BASE_DATA=D with NFL/nflverse/{player_stats,rosters_weekly,players,schedules}; local Odds/bettingpros_nfl)
MLB_BASE_DATA=D PYTHONPATH=. python -m nfl.props.residual build --bp-dir D/Odds/bettingpros_nfl --work-dir W --seasons 2021-2026
PYTHONPATH=. python -m nfl.props.residual run --table W/residual_table.parquet --report-holdout --out-json r.json --out-preds p.parquet
PYTHONPATH=. python -m scripts.props_residual_posthoc --table W/residual_table.parquet --preds p.parquet --holdout 2025
```
Data used: bucket copies pulled 2026-11-04 (read-only `gcloud storage cp`); master `player_boxscores_master.csv` through 2026-11-01; BettingPros NBA to 2026-10-14, NFL to 2026-10-13. Python 3.14.7, pandas 3.0.3, xgboost / scikit-learn from a system-site venv.
Tests: `python -m pytest tests/test_props_residual.py` (14 tests: cluster SE, offset ridge, leakage guards, null and planted-signal recovery, ROI arithmetic by hand, market extras, the held-out guard).
