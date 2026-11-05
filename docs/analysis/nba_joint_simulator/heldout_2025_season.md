Games simulated: 1322 of 1322; draws per game: 4000; seasons [2025]; eligible player-games (last-10 minutes >= 20): 17414.

Game-level input check (actual minus the simulator's input mean): total residual mean -5.97, sd 32.46 (input sd 18.88); margin residual mean -0.35, sd 14.65 (input sd 13.84).

### 1. Consistency: simulated vs analytic P(over)

Gap = simulated minus analytic P(over) over every (player, stat, line) row (proxy L10 line + model-centred lines at z = -1, 0, +1). `3 SE` = share of rows whose |gap| exceeds three Monte-Carlo standard errors.

| stat | rows | MC SE | structural bias | structural mean abs gap | structural p95 abs gap | structural > 3 SE | anchored mean abs gap | anchored > 3 SE |
|---|---|---|---|---|---|---|---|---|
| points | 69656 | 0.0068 | -0.0254 | 0.0355 | 0.0698 | 0.7055 | 0.0001 | 0.0000 |
| rebounds | 69656 | 0.0067 | -0.0013 | 0.0065 | 0.0187 | 0.0572 | 0.0001 | 0.0000 |
| assists | 69656 | 0.0067 | -0.0008 | 0.0063 | 0.0169 | 0.0310 | 0.0001 | 0.0000 |
| threes | 69656 | 0.0060 | -0.0010 | 0.0060 | 0.0171 | 0.0276 | 0.0001 | 0.0000 |
| pra | 69656 | 0.0068 | -0.0207 | 0.0293 | 0.0569 | 0.6701 | 0.0087 | 0.0665 |
| pr | 69656 | 0.0068 | -0.0222 | 0.0313 | 0.0608 | 0.6853 | 0.0067 | 0.0270 |
| pa | 69656 | 0.0068 | -0.0233 | 0.0323 | 0.0637 | 0.6782 | 0.0051 | 0.0159 |
| ra | 69656 | 0.0068 | -0.0204 | 0.0264 | 0.0533 | 0.6042 | 0.0258 | 0.5975 |

### 2. Simulated vs empirical within-game correlation

Pooled Pearson correlation of residuals (actual minus the engine's mean, scaled by the engine's sd; clipped at +/-6) over every pair of the type, rotation players (last-10 minutes >= 12). Empirical: box scores; simulated: the structural draws. SE = game bootstrap.

| pair type | pairs | empirical | +/- SE | simulated | gap |
|---|---|---|---|---|---|
| teammates points-points | 99090 | -0.005 | +0.003 | -0.046 | -0.041 |
| teammates rebounds-rebounds | 99090 | -0.001 | +0.004 | -0.019 | -0.019 |
| teammates assists-assists | 99090 | -0.002 | +0.003 | -0.010 | -0.008 |
| teammates threes-threes | 99090 | -0.011 | +0.003 | +0.001 | +0.013 |
| teammates points-assists (A pts, B ast) | 198180 | +0.047 | +0.003 | +0.004 | -0.043 |
| teammates points-rebounds (A pts, B reb) | 198180 | +0.008 | +0.003 | -0.032 | -0.040 |
| teammates rebounds-assists (A reb, B ast) | 198180 | +0.001 | +0.003 | -0.023 | -0.024 |
| opponents points-points | 108603 | +0.019 | +0.003 | +0.010 | -0.009 |
| opponents rebounds-rebounds | 108603 | +0.019 | +0.004 | +0.002 | -0.017 |
| opponents assists-assists | 108603 | +0.006 | +0.003 | +0.007 | +0.001 |
| opponents points-assists (A pts, B ast) | 217206 | +0.010 | +0.002 | +0.008 | -0.002 |
| same player points-assists | 23946 | +0.233 | +0.007 | +0.255 | +0.022 |
| same player points-rebounds | 23946 | +0.341 | +0.006 | +0.325 | -0.016 |
| same player rebounds-assists | 23946 | +0.251 | +0.007 | +0.229 | -0.023 |
| same player points-threes | 23946 | +0.643 | +0.005 | +0.684 | +0.041 |
| player points vs game total | 23946 | +0.210 | +0.006 | +0.145 | -0.065 |
| player rebounds vs game total | 23946 | +0.065 | +0.008 | -0.045 | -0.110 |
| player assists vs game total | 23946 | +0.161 | +0.006 | +0.119 | -0.042 |
| player points vs own team margin | 23946 | +0.074 | +0.006 | +0.108 | +0.034 |

RMS gap over the 19 rows: 0.039.

### 3. Walk-forward calibration (log score, Brier, ECE)

Log score is mean negative log-likelihood of `over` at the stated line (lower = better). `d` = analytic minus simulated (positive = the simulation is better); z uses a game-clustered SE. `analytic` = what the board computes today (components: the engine's marginal; pra / pr / pa / ra: Normal with moment-matched variance from the shrunk within-player correlation).

| stat | lines | rows | LL analytic | LL structural | d LL | z | LL anchored | d LL | z | ECE an. | ECE str. | ECE anch. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| points | proxy L10 line | 17414 | 0.6813 | 0.6760 | +0.0052 | +7.0289 | 0.6813 | -0.0000 | -0.2384 | 0.0479 | 0.0033 | 0.0479 |
| points | model-centred lines | 52242 | 0.5182 | 0.5112 | +0.0071 | +17.7639 | 0.5182 | +0.0000 | +0.5708 | 0.0258 | 0.0112 | 0.0258 |
| rebounds | proxy L10 line | 17414 | 0.6670 | 0.6671 | -0.0002 | -1.3398 | 0.6670 | +0.0000 | +0.5116 | 0.0122 | 0.0115 | 0.0119 |
| rebounds | model-centred lines | 52242 | 0.4925 | 0.4926 | -0.0001 | -1.2214 | 0.4925 | +0.0000 | +1.8593 | 0.0178 | 0.0203 | 0.0176 |
| assists | proxy L10 line | 17414 | 0.6640 | 0.6645 | -0.0005 | -3.8400 | 0.6640 | -0.0000 | -0.2540 | 0.0066 | 0.0068 | 0.0065 |
| assists | model-centred lines | 52242 | 0.5080 | 0.5082 | -0.0002 | -1.9529 | 0.5080 | -0.0000 | -0.0314 | 0.0097 | 0.0096 | 0.0095 |
| threes | proxy L10 line | 17414 | 0.6085 | 0.6083 | +0.0002 | +1.3410 | 0.6085 | +0.0000 | +1.1682 | 0.0142 | 0.0127 | 0.0142 |
| threes | model-centred lines | 52242 | 0.4530 | 0.4529 | +0.0001 | +0.8523 | 0.4530 | +0.0000 | +1.4779 | 0.0103 | 0.0093 | 0.0103 |
| pra | proxy L10 line | 17414 | 0.6780 | 0.6744 | +0.0036 | +5.6254 | 0.6772 | +0.0008 | +4.1985 | 0.0425 | 0.0110 | 0.0342 |
| pra | model-centred lines | 52242 | 0.5138 | 0.5092 | +0.0046 | +14.1269 | 0.5139 | -0.0000 | -0.2081 | 0.0254 | 0.0124 | 0.0227 |
| pr | proxy L10 line | 17414 | 0.6798 | 0.6752 | +0.0046 | +6.7959 | 0.6791 | +0.0006 | +4.3670 | 0.0473 | 0.0102 | 0.0414 |
| pr | model-centred lines | 52242 | 0.5133 | 0.5077 | +0.0056 | +16.3261 | 0.5130 | +0.0003 | +3.2621 | 0.0262 | 0.0116 | 0.0240 |
| pa | proxy L10 line | 17414 | 0.6793 | 0.6755 | +0.0039 | +5.3268 | 0.6791 | +0.0003 | +2.8565 | 0.0421 | 0.0074 | 0.0388 |
| pa | model-centred lines | 52242 | 0.5176 | 0.5121 | +0.0055 | +14.9099 | 0.5178 | -0.0001 | -1.8143 | 0.0223 | 0.0107 | 0.0211 |
| ra | proxy L10 line | 17414 | 0.6749 | 0.6708 | +0.0040 | +6.6058 | 0.6707 | +0.0041 | +6.8588 | 0.0477 | 0.0105 | 0.0106 |
| ra | model-centred lines | 52242 | 0.5049 | 0.5026 | +0.0023 | +8.8366 | 0.5028 | +0.0021 | +8.5430 | 0.0292 | 0.0153 | 0.0125 |

### 4. SGP-style pair events (accuracy of P(A and B), no prices)

Each pair is two over-legs at model-centred lines (floor(mu) + 0.5), eligible players only. `d LL` = independence (product of the analytic marginals) minus the simulated joint, per pair (positive = the simulated joint is better); `dependence only` compares the structural joint with the product of the structural simulation's OWN marginals, so the marginal shape cancels and only the dependence is scored; lift columns are sum(joint) / sum(product of that column's own marginals) for the simulations and realised / analytic product for the box scores.

| pair type | pairs | hit rate | p indep (analytic) | p structural | p anchored | d LL structural | z | d LL anchored | z | dependence only: d LL structural vs its own marginals | z | lift realised | lift structural | lift anchored |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| opponents points-points | 57544 | 0.202 | 0.250 | 0.203 | 0.252 | +0.0066 | +7.07 | -0.0005 | -10.02 | +0.0001 | +1.01 | 0.807 | 1.010 | 1.008 |
| player assists over x game total over | 17414 | 0.189 | 0.223 | 0.244 | 0.244 | -0.0040 | -4.80 | -0.0037 | -4.53 | -0.0038 | -4.51 | 0.846 | 1.093 | 1.093 |
| player points over x game total over | 17414 | 0.200 | 0.251 | 0.251 | 0.276 | +0.0022 | +12.67 | -0.0064 | -6.48 | -0.0033 | -3.05 | 0.797 | 1.115 | 1.103 |
| player rebounds over x game total over | 17414 | 0.168 | 0.225 | 0.217 | 0.218 | +0.0015 | +4.86 | +0.0017 | +5.96 | +0.0017 | +5.90 | 0.748 | 0.968 | 0.968 |
| same player points-assists | 17414 | 0.228 | 0.223 | 0.235 | 0.258 | +0.0000 | +0.09 | -0.0021 | -2.93 | +0.0027 | +3.58 | 1.026 | 1.178 | 1.160 |
| same player points-rebounds | 17414 | 0.240 | 0.224 | 0.245 | 0.268 | +0.0005 | +1.06 | -0.0012 | -1.47 | +0.0047 | +5.40 | 1.070 | 1.219 | 1.196 |
| same player rebounds-assists | 17414 | 0.227 | 0.200 | 0.231 | 0.231 | +0.0022 | +3.15 | +0.0025 | +3.61 | +0.0027 | +3.82 | 1.139 | 1.160 | 1.158 |
| teammates points-assists (A pts, B ast) | 102286 | 0.200 | 0.223 | 0.201 | 0.224 | +0.0015 | +3.84 | -0.0001 | -1.81 | +0.0001 | +1.72 | 0.901 | 1.008 | 1.007 |
| teammates points-points | 51143 | 0.189 | 0.250 | 0.189 | 0.239 | +0.0108 | +8.91 | +0.0037 | +14.18 | +0.0008 | +2.44 | 0.757 | 0.940 | 0.954 |
| teammates rebounds-rebounds | 51143 | 0.188 | 0.201 | 0.198 | 0.199 | +0.0006 | +3.95 | +0.0007 | +5.40 | +0.0007 | +5.69 | 0.933 | 0.989 | 0.989 |

