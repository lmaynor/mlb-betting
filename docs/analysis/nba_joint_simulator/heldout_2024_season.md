Games simulated: 1317 of 1321; draws per game: 4000; seasons [2024]; eligible player-games (last-10 minutes >= 20): 17015.

Game-level input check (actual minus the simulator's input mean): total residual mean -5.92, sd 30.69 (input sd 18.88); margin residual mean -0.27, sd 14.29 (input sd 13.84).

### 1. Consistency: simulated vs analytic P(over)

Gap = simulated minus analytic P(over) over every (player, stat, line) row (proxy L10 line + model-centred lines at z = -1, 0, +1). `3 SE` = share of rows whose |gap| exceeds three Monte-Carlo standard errors.

| stat | rows | MC SE | structural bias | structural mean abs gap | structural p95 abs gap | structural > 3 SE | anchored mean abs gap | anchored > 3 SE |
|---|---|---|---|---|---|---|---|---|
| points | 68060 | 0.0068 | -0.0251 | 0.0357 | 0.0698 | 0.7172 | 0.0001 | 0.0000 |
| rebounds | 68060 | 0.0067 | -0.0014 | 0.0067 | 0.0199 | 0.0640 | 0.0001 | 0.0000 |
| assists | 68060 | 0.0067 | -0.0011 | 0.0066 | 0.0178 | 0.0359 | 0.0001 | 0.0000 |
| threes | 68060 | 0.0061 | -0.0011 | 0.0061 | 0.0175 | 0.0292 | 0.0001 | 0.0000 |
| pra | 68060 | 0.0068 | -0.0205 | 0.0294 | 0.0571 | 0.6763 | 0.0083 | 0.0529 |
| pr | 68060 | 0.0068 | -0.0220 | 0.0316 | 0.0612 | 0.7008 | 0.0065 | 0.0234 |
| pa | 68060 | 0.0068 | -0.0231 | 0.0324 | 0.0640 | 0.6892 | 0.0049 | 0.0121 |
| ra | 68060 | 0.0068 | -0.0202 | 0.0259 | 0.0529 | 0.5926 | 0.0251 | 0.5869 |

### 2. Simulated vs empirical within-game correlation

Pooled Pearson correlation of residuals (actual minus the engine's mean, scaled by the engine's sd; clipped at +/-6) over every pair of the type, rotation players (last-10 minutes >= 12). Empirical: box scores; simulated: the structural draws. SE = game bootstrap.

| pair type | pairs | empirical | +/- SE | simulated | gap |
|---|---|---|---|---|---|
| teammates points-points | 93729 | -0.003 | +0.003 | -0.045 | -0.042 |
| teammates rebounds-rebounds | 93729 | +0.000 | +0.003 | -0.020 | -0.020 |
| teammates assists-assists | 93729 | +0.004 | +0.004 | -0.010 | -0.015 |
| teammates threes-threes | 93729 | -0.004 | +0.003 | +0.000 | +0.004 |
| teammates points-assists (A pts, B ast) | 187458 | +0.052 | +0.003 | +0.005 | -0.047 |
| teammates points-rebounds (A pts, B reb) | 187458 | +0.014 | +0.003 | -0.032 | -0.046 |
| teammates rebounds-assists (A reb, B ast) | 187458 | +0.007 | +0.003 | -0.023 | -0.031 |
| opponents points-points | 103039 | +0.016 | +0.003 | +0.010 | -0.005 |
| opponents rebounds-rebounds | 103039 | +0.013 | +0.003 | +0.002 | -0.011 |
| opponents assists-assists | 103039 | +0.004 | +0.003 | +0.007 | +0.003 |
| opponents points-assists (A pts, B ast) | 206078 | +0.009 | +0.003 | +0.008 | -0.000 |
| same player points-assists | 23276 | +0.219 | +0.008 | +0.250 | +0.032 |
| same player points-rebounds | 23276 | +0.334 | +0.007 | +0.320 | -0.013 |
| same player rebounds-assists | 23276 | +0.244 | +0.007 | +0.222 | -0.022 |
| same player points-threes | 23276 | +0.637 | +0.005 | +0.682 | +0.045 |
| player points vs game total | 23276 | +0.205 | +0.006 | +0.147 | -0.058 |
| player rebounds vs game total | 23276 | +0.071 | +0.008 | -0.045 | -0.116 |
| player assists vs game total | 23276 | +0.159 | +0.006 | +0.121 | -0.038 |
| player points vs own team margin | 23276 | +0.084 | +0.006 | +0.109 | +0.025 |

RMS gap over the 19 rows: 0.040.

### 3. Walk-forward calibration (log score, Brier, ECE)

Log score is mean negative log-likelihood of `over` at the stated line (lower = better). `d` = analytic minus simulated (positive = the simulation is better); z uses a game-clustered SE. `analytic` = what the board computes today (components: the engine's marginal; pra / pr / pa / ra: Normal with moment-matched variance from the shrunk within-player correlation).

| stat | lines | rows | LL analytic | LL structural | d LL | z | LL anchored | d LL | z | ECE an. | ECE str. | ECE anch. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| points | proxy L10 line | 17015 | 0.6780 | 0.6737 | +0.0042 | +5.6278 | 0.6780 | -0.0000 | -0.0840 | 0.0463 | 0.0086 | 0.0463 |
| points | model-centred lines | 51045 | 0.5198 | 0.5127 | +0.0071 | +18.0629 | 0.5198 | +0.0000 | +0.7814 | 0.0259 | 0.0116 | 0.0259 |
| rebounds | proxy L10 line | 17015 | 0.6679 | 0.6682 | -0.0003 | -2.1044 | 0.6679 | -0.0000 | -0.9233 | 0.0112 | 0.0103 | 0.0114 |
| rebounds | model-centred lines | 51045 | 0.4995 | 0.4994 | +0.0001 | +0.5130 | 0.4995 | +0.0000 | +0.5332 | 0.0135 | 0.0163 | 0.0134 |
| assists | proxy L10 line | 17015 | 0.6601 | 0.6603 | -0.0002 | -1.4464 | 0.6601 | +0.0000 | +0.4325 | 0.0072 | 0.0068 | 0.0070 |
| assists | model-centred lines | 51045 | 0.5119 | 0.5118 | +0.0001 | +1.2518 | 0.5119 | -0.0000 | -0.4757 | 0.0145 | 0.0143 | 0.0145 |
| threes | proxy L10 line | 17015 | 0.6092 | 0.6093 | -0.0001 | -0.7780 | 0.6092 | +0.0000 | +0.9416 | 0.0139 | 0.0111 | 0.0138 |
| threes | model-centred lines | 51045 | 0.4620 | 0.4620 | -0.0000 | -0.0905 | 0.4620 | +0.0000 | +0.6589 | 0.0079 | 0.0079 | 0.0079 |
| pra | proxy L10 line | 17015 | 0.6762 | 0.6719 | +0.0043 | +6.6327 | 0.6752 | +0.0010 | +6.0080 | 0.0459 | 0.0088 | 0.0382 |
| pra | model-centred lines | 51045 | 0.5167 | 0.5113 | +0.0053 | +16.1138 | 0.5163 | +0.0004 | +3.4479 | 0.0289 | 0.0153 | 0.0263 |
| pr | proxy L10 line | 17015 | 0.6787 | 0.6738 | +0.0048 | +7.1803 | 0.6778 | +0.0008 | +5.8883 | 0.0489 | 0.0087 | 0.0434 |
| pr | model-centred lines | 51045 | 0.5169 | 0.5110 | +0.0059 | +16.8765 | 0.5165 | +0.0004 | +4.0757 | 0.0281 | 0.0148 | 0.0260 |
| pa | proxy L10 line | 17015 | 0.6767 | 0.6726 | +0.0042 | +5.6805 | 0.6764 | +0.0003 | +3.2661 | 0.0450 | 0.0070 | 0.0426 |
| pa | model-centred lines | 51045 | 0.5178 | 0.5119 | +0.0060 | +16.0246 | 0.5179 | -0.0000 | -0.3115 | 0.0273 | 0.0134 | 0.0263 |
| ra | proxy L10 line | 17015 | 0.6775 | 0.6730 | +0.0045 | +7.3483 | 0.6730 | +0.0045 | +7.5254 | 0.0496 | 0.0110 | 0.0114 |
| ra | model-centred lines | 51045 | 0.5050 | 0.5027 | +0.0023 | +8.7820 | 0.5029 | +0.0021 | +8.8353 | 0.0284 | 0.0146 | 0.0145 |

### 4. SGP-style pair events (accuracy of P(A and B), no prices)

Each pair is two over-legs at model-centred lines (floor(mu) + 0.5), eligible players only. `d LL` = independence (product of the analytic marginals) minus the simulated joint, per pair (positive = the simulated joint is better); `dependence only` compares the structural joint with the product of the structural simulation's OWN marginals, so the marginal shape cancels and only the dependence is scored; lift columns are sum(joint) / sum(product of that column's own marginals) for the simulations and realised / analytic product for the box scores.

| pair type | pairs | hit rate | p indep (analytic) | p structural | p anchored | d LL structural | z | d LL anchored | z | dependence only: d LL structural vs its own marginals | z | lift realised | lift structural | lift anchored |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| opponents points-points | 55022 | 0.200 | 0.250 | 0.204 | 0.252 | +0.0067 | +7.15 | -0.0005 | -10.37 | -0.0000 | -0.24 | 0.801 | 1.011 | 1.009 |
| player assists over x game total over | 17015 | 0.183 | 0.223 | 0.244 | 0.244 | -0.0048 | -5.84 | -0.0046 | -5.59 | -0.0046 | -5.56 | 0.819 | 1.094 | 1.094 |
| player points over x game total over | 17015 | 0.195 | 0.250 | 0.251 | 0.277 | +0.0020 | +10.73 | -0.0070 | -6.87 | -0.0039 | -3.51 | 0.779 | 1.118 | 1.105 |
| player rebounds over x game total over | 17015 | 0.165 | 0.225 | 0.217 | 0.218 | +0.0017 | +5.35 | +0.0017 | +6.17 | +0.0017 | +6.07 | 0.734 | 0.968 | 0.968 |
| same player points-assists | 17015 | 0.219 | 0.223 | 0.234 | 0.257 | -0.0004 | -1.32 | -0.0035 | -5.11 | +0.0008 | +1.12 | 0.982 | 1.172 | 1.154 |
| same player points-rebounds | 17015 | 0.238 | 0.225 | 0.244 | 0.267 | +0.0002 | +0.45 | -0.0017 | -2.24 | +0.0039 | +4.73 | 1.062 | 1.211 | 1.190 |
| same player rebounds-assists | 17015 | 0.224 | 0.200 | 0.229 | 0.230 | +0.0017 | +2.82 | +0.0018 | +2.90 | +0.0020 | +3.19 | 1.118 | 1.150 | 1.148 |
| teammates points-assists (A pts, B ast) | 97918 | 0.197 | 0.223 | 0.202 | 0.225 | +0.0019 | +4.92 | -0.0002 | -3.40 | +0.0001 | +1.21 | 0.884 | 1.010 | 1.009 |
| teammates points-points | 48959 | 0.186 | 0.250 | 0.190 | 0.238 | +0.0116 | +9.50 | +0.0038 | +14.12 | +0.0009 | +2.78 | 0.744 | 0.940 | 0.953 |
| teammates rebounds-rebounds | 48959 | 0.189 | 0.202 | 0.198 | 0.199 | +0.0009 | +5.53 | +0.0009 | +6.76 | +0.0009 | +6.96 | 0.939 | 0.989 | 0.989 |

