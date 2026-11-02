# NBA engine walk-forward (tune)

Rows scored: 960484; seasons [np.int64(2021), np.int64(2022)]. ACCURACY only (no prop lines). Absences are ORACLE (upper bound); no spreads (blowout / OT terms not exercised); Brier uses a proxy line.

## Levels (mean over rotation player-games)

| stat | variant | n | MAE | bias | log score | Brier |
|---|---|---|---|---|---|---|
| assists | eta_0.4+shot_0.5 | 34303 | 1.503 | +0.089 | -1.9270 | 0.2342 |
| assists | hier | 34303 | 1.529 | +0.233 | -1.9315 | 0.2354 |
| blocks | eta_0.4+shot_0.5 | 34303 | 0.568 | +0.003 | -0.9109 | 0.2015 |
| blocks | hier | 34303 | 0.575 | +0.036 | -0.9135 | 0.2024 |
| pa | eta_0.4+shot_0.5 | 34303 | 5.649 | +0.571 | -3.4104 | 0.2420 |
| pa | hier | 34303 | 5.782 | +1.350 | -3.4181 | 0.2442 |
| points | eta_0.4+shot_0.5 | 34303 | 5.215 | +0.482 | -3.3233 | 0.2423 |
| points | hier | 34303 | 5.328 | +1.117 | -3.3290 | 0.2446 |
| pr | eta_0.4+shot_0.5 | 34303 | 6.133 | +0.661 | -3.4933 | 0.2418 |
| pr | eta_0.4+shot_0.5+trkreb_0.5 | 34303 | 6.133 | +0.662 | -3.4932 | 0.2418 |
| pr | eta_0.4+shot_0.5+trkreb_1 | 34303 | 6.134 | +0.664 | -3.4932 | 0.2419 |
| pr | hier | 34303 | 6.285 | +1.506 | -3.5030 | 0.2446 |
| pra | eta_0.4+shot_0.5 | 34303 | 6.604 | +0.750 | -3.5668 | 0.2418 |
| pra | eta_0.4+shot_0.5+trkreb_0.5 | 34303 | 6.604 | +0.751 | -3.5668 | 0.2418 |
| pra | eta_0.4+shot_0.5+trkreb_1 | 34303 | 6.604 | +0.753 | -3.5668 | 0.2419 |
| pra | hier | 34303 | 6.771 | +1.739 | -3.5778 | 0.2448 |
| ra | eta_0.4+shot_0.5 | 34303 | 2.823 | +0.268 | -2.6671 | 0.2412 |
| ra | eta_0.4+shot_0.5+trkreb_0.5 | 34303 | 2.823 | +0.269 | -2.6673 | 0.2413 |
| ra | eta_0.4+shot_0.5+trkreb_1 | 34303 | 2.825 | +0.271 | -2.6678 | 0.2415 |
| ra | hier | 34303 | 2.890 | +0.621 | -2.6790 | 0.2451 |
| rebounds | eta_0.4+shot_0.5 | 34303 | 2.070 | +0.179 | -2.3044 | 0.2372 |
| rebounds | eta_0.4+shot_0.5+trkreb_0.5 | 34303 | 2.070 | +0.180 | -2.3047 | 0.2373 |
| rebounds | eta_0.4+shot_0.5+trkreb_1 | 34303 | 2.072 | +0.182 | -2.3054 | 0.2375 |
| rebounds | hier | 34303 | 2.118 | +0.388 | -2.3124 | 0.2390 |
| steals | eta_0.4+shot_0.5 | 34303 | 0.774 | +0.003 | -1.2339 | 0.2291 |
| steals | hier | 34303 | 0.787 | +0.059 | -1.2392 | 0.2308 |
| threes | eta_0.4+shot_0.5 | 34303 | 1.015 | +0.067 | -1.4651 | 0.2103 |
| threes | hier | 34303 | 1.028 | +0.133 | -1.4684 | 0.2112 |

## Paired vs `eta_0.4+shot_0.5` (positive = variant better; z = log-score gain / player-clustered SE; adopt bar z >= 2.81 and MAE gain > 0)

| stat | variant | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|---|
| points | hier | 33899 | -0.0055 | -3.30 | -0.1102 | -0.00215 |
| rebounds | eta_0.4+shot_0.5+trkreb_0.5 | 33899 | -0.0002 | -2.49 | -0.0006 | -0.00008 |
| rebounds | eta_0.4+shot_0.5+trkreb_1 | 33899 | -0.0009 | -4.46 | -0.0024 | -0.00030 |
| rebounds | hier | 33899 | -0.0080 | -10.67 | -0.0480 | -0.00188 |
| assists | hier | 33899 | -0.0044 | -5.64 | -0.0252 | -0.00116 |
| threes | hier | 33899 | -0.0033 | -7.75 | -0.0130 | -0.00085 |
| steals | hier | 33899 | -0.0052 | -13.89 | -0.0131 | -0.00165 |
| blocks | hier | 33899 | -0.0027 | -8.33 | -0.0076 | -0.00083 |
| pr | eta_0.4+shot_0.5+trkreb_0.5 | 33899 | +0.0001 | +1.26 | -0.0003 | -0.00004 |
| pr | eta_0.4+shot_0.5+trkreb_1 | 33899 | +0.0001 | +0.74 | -0.0010 | -0.00010 |
| pr | hier | 33899 | -0.0095 | -5.84 | -0.1484 | -0.00280 |
| pa | hier | 33899 | -0.0075 | -4.40 | -0.1308 | -0.00211 |
| ra | eta_0.4+shot_0.5+trkreb_0.5 | 33899 | -0.0002 | -1.84 | -0.0005 | -0.00010 |
| ra | eta_0.4+shot_0.5+trkreb_1 | 33899 | -0.0007 | -2.93 | -0.0019 | -0.00028 |
| ra | hier | 33899 | -0.0118 | -10.17 | -0.0668 | -0.00384 |
| pra | eta_0.4+shot_0.5+trkreb_0.5 | 33899 | +0.0001 | +0.80 | -0.0002 | -0.00004 |
| pra | eta_0.4+shot_0.5+trkreb_1 | 33899 | +0.0000 | +0.29 | -0.0008 | -0.00011 |
| pra | hier | 33899 | -0.0108 | -6.47 | -0.1646 | -0.00301 |

## Old engine vs `hier` (positive = `hier` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5` (positive = `eta_0.4+shot_0.5` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5+trkreb_0.5` (positive = `eta_0.4+shot_0.5+trkreb_0.5` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5+trkreb_1` (positive = `eta_0.4+shot_0.5+trkreb_1` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Selected on this season

(none)
