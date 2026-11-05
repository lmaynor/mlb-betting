# Props market-residual model: generated tables, NFL (2026-11-05)

Unedited output of `python -m nfl.props.residual run --report-holdout` (the held-out season 2025 is scored once; 2026 is the partial season
after it, weeks 1-5 at the time of writing). The post-hoc section at the end comes from `scripts.props_residual_posthoc`, run AFTER the
held-out season was scored. The reading is in `props_market_residual_2026-11-05.md`.

### NFL: market-residual model, walk-forward (UNVALIDATED)

Seasons in the table: [2021, 2022, 2023, 2024, 2025, 2026]; rows by season {2021: 4379, 2022: 4466, 2023: 5994, 2024: 6798, 2025: 8632, 2026: 3165}; rows by stat {'interceptions': 1454, 'pass_attempts': 1265, 'pass_completions': 1680, 'pass_tds': 1794, 'pass_yds': 1301, 'rec_yds': 9283, 'receptions': 9130, 'rush_attempts': 3080, 'rush_yds': 4447}.
Held-out season: 2025 (SCORED in this run).

Folds (hyper-parameters chosen on the season before the test season; `valid d` = paired log-loss gain of the best grid point vs the market there, negative = better):

| test season | n train | n test | ridge (lam_main, lam_stat) | gbdt (depth, rounds) | valid d ridge | valid d gbdt |
|---|---|---|---|---|---|---|
| 2023 | 8845 | 5994 | [1000000.0, 300000.0] | [2.0, 60.0] | -0.00072 | -0.00169 |
| 2024 | 14839 | 6798 | [100000.0, 30000.0] | [2.0, 60.0] | +0.00290 | +0.00124 |
| 2025 | 21637 | 8632 | [1000000.0, 300000.0] | [2.0, 60.0] | +0.00009 | -0.00032 |
| 2026 | 30269 | 3165 | [1000000.0, 3000.0] | [2.0, 60.0] | -0.00032 | -0.00014 |

#### development folds pooled (seasons the inner validation could still see)

n = 12,792 props, 114 game dates, over rate 0.493, market ECE 0.0165

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6876 | 0.6890 | +0.00143 (0.00076) | +1.88 | -0.00187 (-3.02) | -0.288% (0.153) | 0.0240 | +0.08 (+0.4) | 0.0280 |
| gbdt | 0.6876 | 0.6880 | +0.00041 (0.00060) | +0.69 | -0.00288 (-4.49) | -0.084% (0.120) | 0.0150 | +0.28 (+0.9) | 0.0188 |
| auto | 0.6876 | 0.6882 | +0.00058 (0.00046) | +1.27 | -0.00272 (-3.48) | -0.117% (0.092) | 0.0162 | +0.05 (+0.1) | 0.0109 |
| blend_c | 0.6876 | 0.6901 | +0.00245 (0.00090) | +2.72 | -0.00085 (-1.11) | -0.477% (0.178) | 0.0243 | +0.00 (+0.0) | 0.0294 |
| calib | 0.6876 | 0.6909 | +0.00330 (0.00091) | +3.62 | n/a | -0.582% (0.163) | 0.0263 | +0.04 (+0.3) | 0.0334 |

No-information controls (every prop, flat 1u): always under at med price ROI -5.64% [-8.2, -3.3]; always under at best price ROI -3.09% [-5.7, -0.7]; always over at med price ROI -7.16% [-9.6, -4.6]; always over at best price ROI -4.42% [-6.9, -1.8].

#### season 2023

n = 5,994 props, 56 game dates, over rate 0.500, market ECE 0.0125

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6883 | 0.6913 | +0.00294 (0.00129) | +2.29 | -0.00114 (-1.19) | -0.587% (0.257) | 0.0382 | -0.05 (-0.2) | 0.0362 |
| gbdt | 0.6883 | 0.6896 | +0.00124 (0.00099) | +1.26 | -0.00284 (-3.20) | -0.248% (0.198) | 0.0263 | +0.05 (+0.1) | 0.0233 |
| auto | 0.6883 | 0.6896 | +0.00124 (0.00099) | +1.26 | -0.00284 (-3.20) | -0.248% (0.198) | 0.0263 | +0.05 (+0.1) | 0.0233 |
| blend_c | 0.6883 | 0.6933 | +0.00496 (0.00161) | +3.08 | +0.00088 (+0.68) | -0.959% (0.316) | 0.0422 | -0.13 (-0.6) | 0.0377 |
| calib | 0.6883 | 0.6924 | +0.00408 (0.00134) | +3.05 | n/a | -0.811% (0.268) | 0.0349 | +0.04 (+0.3) | 0.0414 |

No-information controls (every prop, flat 1u): always under at med price ROI -6.77% [-10.1, -3.4]; always under at best price ROI -4.23% [-7.6, -0.8]; always over at med price ROI -5.97% [-9.0, -2.5]; always over at best price ROI -3.20% [-6.3, +0.4].

#### season 2024

n = 6,798 props, 58 game dates, over rate 0.487, market ECE 0.0286

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6870 | 0.6871 | +0.00010 (0.00086) | +0.12 | -0.00251 (-3.15) | -0.023% (0.173) | 0.0186 | +0.44 (+0.9) | 0.0207 |
| gbdt | 0.6870 | 0.6866 | -0.00032 (0.00070) | -0.45 | -0.00293 (-3.16) | +0.062% (0.142) | 0.0228 | +0.76 (+1.3) | 0.0149 |
| auto | 0.6870 | 0.6870 | +0.00000 (0.00000) | +nan | -0.00261 (-2.08) | +0.000% (0.000) | 0.0286 | +nan (+nan) | 0.0000 |
| blend_c | 0.6870 | 0.6872 | +0.00024 (0.00082) | +0.29 | -0.00237 (-2.97) | -0.051% (0.165) | 0.0192 | +0.39 (+1.1) | 0.0220 |
| calib | 0.6870 | 0.6896 | +0.00261 (0.00125) | +2.08 | n/a | -0.380% (0.194) | 0.0220 | +0.04 (+0.2) | 0.0264 |

No-information controls (every prop, flat 1u): always under at med price ROI -4.65% [-8.3, -0.4]; always under at best price ROI -2.08% [-5.8, +2.4]; always over at med price ROI -8.21% [-12.7, -4.5]; always over at best price ROI -5.49% [-10.1, -1.6].

#### season 2025 (HELD OUT, scored once)

n = 8,632 props, 57 game dates, over rate 0.487, market ECE 0.0100

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6886 | 0.6886 | -0.00001 (0.00042) | -0.01 | -0.00031 (-0.93) | +0.004% (0.085) | 0.0146 | +0.52 (+1.4) | 0.0171 |
| gbdt | 0.6886 | 0.6885 | -0.00014 (0.00028) | -0.50 | -0.00045 (-1.12) | +0.029% (0.056) | 0.0143 | +0.74 (+1.6) | 0.0103 |
| auto | 0.6886 | 0.6885 | -0.00014 (0.00028) | -0.50 | -0.00045 (-1.12) | +0.029% (0.056) | 0.0143 | +0.74 (+1.6) | 0.0103 |
| blend_c | 0.6886 | 0.6883 | -0.00032 (0.00045) | -0.70 | -0.00062 (-1.52) | +0.068% (0.090) | 0.0151 | +0.68 (+2.8) | 0.0181 |
| calib | 0.6886 | 0.6889 | +0.00030 (0.00056) | +0.55 | n/a | -0.053% (0.109) | 0.0111 | +0.37 (+1.4) | 0.0198 |

No-information controls (every prop, flat 1u): always under at med price ROI -5.08% [-7.3, -2.9]; always under at best price ROI -1.05% [-3.3, +1.3]; always over at med price ROI -7.99% [-10.4, -5.5]; always over at best price ROI -4.09% [-6.6, -1.5].

#### season 2026

n = 3,165 props, 16 game dates, over rate 0.486, market ECE 0.0177

| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |
|---|---|---|---|---|---|---|---|---|---|
| ridge | 0.6890 | 0.6890 | +0.00005 (0.00110) | +0.05 | +0.00070 (+1.17) | -0.011% (0.222) | 0.0138 | +0.47 (+0.7) | 0.0164 |
| gbdt | 0.6890 | 0.6894 | +0.00046 (0.00048) | +0.95 | +0.00110 (+2.11) | -0.092% (0.097) | 0.0134 | -0.32 (-0.4) | 0.0099 |
| auto | 0.6890 | 0.6890 | +0.00005 (0.00110) | +0.05 | +0.00070 (+1.17) | -0.011% (0.222) | 0.0138 | +0.47 (+0.7) | 0.0164 |
| blend_c | 0.6890 | 0.6893 | +0.00034 (0.00114) | +0.29 | +0.00098 (+1.53) | -0.069% (0.231) | 0.0261 | +0.31 (+0.5) | 0.0174 |
| calib | 0.6890 | 0.6883 | -0.00065 (0.00076) | -0.85 | n/a | +0.120% (0.150) | 0.0200 | +0.75 (+2.6) | 0.0173 |

No-information controls (every prop, flat 1u): always under at med price ROI -4.26% [-8.1, -0.2]; always under at best price ROI -0.26% [-4.2, +3.9]; always over at med price ROI -7.77% [-12.5, -3.5]; always over at best price ROI -3.07% [-8.0, +1.5].

##### ROI, development folds pooled, model `ridge` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 8 | -2.18% [-100.0, +56.5] | 0.500 | -6.39% | +4.20% [-93.5, +62.7] | n/a (0) | [-100.0, +56.5] |
| med | 4-6% | 4 | +44.01% [-51.6, +94.5] | 0.750 | -6.31% | +50.33% [-45.1, +100.5] | n/a (0) | [-51.6, +94.5] |
| med | 6-8% | 0 | | | | | n/a (0) | |
| med | 8+% | 0 | | | | | n/a (0) | |
| best | 2-4% | 1922 | -1.24% [-6.9, +4.6] | 0.491 | -0.88% | -0.36% [-6.0, +5.4] | +4.16% (274) | [-22.4, +11.0] |
| best | 4-6% | 355 | +0.60% [-8.0, +9.6] | 0.482 | +2.89% | -2.29% [-11.0, +6.7] | +20.24% (47) | [-54.0, +15.9] |
| best | 6-8% | 61 | -1.57% [-30.8, +25.0] | 0.459 | +8.23% | -9.80% [-39.1, +16.6] | +22.76% (18) | [-76.8, +33.1] |
| best | 8+% | 24 | +0.23% [-40.6, +43.6] | 0.458 | +122.54% | -122.32% [-342.5, +25.0] | -16.80% (11) | [-39.4, +76.7] |
| med | all >= 2% | 12 | +13.21% [-43.1, +53.7] | | -6.36% | +19.58% [-36.4, +60.0] | n/a (0) | [-43.1, +53.7] |
| best | all >= 2% | 2362 | -0.96% [-6.5, +4.3] | | +1.17% | -2.13% [-8.0, +3.5] | +6.61% (350) | [-20.5, +4.9] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 2362 | +1.47% | | | | model - control -2.43% | [-8.4, +3.6] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 2362 | -5.58% | | | | model - control +4.62% | [+1.2, +8.1] |

Edge-bucket monotonicity at the best price: 1 of 2 steps up, rank correlation -0.50.
No-model control, all buckets pooled (best-quote vs consensus alone): 350 bets at >= 2%, ROI +6.61%, market-expected +14.64%, excess -8.03%.

##### per stat, development folds pooled, model `ridge`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| interceptions | 621 | -0.00013 (0.00266) | -0.05 | +0.041% | +0.57 (+0.7) | 115 | -1.21% | -18.09% | -39.33% |
| pass_attempts | 612 | +0.00168 (0.00244) | +0.69 | -0.334% | -0.03 (-0.0) | 87 | -2.62% | -1.75% | -33.33% |
| pass_completions | 625 | +0.00383 (0.00223) | +1.71 | -0.770% | -0.75 (-1.1) | 122 | +0.28% | +0.43% | -9.21% |
| pass_tds | 641 | +0.00378 (0.00215) | +1.75 | -0.762% | -0.62 (-0.9) | 221 | -8.90% | -11.86% | +13.94% |
| pass_yds | 416 | +0.00504 (0.00316) | +1.59 | -1.005% | -0.77 (-1.0) | 11 | +9.73% | +9.78% | -47.50% |
| rec_yds | 3367 | +0.00284 (0.00101) | +2.81 | -0.567% | -0.28 (-1.0) | 289 | -8.98% | -8.40% | +6.35% |
| receptions | 3508 | +0.00151 (0.00107) | +1.42 | -0.306% | +0.04 (+0.1) | 1014 | -0.98% | -1.48% | +9.67% |
| rush_attempts | 1354 | -0.00237 (0.00134) | -1.77 | +0.474% | +1.17 (+3.1) | 332 | +7.15% | +7.07% | +7.27% |
| rush_yds | 1648 | -0.00070 (0.00150) | -0.47 | +0.138% | +0.69 (+1.6) | 171 | +6.69% | +7.27% | +22.40% |

Residual quintiles, `ridge` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 2559 | -3.70 pts | -0.63 pts |
| 2 | 2559 | -3.62 pts | +0.63 pts |
| 3 | 2558 | -2.80 pts | -0.48 pts |
| 4 | 2558 | -2.15 pts | +0.77 pts |
| 5 | 2558 | -1.68 pts | -2.27 pts |

##### ROI, development folds pooled, model `gbdt` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 0 | | | | | n/a (0) | |
| med | 4-6% | 0 | | | | | n/a (0) | |
| med | 6-8% | 0 | | | | | n/a (0) | |
| med | 8+% | 0 | | | | | n/a (0) | |
| best | 2-4% | 982 | -2.90% [-11.9, +5.0] | 0.466 | +0.50% | -3.40% [-12.2, +4.5] | +4.16% (274) | [-23.3, +9.0] |
| best | 4-6% | 154 | +4.73% [-8.9, +17.8] | 0.487 | +4.69% | +0.04% [-13.5, +13.1] | +20.24% (47) | [-47.1, +14.2] |
| best | 6-8% | 29 | -30.90% [-72.0, +8.8] | 0.310 | +10.87% | -41.77% [-83.2, -1.8] | +22.76% (18) | [-108.0, -2.2] |
| best | 8+% | 16 | +17.14% [-33.5, +81.5] | 0.500 | +178.67% | -161.53% [-434.1, +57.8] | -16.80% (11) | [-21.9, +106.6] |
| best | all >= 2% | 1181 | -2.32% [-9.5, +3.6] | | +3.72% | -6.04% [-13.5, +1.0] | +6.61% (350) | [-22.3, +3.1] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 1181 | +3.00% | | | | model - control -5.32% | [-13.4, +2.7] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 1181 | -3.12% | | | | model - control +0.80% | [-5.4, +7.8] |

Edge-bucket monotonicity at the best price: not computable (fewer than 3 buckets with >= 30 bets).
No-model control, all buckets pooled (best-quote vs consensus alone): 350 bets at >= 2%, ROI +6.61%, market-expected +14.64%, excess -8.03%.

##### per stat, development folds pooled, model `gbdt`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| interceptions | 621 | -0.00165 (0.00168) | -0.98 | +0.330% | +1.79 (+1.4) | 40 | -9.68% | -59.64% | -39.33% |
| pass_attempts | 612 | -0.00074 (0.00195) | -0.38 | +0.149% | +0.84 (+0.9) | 52 | +6.87% | +7.01% | -33.33% |
| pass_completions | 625 | +0.00336 (0.00181) | +1.85 | -0.676% | -1.28 (-1.4) | 72 | -12.60% | -13.57% | -9.21% |
| pass_tds | 641 | +0.00190 (0.00140) | +1.36 | -0.387% | -1.07 (-0.9) | 90 | -10.37% | -19.14% | +13.94% |
| pass_yds | 416 | +0.00459 (0.00307) | +1.49 | -0.915% | -0.79 (-0.9) | 6 | +2.01% | +0.08% | -47.50% |
| rec_yds | 3367 | +0.00122 (0.00078) | +1.56 | -0.244% | -0.02 (-0.1) | 120 | -9.52% | -10.09% | +6.35% |
| receptions | 3508 | +0.00048 (0.00069) | +0.70 | -0.100% | +0.14 (+0.3) | 551 | -0.68% | -2.77% | +9.67% |
| rush_attempts | 1354 | -0.00198 (0.00094) | -2.10 | +0.397% | +1.65 (+3.0) | 178 | +2.59% | +1.35% | +7.27% |
| rush_yds | 1648 | -0.00095 (0.00129) | -0.74 | +0.192% | +0.92 (+1.6) | 72 | +2.37% | +1.18% | +22.40% |

Residual quintiles, `gbdt` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 2559 | -3.63 pts | -1.51 pts |
| 2 | 2559 | -2.36 pts | -0.27 pts |
| 3 | 2558 | -1.74 pts | -0.27 pts |
| 4 | 2558 | -1.17 pts | +0.23 pts |
| 5 | 2558 | -0.34 pts | -0.17 pts |

##### ROI, held-out 2025, model `ridge` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 8 | -24.76% [-80.8, +50.5] | 0.375 | +2.12% | -26.88% [-82.8, +48.3] | +76.92% (1) | [-154.8, +29.0] |
| med | 4-6% | 1 | +76.92% [+76.9, +76.9] | 1.000 | +6.09% | +70.83% [+70.8, +70.8] | n/a (0) | [+76.9, +76.9] |
| med | 6-8% | 0 | | | | | n/a (0) | |
| med | 8+% | 0 | | | | | n/a (0) | |
| best | 2-4% | 978 | +2.02% [-4.0, +8.0] | 0.492 | +3.48% | -1.46% [-7.5, +4.5] | -1.26% (828) | [-5.2, +12.3] |
| best | 4-6% | 446 | +1.40% [-9.6, +12.8] | 0.482 | +7.44% | -6.04% [-17.1, +5.4] | +16.22% (281) | [-29.0, +1.2] |
| best | 6-8% | 156 | +22.44% [+6.4, +38.7] | 0.564 | +12.29% | +10.15% [-5.7, +26.6] | -9.33% (86) | [+8.3, +52.6] |
| best | 8+% | 72 | +26.32% [-4.7, +53.4] | 0.556 | +24.07% | +2.25% [-28.6, +30.7] | +30.86% (65) | [-26.8, +14.5] |
| med | all >= 2% | 9 | -13.46% [-77.2, +53.4] | | +2.56% | -16.02% [-79.1, +50.0] | +76.92% (1) | [-137.6, +29.0] |
| best | all >= 2% | 1652 | +4.84% [-0.0, +9.6] | | +6.28% | -1.44% [-6.5, +3.5] | +3.75% (1260) | [-4.7, +7.5] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 1652 | +3.13% | | | | model - control +1.71% | [-3.4, +7.4] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 1652 | +5.32% | | | | model - control -0.48% | [-4.3, +4.1] |

Edge-bucket monotonicity at the best price: 2 of 3 steps up, rank correlation +0.80.
No-model control, all buckets pooled (best-quote vs consensus alone): 1260 bets at >= 2%, ROI +3.75%, market-expected +8.81%, excess -5.06%.

##### per stat, held-out 2025, model `ridge`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| interceptions | 419 | -0.00169 (0.00148) | -1.14 | +0.343% | +1.95 (+1.6) | 104 | +9.28% | +4.39% | -13.06% |
| pass_attempts | 409 | -0.00120 (0.00113) | -1.06 | +0.240% | +1.57 (+1.6) | 79 | +1.32% | -3.76% | +16.02% |
| pass_completions | 415 | -0.00007 (0.00146) | -0.04 | +0.013% | +0.56 (+0.4) | 86 | +2.49% | -1.66% | -4.99% |
| pass_tds | 425 | +0.00141 (0.00175) | +0.80 | -0.276% | -0.76 (-0.5) | 110 | -2.42% | -7.40% | +8.43% |
| pass_yds | 361 | +0.00237 (0.00147) | +1.62 | -0.474% | -1.58 (-1.2) | 12 | +25.00% | +17.18% | +7.19% |
| rec_yds | 2264 | +0.00067 (0.00076) | +0.88 | -0.135% | -0.06 (-0.1) | 108 | -10.36% | -17.42% | -7.16% |
| receptions | 2292 | -0.00008 (0.00067) | -0.12 | +0.019% | +0.58 (+1.0) | 815 | +5.85% | -1.20% | +4.64% |
| rush_attempts | 877 | -0.00074 (0.00136) | -0.54 | +0.150% | +1.07 (+1.0) | 232 | +6.88% | +1.35% | +5.54% |
| rush_yds | 1170 | -0.00082 (0.00071) | -1.16 | +0.173% | +1.21 (+2.1) | 106 | +13.51% | +7.16% | +13.05% |

Residual quintiles, `ridge` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 1727 | -1.82 pts | -2.95 pts |
| 2 | 1727 | -1.73 pts | +0.00 pts |
| 3 | 1726 | -1.72 pts | -3.01 pts |
| 4 | 1726 | -1.69 pts | -1.07 pts |
| 5 | 1726 | -1.60 pts | +3.06 pts |

##### ROI, held-out 2025, model `gbdt` (flat 1u, one bet per prop, edge = model P(side) - implied price)

| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |
|---|---|---|---|---|---|---|---|---|
| med | 2-4% | 6 | +76.40% [-15.1, +114.3] | 0.833 | +3.77% | +72.63% [-18.5, +110.3] | +76.92% (1) | [-37.2, +111.6] |
| med | 4-6% | 0 | | | | | n/a (0) | |
| med | 6-8% | 0 | | | | | n/a (0) | |
| med | 8+% | 0 | | | | | n/a (0) | |
| best | 2-4% | 856 | -2.92% [-8.4, +2.4] | 0.464 | +5.13% | -8.05% [-13.4, -2.8] | -1.26% (828) | [-8.2, +4.8] |
| best | 4-6% | 326 | +7.42% [-6.4, +22.3] | 0.497 | +9.22% | -1.80% [-15.6, +13.0] | +16.22% (281) | [-20.3, +2.6] |
| best | 6-8% | 116 | +3.56% [-15.2, +23.6] | 0.466 | +13.78% | -10.22% [-29.0, +9.8] | -9.33% (86) | [-11.1, +35.2] |
| best | 8+% | 71 | +31.34% [+0.4, +61.2] | 0.577 | +25.05% | +6.28% [-25.2, +36.3] | +30.86% (65) | [-15.3, +18.1] |
| med | all >= 2% | 6 | +76.40% [-15.1, +114.3] | | +3.77% | +72.63% [-18.5, +110.3] | +76.92% (1) | [-37.2, +111.6] |
| best | all >= 2% | 1369 | +1.87% [-3.6, +7.4] | | +7.87% | -6.00% [-11.6, -0.5] | +3.75% (1260) | [-6.6, +2.8] |
| best | >= 2%, vs `market` control with the SAME number of bets (top-K by its own edge) | 1369 | +3.39% | | | | model - control -1.52% | [-5.7, +3.3] |
| best | >= 2%, vs `calibration` control with the SAME number of bets (top-K by its own edge) | 1369 | +5.49% | | | | model - control -3.62% | [-10.8, +4.0] |

Edge-bucket monotonicity at the best price: 2 of 3 steps up, rank correlation +0.80.
No-model control, all buckets pooled (best-quote vs consensus alone): 1260 bets at >= 2%, ROI +3.75%, market-expected +8.81%, excess -5.06%.

##### per stat, held-out 2025, model `gbdt`

| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |
|---|---|---|---|---|---|---|---|---|---|
| interceptions | 419 | -0.00061 (0.00065) | -0.93 | +0.119% | +2.28 (+1.2) | 71 | -11.75% | -18.84% | -13.06% |
| pass_attempts | 409 | -0.00036 (0.00091) | -0.39 | +0.071% | +1.16 (+0.7) | 58 | +9.66% | +3.32% | +16.02% |
| pass_completions | 415 | +0.00046 (0.00079) | +0.58 | -0.092% | -0.53 (-0.3) | 62 | -6.55% | -11.97% | -4.99% |
| pass_tds | 425 | +0.00021 (0.00086) | +0.24 | -0.038% | -0.28 (-0.1) | 76 | +8.43% | +1.35% | +8.43% |
| pass_yds | 361 | +0.00264 (0.00133) | +1.98 | -0.527% | -2.02 (-1.6) | 13 | +15.38% | +7.91% | +7.19% |
| rec_yds | 2264 | +0.00010 (0.00054) | +0.18 | -0.019% | +0.38 (+0.6) | 107 | -15.10% | -22.41% | -7.16% |
| receptions | 2292 | -0.00024 (0.00038) | -0.63 | +0.048% | +1.10 (+1.1) | 710 | +2.51% | -6.15% | +4.64% |
| rush_attempts | 877 | -0.00055 (0.00074) | -0.73 | +0.110% | +1.35 (+1.2) | 179 | +6.90% | -0.44% | +5.54% |
| rush_yds | 1170 | -0.00105 (0.00065) | -1.61 | +0.221% | +1.75 (+2.4) | 93 | +10.68% | +3.34% | +13.05% |

Residual quintiles, `gbdt` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)

| quintile | n | mean model gap | realised gap |
|---|---|---|---|
| 1 | 1727 | -2.09 pts | -0.79 pts |
| 2 | 1727 | -1.36 pts | -1.64 pts |
| 3 | 1726 | -0.92 pts | -1.25 pts |
| 4 | 1726 | -0.48 pts | -0.38 pts |
| 5 | 1726 | +0.22 pts | +0.08 pts |

Verdicts on the held-out season (all four checks must hold; bars set before this season was scored: log-loss z vs market <= -2.58, z vs the calibration-only model <= -1.96, residual-slope t >= 2.0 with b > 0, best-price >= 2% ROI above the same-number-of-bets line-shopping control with the paired CI above 0):

- ridge: does not clear | logloss_z_vs_market=no, logloss_z_vs_calibration=no, slope_t=no, roi_beats_matched_line_shopping_control=no
- gbdt: does not clear | logloss_z_vs_market=no, logloss_z_vs_calibration=no, slope_t=no, roi_beats_matched_line_shopping_control=no
- auto: does not clear | logloss_z_vs_market=no, logloss_z_vs_calibration=no, slope_t=no, roi_beats_matched_line_shopping_control=no

Sharp-vs-soft skew (Novig / ProphetX only quote from the 2025 season, so this CANNOT be trained on history; exploratory):

```
{
 "n": 9218,
 "seasons": [
  2025,
  2026
 ],
 "coverage": 0.7813850979062473,
 "mean_skew": -0.003982353643205915,
 "sd_skew": 0.06677771910227775,
 "corr_skew_vs_realised_gap": 0.015089767501047529,
 "cv_logloss": {
  "d": -2.0824777748407573e-05,
  "se": 0.0002839211349644447,
  "z": -0.07334705023285938,
  "ll_model": 0.6886935700713273,
  "ll_market": 0.6887143948490757
 },
 "cv_slope": {
  "b": 0.5480633173878465,
  "se": 0.6176972035136539,
  "t": 0.8872685747487472,
  "sd_gap": 0.0086498298328043
 }
}
```


### Post-hoc robustness (scripts.props_residual_posthoc)


## development folds: n=12792

paired log loss a - b (negative = a better), date-clustered z:
      a       b        d      se        z
  ridge     mkt  0.00143 0.00076  1.87670
  ridge    gbdt  0.00102 0.00027  3.76563
  ridge blend_c -0.00102 0.00038 -2.65106
  ridge   calib -0.00187 0.00062 -3.02312
   gbdt     mkt  0.00041 0.00060  0.69168
   gbdt   ridge -0.00102 0.00027 -3.76563
   gbdt blend_c -0.00204 0.00051 -4.01502
   gbdt   calib -0.00288 0.00064 -4.49439
blend_c     mkt  0.00245 0.00090  2.72159
blend_c   ridge  0.00102 0.00038  2.65106
blend_c    gbdt  0.00204 0.00051  4.01502
blend_c   calib -0.00085 0.00076 -1.11230
  calib     mkt  0.00330 0.00091  3.61907
  calib   ridge  0.00187 0.00062  3.02312
  calib    gbdt  0.00288 0.00064  4.49439
  calib blend_c  0.00085 0.00076  1.11230

per stat, d LL vs market (z):
            stat    n           ridge            gbdt         blend_c           calib
   interceptions  621 -0.00013 (-0.1) -0.00165 (-1.0) +0.00061 (+0.3) +0.00193 (+0.4)
   pass_attempts  612 +0.00168 (+0.7) -0.00074 (-0.4) +0.01512 (+2.2) +0.00920 (+2.2)
pass_completions  625 +0.00383 (+1.7) +0.00336 (+1.9) +0.00422 (+1.8) -0.00081 (-0.5)
        pass_tds  641 +0.00378 (+1.8) +0.00190 (+1.4) +0.00385 (+2.0) +0.00397 (+1.9)
        pass_yds  416 +0.00504 (+1.6) +0.00459 (+1.5) +0.00391 (+1.7) +0.00996 (+1.7)
         rec_yds 3367 +0.00284 (+2.8) +0.00122 (+1.6) +0.00360 (+3.1) +0.00406 (+3.1)
      receptions 3508 +0.00151 (+1.4) +0.00048 (+0.7) +0.00241 (+1.8) +0.00257 (+2.2)
   rush_attempts 1354 -0.00237 (-1.8) -0.00198 (-2.1) -0.00337 (-2.1) +0.00003 (+0.0)
        rush_yds 1648 -0.00070 (-0.5) -0.00095 (-0.7) -0.00061 (-0.5) +0.00392 (+1.1)

by month, p_ridge:
  month    n        d        z
2023-10 1620  0.00351  1.60887
2023-12 1792  0.00100  0.41249
2024-09 1501 -0.00054 -0.36414
2024-10 1626  0.00007  0.02354
2024-11 1572  0.00063  0.57850
2024-12 1853  0.00034  0.23273

by month, p_gbdt:
  month    n        d        z
2023-10 1620  0.00203  1.19723
2023-12 1792 -0.00072 -0.42272
2024-09 1501 -0.00095 -0.64034
2024-10 1626 -0.00041 -0.18901
2024-11 1572 -0.00041 -0.46708
2024-12 1853  0.00017  0.14711

ROI at the MEDIAN book price by minimum model edge (flat 1u, one bet per prop):
  model  min_edge  n_bets     roi             ci  mkt_expected
  ridge      0.00    4928 -0.0611   [-9.3, -2.5]       -0.0623
  ridge      0.01     371 -0.1151  [-22.4, -0.1]       -0.0516
  ridge      0.02      12  0.1321 [-38.9, +50.2]       -0.0636
   gbdt      0.00    1917 -0.0676  [-12.7, -1.2]       -0.0623
   gbdt      0.01     205  0.0478 [-13.3, +23.2]       -0.0589
   gbdt      0.02       0     NaN                          NaN
blend_c      0.00    3820 -0.0706  [-10.6, -3.5]       -0.0620
blend_c      0.01    1582 -0.0625  [-11.8, +0.1]       -0.0618
blend_c      0.02     713 -0.0643  [-12.9, +0.5]       -0.0622
  calib      0.00    5578 -0.0611   [-9.3, -3.2]       -0.0620
  calib      0.01    2953 -0.0707  [-11.0, -3.2]       -0.0613
  calib      0.02    1672 -0.0869  [-13.9, -3.0]       -0.0630
    mkt      0.00       0     NaN                          NaN
    mkt      0.01       0     NaN                          NaN
    mkt      0.02       0     NaN                          NaN

## held-out 2025: n=8632

paired log loss a - b (negative = a better), date-clustered z:
      a       b        d      se        z
  ridge     mkt -0.00001 0.00042 -0.01205
  ridge    gbdt  0.00014 0.00026  0.51977
  ridge blend_c  0.00031 0.00029  1.08696
  ridge   calib -0.00031 0.00033 -0.93265
   gbdt     mkt -0.00014 0.00028 -0.50305
   gbdt   ridge -0.00014 0.00026 -0.51977
   gbdt blend_c  0.00018 0.00037  0.47830
   gbdt   calib -0.00045 0.00040 -1.12128
blend_c     mkt -0.00032 0.00045 -0.69760
blend_c   ridge -0.00031 0.00029 -1.08696
blend_c    gbdt -0.00018 0.00037 -0.47830
blend_c   calib -0.00062 0.00041 -1.52330
  calib     mkt  0.00030 0.00056  0.54753
  calib   ridge  0.00031 0.00033  0.93265
  calib    gbdt  0.00045 0.00040  1.12128
  calib blend_c  0.00062 0.00041  1.52330

per stat, d LL vs market (z):
            stat    n           ridge            gbdt         blend_c           calib
   interceptions  419 -0.00169 (-1.1) -0.00061 (-0.9) -0.00153 (-1.1) -0.00243 (-0.7)
   pass_attempts  409 -0.00120 (-1.1) -0.00036 (-0.4) +0.00013 (+0.1) +0.00068 (+0.6)
pass_completions  415 -0.00007 (-0.0) +0.00046 (+0.6) -0.00218 (-1.7) +0.00205 (+0.9)
        pass_tds  425 +0.00141 (+0.8) +0.00021 (+0.2) +0.00167 (+1.0) +0.00017 (+0.5)
        pass_yds  361 +0.00237 (+1.6) +0.00264 (+2.0) -0.00086 (-0.7) +0.00291 (+0.8)
         rec_yds 2264 +0.00067 (+0.9) +0.00010 (+0.2) +0.00056 (+0.8) +0.00054 (+0.8)
      receptions 2292 -0.00008 (-0.1) -0.00024 (-0.6) -0.00032 (-0.4) +0.00014 (+0.2)
   rush_attempts  877 -0.00074 (-0.5) -0.00055 (-0.7) -0.00070 (-0.3) -0.00016 (-0.1)
        rush_yds 1170 -0.00082 (-1.2) -0.00105 (-1.6) -0.00134 (-1.4) -0.00001 (-0.0)

by month, p_ridge:
  month    n        d        z
2025-10 2040  0.00043  0.45454
2025-11 2522 -0.00026 -0.31941
2025-12 2236  0.00041  0.46243

by month, p_gbdt:
  month    n        d        z
2025-10 2040  0.00034  0.50795
2025-11 2522 -0.00063 -1.12569
2025-12 2236  0.00021  0.35548

ROI at the MEDIAN book price by minimum model edge (flat 1u, one bet per prop):
  model  min_edge  n_bets     roi              ci  mkt_expected
  ridge      0.00      44  0.0438  [-24.7, +33.7]       -0.0085
  ridge      0.01      24  0.0274  [-31.9, +46.8]        0.0045
  ridge      0.02       9 -0.1346  [-77.2, +54.2]        0.0256
   gbdt      0.00      27  0.0125  [-37.6, +53.6]       -0.0097
   gbdt      0.01       8  0.5617 [-43.2, +111.5]        0.0269
   gbdt      0.02       6  0.7640 [-16.3, +114.3]        0.0377
blend_c      0.00     593  0.0292   [-5.1, +10.3]       -0.0595
blend_c      0.01     296 -0.0115   [-13.2, +9.5]       -0.0586
blend_c      0.02     163 -0.0480   [-18.4, +6.8]       -0.0563
  calib      0.00     579 -0.0152   [-11.8, +9.0]       -0.0559
  calib      0.01     174  0.0178  [-15.5, +17.6]       -0.0501
  calib      0.02      96  0.0101  [-19.2, +24.6]       -0.0497
    mkt      0.00      14  0.1931  [-33.8, +71.1]        0.0244
    mkt      0.01       8  0.8668 [+22.3, +115.9]        0.0341
    mkt      0.02       1  0.7692  [+76.9, +76.9]        0.0609
