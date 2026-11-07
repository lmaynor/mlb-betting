# Verification of PR #189 (skewed NBA points marginal) on the held-out player-games (2026-11-05)

**What this is.** PR #189 (`feat/nba-skewed-points-marginal`, head `554cc032`, not merged, awaiting Argon) replaces the NBA points Normal with a 3 x threes + nonthree
marginal at the board's own (mu, sd), and says it did NOT re-run the 34k-game calibration because the masters live in GCS. This note re-runs that walk-forward on the board
AS SERVED: the PR's own `ev.daily_projections` through `nba.props.engine_eval.replay`, `NBA_SKEWED_POINTS=0` versus `=1`, on 2024-25 + 2025-26.
Nothing in #189 was touched or commented on. **Accuracy, not edge: no price, CLV or ROI is claimed here.**

## Verdict

**Partially reproduces. Recommendation: default-ON is defensible but only after the two fixes below, and the PR's gain figures should be restated.**

| Claim in #189 | Re-run | Result |
|---|---|---|
| L10-proxy line: ECE 0.0471 -> 0.0053, log-score +0.0047 | ECE 0.0472 -> 0.0072, +0.0043 (z +7.7) | **Reproduces** (91% of the gain) |
| Model-centred lines: ECE 0.026 -> 0.011, log-score +0.0071 (z +25) | ECE 0.0259 -> 0.0101, **+0.0023 (z +11.3)** | **ECE reproduces, log-score gain is one third of the quoted figure** |
| Mean and sd preserved | mean error 9e-5 (max +1.18; 6 rows off by > 0.25), sd ratio 0.999 - 1.003 | **Reproduces** (with a floor bug, below) |
| pra / pr / pa / ra unchanged | exactly equal (p_over, mu, sd, log score) on all 34,451 rows each | **Confirmed** |
| Nothing else in `make_row` moves | only `p_over_model`, `p_model_side`, `side`, `proj_p10`, `proj_p90` (points) | **Confirmed**, but `side` flips on 26% of points rows (below) |
| (not claimed) real banked lines | n = 11,828: ECE 0.0965 -> 0.0947, log-score +0.0016 (z +1.8, not significant) | **No demonstrated benefit on real lines** |

Why partially: (1) the gain is concentrated at lines at or above the board's mean; the closed form has a **thin left tail** (P(0 points) = 0.3% against 2.9% observed), so it is
*worse* than the Normal for lines far below the mean (z <= -1.5: log-score -0.017, z -11.5), which the simulator's minutes mixture did not have; (2) a Normal merely shifted by
-0.11 sd (one constant, fit on 2024-25 only) recovers about 90% of the proxy-line gain and about two thirds of the model-centred gain, so most of what #189 buys is the median
offset, not detailed shape; (3) against the real BettingPros book lines neither variant has any resolving power (actual over-rate is 0.42 - 0.54 in every bucket with n > 100; both are
worse than always saying 0.5) and the de-vigged market beats both by 0.036 - 0.038 log loss.

## Setup

* Code: PR #189 head `554cc032` in a detached worktree; `engine_eval.replay` with variant `eta_0.4+shot_0.5` (= the shipped `NBA_FEATURES` defaults), stats points / rebounds /
  assists / threes / pr / pa / ra / pra, `HISTORY_SEASONS=2`, oracle absences, no spreads (all as `engine_eval` documents; the absolute numbers are an upper bound, the pairing is fair).
* Data (read-only): `gs://concrete-crow-445205-m4-mlb-data/NBA/player_boxscores_master.csv` (refreshed 2026-11-05; 34,451 eligible points rows, #186 had 34,429: 17,037 in
  2024-25, 17,414 in 2025-26, same eligibility rule L10 minutes >= 20). Real lines: `Odds/bettingpros_nba/points/*.parquet` (BettingPros historical crawl), per (date, player) the
  modal line across `book_kind == "book"` rows, 2024-10-22..2026-06-13; every modal line is x.5; **covers 34% of the replay rows** (11,828).
* Four replays (flag 0 / 1 x two seasons), every `res` dict kept, so the Normal and skewed prices are computed on identical (mu, sd, mu3, sd3). Flag-1 served `p_over` equals the
  recomputed skewed price to 0.0, and the hand-computed Normal equals the flag-0 served `p_over` to 0.0.
* Lines: L10 proxy (`floor(L10 mean)+0.5`, as `engine_eval`), model-centred `floor(mu + z sd)+0.5`, and the real modal line. Metrics: ECE with 10 equal-width bins; paired
  binary log loss of P(over) against `y > line`; **date-clustered** SE (the #186 note clustered on games, so the z values here are somewhat smaller for the same effect).

## 1. Calibration of P(over), points (Normal -> skewed)

| lines | n | actual over | mean P(over) N / S | ECE N -> S | log loss N / S | paired gain (z) |
|---|---|---|---|---|---|---|
| L10 proxy, pooled | 34,451 | 0.458 | 0.502 / 0.453 | 0.0472 -> 0.0072 | 0.6797 / 0.6754 | +0.0043 (+7.7) |
| L10 proxy 2024-25 | 17,037 | 0.458 | 0.502 / 0.453 | 0.0466 -> 0.0090 | 0.6781 / 0.6740 | +0.0041 (+5.1) |
| L10 proxy 2025-26 | 17,414 | 0.457 | 0.502 / 0.453 | 0.0479 -> 0.0079 | 0.6813 / 0.6767 | +0.0045 (+5.9) |
| model z = -1 | 34,451 | 0.832 | 0.841 / 0.850 | 0.0088 -> 0.0173 | 0.4517 / 0.4516 | +0.0001 (+0.4) |
| model z = 0 | 34,451 | 0.447 | 0.500 / 0.447 | 0.0529 -> 0.0034 | 0.6922 / 0.6866 | +0.0057 (+10.0) |
| model z = +1 | 34,451 | 0.144 | 0.159 / 0.154 | 0.0160 -> 0.0095 | 0.4133 / 0.4122 | +0.0011 (+12.1) |
| **model-centred, stacked z = -1, 0, +1** | 103,353 | | | **0.0259 -> 0.0101** | 0.5191 / 0.5168 | **+0.0023 (+11.3)** |
| REAL banked lines, pooled | 11,828 | 0.495 | 0.526 / 0.480 | 0.0965 -> 0.0947 | 0.7298 / 0.7282 | +0.0016 (+1.8) |
| REAL 2024-25 / 2025-26 | 5,890 / 5,938 | 0.496 / 0.493 | | 0.097 -> 0.099 / 0.096 -> 0.091 | | +0.0012 (+0.9) / +0.0020 (+1.6) |

The study's model-centred log loss for the Normal (0.5190) is reproduced exactly (0.5191); the simulator reached 0.5119, the closed form reaches 0.5168. The missing 0.0049 is the
left side (section 2).

Real lines against the de-vigged market (same 11,828 rows, mean of two-sided real books at the modal line): log loss market **0.6921**, Normal 0.7298 (+0.0377, z +14.1), skewed 0.7282
(+0.0361, z +13.5); market ECE 0.0083. A constant 0.5 scores 0.6931, so on real lines both model variants are worse than a coin. The skewed price roughly halves the average bias
(Normal +0.031, skewed -0.015 against the actual 0.495) but adds no resolution.

### Reliability by predicted-probability bucket (pooled; pred = mean P(over), act = realised over-rate)

L10 proxy lines:

| bucket | n N | pred N | act N | n S | pred S | act S |
|---|---|---|---|---|---|---|
| 0.1-0.2 | 142 | 0.161 | 0.310 | 221 | 0.168 | 0.321 |
| 0.2-0.3 | 748 | 0.261 | 0.302 | 1,433 | 0.264 | 0.320 |
| 0.3-0.4 | 3,504 | 0.363 | 0.350 | 7,539 | 0.361 | 0.359 |
| 0.4-0.5 | 11,768 | 0.457 | 0.397 | 15,198 | 0.450 | 0.448 |
| 0.5-0.6 | 13,666 | 0.545 | 0.498 | 8,220 | 0.540 | 0.551 |
| 0.6-0.7 | 4,216 | 0.635 | 0.594 | 1,673 | 0.633 | 0.634 |
| 0.7-0.8 | 388 | 0.726 | 0.691 | 152 | 0.725 | 0.730 |
| 0.8-0.9 | 14 | 0.823 | 0.857 | 13 | 0.833 | 0.846 |

The Normal over-predicts over in every bucket from 0.4 up by 0.035-0.06; the skewed price sits on the diagonal from 0.3 to 0.9 and under-predicts over below 0.3 (0.17 against 0.32).

REAL banked lines (the 10 buckets are almost flat at 0.43 - 0.54 for both):

| bucket | n N | pred N | act N | n S | pred S | act S |
|---|---|---|---|---|---|---|
| 0.1-0.2 | 139 | 0.157 | 0.446 | 175 | 0.159 | 0.457 |
| 0.3-0.4 | 960 | 0.359 | 0.514 | 1,832 | 0.361 | 0.506 |
| 0.4-0.5 | 2,924 | 0.459 | 0.493 | 4,331 | 0.453 | 0.497 |
| 0.5-0.6 | 4,506 | 0.548 | 0.496 | 3,268 | 0.544 | 0.485 |
| 0.6-0.7 | 2,229 | 0.641 | 0.484 | 1,179 | 0.641 | 0.502 |
| 0.7-0.8 | 590 | 0.741 | 0.536 | 353 | 0.743 | 0.538 |
| 0.8-0.9 | 109 | 0.837 | 0.422 | 95 | 0.841 | 0.432 |

The real modal line already sits near the median (line z = (line - mu)/sd has median -0.08, 5-95% range -0.56 to +0.49), so a model that moves P(over) by 0.1-0.4 away from 0.5
against the market is mostly wrong; the skew shifts those probabilities, it does not make them informative.

## 2. Behaviour at low and high lines, and P(over) direction vs the Normal

Model-centred lines `floor(mu + z sd) + 0.5`, pooled 2024-25 + 2025-26, 34,451 rows each (bias = mean predicted - actual over-rate):

| z of line | actual over | bias Normal | bias skewed | skewed minus Normal log loss gain (z) |
|---|---|---|---|---|
| -2.5 | 1.000 | -0.006 | 0.000 | +0.0063 (n/a, base rate 1) |
| -2.0 | 0.992 | -0.014 | +0.008 | -0.0031 (-1.7) |
| **-1.5** | 0.940 | -0.007 | **+0.029** | **-0.0166 (-11.5)** |
| -1.0 | 0.832 | +0.009 | +0.017 | +0.0001 (+0.4) |
| -0.5 | 0.662 | +0.029 | -0.007 | +0.0016 (+3.8) |
| 0 | 0.447 | +0.053 | 0.000 | +0.0057 (+10.0) |
| +0.5 | 0.263 | +0.046 | +0.011 | +0.0056 (+13.8) |
| +1.0 | 0.144 | +0.015 | +0.010 | +0.0011 (+12.1) |
| +1.5 | 0.076 | -0.009 | +0.005 | +0.0006 (+2.0) |
| +2.0 | 0.040 | -0.017 | 0.000 | +0.0050 (+8.6) |
| +2.5 | 0.021 | -0.015 | -0.003 | +0.0105 (+12.2) |

* **Direction.** At lines inside about +-0.5 sd of the mean the skewed P(over) is lower than the Normal's by 0.05 on average (it is lower on 99.9% of the L10-proxy rows; mean
  -0.049; at the real lines -0.046, 5-95% range -0.062 to -0.020). Above +0.5 sd the two nearly agree (-0.009, -0.003) and above +1.5 sd the skewed price is higher (right tail).
  Below -1 sd the skewed price is *higher* (+0.012 at -1.5..-1) and wrong.
* **High lines.** L10-proxy lines >= 26.5: actual over 0.398, Normal predicts 0.495 (bias +0.097), skewed 0.435 (+0.037), gain +0.0163 (z +5.7). Real lines >= 26.5 (n = 610): Normal bias
  +0.022, skewed -0.038, gain -0.0022 (z -0.4).
* **Low lines.** Real lines <= 10.5 (n = 4,248): Normal bias +0.058, skewed +0.018, gain +0.0040 (z +2.4). Real lines 11.5-15.5: bias +0.006 / -0.037, gain 0.0000.
  The failure is further out: at z = -1.5 the Normal predicts 0.933, the skewed price 0.968, actual 0.940; the skewed rows with P(over) in [0.9, 0.95) (n = 1,468) predict 0.946 and
  realise 0.868, those >= 0.95 (n = 32,983) predict 0.969 and realise 0.943.
* **Cause (left tail).** 2.9% of eligible player-games have 0 points (995 rows; early exits, DNP-after-tip) and 6.4% have <= 2. The skewed marginal gives P(Y = 0) = 0.3% and
  P(Y <= 2) = 3.1%; the Normal's mass below zero leaks onto 0 and gives 6.1% for <= 2 by accident. The PIT histogram confirms it: lowest 5% bin holds 7.9% of outcomes under the skewed
  price (Normal 4.2%), while the overall PIT is much flatter (KS distance 0.029 against 0.055).
* **Own-distribution log score of the outcome** (`engine_eval._logp` still scores points with the Normal, so `engine_eval`'s own points log score cannot see this change): skewed
  -3.380 against Normal -3.343 (gain -0.037, z -8.4). Excluding the 995 zero-point rows the gain is **+0.067 (z +27.9)**; on the zero rows alone it is -3.54 per row. So the shape is
  much better for every game in which the player plays, and the price of 0 points is badly short.

## 3. Mean / sd preservation and which route each row takes

Moments of the served skewed pmf against the board's (mu, sd), all 34,451 points rows: mean error mean +0.0001, 0.1%..99.9% range -0.0015..+0.0098, max +1.18; sd ratio 0.9991..1.0028
(no row outside +-5%). Board mean 14.60, sd 7.27, 3 x mu3 is 36.9% of mu on average.

| route in `_nba_points_skew_components` | rows | share | actual over / P(over) N / S (proxy) | log-score gain at proxy (z) | at z = 0 (z) |
|---|---|---|---|---|---|
| split: 3 x NegBin(threes) + NegBin/gamma(nonthree), as described | 31,018 | 90.0% | 0.455 / 0.504 / 0.455 | **+0.0055 (+9.2)** | **+0.0069 (+11.4)** |
| threes variance shrunk to leave the nonthree leg dispersion | 1,178 | 3.4% | 0.487 / 0.489 / 0.440 | -0.0058 (-2.1) | -0.0055 (-1.9) |
| single gamma on the total (the threes variance does not fit) | 1,583 | 4.6% | 0.491 / 0.475 / 0.415 | -0.0101 (-3.0) | -0.0091 (-2.5) |
| no threes leg (mu3 or sd3 <= 0) | 672 | 2.0% | 0.460 / 0.492 / 0.448 | +0.0028 (+0.8) | +0.0026 (+0.7) |

The 90% of rows that take the described construction carry the whole gain. The two "degenerate input" ladders the PR added (8% of rows; 36% of players with mu <= 8 take the single-gamma route) **lose** to the Normal and push P(over) to 0.415-0.44 against an actual 0.49; they have no validation behind them.

## 4. Everything else in `make_row` / combos

* **Flag 0 vs flag 1, all 8 stats, 34,451 rows each (exact array equality):** mu, sd, `p_over`, `logp`, `abs_err`, outcome identical for rebounds, assists, threes, **pr, pa, ra,
  pra** (max |d p_over| = 0, 0 rows differ); for points only `p_over` differs (34,451 rows, max 0.091) and mu / sd / logp are identical. flag-0 prices equal the hand-computed pre-#189 Normal formula to 0.0.
* **`make_row` differential**, 1,640 board rows (205 players x 8 stats, December 2025, real replay inputs, real BettingPros line for points where banked, `cons=None`), 88,560 field
  comparisons: the only fields that differ are on points: `p_over_model` (mean -0.049, range -0.076..-0.009), `p_model_side` (mean +0.016), `side` (**54 of 205 points rows flip
  over -> under**, this test has no market so `side` is decided by P(over) against 0.5), `proj_p10` (+0.87, up to +2.8) and `proj_p90` (+0.42). Every combo row and every other stat is
  field-for-field identical. With a real consensus the market-relative edge fields (`model_edge`, `market_ev`, blend EV, tier gates) will follow `p_over_model`: expect the points
  board to lean toward Under by about 4.6 probability points on average at the real lines (5-95%: 2.0 - 6.2) relative to today. That was not run with a market.
* **`proj_p10` / `proj_p90`** (replay, all rows): p10 mean 5.28 -> 6.15 (shift +0.87, 1-99%: -0.09..+2.52), p90 23.92 -> 24.29 (+0.37, -0.25..+1.06), and they are now integers.
  Empirical coverage: y < p10 10.6% (Normal 10.1%), y > p90 8.3% (Normal 10.1%); inclusive (y <= p10 / y >= p90) 14.6% / 10.6%. Neither claims validated coverage.
* `NBA_SKEWED_POINTS` is read each call (`os.environ`), so the kill switch works in-process; with it off `_nba_component` supplies no split and prices are bit-identical.
* `calibration("nba", ...)` returns None for every non-NFL sport, so `mult` is 1.0 for NBA today and the "calibration multiplier on mu3" path is not exercised (it is proportional, so it
  would be correct if an NBA coefficient were added).
* No other caller of `_nba_component` / `p_over` prices NBA points; `nba/sim/adapter.py` reads `mu` / `sd` only, which are unchanged. The unit file `tests/test_nba_skewed_points.py`
  passes (15 / 15).

## 5. Control: is it the shape or just a median offset?

Normal with its mean moved by k sd, k = -0.11 fitted on 2024-25 L10-proxy lines only (the median of (y - mu)/sd is -0.127), scored on 2025-26:

| lines | ECE N / shifted N / skewed | log loss N / shifted N / skewed |
|---|---|---|
| L10 proxy | 0.0479 / 0.0115 / 0.0079 | 0.6813 / 0.6772 / 0.6767 |
| model z = 0 | 0.0522 / 0.0109 / 0.0023 | 0.6920 / 0.6867 / 0.6864 |
| model-centred stacked | 0.0258 / 0.0153 / 0.0096 | 0.5182 / 0.5167 / 0.5159 |
| REAL banked | 0.0959 / 0.0893 / 0.0906 | 0.7295 / 0.7279 / 0.7275 |

A one-parameter shift recovers 89% (proxy) / 95% (z = 0) / 65% (stacked) of the skewed price's gain. The skewed shape adds real ECE improvement in the tails (stacked 0.0153 -> 0.0096) but
this is why the real-line benefit is nil: the market has already done the median correction.

## 6. Defects found in the diff (failing inputs)

1. **Fallback ladders lose to the Normal (8% of rows).** The `gamma_total` and threes-variance-shrink routes (section 3) are unvalidated and score -0.006..-0.010 against the Normal
   (z -2 to -3), lowering P(over) by ~0.06 with no support. Failing example: `mu=7.5, sd=3.9, mu3=1.9, sd3=1.7` (9 x sd3^2 = 26 > 15.2 total variance) returns no split and prices a single gamma, P(over 6.5) = 0.537
   against the Normal's 0.601.
2. **Left tail too thin / zero-point games.** P(0) = 0.3% vs 2.9%; any line at or below mu - 1.5 sd is over-priced as an Over (0.968 predicted, 0.940 realised; >= 0.95 buckets).
   Not reachable at main lines (real lines never sit below z = -1.4, 5th percentile -0.56), reachable by alt rungs or low-line props if they are ever priced from `p_over`.
3. **The 0.05 x mu floor breaks mean preservation when threes and points projections disagree** (3 x mu3 > ~0.95 mu): 78 rows (0.23%), 6 rows off by more than 0.25 points. Failing input
   (AJ Green 2026-02-13 replay row): `mu=10.4467, sd=5.8223, mu3=3.7268, sd3=2.3020` -> the served pmf has mean **11.63** (+1.18, +11%), sd 5.835; P(over) is inflated accordingly. The
   mean of the construction is 3 x mu3 + 0.05 mu whenever the floor binds. Cap mu3 at mu/3 (or rescale the threes leg) rather than flooring N.
4. **NaN in the split raises, it does not degrade.** `p_over("normal", 20, 19.5, None, sd=7, mu3=float("nan"), sd3=2.0)` -> `ValueError: cannot convert float NaN to integer`
   (`int(nbinom.ppf(...))` in `nba_points_skew_cdf`; same in `_nba_points_skew_pmf` via `proj_range`, with a NaN `sd3` too). The Normal path returns NaN for a NaN sd. Neither
   `make_row` call site (`slate_rows`, `rotation_rows`) has a try/except, so one NaN threes projection would abort the sport's board build. Zero such rows in the 34,451 replayed
   (threes component was never None either), so latent; guard with `math.isfinite` and fall back to the Normal.
5. **Under-dispersed threes inflate the sd (unreachable today).** `_negbin_n_p` clips alpha to 1e-6, so a threes leg with sd3^2 < mu3 is priced at variance mu3: `mu=20, sd=7, mu3=2,
   sd3=0.3` -> total sd **8.14** (+16%). The board's threes component is a NegBin (sd3 >= sqrt(mu3)), replay sd ratio is 0.999-1.003, so this only bites if the threes family changes.
6. **Whole-number lines.** `p_over(24.0) == p_over(24.5) == 0.4427` while `p_over(23.5) = 0.4922` (Normal: 0.5000 / 0.4751 / 0.5249): the push mass (pmf(24) = 0.0495) is on
   neither side, and anything computing under as `1 - p_over` books it as an Under win. Same convention as the count stats, so deliberate; 1 of 74,950 banked NBA points book lines is
   a whole number, so immaterial for points.
7. **Kill switch only recognises "0".** `NBA_SKEWED_POINTS=false`, `off`, `no` or empty leave the skewed price ON (`!= "0"`).
8. **Harness gap.** `engine_eval._logp` and `levels()` still score points with the Normal; only the Brier at the proxy line sees the change. The own-distribution score in section 2 was
   computed outside `engine_eval`. Worth a `family="points_skew"` branch so future replays measure the served distribution (and would have shown the zero-point cost).
9. Cost: points now also computes the threes component (2 components instead of 1 on points rows; pra / pr / pa each gain one extra threes component through `_nba_component("points")`); when the threes component is None the row silently
   prices the Normal (0 rows in the replay).

## 7. Recommendation on default-ON

* **For ON:** at the lines the board actually posts (real lines sit within -0.6..+0.5 sd) the skewed price removes a clear 3-5 point over-bias (Normal mean P(over) 0.526 against 0.495
  realised; skewed 0.480), ECE falls 0.047 -> 0.007 at the proxy line in both seasons, the real-line log score moves the right way (+0.0016, both seasons positive, z +1.8) and
  nothing else on the board changes. It is a lower-bias price than the Normal on 90% of rows.
* **Against ON as written:** the headline gains are overstated by the PR description (+0.0071 model-centred is +0.0023 here); the real-line benefit is not statistically established;
  8% of rows take unvalidated ladders that lose; the left tail is wrong for low lines; one NaN can abort a board build; and it systematically moves the points board toward Under
  with no demonstrated edge (the model is worse than 0.5 against real lines either way).
* **Position:** keep default ON only if the PR (a) guards non-finite mu3/sd3, (b) replaces the floor with a cap so the mean is preserved, (c) drops or validates the two fallback ladders
  (falling back to the Normal on those rows would recover 0.006-0.010 of log loss per row on 8% of rows), (d) restates the model-centred gain as +0.0023 and describes it as a median/skew correction, not
  an edge, and (e) either adds a zero-minute/early-exit mass or limits the skewed price to lines >= mu - 1 sd. Without (a)-(c) ship default OFF and flip it after review. This
  note supports calibration only; it says nothing about CLV or profit, which the repo's own rule requires before any pick-quality claim.

## What was NOT verified

* No price, CLV or ROI; real lines are the BettingPros modal book line per day (snapshot timing is not closing-line exact), 34% coverage, and only points were banked for this test
  (no real pra lines were scored, combos were checked for equality, not calibration).
* The closed form was not re-compared with the simulator's draws (the `nba/sim` replay was not re-run); the "one third" figure compares the closed form's model-centred gain against the
  simulator's published +0.0071, from the #186 note.
* Oracle absences (upper bound), no spreads, 2026-27 season not replayed, one engine variant (`eta_0.4+shot_0.5`).
* The `make_row` differential had no market (`cons=None`), so edge / EV / tier / blend fields were not exercised end to end; `side` flips are therefore a P(over)-vs-0.5 proxy.
* ECE uses 10 equal-width bins and is sensitive to bucket occupancy; log-loss gains are the more reliable column. z values are date-clustered (#186 used game clusters).
* The full repo test suite was not re-run (the PR's own 15 tests pass; PR reports 3,511 passed with a failure set identical to main).

## Reproduce

    gcloud storage cp gs://concrete-crow-445205-m4-mlb-data/NBA/player_boxscores_master.csv .          # read-only
    gcloud storage cp -r "gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nba/points/*" bp_points/
    git fetch origin pull/189/head:pr189 && git worktree add --detach ../pr189wt pr189
    # in the worktree: for FLAG in 0 1, SEASON in 2024 2025: NBA_SKEWED_POINTS=$FLAG, wrap nba.props.engine_eval._p_over to keep each `res`
    # (family, mu, sd, mu3, sd3) and its line, then engine_eval.replay(index, dnp, [SEASON], {"ship": ("new", parse_variant("eta_0.4+shot_0.5"), STATS)});
    # price other lines with DP.nba_points_skew_cdf (served) and scipy.stats.norm (the old Normal); score `y > line`.
