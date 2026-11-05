# Props market-residual model: generated tables, NBA (2026-11-05)

Unedited output of `python -m nba.props.residual run --report-holdout` (the held-out season is scored once; the fold lines show the
hyper-parameters picked on the season before each test season). The post-hoc section at the end comes from `scripts.props_residual_posthoc`,
which was run AFTER the held-out season was scored and selects nothing. The reading is in `props_market_residual_2026-11-05.md`.

### NBA: market-residual model, walk-forward (UNVALIDATED)

Seasons in the table: [2021, 2022, 2023, 2024, 2025]; rows by season {2021: 39153, 2022: 65902, 2023: 83108, 2024: 88110, 2025: 86504}; rows by stat {'assists': 76077, 'points': 77513, 'pra': 60719, 'rebounds': 81987, 'threes': 66481}.
Held-out season: 2025 (SCORED in this run).

Folds (hyper-parameters chosen on the season before the test season; `valid d` = paired log-loss gain of the best grid point vs the market there, negative = better):

| test season | n train | n test | ridge (lam_main, lam_stat) | gbdt (depth, rounds) | valid d ridge | valid d gbdt |
|---|---|---|---|---|---|---|
| 2023 | 105055 | 83108 | [1000000.0, 3000.0] | [1.0, 200.0] | -0.00107 | -0.00103 |
| 2024 | 188163 | 88110 | [1000000.0, 3000.0] | [2.0, 200.0] | -0.00096 | -0.00085 |
| 2025 | 276273 | 86504 | [100000.0, 30000.0] | [2.0, 60.0] | -0.00034 | -0.00045 |

#### development folds pooled (seasons the inner validation could still see)

n = 171,218 props, 422 game dates, over rate 0.483, market ECE 0.0134

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6854 | 0.6848 | -0.00059 (0.00015) | -3.97 | -0.00024 (-2.54) | +0.118% (0.030) | 0.0059 | +0.74 (+12.3) | 0.0196 |
| gbdt | 0.6854 | 0.6848 | -0.00064 (0.00015) | -4.30 | -0.00028 (-3.09) | +0.128% (0.030) | 0.0055 | +0.79 (+11.6) | 0.0187 |
| auto | 0.6854 | 0.6848 | -0.00059 (0.00015) | -3.97 | -0.00024 (-2.54) | +0.118% (0.030) | 0.0059 | +0.74 (+12.3) | 0.0196 |
| blend_c | 0.6854 | 0.6850 | -0.00040 (0.00013) | -2.96 | -0.00004 (-0.53) | +0.080% (0.027) | 0.0077 | +0.69 (+10.8) | 0.0184 |
| calib | 0.6854 | 0.6851 | -0.00035 (0.00013) | -2.81 | n/a | +0.071% (0.025) | 0.0060 | +0.71 (+9.6) | 0.0184 |

No-information controls (every prop, flat 1u): always under at med price ROI -4.24% [-4.8, -3.7]; always under at best price ROI -1.22% [-1.8, -0.6]; always over at med price ROI -8.98% [-9.6, -8.4]; always over at best price ROI -5.88% [-6.5, -5.3].

#### season 2023

n = 83,108 props, 208 game dates, over rate 0.482, market ECE 0.0153

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6857 | 0.6847 | -0.00096 (0.00022) | -4.40 | -0.00043 (-3.15) | +0.192% (0.043) | 0.0062 | +0.87 (+10.4) | 0.0202 |
| gbdt | 0.6857 | 0.6848 | -0.00085 (0.00023) | -3.75 | -0.00032 (-2.30) | +0.171% (0.045) | 0.0042 | +0.88 (+8.7) | 0.0192 |
| auto | 0.6857 | 0.6847 | -0.00096 (0.00022) | -4.40 | -0.00043 (-3.15) | +0.192% (0.043) | 0.0062 | +0.87 (+10.4) | 0.0202 |
| blend_c | 0.6857 | 0.6850 | -0.00070 (0.00020) | -3.44 | -0.00017 (-1.38) | +0.141% (0.041) | 0.0090 | +0.80 (+9.2) | 0.0195 |
| calib | 0.6857 | 0.6851 | -0.00053 (0.00018) | -2.89 | n/a | +0.107% (0.037) | 0.0056 | +0.79 (+8.0) | 0.0194 |

No-information controls (every prop, flat 1u): always under at med price ROI -3.78% [-4.7, -2.9]; always under at best price ROI -0.88% [-1.9, +0.0]; always over at med price ROI -9.30% [-10.2, -8.4]; always over at best price ROI -6.35% [-7.2, -5.4].

#### season 2024

n = 88,110 props, 214 game dates, over rate 0.484, market ECE 0.0116

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6852 | 0.6849 | -0.00024 (0.00020) | -1.23 | -0.00006 (-0.46) | +0.049% (0.040) | 0.0067 | +0.60 (+7.2) | 0.0190 |
| gbdt | 0.6852 | 0.6847 | -0.00044 (0.00019) | -2.29 | -0.00025 (-2.06) | +0.088% (0.039) | 0.0066 | +0.71 (+7.7) | 0.0182 |
| auto | 0.6852 | 0.6849 | -0.00024 (0.00020) | -1.23 | -0.00006 (-0.46) | +0.049% (0.040) | 0.0067 | +0.60 (+7.2) | 0.0190 |
| blend_c | 0.6852 | 0.6851 | -0.00011 (0.00017) | -0.64 | +0.00008 (+0.68) | +0.022% (0.035) | 0.0079 | +0.56 (+6.2) | 0.0175 |
| calib | 0.6852 | 0.6850 | -0.00019 (0.00017) | -1.09 | n/a | +0.036% (0.034) | 0.0075 | +0.62 (+5.6) | 0.0175 |

No-information controls (every prop, flat 1u): always under at med price ROI -4.67% [-5.4, -3.8]; always under at best price ROI -1.53% [-2.3, -0.6]; always over at med price ROI -8.67% [-9.6, -7.9]; always over at best price ROI -5.44% [-6.4, -4.7].

#### season 2025 (HELD OUT, scored once)

n = 86,504 props, 212 game dates, over rate 0.481, market ECE 0.0137

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6873 | 0.6868 | -0.00055 (0.00017) | -3.28 | -0.00026 (-2.55) | +0.110% (0.034) | 0.0055 | +0.83 (+8.2) | 0.0164 |
| gbdt | 0.6873 | 0.6868 | -0.00053 (0.00012) | -4.50 | -0.00023 (-2.81) | +0.105% (0.024) | 0.0065 | +1.22 (+7.6) | 0.0108 |
| auto | 0.6873 | 0.6868 | -0.00053 (0.00012) | -4.50 | -0.00023 (-2.81) | +0.105% (0.024) | 0.0065 | +1.22 (+7.6) | 0.0108 |
| blend_c | 0.6873 | 0.6866 | -0.00072 (0.00016) | -4.58 | -0.00043 (-4.32) | +0.143% (0.031) | 0.0056 | +0.97 (+9.4) | 0.0160 |
| calib | 0.6873 | 0.6870 | -0.00029 (0.00013) | -2.30 | n/a | +0.059% (0.026) | 0.0061 | +0.74 (+7.2) | 0.0156 |

No-information controls (every prop, flat 1u): always under at med price ROI -4.04% [-4.7, -3.3]; always under at best price ROI +0.76% [-0.1, +1.7]; always over at med price ROI -9.20% [-10.0, -8.5]; always over at best price ROI -4.13% [-5.0, -3.3].

##### ROI, development folds pooled, model `ridge` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 3200 | +3.15% [+0.2, +6.3] | 0.556 | -6.34% | +9.49% [+6.5, +12.6] | n/a (0) | [+0.2, +6.3] |
| med | 4-6% | 1033 | +8.83% [+3.3, +14.2] | 0.583 | -6.40% | +15.23% [+9.7, +20.6] | n/a (0) | [+3.3, +14.2] |
| med | 6-8% | 417 | +4.26% [-4.6, +12.6] | 0.561 | -6.37% | +10.62% [+1.8, +18.9] | n/a (0) | [-4.6, +12.6] |
| med | 8+% | 204 | +6.35% [-5.1, +18.2] | 0.569 | -6.44% | +12.78% [+1.2, +24.6] | n/a (0) | [-5.1, +18.2] |
| best | 2-4% | 19141 | +2.59% [+1.2, +4.0] | 0.522 | -0.31% | +2.90% [+1.5, +4.3] | +1.28% (5463) | [-1.8, +4.2] |
| best | 4-6% | 5509 | +7.78% [+5.0, +10.5] | 0.542 | +1.45% | +6.34% [+3.5, +9.1] | +5.57% (1061) | [-3.9, +8.4] |
| best | 6-8% | 1770 | +8.97% [+4.3, +14.4] | 0.545 | +2.78% | +6.18% [+1.4, +11.6] | +17.96% (357) | [-20.8, +2.4] |
| best | 8+% | 1140 | +19.54% [+12.7, +26.2] | 0.569 | +9.63% | +9.91% [+3.4, +16.1] | +26.07% (382) | [-17.9, +4.1] |
| med | all >= 2% | 4854 | +4.59% [+2.3, +7.1] | | -6.36% | +10.95% [+8.7, +13.4] | n/a (0) | [+2.3, +7.1] |
| best | all >= 2% | 27560 | +4.74% [+3.5, +6.0] | | +0.65% | +4.09% [+2.9, +5.4] | +4.03% (7263) | [-1.8, +3.2] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 27560 | +0.12% | | | | model - control +4.62% | [+3.2, +6.1] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 27560 | +3.16% | | | | model - control +1.58% | [+0.4, +2.8] |

Edge-bucket monotonicity at the best price: 3 of 3 steps up, rank correlation +1.00.
No-model control, all buckets pooled (best-quote vs consensus alone): 7263 bets at >= 2%, ROI +4.03%, market-expected +8.17%, excess -4.14%.

##### per stat, development folds pooled, model `ridge`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| assists | 34828 | -0.00017 (0.00014) | -1.24 | +0.035% | +0.76 (+3.7) | 3502 | +5.27% | -0.23% | +7.31% |
| points | 35021 | -0.00020 (0.00024) | -0.84 | +0.040% | +0.59 (+5.4) | 4307 | +1.41% | +0.69% | -8.52% |
| pra | 33623 | -0.00033 (0.00018) | -1.88 | +0.066% | +0.78 (+5.2) | 1285 | +1.63% | -0.29% | -6.27% |
| rebounds | 35369 | -0.00142 (0.00036) | -3.93 | +0.283% | +0.80 (+10.4) | 10319 | +6.60% | +7.19% | +3.10% |
| threes | 32377 | -0.00083 (0.00034) | -2.44 | +0.167% | +0.72 (+8.0) | 8147 | +4.41% | +4.49% | +7.36% |

Residual quintiles, `ridge` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 34244 | -4.21 pts | -3.24 pts |
| 2 | 34244 | -2.39 pts | -1.96 pts |
| 3 | 34244 | -1.65 pts | -1.01 pts |
| 4 | 34243 | -0.93 pts | -0.67 pts |
| 5 | 34243 | +0.39 pts | +0.61 pts |

##### ROI, development folds pooled, model `gbdt` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 618 | +7.10% [-0.3, +14.9] | 0.528 | -5.94% | +13.04% [+5.6, +20.8] | n/a (0) | [-0.3, +14.9] |
| med | 4-6% | 0 | | | | | n/a (0) | |
| med | 6-8% | 0 | | | | | n/a (0) | |
| med | 8+% | 0 | | | | | n/a (0) | |
| best | 2-4% | 20359 | +3.06% [+1.5, +4.5] | 0.525 | -0.42% | +3.49% [+1.9, +4.9] | +1.28% (5463) | [-1.6, +4.7] |
| best | 4-6% | 4718 | +6.59% [+3.5, +9.6] | 0.528 | +3.15% | +3.44% [+0.4, +6.4] | +5.57% (1061) | [-5.6, +7.5] |
| best | 6-8% | 1007 | +8.65% [+2.0, +15.1] | 0.521 | +8.09% | +0.56% [-6.3, +7.1] | +17.96% (357) | [-21.7, +2.3] |
| best | 8+% | 544 | +27.24% [+16.6, +38.8] | 0.559 | +22.10% | +5.14% [-5.0, +15.2] | +26.07% (382) | [-8.7, +9.9] |
| med | all >= 2% | 618 | +7.10% [-0.3, +14.9] | | -5.94% | +13.04% [+5.6, +20.8] | n/a (0) | [-0.3, +14.9] |
| best | all >= 2% | 26628 | +4.39% [+3.0, +5.7] | | +0.99% | +3.40% [+2.0, +4.7] | +4.03% (7263) | [-2.2, +2.9] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 26628 | +0.19% | | | | model - control +4.21% | [+2.4, +5.7] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 26628 | +3.28% | | | | model - control +1.11% | [-0.2, +2.3] |

Edge-bucket monotonicity at the best price: 3 of 3 steps up, rank correlation +1.00.
No-model control, all buckets pooled (best-quote vs consensus alone): 7263 bets at >= 2%, ROI +4.03%, market-expected +8.17%, excess -4.14%.

##### per stat, development folds pooled, model `gbdt`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| assists | 34828 | -0.00009 (0.00017) | -0.50 | +0.020% | +0.59 (+3.8) | 4518 | +4.77% | +0.60% | +7.31% |
| points | 35021 | -0.00028 (0.00024) | -1.15 | +0.056% | +0.63 (+5.7) | 4926 | +0.63% | +0.87% | -8.52% |
| pra | 33623 | -0.00034 (0.00026) | -1.32 | +0.069% | +0.64 (+6.0) | 3368 | +0.52% | +1.43% | -6.27% |
| rebounds | 35369 | -0.00152 (0.00028) | -5.36 | +0.303% | +1.03 (+10.4) | 8457 | +6.33% | +5.95% | +3.10% |
| threes | 32377 | -0.00096 (0.00026) | -3.74 | +0.195% | +0.95 (+8.0) | 5359 | +6.92% | +5.32% | +7.36% |

Residual quintiles, `gbdt` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 34244 | -4.00 pts | -3.19 pts |
| 2 | 34244 | -2.63 pts | -2.07 pts |
| 3 | 34244 | -1.58 pts | -1.59 pts |
| 4 | 34243 | -0.81 pts | +0.05 pts |
| 5 | 34243 | +0.10 pts | +0.53 pts |

##### ROI, held-out 2025, model `ridge` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 794 | -3.75% [-10.9, +2.7] | 0.524 | -5.99% | +2.24% [-5.0, +8.9] | +4.87% (12) | [-64.1, +52.5] |
| med | 4-6% | 181 | +9.08% [-4.1, +22.0] | 0.591 | -6.02% | +15.11% [+2.0, +28.3] | -100.00% (2) | [+5.0, +121.8] |
| med | 6-8% | 51 | -2.08% [-27.5, +22.1] | 0.529 | -4.99% | +2.91% [-22.9, +27.3] | n/a (0) | [-27.5, +22.1] |
| med | 8+% | 23 | +10.21% [-24.5, +44.4] | 0.565 | -4.40% | +14.61% [-20.4, +49.3] | -100.00% (1) | [-16.3, +138.8] |
| best | 2-4% | 10730 | +4.30% [+2.4, +6.1] | 0.522 | +1.85% | +2.45% [+0.6, +4.3] | +5.04% (6705) | [-3.6, +2.2] |
| best | 4-6% | 4248 | +6.56% [+3.8, +9.4] | 0.520 | +5.34% | +1.23% [-1.6, +4.1] | +6.88% (2097) | [-5.6, +5.1] |
| best | 6-8% | 1644 | +6.69% [+2.5, +10.8] | 0.509 | +9.12% | -2.43% [-6.9, +1.5] | +2.49% (696) | [-5.0, +13.0] |
| best | 8+% | 1649 | +68.33% [+49.2, +93.4] | 0.491 | +64.56% | +3.77% [-7.4, +18.6] | +85.15% (1242) | [-30.4, -8.6] |
| med | all >= 2% | 1049 | -1.15% [-7.4, +4.5] | | -5.91% | +4.76% [-1.6, +10.4] | -16.11% (15) | [-37.7, +66.0] |
| best | all >= 2% | 18271 | +10.82% [+8.0, +13.5] | | +8.98% | +1.85% [-0.2, +3.8] | +14.50% (10740) | [-6.0, -1.3] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 18271 | +7.78% | | | | model - control +3.05% | [+1.5, +4.6] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 18271 | +9.67% | | | | model - control +1.15% | [+0.1, +2.3] |

Edge-bucket monotonicity at the best price: 3 of 3 steps up, rank correlation +1.00.
No-model control, all buckets pooled (best-quote vs consensus alone): 10740 bets at >= 2%, ROI +14.50%, market-expected +16.54%, excess -2.04%.

##### per stat, held-out 2025, model `ridge`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| assists | 16186 | -0.00048 (0.00017) | -2.83 | +0.096% | +1.33 (+4.6) | 3244 | +18.04% | +0.26% | +19.09% |
| points | 19485 | +0.00004 (0.00023) | +0.15 | -0.007% | +0.47 (+2.6) | 2862 | +8.21% | -2.17% | +13.03% |
| pra | 15970 | -0.00027 (0.00023) | -1.17 | +0.054% | +0.82 (+3.0) | 1037 | -1.15% | -7.25% | +1.29% |
| rebounds | 19051 | -0.00078 (0.00039) | -2.00 | +0.157% | +0.75 (+6.1) | 7225 | +12.02% | +3.80% | +16.70% |
| threes | 15812 | -0.00137 (0.00036) | -3.85 | +0.273% | +1.10 (+7.1) | 3903 | +7.71% | +4.91% | +9.33% |

Residual quintiles, `ridge` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 17301 | -3.38 pts | -2.66 pts |
| 2 | 17301 | -1.96 pts | -1.45 pts |
| 3 | 17301 | -1.43 pts | -1.50 pts |
| 4 | 17301 | -0.96 pts | -1.34 pts |
| 5 | 17300 | +0.01 pts | +0.12 pts |

##### ROI, held-out 2025, model `gbdt` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 38 | -3.68% [-35.9, +24.4] | 0.474 | +1.95% | -5.64% [-38.6, +22.7] | +4.87% (12) | [-62.4, +46.9] |
| med | 4-6% | 5 | -16.82% [-100.0, +76.1] | 0.400 | +7.23% | -24.05% [-109.4, +68.9] | -100.00% (2) | [-4.5, +170.5] |
| med | 6-8% | 1 | -100.00% [-100.0, -100.0] | 0.000 | +12.44% | -112.44% [-112.4, -112.4] | n/a (0) | [-100.0, -100.0] |
| med | 8+% | 1 | -100.00% [-100.0, -100.0] | 0.000 | +19.79% | -119.79% [-119.8, -119.8] | -100.00% (1) | [+0.0, +0.0] |
| best | 2-4% | 8200 | +4.50% [+2.7, +6.5] | 0.511 | +3.81% | +0.69% [-1.1, +2.8] | +5.04% (6705) | [-3.2, +2.4] |
| best | 4-6% | 2991 | +5.55% [+1.9, +9.6] | 0.501 | +8.11% | -2.56% [-6.2, +1.6] | +6.88% (2097) | [-6.0, +4.1] |
| best | 6-8% | 1045 | +5.74% [-1.3, +12.9] | 0.488 | +13.00% | -7.26% [-14.3, -0.2] | +2.49% (696) | [-4.7, +11.0] |
| best | 8+% | 1320 | +81.67% [+57.9, +112.4] | 0.470 | +79.37% | +2.30% [-12.0, +19.6] | +85.15% (1242) | [-8.8, +0.6] |
| med | all >= 2% | 45 | -9.42% [-34.4, +18.0] | | +3.17% | -12.59% [-37.1, +15.1] | -16.11% (15) | [-48.7, +57.0] |
| best | all >= 2% | 13556 | +12.34% [+8.9, +15.6] | | +12.83% | -0.48% [-2.6, +1.8] | +14.50% (10740) | [-3.9, -0.3] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 13556 | +10.79% | | | | model - control +1.55% | [+0.1, +3.0] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 13556 | +12.69% | | | | model - control -0.35% | [-1.7, +0.8] |

Edge-bucket monotonicity at the best price: 3 of 3 steps up, rank correlation +1.00.
No-model control, all buckets pooled (best-quote vs consensus alone): 10740 bets at >= 2%, ROI +14.50%, market-expected +16.54%, excess -2.04%.

##### per stat, held-out 2025, model `gbdt`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| assists | 16186 | -0.00047 (0.00016) | -2.99 | +0.095% | +1.47 (+4.6) | 3226 | +18.38% | +0.36% | +19.09% |
| points | 19485 | -0.00021 (0.00017) | -1.24 | +0.043% | +0.80 (+3.3) | 2201 | +9.38% | -4.29% | +13.03% |
| pra | 15970 | -0.00025 (0.00022) | -1.13 | +0.050% | +0.82 (+2.9) | 1003 | -1.70% | -8.06% | +1.29% |
| rebounds | 19051 | -0.00084 (0.00023) | -3.68 | +0.168% | +1.33 (+5.9) | 4997 | +14.70% | +1.53% | +16.70% |
| threes | 15812 | -0.00088 (0.00019) | -4.56 | +0.175% | +1.99 (+6.1) | 2129 | +7.34% | +1.02% | +9.33% |

Residual quintiles, `gbdt` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 17301 | -2.23 pts | -2.17 pts |
| 2 | 17301 | -1.62 pts | -1.90 pts |
| 3 | 17301 | -1.01 pts | -2.27 pts |
| 4 | 17301 | -0.42 pts | -0.63 pts |
| 5 | 17300 | +0.05 pts | +0.14 pts |

Verdicts on the held-out season (all four checks must hold; bars set before this season was scored: log-loss z vs market <= -2.58, z vs the calibration-only model <= -1.96, residual-slope t >= 2.0 with b > 0, best-price >= 2% ROI above the same-number-of-bets line-shopping control with the paired CI above 0):

- ridge: CLEARS | logloss_z_vs_market=yes, logloss_z_vs_calibration=yes, slope_t=yes, roi_beats_matched_line_shopping_control=yes
- gbdt: CLEARS | logloss_z_vs_market=yes, logloss_z_vs_calibration=yes, slope_t=yes, roi_beats_matched_line_shopping_control=yes
- auto: CLEARS | logloss_z_vs_market=yes, logloss_z_vs_calibration=yes, slope_t=yes, roi_beats_matched_line_shopping_control=yes

Sharp-vs-soft skew (Novig / ProphetX only quote from the 2025 season, so this CANNOT be trained on history; exploratory):

```
{
 "n": 51832,
 "seasons": [
  2025
 ],
 "coverage": 0.5991861648016277,
 "mean_skew": -0.0142791419892905,
 "sd_skew": 0.07444295767176436,
 "corr_skew_vs_realised_gap": 0.02090933585779347,
 "cv_logloss": {
  "d": -0.0006309775781643954,
  "se": 0.00016827272479072697,
  "z": -3.7497317461823543,
  "ll_model": 0.6876372568576211,
  "ll_market": 0.6882682344357856
 },
 "cv_slope": {
  "b": 0.9275646800067141,
  "se": 0.1138870346697967,
  "t": 8.144602962892913,
  "sd_gap": 0.010390262929294415
 }
}
```


### Post-hoc robustness (scripts.props_residual_posthoc)


## development folds: n=171218

paired log loss a - b (negative = a better), date-clustered z:
      a       b        d      se        z
  ridge     mkt -0.00059 0.00015 -3.97446
  ridge    gbdt  0.00005 0.00006  0.78535
  ridge blend_c -0.00019 0.00005 -3.52794
  ridge   calib -0.00024 0.00009 -2.53912
   gbdt     mkt -0.00064 0.00015 -4.30414
   gbdt   ridge -0.00005 0.00006 -0.78535
   gbdt blend_c -0.00024 0.00007 -3.33767
   gbdt   calib -0.00028 0.00009 -3.09189
blend_c     mkt -0.00040 0.00013 -2.95883
blend_c   ridge  0.00019 0.00005  3.52794
blend_c    gbdt  0.00024 0.00007  3.33767
blend_c   calib -0.00004 0.00008 -0.53328
  calib     mkt -0.00035 0.00013 -2.81255
  calib   ridge  0.00024 0.00009  2.53912
  calib    gbdt  0.00028 0.00009  3.09189
  calib blend_c  0.00004 0.00008  0.53328

per stat, d LL vs market (z):
    stat     n           ridge            gbdt         blend_c           calib
 assists 34828 -0.00017 (-1.2) -0.00009 (-0.5) +0.00028 (+1.5) +0.00003 (+0.6)
  points 35021 -0.00020 (-0.8) -0.00028 (-1.1) -0.00024 (-1.7) -0.00007 (-0.3)
     pra 33623 -0.00033 (-1.9) -0.00034 (-1.3) -0.00029 (-2.0) -0.00025 (-1.3)
rebounds 35369 -0.00142 (-3.9) -0.00152 (-5.4) -0.00099 (-3.0) -0.00094 (-3.6)
  threes 32377 -0.00083 (-2.4) -0.00096 (-3.7) -0.00076 (-2.3) -0.00054 (-1.8)

by month, p_ridge:
  month     n        d        z
2023-10  3454 -0.00036 -0.45004
2023-11 14039 -0.00076 -1.52876
2023-12 12277 -0.00112 -2.14487
2024-01 14581 -0.00156 -2.85293
2024-02 10876 -0.00067 -1.32506
2024-03 14631 -0.00076 -1.30274
2024-04  9793 -0.00112 -1.43309
2024-05  3069 -0.00026 -0.24816
2024-10  4454  0.00050  0.61761
2024-11 13020  0.00008  0.18012
2024-12 12688 -0.00060 -1.15620
2025-01 16025 -0.00077 -1.94676
2025-02 12641  0.00002  0.03405
2025-03 16027 -0.00015 -0.31791
2025-04  9613  0.00062  0.83572
2025-05  3027 -0.00227 -1.89709

by month, p_gbdt:
  month     n        d        z
2023-10  3454  0.00000  0.00853
2023-11 14039 -0.00061 -1.10333
2023-12 12277 -0.00089 -1.68166
2024-01 14581 -0.00160 -2.87916
2024-02 10876 -0.00059 -0.98004
2024-03 14631 -0.00054 -0.98244
2024-04  9793 -0.00101 -1.22723
2024-05  3069 -0.00059 -0.50412
2024-10  4454 -0.00008 -0.10817
2024-11 13020 -0.00003 -0.07410
2024-12 12688 -0.00076 -1.47091
2025-01 16025 -0.00084 -2.04472
2025-02 12641 -0.00021 -0.38127
2025-03 16027 -0.00017 -0.36566
2025-04  9613 -0.00010 -0.15473
2025-05  3027 -0.00197 -1.71643

ROI at the MEDIAN book price by minimum model edge (flat 1u, one bet per prop):
  model  min_edge  n_bets     roi             ci  mkt_expected
  ridge      0.00   20875  0.0005   [-1.1, +1.5]       -0.0632
  ridge      0.01    9565  0.0094   [-1.3, +2.6]       -0.0634
  ridge      0.02    4854  0.0459   [+2.3, +7.0]       -0.0636
   gbdt      0.00   26330 -0.0094   [-2.3, +0.3]       -0.0637
   gbdt      0.01    7272  0.0119   [-1.4, +4.0]       -0.0629
   gbdt      0.02     618  0.0710  [-1.0, +14.6]       -0.0594
blend_c      0.00   17402 -0.0055   [-1.8, +0.9]       -0.0633
blend_c      0.01    8627 -0.0025   [-1.9, +1.6]       -0.0633
blend_c      0.02    4431  0.0350   [+0.9, +6.0]       -0.0635
  calib      0.00     931 -0.0565  [-11.3, +0.6]       -0.0533
  calib      0.01      34  0.0765 [-17.4, +34.0]       -0.0592
  calib      0.02       9  0.0894 [-46.0, +63.1]       -0.0583
    mkt      0.00       0     NaN                          NaN
    mkt      0.01       0     NaN                          NaN
    mkt      0.02       0     NaN                          NaN

## held-out 2025: n=86504

paired log loss a - b (negative = a better), date-clustered z:
      a       b        d      se        z
  ridge     mkt -0.00055 0.00017 -3.27662
  ridge    gbdt -0.00003 0.00008 -0.31762
  ridge blend_c  0.00017 0.00007  2.37375
  ridge   calib -0.00026 0.00010 -2.55274
   gbdt     mkt -0.00053 0.00012 -4.49695
   gbdt   ridge  0.00003 0.00008  0.31762
   gbdt blend_c  0.00019 0.00009  2.22887
   gbdt   calib -0.00023 0.00008 -2.80887
blend_c     mkt -0.00072 0.00016 -4.58424
blend_c   ridge -0.00017 0.00007 -2.37375
blend_c    gbdt -0.00019 0.00009 -2.22887
blend_c   calib -0.00043 0.00010 -4.31520
  calib     mkt -0.00029 0.00013 -2.30072
  calib   ridge  0.00026 0.00010  2.55274
  calib    gbdt  0.00023 0.00008  2.80887
  calib blend_c  0.00043 0.00010  4.31520

per stat, d LL vs market (z):
    stat     n           ridge            gbdt         blend_c           calib
 assists 16186 -0.00048 (-2.8) -0.00047 (-3.0) -0.00057 (-2.5) -0.00015 (-2.4)
  points 19485 +0.00004 (+0.2) -0.00021 (-1.2) -0.00032 (-2.0) -0.00026 (-1.2)
     pra 15970 -0.00027 (-1.2) -0.00025 (-1.1) -0.00029 (-1.5) -0.00025 (-1.2)
rebounds 19051 -0.00078 (-2.0) -0.00084 (-3.7) -0.00082 (-2.2) -0.00001 (-0.0)
  threes 15812 -0.00137 (-3.8) -0.00088 (-4.6) -0.00169 (-4.6) -0.00087 (-3.0)

by month, p_ridge:
  month     n        d        z
2025-10  5365  0.00007  0.12169
2025-11 15184 -0.00042 -1.16584
2025-12 13305 -0.00040 -0.90472
2026-01 16027 -0.00103 -2.58652
2026-02 11185 -0.00081 -1.39138
2026-03 14579 -0.00058 -1.61072
2026-04  7612 -0.00008 -0.12380
2026-05  2822 -0.00092 -0.91435

by month, p_gbdt:
  month     n        d        z
2025-10  5365  0.00001  0.03247
2025-11 15184 -0.00050 -1.96742
2025-12 13305 -0.00051 -1.53925
2026-01 16027 -0.00072 -2.48102
2026-02 11185 -0.00074 -2.20120
2026-03 14579 -0.00044 -1.69035
2026-04  7612 -0.00029 -0.64132
2026-05  2822 -0.00109 -1.69649

ROI at the MEDIAN book price by minimum model edge (flat 1u, one bet per prop):
  model  min_edge  n_bets     roi             ci  mkt_expected
  ridge      0.00    5131  0.0001   [-2.7, +2.4]       -0.0605
  ridge      0.01    2193  0.0001   [-4.1, +3.8]       -0.0599
  ridge      0.02    1049 -0.0115   [-6.9, +4.6]       -0.0591
   gbdt      0.00     370 -0.0190  [-10.8, +6.9]       -0.0203
   gbdt      0.01      99  0.0373 [-13.6, +17.9]        0.0114
   gbdt      0.02      45 -0.0942 [-33.8, +15.2]        0.0317
blend_c      0.00    5311  0.0240   [-0.3, +5.4]       -0.0612
blend_c      0.01    2423  0.0219   [-2.3, +6.3]       -0.0604
blend_c      0.02    1247  0.0135   [-5.2, +7.4]       -0.0601
  calib      0.00     525 -0.0578  [-13.8, +1.6]       -0.0240
  calib      0.01     142 -0.1708  [-29.0, -4.0]        0.0016
  calib      0.02      54 -0.2705  [-48.6, -4.3]        0.0240
    mkt      0.00     107 -0.0791  [-23.6, +7.3]        0.0216
    mkt      0.01      41  0.1516 [-10.5, +46.2]        0.0459
    mkt      0.02      15 -0.1611 [-64.3, +24.9]        0.0761
