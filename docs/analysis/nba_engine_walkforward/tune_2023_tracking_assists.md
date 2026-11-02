# NBA engine walk-forward (tune)

Rows scored: 466340; seasons [np.int64(2023)]. ACCURACY only (no prop lines). Absences are ORACLE (upper bound); no spreads (blowout / OT terms not exercised); Brier uses a proxy line.

## Levels (mean over rotation player-games)

| stat | variant | n | MAE | bias | log score | Brier |
|---|---|---|---|---|---|---|
| assists | eta_0.4+shot_0.5 | 16655 | 1.573 | +0.065 | -1.9855 | 0.2360 |
| assists | eta_0.4+shot_0.5+trkast_0.5 | 16655 | 1.574 | +0.065 | -1.9860 | 0.2362 |
| assists | eta_0.4+shot_0.5+trkast_1 | 16655 | 1.576 | +0.065 | -1.9872 | 0.2366 |
| assists | hier | 16655 | 1.600 | +0.201 | -1.9904 | 0.2372 |
| blocks | eta_0.4+shot_0.5 | 16655 | 0.610 | +0.004 | -0.9642 | 0.2062 |
| blocks | hier | 16655 | 0.618 | +0.040 | -0.9672 | 0.2071 |
| pa | eta_0.4+shot_0.5 | 16655 | 5.768 | +0.549 | -3.4319 | 0.2429 |
| pa | eta_0.4+shot_0.5+trkast_0.5 | 16655 | 5.769 | +0.549 | -3.4319 | 0.2429 |
| pa | eta_0.4+shot_0.5+trkast_1 | 16655 | 5.770 | +0.550 | -3.4320 | 0.2429 |
| pa | hier | 16655 | 5.904 | +1.281 | -3.4404 | 0.2449 |
| points | eta_0.4+shot_0.5 | 16655 | 5.271 | +0.484 | -3.3456 | 0.2422 |
| points | hier | 16655 | 5.388 | +1.080 | -3.3520 | 0.2441 |
| pr | eta_0.4+shot_0.5 | 16655 | 6.226 | +0.635 | -3.5140 | 0.2424 |
| pr | hier | 16655 | 6.375 | +1.428 | -3.5233 | 0.2448 |
| pra | eta_0.4+shot_0.5 | 16655 | 6.754 | +0.700 | -3.5884 | 0.2430 |
| pra | eta_0.4+shot_0.5+trkast_0.5 | 16655 | 6.755 | +0.700 | -3.5884 | 0.2430 |
| pra | eta_0.4+shot_0.5+trkast_1 | 16655 | 6.755 | +0.700 | -3.5884 | 0.2430 |
| pra | hier | 16655 | 6.923 | +1.629 | -3.5990 | 0.2456 |
| ra | eta_0.4+shot_0.5 | 16655 | 2.875 | +0.216 | -2.6896 | 0.2420 |
| ra | eta_0.4+shot_0.5+trkast_0.5 | 16655 | 2.875 | +0.215 | -2.6896 | 0.2421 |
| ra | eta_0.4+shot_0.5+trkast_1 | 16655 | 2.876 | +0.216 | -2.6899 | 0.2422 |
| ra | hier | 16655 | 2.940 | +0.549 | -2.7009 | 0.2458 |
| rebounds | eta_0.4+shot_0.5 | 16655 | 2.058 | +0.151 | -2.3020 | 0.2377 |
| rebounds | hier | 16655 | 2.106 | +0.348 | -2.3097 | 0.2397 |
| steals | eta_0.4+shot_0.5 | 16655 | 0.775 | -0.010 | -1.2416 | 0.2283 |
| steals | hier | 16655 | 0.787 | +0.041 | -1.2455 | 0.2296 |
| threes | eta_0.4+shot_0.5 | 16655 | 1.019 | +0.025 | -1.4890 | 0.2096 |
| threes | hier | 16655 | 1.028 | +0.085 | -1.4900 | 0.2098 |

## Paired vs `eta_0.4+shot_0.5` (positive = variant better; z = log-score gain / player-clustered SE; adopt bar z >= 2.81 and MAE gain > 0)

| stat | variant | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|---|
| points | hier | 16483 | -0.0066 | -3.73 | -0.1166 | -0.00193 |
| rebounds | hier | 16483 | -0.0076 | -7.85 | -0.0474 | -0.00196 |
| assists | eta_0.4+shot_0.5+trkast_0.5 | 16483 | -0.0005 | -2.85 | -0.0007 | -0.00018 |
| assists | eta_0.4+shot_0.5+trkast_1 | 16483 | -0.0018 | -4.28 | -0.0025 | -0.00059 |
| assists | hier | 16483 | -0.0048 | -5.62 | -0.0265 | -0.00120 |
| threes | hier | 16483 | -0.0011 | -2.41 | -0.0090 | -0.00018 |
| steals | hier | 16483 | -0.0039 | -8.25 | -0.0122 | -0.00126 |
| blocks | hier | 16483 | -0.0030 | -5.55 | -0.0078 | -0.00086 |
| pr | hier | 16483 | -0.0093 | -5.08 | -0.1495 | -0.00237 |
| pa | eta_0.4+shot_0.5+trkast_0.5 | 16483 | -0.0000 | -0.12 | -0.0005 | -0.00000 |
| pa | eta_0.4+shot_0.5+trkast_1 | 16483 | -0.0001 | -0.53 | -0.0014 | -0.00003 |
| pa | hier | 16483 | -0.0086 | -4.83 | -0.1364 | -0.00196 |
| ra | eta_0.4+shot_0.5+trkast_0.5 | 16483 | -0.0000 | -0.21 | -0.0002 | -0.00003 |
| ra | eta_0.4+shot_0.5+trkast_1 | 16483 | -0.0003 | -1.01 | -0.0011 | -0.00013 |
| ra | hier | 16483 | -0.0112 | -8.18 | -0.0647 | -0.00380 |
| pra | eta_0.4+shot_0.5+trkast_0.5 | 16483 | -0.0000 | -0.07 | -0.0005 | -0.00001 |
| pra | eta_0.4+shot_0.5+trkast_1 | 16483 | -0.0001 | -0.50 | -0.0013 | -0.00003 |
| pra | hier | 16483 | -0.0106 | -5.79 | -0.1694 | -0.00249 |

## Old engine vs `hier` (positive = `hier` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5` (positive = `eta_0.4+shot_0.5` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5+trkast_0.5` (positive = `eta_0.4+shot_0.5+trkast_0.5` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Old engine vs `eta_0.4+shot_0.5+trkast_1` (positive = `eta_0.4+shot_0.5+trkast_1` better)

| stat | n | d log score | z | d MAE | d Brier |
|---|---|---|---|---|---|

## Selected on this season

(none)
