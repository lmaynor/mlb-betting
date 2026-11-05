Games simulated: 2639 of 2643; draws per game: 4000; seasons [2024, 2025]; eligible player-games (last-10 minutes >= 20): 34429.

Game-level input check (actual minus the simulator's input mean): total residual mean -5.95, sd 31.59 (input sd 18.88); margin residual mean -0.31, sd 14.47 (input sd 13.84).

### 1. Consistency: simulated vs analytic P(over)

Gap = simulated minus analytic P(over) over every (player, stat, line) row (proxy L10 line + model-centred lines at z = -1, 0, +1). `3 SE` = share of rows whose |gap| exceeds three Monte-Carlo standard errors.

| stat | rows | MC SE | structural bias | structural mean abs gap | structural p95 abs gap | structural > 3 SE | anchored mean abs gap | anchored > 3 SE |
|---|---|---|---|---|---|---|---|---|
| points | 137716 | 0.0068 | -0.0253 | 0.0356 | 0.0698 | 0.7113 | 0.0001 | 0.0000 |
| rebounds | 137716 | 0.0067 | -0.0014 | 0.0066 | 0.0193 | 0.0605 | 0.0001 | 0.0000 |
| assists | 137716 | 0.0067 | -0.0009 | 0.0065 | 0.0174 | 0.0334 | 0.0001 | 0.0000 |
| threes | 137716 | 0.0061 | -0.0011 | 0.0060 | 0.0173 | 0.0284 | 0.0001 | 0.0000 |
| pra | 137716 | 0.0068 | -0.0206 | 0.0293 | 0.0570 | 0.6732 | 0.0085 | 0.0597 |
| pr | 137716 | 0.0068 | -0.0221 | 0.0315 | 0.0610 | 0.6930 | 0.0066 | 0.0252 |
| pa | 137716 | 0.0068 | -0.0232 | 0.0323 | 0.0638 | 0.6836 | 0.0050 | 0.0140 |
| ra | 137716 | 0.0068 | -0.0203 | 0.0262 | 0.0531 | 0.5984 | 0.0254 | 0.5922 |

### 2. Simulated vs empirical within-game correlation

Pooled Pearson correlation of residuals (actual minus the engine's mean, scaled by the engine's sd; clipped at +/-6) over every pair of the type, rotation players (last-10 minutes >= 12). Empirical: box scores; simulated: the structural draws. SE = game bootstrap.

| pair type | pairs | empirical | +/- SE | simulated | gap |
|---|---|---|---|---|---|
| teammates points-points | 192819 | -0.004 | +0.002 | -0.046 | -0.042 |
| teammates rebounds-rebounds | 192819 | -0.000 | +0.003 | -0.020 | -0.019 |
| teammates assists-assists | 192819 | +0.001 | +0.002 | -0.010 | -0.011 |
| teammates threes-threes | 192819 | -0.008 | +0.002 | +0.001 | +0.009 |
| teammates points-assists (A pts, B ast) | 385638 | +0.049 | +0.002 | +0.004 | -0.045 |
| teammates points-rebounds (A pts, B reb) | 385638 | +0.011 | +0.002 | -0.032 | -0.043 |
| teammates rebounds-assists (A reb, B ast) | 385638 | +0.004 | +0.002 | -0.023 | -0.027 |
| opponents points-points | 211642 | +0.017 | +0.002 | +0.010 | -0.007 |
| opponents rebounds-rebounds | 211642 | +0.016 | +0.002 | +0.002 | -0.014 |
| opponents assists-assists | 211642 | +0.005 | +0.002 | +0.007 | +0.002 |
| opponents points-assists (A pts, B ast) | 423284 | +0.009 | +0.002 | +0.008 | -0.001 |
| same player points-assists | 47222 | +0.226 | +0.005 | +0.253 | +0.027 |
| same player points-rebounds | 47222 | +0.337 | +0.005 | +0.323 | -0.014 |
| same player rebounds-assists | 47222 | +0.248 | +0.005 | +0.225 | -0.023 |
| same player points-threes | 47222 | +0.640 | +0.003 | +0.683 | +0.043 |
| player points vs game total | 47222 | +0.207 | +0.004 | +0.146 | -0.062 |
| player rebounds vs game total | 47222 | +0.068 | +0.005 | -0.045 | -0.113 |
| player assists vs game total | 47222 | +0.160 | +0.004 | +0.120 | -0.040 |
| player points vs own team margin | 47222 | +0.079 | +0.004 | +0.108 | +0.030 |

RMS gap over the 19 rows: 0.039.

### 3. Walk-forward calibration (log score, Brier, ECE)

Log score is mean negative log-likelihood of `over` at the stated line (lower = better). `d` = analytic minus simulated (positive = the simulation is better); z uses a game-clustered SE. `analytic` = what the board computes today (components: the engine's marginal; pra / pr / pa / ra: Normal with moment-matched variance from the shrunk within-player correlation).

| stat | lines | rows | LL analytic | LL structural | d LL | z | LL anchored | d LL | z | ECE an. | ECE str. | ECE anch. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| points | proxy L10 line | 34429 | 0.6796 | 0.6749 | +0.0047 | +8.9571 | 0.6796 | -0.0000 | -0.2281 | 0.0471 | 0.0053 | 0.0471 |
| points | model-centred lines | 103287 | 0.5190 | 0.5119 | +0.0071 | +25.3326 | 0.5190 | +0.0000 | +0.9581 | 0.0258 | 0.0113 | 0.0258 |
| rebounds | proxy L10 line | 34429 | 0.6675 | 0.6677 | -0.0002 | -2.4401 | 0.6675 | -0.0000 | -0.2834 | 0.0116 | 0.0108 | 0.0115 |
| rebounds | model-centred lines | 103287 | 0.4959 | 0.4960 | -0.0000 | -0.4613 | 0.4959 | +0.0000 | +1.6826 | 0.0154 | 0.0183 | 0.0152 |
| assists | proxy L10 line | 34429 | 0.6621 | 0.6624 | -0.0004 | -3.6980 | 0.6621 | +0.0000 | +0.1294 | 0.0062 | 0.0060 | 0.0062 |
| assists | model-centred lines | 103287 | 0.5099 | 0.5100 | -0.0000 | -0.3863 | 0.5099 | -0.0000 | -0.3612 | 0.0120 | 0.0117 | 0.0119 |
| threes | proxy L10 line | 34429 | 0.6088 | 0.6088 | +0.0000 | +0.4069 | 0.6088 | +0.0000 | +1.4923 | 0.0130 | 0.0119 | 0.0130 |
| threes | model-centred lines | 103287 | 0.4574 | 0.4574 | +0.0000 | +0.5497 | 0.4574 | +0.0000 | +1.5063 | 0.0088 | 0.0079 | 0.0088 |
| pra | proxy L10 line | 34429 | 0.6771 | 0.6732 | +0.0040 | +8.6642 | 0.6762 | +0.0009 | +7.1656 | 0.0442 | 0.0065 | 0.0362 |
| pra | model-centred lines | 103287 | 0.5152 | 0.5103 | +0.0050 | +21.3667 | 0.5151 | +0.0002 | +2.1992 | 0.0271 | 0.0136 | 0.0245 |
| pr | proxy L10 line | 34429 | 0.6792 | 0.6745 | +0.0047 | +9.8802 | 0.6785 | +0.0007 | +7.2204 | 0.0480 | 0.0084 | 0.0423 |
| pr | model-centred lines | 103287 | 0.5151 | 0.5093 | +0.0057 | +23.4797 | 0.5148 | +0.0003 | +5.1857 | 0.0272 | 0.0131 | 0.0250 |
| pa | proxy L10 line | 34429 | 0.6781 | 0.6740 | +0.0040 | +7.7835 | 0.6778 | +0.0003 | +4.3192 | 0.0436 | 0.0052 | 0.0406 |
| pa | model-centred lines | 103287 | 0.5177 | 0.5120 | +0.0057 | +21.8642 | 0.5178 | -0.0001 | -1.5490 | 0.0248 | 0.0112 | 0.0236 |
| ra | proxy L10 line | 34429 | 0.6761 | 0.6719 | +0.0043 | +9.8596 | 0.6718 | +0.0043 | +10.1613 | 0.0486 | 0.0106 | 0.0109 |
| ra | model-centred lines | 103287 | 0.5049 | 0.5026 | +0.0023 | +12.4600 | 0.5028 | +0.0021 | +12.2785 | 0.0287 | 0.0148 | 0.0131 |

### 4. SGP-style pair events (accuracy of P(A and B), no prices)

Each pair is two over-legs at model-centred lines (floor(mu) + 0.5), eligible players only. `d LL` = independence (product of the analytic marginals) minus the simulated joint, per pair (positive = the simulated joint is better); `dependence only` compares the structural joint with the product of the structural simulation's OWN marginals, so the marginal shape cancels and only the dependence is scored; lift columns are sum(joint) / sum(product of that column's own marginals) for the simulations and realised / analytic product for the box scores.

| pair type | pairs | hit rate | p indep (analytic) | p structural | p anchored | d LL structural | z | d LL anchored | z | dependence only: d LL structural vs its own marginals | z | lift realised | lift structural | lift anchored |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| opponents points-points | 112566 | 0.201 | 0.250 | 0.204 | 0.252 | +0.0067 | +10.06 | -0.0005 | -14.42 | +0.0000 | +0.57 | 0.804 | 1.011 | 1.008 |
| player assists over x game total over | 34429 | 0.186 | 0.223 | 0.244 | 0.244 | -0.0044 | -7.51 | -0.0042 | -7.15 | -0.0042 | -7.11 | 0.833 | 1.094 | 1.093 |
| player points over x game total over | 34429 | 0.197 | 0.250 | 0.251 | 0.276 | +0.0021 | +16.52 | -0.0067 | -9.44 | -0.0036 | -4.64 | 0.788 | 1.117 | 1.104 |
| player rebounds over x game total over | 34429 | 0.166 | 0.225 | 0.217 | 0.218 | +0.0016 | +7.22 | +0.0017 | +8.58 | +0.0017 | +8.47 | 0.741 | 0.968 | 0.968 |
| same player points-assists | 34429 | 0.224 | 0.223 | 0.235 | 0.258 | -0.0002 | -0.82 | -0.0028 | -5.65 | +0.0018 | +3.36 | 1.004 | 1.175 | 1.157 |
| same player points-rebounds | 34429 | 0.239 | 0.224 | 0.244 | 0.268 | +0.0003 | +1.08 | -0.0015 | -2.61 | +0.0043 | +7.18 | 1.066 | 1.215 | 1.193 |
| same player rebounds-assists | 34429 | 0.226 | 0.200 | 0.230 | 0.231 | +0.0020 | +4.23 | +0.0022 | +4.63 | +0.0024 | +4.98 | 1.129 | 1.155 | 1.153 |
| teammates points-assists (A pts, B ast) | 200204 | 0.199 | 0.223 | 0.202 | 0.224 | +0.0017 | +6.17 | -0.0001 | -3.69 | +0.0001 | +2.07 | 0.893 | 1.009 | 1.008 |
| teammates points-points | 100102 | 0.188 | 0.250 | 0.189 | 0.239 | +0.0112 | +13.01 | +0.0038 | +20.01 | +0.0008 | +3.69 | 0.750 | 0.940 | 0.954 |
| teammates rebounds-rebounds | 100102 | 0.188 | 0.201 | 0.198 | 0.199 | +0.0007 | +6.74 | +0.0008 | +8.61 | +0.0008 | +8.96 | 0.936 | 0.989 | 0.989 |

