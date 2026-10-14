# NBA player-prop projections -- offline evaluation (2026-10-14)

**Status: paper / LOG-ONLY. No edge is claimed. No NBA prop odds history exists, so this document measures projection accuracy and distribution calibration only -- never ROI, CLV or edge.**

Data: SportsBlaze box scores, 186,447 played player-games through 2026-06-13 (seasons 2019-20..2025-26, preseason excluded; 2019-20 is truncated at the COVID break). Test seasons (start year) [2022, 2023, 2024, 2025]; the headline is 2025-26 (the latest full season). Regular-season rows only. Every row's features use strictly earlier ET game days (`nba/props/features.py`); the model that scores a season was trained on seasons <= T-2, tuned on T-1 and refit on <= T-1, and its spread / family / recalibration come from T-1's out-of-sample predictions. All numbers below are produced by `python -m nba.props.evaluate`.

## Summary

- **Accuracy**: pooled over four rolling-origin test seasons the model's MAE is points 4.547 vs 4.634 for the best naive baseline (1.9% lower); rebounds 1.882 vs 1.912 for the best naive baseline (1.6% lower); assists 1.319 vs 1.339 for the best naive baseline (1.5% lower); threes 0.871 vs 0.875 for the best naive baseline (0.5% lower); pra 5.981 vs 6.146 for the best naive baseline (2.7% lower). The gains are small, consistent in every season and statistically distinguishable from zero (date-cluster bootstrap), and they are the whole story: nothing here is large enough to be called an edge.
- **Calibration**: P(over) at proxy lines has ECE (after PIT recalibration) of points 0.009; rebounds 0.004; assists 0.005; threes 0.007; pra 0.007; the family-only NegBin was ~3pp too low near the centre for points/PRA before recalibration. The model is OVERCONFIDENT in the far tail (predicted P(over) >= 0.7 realizes noticeably lower for points/PRA), which is the region where a model-vs-market disagreement would live -- see the EV cap in the scorer.
- **Minutes are the lever the model does not have**: giving it the game's ACTUAL minutes would cut MAE by points 19%; rebounds 15%; assists 10%; threes 9%; pra 28% (an upper bound; lineup/injury data would recover only part of it).
- **Not shown**: any profit, ROI, CLV or edge versus sportsbook prices. No NBA prop odds history exists; proxy lines are a naive forecaster's own line, strictly weaker than a market. See section 6.

## 1. Point accuracy vs naive baselines (MAE, lower is better)

Pooled over all test seasons. `gain` = baseline MAE minus model MAE, with a date-cluster bootstrap 95% CI (positive = the model is better).

| stat | n | model MAE | L10 mean MAE (gain; 95% CI) | EWM(hl=8) MAE (gain; 95% CI) | season-to-date mean MAE (gain; 95% CI) | minutes-adjusted rate MAE (gain; 95% CI) |
|---|---|---|---|---|---|---|
| points | 103,293 | 4.547 | 4.711 (+0.163; +0.152..+0.177) | 4.634 (+0.086; +0.077..+0.096) | 4.690 (+0.142; +0.129..+0.157) | 4.654 (+0.107; +0.098..+0.118) |
| rebounds | 103,293 | 1.882 | 1.951 (+0.069; +0.065..+0.073) | 1.916 (+0.034; +0.030..+0.037) | 1.936 (+0.054; +0.049..+0.058) | 1.912 (+0.030; +0.027..+0.034) |
| assists | 103,293 | 1.319 | 1.363 (+0.045; +0.042..+0.048) | 1.339 (+0.020; +0.018..+0.022) | 1.350 (+0.031; +0.028..+0.035) | 1.339 (+0.020; +0.017..+0.022) |
| threes | 103,293 | 0.871 | 0.892 (+0.021; +0.019..+0.023) | 0.876 (+0.006; +0.005..+0.007) | 0.880 (+0.009; +0.007..+0.011) | 0.875 (+0.004; +0.003..+0.006) |
| pra | 103,293 | 5.981 | 6.234 (+0.252; +0.236..+0.271) | 6.146 (+0.165; +0.151..+0.179) | 6.262 (+0.281; +0.259..+0.305) | 6.182 (+0.201; +0.187..+0.218) |

Relative gain over the best naive baseline (EWM or minutes-adjusted, whichever is lower): points 1.9%; rebounds 1.6%; assists 1.5%; threes 0.5%; pra 2.7%.

Latest full season (2025-26), model vs the best naive baseline:

| stat | n | model MAE | model RMSE | model R2 | model bias | best baseline | its MAE | its R2 |
|---|---|---|---|---|---|---|---|---|
| points | 26,134 | 4.527 | 5.926 | 0.531 | -0.127 | EWM(hl=8) | 4.627 | 0.509 |
| rebounds | 26,134 | 1.868 | 2.454 | 0.453 | 0.017 | minutes-adjusted rate | 1.902 | 0.431 |
| assists | 26,134 | 1.331 | 1.807 | 0.505 | -0.018 | EWM(hl=8) | 1.354 | 0.484 |
| threes | 26,134 | 0.886 | 1.225 | 0.325 | 0.008 | minutes-adjusted rate | 0.889 | 0.309 |
| pra | 26,134 | 5.936 | 7.660 | 0.589 | -0.093 | EWM(hl=8) | 6.135 | 0.560 |

Per-season model MAE, and the improvement over the EWM(hl=8) baseline in MAE points (stability across the rolling origin):

| stat | 2022 MAE (gain) | 2023 MAE (gain) | 2024 MAE (gain) | 2025 MAE (gain) |
|---|---|---|---|---|
| points | 4.596 (+0.085) | 4.500 (+0.081) | 4.568 (+0.079) | 4.527 (+0.101) |
| rebounds | 1.901 (+0.029) | 1.859 (+0.031) | 1.901 (+0.036) | 1.868 (+0.039) |
| assists | 1.287 (+0.013) | 1.327 (+0.024) | 1.328 (+0.021) | 1.331 (+0.022) |
| threes | 0.855 (+0.006) | 0.853 (+0.006) | 0.888 (+0.006) | 0.886 (+0.005) |
| pra | 6.028 (+0.154) | 5.963 (+0.153) | 6.000 (+0.153) | 5.936 (+0.199) |

## 2. Predicted distribution quality (CRPS and log score, lower is better)

Every forecaster uses the same family, the same spread protocol (spread fitted on the previous season's residuals) and the same PIT recalibration protocol (`+cal`, fitted on the previous season), so the baseline comparison isolates the quality of the MEAN. The deployed model distribution is the `gbm + cal` column. Latest full season (2025-26):

| stat | family | model CRPS (binned) | model CRPS (gbm spread) | model CRPS (gbm + cal) | best baseline CRPS (+cal) | model logscore (gbm) | model logscore (gbm + cal) | best baseline logscore (+cal) |
|---|---|---|---|---|---|---|---|---|
| points | nbinom | 3.187 | 3.187 | 3.179 | 3.267 (EWM(hl=8)) | 3.026 | 3.006 | 3.033 (EWM(hl=8)) |
| rebounds | nbinom | 1.297 | 1.294 | 1.294 | 1.329 (minutes-adjusted rate) | 2.151 | 2.152 | 2.186 (minutes-adjusted rate) |
| assists | nbinom | 0.908 | 0.908 | 0.907 | 0.931 (EWM(hl=8)) | 1.755 | 1.757 | 1.785 (minutes-adjusted rate) |
| threes | nbinom | 0.582 | 0.582 | 0.582 | 0.592 (minutes-adjusted rate) | 1.303 | 1.303 | 1.322 (minutes-adjusted rate) |
| pra | nbinom | 4.189 | 4.192 | 4.184 | 4.344 (EWM(hl=8)) | 3.361 | 3.349 | 3.387 (EWM(hl=8)) |

## 3. Minutes / availability uncertainty (the dominant NBA prop driver)

A row is 'uncertain minutes' when minutes std over the last 10 games >= 7.5, or >= 3 team games missed in the last 10, or minutes L5/L20 outside (0.7, 1.3). `z_rms` = RMS of (outcome - mean)/predicted sd (ideal 1.0; above 1 = spread too narrow), `cov80` = share of outcomes inside the central 80% interval (ideal 0.80). The scorer abstains on these rows (they are logged, never flagged). No lineup or injury data is available, so the spread is the only place this uncertainty can enter the forecast.

| stat | bucket | spread | share of rows | mean predicted sd | z_rms | cov80 |
|---|---|---|---|---|---|---|
| points | stable | gbm | 54.1% | 6.299 | 0.988 | 0.788 |
| points | stable | binned | 54.1% | 6.333 | 0.977 | 0.792 |
| points | uncertain minutes | gbm | 45.9% | 5.266 | 1.017 | 0.771 |
| points | uncertain minutes | binned | 45.9% | 5.158 | 1.028 | 0.764 |
| rebounds | stable | gbm | 54.1% | 2.509 | 0.991 | 0.812 |
| rebounds | stable | binned | 54.1% | 2.589 | 0.958 | 0.826 |
| rebounds | uncertain minutes | gbm | 45.9% | 2.254 | 1.023 | 0.819 |
| rebounds | uncertain minutes | binned | 45.9% | 2.174 | 1.055 | 0.808 |
| assists | stable | gbm | 54.1% | 1.857 | 0.993 | 0.840 |
| assists | stable | binned | 54.1% | 1.882 | 0.976 | 0.846 |
| assists | uncertain minutes | gbm | 45.9% | 1.501 | 1.013 | 0.866 |
| assists | uncertain minutes | binned | 45.9% | 1.458 | 1.035 | 0.861 |
| threes | stable | gbm | 54.1% | 1.236 | 0.978 | 0.872 |
| threes | stable | binned | 54.1% | 1.238 | 0.970 | 0.873 |
| threes | uncertain minutes | gbm | 45.9% | 1.000 | 1.000 | 0.893 |
| threes | uncertain minutes | binned | 45.9% | 0.980 | 1.016 | 0.893 |
| pra | stable | gbm | 54.1% | 8.113 | 0.981 | 0.801 |
| pra | stable | binned | 54.1% | 8.126 | 0.974 | 0.805 |
| pra | uncertain minutes | gbm | 45.9% | 7.198 | 1.019 | 0.775 |
| pra | uncertain minutes | binned | 45.9% | 7.001 | 1.033 | 0.768 |

Oracle diagnostic (2025-26; NOT a usable model): the same pipeline with the target game's ACTUAL minutes added as a feature. The gap is the error attributable to not knowing minutes in advance -- an UPPER bound on what lineup/injury data (follow-up: ESPN injuries endpoint) could remove, since actual minutes also carry foul trouble and blowouts that nothing pre-game can forecast.

| stat | MAE, real model (no minutes) | MAE, actual-minutes oracle | share of MAE explained by minutes |
|---|---|---|---|
| points | 4.527 | 3.652 | 19.3% |
| rebounds | 1.868 | 1.585 | 15.1% |
| assists | 1.331 | 1.201 | 9.8% |
| threes | 0.886 | 0.806 | 9.0% |
| pra | 5.936 | 4.274 | 28.0% |

## 4. P(over) at PROXY lines -- calibration and model-vs-naive disagreement (PROXY, NOT a market)

Proxy line = the half-point line at which the NAIVE EWM(8) forecaster's own P(over) is closest to 0.5 (how a book balances a market). Pooled over test seasons. Two questions: (a) is the model's P(over) calibrated, and (b) where the model's P(over) differs from the naive forecaster's, does the realized over-rate follow the model? `closed` = the share of the model-vs-naive gap that the realized over-rate actually covers (1 = the model's view was right, 0 = the naive view was right, negative = worse than naive). A real book line additionally prices injuries, rest news and sharp action that box scores cannot see, so none of this transfers to a market edge; these are not bets.

### points (n=103,293; share of rows that went over the proxy line 0.507)

| forecaster | Brier | log loss | ECE |
|---|---|---|---|
| naive EWM(8) + PIT recalibration | 0.2501 | 0.6940 | 0.0101 |
| model, family only | 0.2431 | 0.6793 | 0.0270 |
| model + PIT recalibration | 0.2424 | 0.6778 | 0.0093 |

| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |
|---|---|---|---|---|
| (-0.001, 0.2] | 331 | 0.157 | 0.166 | +0.009 |
| (0.2, 0.3] | 1,672 | 0.264 | 0.242 | -0.021 |
| (0.3, 0.4] | 10,374 | 0.364 | 0.374 | +0.010 |
| (0.4, 0.45] | 15,337 | 0.428 | 0.438 | +0.010 |
| (0.45, 0.5] | 24,042 | 0.476 | 0.486 | +0.010 |
| (0.5, 0.55] | 23,030 | 0.524 | 0.534 | +0.010 |
| (0.55, 0.6] | 14,627 | 0.572 | 0.571 | -0.002 |
| (0.6, 0.7] | 10,808 | 0.638 | 0.636 | -0.002 |
| (0.7, 0.8] | 2,650 | 0.737 | 0.675 | -0.062 |
| (0.8, 1.0] | 422 | 0.838 | 0.746 | -0.091 |

| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |
|---|---|---|---|---|---|---|
| 0.00-0.02 | model more OVER than naive | 10,197 | 0.499 | 0.509 | 0.521 | 2.23 |
| 0.00-0.02 | model more UNDER than naive | 10,295 | 0.499 | 0.489 | 0.497 | 0.20 |
| 0.02-0.05 | model more OVER than naive | 13,517 | 0.499 | 0.534 | 0.541 | 1.22 |
| 0.02-0.05 | model more UNDER than naive | 13,770 | 0.500 | 0.465 | 0.475 | 0.71 |
| 0.05-0.10 | model more OVER than naive | 14,749 | 0.499 | 0.571 | 0.575 | 1.05 |
| 0.05-0.10 | model more UNDER than naive | 15,138 | 0.500 | 0.427 | 0.436 | 0.88 |
| 0.10-1.00 | model more OVER than naive | 14,083 | 0.481 | 0.654 | 0.632 | 0.88 |
| 0.10-1.00 | model more UNDER than naive | 11,544 | 0.500 | 0.345 | 0.354 | 0.94 |

### rebounds (n=103,293; share of rows that went over the proxy line 0.501)

| forecaster | Brier | log loss | ECE |
|---|---|---|---|
| naive EWM(8) + PIT recalibration | 0.2482 | 0.6903 | 0.0072 |
| model, family only | 0.2397 | 0.6720 | 0.0029 |
| model + PIT recalibration | 0.2397 | 0.6720 | 0.0040 |

| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |
|---|---|---|---|---|
| (-0.001, 0.2] | 268 | 0.166 | 0.146 | -0.021 |
| (0.2, 0.3] | 2,443 | 0.265 | 0.257 | -0.007 |
| (0.3, 0.4] | 12,718 | 0.363 | 0.367 | +0.004 |
| (0.4, 0.45] | 17,445 | 0.427 | 0.430 | +0.003 |
| (0.45, 0.5] | 21,472 | 0.475 | 0.478 | +0.003 |
| (0.5, 0.55] | 19,544 | 0.524 | 0.532 | +0.008 |
| (0.55, 0.6] | 14,240 | 0.573 | 0.573 | -0.000 |
| (0.6, 0.7] | 11,637 | 0.639 | 0.641 | +0.002 |
| (0.7, 0.8] | 3,003 | 0.741 | 0.730 | -0.011 |
| (0.8, 1.0] | 523 | 0.829 | 0.813 | -0.017 |

| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |
|---|---|---|---|---|---|---|
| 0.00-0.02 | model more OVER than naive | 10,778 | 0.498 | 0.508 | 0.514 | 1.63 |
| 0.00-0.02 | model more UNDER than naive | 11,330 | 0.497 | 0.487 | 0.486 | 1.02 |
| 0.02-0.05 | model more OVER than naive | 13,309 | 0.499 | 0.534 | 0.543 | 1.28 |
| 0.02-0.05 | model more UNDER than naive | 15,305 | 0.496 | 0.462 | 0.460 | 1.06 |
| 0.05-0.10 | model more OVER than naive | 14,090 | 0.498 | 0.570 | 0.574 | 1.05 |
| 0.05-0.10 | model more UNDER than naive | 14,748 | 0.496 | 0.425 | 0.428 | 0.95 |
| 0.10-1.00 | model more OVER than naive | 12,943 | 0.467 | 0.640 | 0.637 | 0.98 |
| 0.10-1.00 | model more UNDER than naive | 10,790 | 0.499 | 0.345 | 0.350 | 0.97 |

### assists (n=103,293; share of rows that went over the proxy line 0.485)

| forecaster | Brier | log loss | ECE |
|---|---|---|---|
| naive EWM(8) + PIT recalibration | 0.2458 | 0.6882 | 0.0173 |
| model, family only | 0.2375 | 0.6675 | 0.0086 |
| model + PIT recalibration | 0.2374 | 0.6674 | 0.0048 |

| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |
|---|---|---|---|---|
| (-0.001, 0.2] | 1,055 | 0.170 | 0.216 | +0.047 |
| (0.2, 0.3] | 5,359 | 0.262 | 0.280 | +0.018 |
| (0.3, 0.4] | 17,917 | 0.359 | 0.364 | +0.005 |
| (0.4, 0.45] | 15,540 | 0.426 | 0.432 | +0.006 |
| (0.45, 0.5] | 18,452 | 0.475 | 0.476 | +0.000 |
| (0.5, 0.55] | 16,946 | 0.524 | 0.526 | +0.002 |
| (0.55, 0.6] | 12,799 | 0.573 | 0.572 | -0.001 |
| (0.6, 0.7] | 12,736 | 0.641 | 0.651 | +0.010 |
| (0.7, 0.8] | 2,428 | 0.727 | 0.729 | +0.002 |
| (0.8, 1.0] | 61 | 0.821 | 0.820 | -0.001 |

| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |
|---|---|---|---|---|---|---|
| 0.00-0.02 | model more OVER than naive | 10,796 | 0.483 | 0.493 | 0.494 | 1.09 |
| 0.00-0.02 | model more UNDER than naive | 10,402 | 0.485 | 0.475 | 0.480 | 0.49 |
| 0.02-0.05 | model more OVER than naive | 14,811 | 0.482 | 0.517 | 0.529 | 1.35 |
| 0.02-0.05 | model more UNDER than naive | 13,306 | 0.488 | 0.453 | 0.459 | 0.84 |
| 0.05-0.10 | model more OVER than naive | 16,154 | 0.472 | 0.544 | 0.552 | 1.11 |
| 0.05-0.10 | model more UNDER than naive | 14,187 | 0.487 | 0.415 | 0.415 | 1.00 |
| 0.10-1.00 | model more OVER than naive | 14,085 | 0.385 | 0.545 | 0.549 | 1.02 |
| 0.10-1.00 | model more UNDER than naive | 9,552 | 0.497 | 0.346 | 0.346 | 1.00 |

### threes (n=103,293; share of rows that went over the proxy line 0.408)

| forecaster | Brier | log loss | ECE |
|---|---|---|---|
| naive EWM(8) + PIT recalibration | 0.2137 | 0.6106 | 0.0173 |
| model, family only | 0.2089 | 0.5949 | 0.0079 |
| model + PIT recalibration | 0.2089 | 0.5951 | 0.0074 |

| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |
|---|---|---|---|---|
| (-0.001, 0.2] | 14,917 | 0.062 | 0.061 | -0.001 |
| (0.2, 0.3] | 8,825 | 0.259 | 0.268 | +0.010 |
| (0.3, 0.4] | 20,495 | 0.355 | 0.362 | +0.007 |
| (0.4, 0.45] | 13,289 | 0.425 | 0.439 | +0.014 |
| (0.45, 0.5] | 12,853 | 0.475 | 0.487 | +0.012 |
| (0.5, 0.55] | 11,600 | 0.524 | 0.524 | -0.000 |
| (0.55, 0.6] | 9,747 | 0.574 | 0.586 | +0.012 |
| (0.6, 0.7] | 9,921 | 0.641 | 0.644 | +0.003 |
| (0.7, 0.8] | 1,625 | 0.727 | 0.708 | -0.020 |
| (0.8, 1.0] | 21 | 0.809 | 0.857 | +0.048 |

| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |
|---|---|---|---|---|---|---|
| 0.00-0.02 | model more OVER than naive | 16,421 | 0.256 | 0.265 | 0.268 | 1.26 |
| 0.00-0.02 | model more UNDER than naive | 9,917 | 0.431 | 0.422 | 0.442 | -1.05 |
| 0.02-0.05 | model more OVER than naive | 14,701 | 0.387 | 0.422 | 0.423 | 1.05 |
| 0.02-0.05 | model more UNDER than naive | 12,718 | 0.461 | 0.426 | 0.438 | 0.66 |
| 0.05-0.10 | model more OVER than naive | 16,175 | 0.389 | 0.461 | 0.457 | 0.94 |
| 0.05-0.10 | model more UNDER than naive | 14,024 | 0.481 | 0.409 | 0.425 | 0.77 |
| 0.10-1.00 | model more OVER than naive | 11,023 | 0.322 | 0.467 | 0.460 | 0.96 |
| 0.10-1.00 | model more UNDER than naive | 8,314 | 0.496 | 0.359 | 0.377 | 0.86 |

### pra (n=103,293; share of rows that went over the proxy line 0.509)

| forecaster | Brier | log loss | ECE |
|---|---|---|---|
| naive EWM(8) + PIT recalibration | 0.2501 | 0.6934 | 0.0101 |
| model, family only | 0.2397 | 0.6720 | 0.0220 |
| model + PIT recalibration | 0.2392 | 0.6710 | 0.0072 |

| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |
|---|---|---|---|---|
| (-0.001, 0.2] | 525 | 0.153 | 0.158 | +0.005 |
| (0.2, 0.3] | 2,195 | 0.262 | 0.249 | -0.013 |
| (0.3, 0.4] | 10,630 | 0.363 | 0.361 | -0.002 |
| (0.4, 0.45] | 14,521 | 0.428 | 0.432 | +0.005 |
| (0.45, 0.5] | 23,099 | 0.476 | 0.480 | +0.004 |
| (0.5, 0.55] | 22,346 | 0.525 | 0.535 | +0.011 |
| (0.55, 0.6] | 14,458 | 0.572 | 0.581 | +0.009 |
| (0.6, 0.7] | 11,354 | 0.640 | 0.645 | +0.006 |
| (0.7, 0.8] | 3,337 | 0.740 | 0.730 | -0.010 |
| (0.8, 1.0] | 828 | 0.846 | 0.781 | -0.064 |

| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |
|---|---|---|---|---|---|---|
| 0.00-0.02 | model more OVER than naive | 9,732 | 0.500 | 0.510 | 0.522 | 2.18 |
| 0.00-0.02 | model more UNDER than naive | 9,808 | 0.499 | 0.489 | 0.491 | 0.81 |
| 0.02-0.05 | model more OVER than naive | 13,301 | 0.500 | 0.535 | 0.545 | 1.31 |
| 0.02-0.05 | model more UNDER than naive | 13,285 | 0.500 | 0.465 | 0.468 | 0.93 |
| 0.05-0.10 | model more OVER than naive | 14,306 | 0.500 | 0.572 | 0.582 | 1.14 |
| 0.05-0.10 | model more UNDER than naive | 14,488 | 0.500 | 0.428 | 0.434 | 0.91 |
| 0.10-1.00 | model more OVER than naive | 15,415 | 0.495 | 0.670 | 0.668 | 0.99 |
| 0.10-1.00 | model more UNDER than naive | 12,958 | 0.500 | 0.338 | 0.334 | 1.03 |

## 5. Comparison with Sports-EV-Bot's published metrics (design reference, untrusted)

How its README numbers were produced (read from source, never run): XGBoost+LightGBM ensemble, Optuna-tuned, chronological 70/30 split (not random -- fine), BUT (i) early stopping uses the TEST set as the eval set (`train.py`, `tune_train.py`); (ii) rows are filtered to ACTUAL minutes >= 10 in the target game and any row with a missing feature is dropped, which conditions on the outcome; (iii) its test period includes the 2025-26 playoffs; (iv) 'directional accuracy' is computed in `train.py` against the player's own rolling L10 MEDIAN (itself one of its input features) while the README attributes it to PrizePicks lines -- a DFS app that posts a NUMBER, not a price, and is not a sharp reference. Neither version is evidence of an edge against sportsbook prices. Our 2025-26 numbers, computed on all played rows and on its MIN>=10 filter; `tie` = share of rows where the outcome equals the L10 median, which the metric scores as 'under' and which dominates for low counts such as threes:

| stat | rows | n | our MAE | our R2 | naive EWM8 MAE | our dir-acc vs L10 median | naive EWM8 dir-acc | tie | bot MAE | bot R2 | bot dir-acc |
|---|---|---|---|---|---|---|---|---|---|---|---|
| points | all played rows | 26,134 | 4.527 | 0.531 | 4.627 | 59.0% | 57.2% | 5% | 4.77 | 0.461 | 60.2% |
| points | MIN>=10 (outcome-conditioned) | 22,393 | 4.729 | 0.457 | 4.833 | 61.7% | 60.0% | 4% | 4.77 | 0.461 | 60.2% |
| rebounds | all played rows | 26,134 | 1.868 | 0.453 | 1.907 | 57.4% | 56.0% | 12% | 1.94 | 0.411 | 57.9% |
| rebounds | MIN>=10 (outcome-conditioned) | 22,393 | 1.931 | 0.397 | 1.966 | 59.9% | 59.1% | 10% | 1.94 | 0.411 | 57.9% |
| assists | all played rows | 26,134 | 1.331 | 0.505 | 1.354 | 55.1% | 54.2% | 20% | 1.43 | 0.465 | 55.5% |
| assists | MIN>=10 (outcome-conditioned) | 22,393 | 1.415 | 0.466 | 1.436 | 58.0% | 57.4% | 16% | 1.43 | 0.465 | 55.5% |
| threes | all played rows | 26,134 | 0.886 | 0.325 | 0.891 | 46.0% | 49.6% | 36% | 0.94 | 0.24 | 54.6% |
| threes | MIN>=10 (outcome-conditioned) | 22,393 | 0.945 | 0.286 | 0.952 | 49.5% | 52.9% | 32% | 0.94 | 0.24 | 54.6% |
| pra | all played rows | 26,134 | 5.936 | 0.589 | 6.135 | 59.8% | 57.3% | 3% | 6.09 | 0.516 | 61.1% |
| pra | MIN>=10 (outcome-conditioned) | 22,393 | 6.072 | 0.505 | 6.274 | 62.5% | 60.1% | 2% | 6.09 | 0.516 | 61.1% |

## 6. What this does and does not show

**It shows**

- The pipeline is leakage-safe by construction and by test: features use only strictly-earlier ET game days, the same code path builds training and live features, and every reported number comes from a model that never saw the season it is scored on (tuning, spread, family and recalibration all come from the previous season).
- Against strong naive forecasters (EWM, L10, season mean, minutes-adjusted rate) the model has the lowest MAE of the five in the pooled sample for every stat and beats the EWM(8) baseline in every test season for every stat (0.5-2.7% lower MAE pooled); its predictive distribution scores better on CRPS and log score in the latest season with the baselines given identical spread and recalibration treatment.
- Its P(over) is calibrated near the centre after PIT recalibration, and its spread is roughly right even for rows with unstable minutes (z_rms 0.96-1.06, 80% coverage 0.76-0.89).
- Where the model disagrees with the naive forecaster, the realized over-rate mostly follows the model (`closed` near 1, noisy in the smallest gap bands), i.e. the disagreement is information, not noise -- relative to a naive history-only forecaster.

**It does NOT show**

- **Any market edge.** Books price lineups, injuries, rest/rotation news and sharp action; none of that is in a box score. A 1-3% MAE improvement over a rolling mean is the kind of gap a book's own projection already contains, and a -110/-110 market needs a 52.4% hit rate just to break even. Every MLB count model in this repo (K, OUTS, HITS, SB) came back NO_EDGE against prices (CLV, not ROI, is the go/no-go); the same prior applies here.
- **Anything about CLV or ROI.** Those need prices. The forward evaluation (`nba/props/forward.py`, criteria in the handoff) is what decides it, from banked snapshots, and the verdict stays UNDETERMINED until then.
- **Transfer to real lines from proxy lines.** The proxy line is built from the same history as the model; its 'edge' is by construction relative to a naive forecaster.
- **That the far tail is trustworthy.** Predicted P(over) >= 0.7 is overconfident for points/PRA (realized up to 6-9pp lower), so the biggest model-vs-market gaps are the ones to trust least (the winner's-curse pattern the repo has seen before).
- **Independence.** One player-game contributes five correlated stat rows, and thousands of rows share a date; the bootstrap clusters by date but the proxy-line tables are descriptive, not tests.
- **Playoffs, preseason or the 2026-27 season.** Test rows are regular season only; 2019-20 is truncated; SportsBlaze has no 2026-27 data yet, so live scoring is gated on fresh history.
- **That the setup was untouched by the test seasons.** The mean objective (Poisson vs squared error) was compared on the 2025-26 split once during development (4.532 vs 4.533 MAE for points, no difference) and the hyperparameters were hand-set, not tuned; the proxy-line definition was changed once after a first version produced implausibly strong hit-rate ladders (it anchored lines at the mean, ~4-5pp off the median for skewed counts). Both are disclosed so nobody has to discover them.
- **Sports-EV-Bot's headline.** Its MAE/R2 are in the same range as ours on the same filter, i.e. not distinguishable from a model built on the same public box scores; its 'directional accuracy' is a rolling-median proxy, not a market, and a naive EWM forecaster scores about the same on it.
