# Model vs historical BettingPros market (NFL / NBA / NHL) -- 2026-10-15

First run of the BettingPros history on real parquets (`mlb_core/odds/bettingpros_loader.py` and the opt-in hooks in
`nfl.models.walkforward`, `nba.props.evaluate`, `nhl.props.study`). Read-only analysis; no deploys, no credits spent.
**This is calibration against a close-ish market, not CLV and not an edge claim.** BettingPros keeps ONE last snapshot per
day, so there is no closing-line series and CLV cannot be computed from it.

## Inputs
| Sport | Consensus rows (>=3 two-sided books) | Notes |
|---|---|---|
| NFL | 58,037 over 331 game days | 11 markets; build took 5h40m (serial GCS reads) |
| NHL (sog) | 74,222 | `--markets sog` |
| NBA | 382,996 | 5 markets: points, rebounds, assists, threes, pra |

Consensus = median of de-vigged two-sided real books (Kalshi, DFS, BettingPros-consensus and open rows excluded) at the modal
line. Rebuild with `scripts/join_bettingpros_history.py --sport <sport> --out <parquet>`.

## Coverage caveats (read before the numbers)
- **`postgame_quote` drops 28-36% of rows.** The dropped quotes were last updated a median of **1-2 minutes after the
  scheduled start** (quantiles 10/90: 0.01 / 0.06-0.10 hours, all three sports). There is no timezone offset (an offset would be
  hours). So the filter removes near-close quotes, and the surviving rows are earlier pre-start snapshots (median 0.1-0.3 h
  before start). A short grace window (about 10 minutes) would recover most of them; not tested.
- **NFL scored only 6-20% of consensus rows in 2022-24 and about 0 in 2025** (`no_model_row` dominates 2025; cause not
  diagnosed). NFL results below are therefore thin and should not be read as a verdict.
- NHL: 41% of consensus rows matched (36% postgame_quote, 17% no_game -- team-code/date mismatches not investigated).
- NBA: 55% scored (29% postgame_quote, 13% no_model_row, 3% unresolved).

## Results
### NFL (walk-forward 2022-2025, pooled over test seasons; only the four stats below were reviewed)
| Stat | n | Brier model / market | blend c (t) |
|---|---|---|---|
| pass_yds | 594 | 0.2508 / 0.2502 | +0.43 (+2.01) |
| rush_yds | 1,493 | 0.2637 / 0.2500 | +0.03 (+0.27) |
| rec_yds | 3,688 | 0.2597 / 0.2499 | +0.02 (+0.20) |
| receptions | 3,878 | 0.2539 / 0.2459 | +0.10 (+1.15) |

The model is worse than the market on every reviewed stat; `c` is indistinguishable from zero except pass_yds (t=+2.0 at
n=594, one of about ten comparisons -- not significant after a multiplicity allowance). No evidence of edge. The other six
NFL stats are in the generated report but were not read.

### NBA (all test seasons)
| Stat | n | Brier model / market | blend c (t) |
|---|---|---|---|
| points | 43,541 | 0.2571 / 0.2495 | +0.07 (+2.99) |
| rebounds | 44,276 | 0.2511 / 0.2469 | +0.19 (+7.71) |
| assists | 42,723 | 0.2505 / 0.2448 | +0.18 (+7.65) |
| threes | 40,639 | 0.2418 / 0.2395 | +0.28 (+8.32) |
| pra | 39,707 | 0.2596 / 0.2496 | +0.05 (+2.52) |
| all | 210,886 | 0.2520 / 0.2461 | +0.14 (+9.81) |

The model alone is worse than the market in Brier on every stat, yet the blend coefficient is significantly positive
(date-clustered): the model adds a small amount of information the market does not already carry. That is detectable, not yet
shown tradable -- no price/vig test was run for NBA. This is the one lead from this run. (Caveat: the NBA training frame uses
actual inactives while live only has the Out list, so the figure is an upper bound -- see CONTEXT s21.)

### NHL shots on goal
- Scores (run with compat first-name matches allowed, n=30,625 scored): Brier model 0.24488 vs market 0.24451; log-loss 0.68288 vs 0.68208; blend c = +0.30 (t = +4.9). Excluding compat matches (n=30,613) gives c = +0.31 (t = +4.9).
- Market fair P(over) averages 50.6% against a realised over rate of 48.5%.
- **That gap does not survive prices.** Flat bet on every Under at the best quoted price: **ROI -0.08%**, 95% CI (date-cluster
  bootstrap) -1.2% .. +1.1%, n=28,960 (compat excluded; unique price join of 30,613 scored). Always Over: -7.9%. Yearly Under ROI: 2022 -6.0%, 2023 -1.5%, 2024 +2.9%, 2025 -1.7%.
  "Best price" is a line-shopped, optimistic fill, and it is still break-even.

## Pooled-line shift model (`ev.props.shift_p_over`, `POOL_SD_FRAC`) -- still unvalidated
- A cross-book test (book A's price shifted to book B's point) is **not** a validation: books quoting different points disagree
  about the mean. The unshifted price won trivially (MAE 0.004-0.010 vs 0.010-0.036 shifted at production sd; shifted bias
  about +/-3 pp in the shift-up/down splits), which says nothing about the normal-shift or the sd fractions.
- The clean test is a single book's alt-line ladder (shared mean), but BettingPros history carries about one line per book:
  0 within-book pairs for pass_yds, 44 for rush_yds, 132 for rec_yds. It cannot run on this data.
- Remaining route: an outcome-based test (nflverse `player_stats` join) of pooled vs exact consensus.
- Probe: `scripts/pooled_shift_probe.py --mode cross|within`.

## Reproduce
```
GCS_BUCKET=<bucket> python scripts/join_bettingpros_history.py --sport nfl --out /tmp/nfl_cons.parquet
python -m nfl.models.walkforward --seasons 2022-2025 --features-cache /tmp/nfl_features.parquet \
    --market-parquet /tmp/nfl_cons.parquet --out /tmp/nfl_report.md          # NFL_MODEL_DIR=<scratch dir>
GCS_BUCKET=<bucket> python scripts/join_bettingpros_history.py --sport nba --out /tmp/nba_cons.parquet
python -m nba.props.evaluate --out /tmp/nba_report.md --preds-cache /tmp/nba_preds.parquet \
    --bettingpros-consensus /tmp/nba_cons.parquet --no-oracle
python scripts/join_bettingpros_history.py --sport nhl --markets sog --out /tmp/nhl_sog_cons.parquet
python scripts/nhl_sog_market_eval.py --consensus /tmp/nhl_sog_cons.parquet --masters-dir /tmp/nhl_masters
python scripts/pooled_shift_probe.py --mode cross
```
Use the repo `.venv` (the system python lacks pyarrow / google-cloud-storage). The consensus builds are slow and I/O bound
(about 1 h NHL, several hours NFL / NBA).

## Next
- NBA: price the blend (flat stake at best price when the blended probability beats the price by a threshold, walk-forward
  thresholds, date-clustered CIs) before treating the signal as anything more than calibration. A follow-up session was proposed.
- Test a 10-minute grace window for the postgame filter to recover the dropped near-close quotes.
- Diagnose the NFL 2025 `no_model_row` hole.

## Addendum 2026-10-16 -- coverage diagnosis (code in PR `feat/quant-audit-fixes`; no new model-vs-market numbers)
Measured on real GCS partitions (small samples, not a rerun of the full builds above):
- **`postgame_quote` is a timestamp-semantics issue, not an offset.** NFL passing-yards, 2024-10-06/07: `updated` is the last genuine quote change
  (median ~6.5 h and 4 h before the scheduled start, 0% after it). 2025-10-05/06 and 2025-11-30: 90-94% of stamps are AFTER the scheduled start,
  clustered 1-5 minutes after it (q50 0.03-0.05 h, q90 0.06-0.09 h; 24% of the 2025-10-05 rows are > 5 min). So the strict filter drops nearly the whole 2025
  close. `consensus_from_long(..., grace_minutes=)` / `join_bettingpros_history.py --grace-minutes N|auto` widens "pregame" to scheduled + N min
  (`auto` = 10 for NBA/NHL/NFL; default 0 keeps old results reproducible). Whether stamps after the start are ever in-play is unverified: compare scores
  with and without the grace before trusting the recovered rows.
- **NFL 2025 `no_model_row`:** on the sampled days every resolved consensus row matched a model row on `(gsis_id, ET game date)` (2024: 19/19, 2025: 46/46),
  so the join key is not the cause there; with the 2025 rows previously dropped as post-start, the full-build figure above was likely a mix of both effects.
  Rebuild NFL with `--grace-minutes auto` for the real coverage.
- **NHL `no_game`:** BettingPros uses `LA/NJ/SJ/TB` (and `ARI` in 2022-24); the game log uses `LAK/NJD/SJS/TBL` with `ARI` through 2024-04 and `UTA` after.
  `nhl.props.history.canon_team` maps them (ARI/UTA by date) and `attach_outcomes` retries a unique +-1 day match. The old blanket `ARI -> UTA` mislabelled
  411 Coyotes games.
- **Blend price test:** `mlb_core.odds.model_vs_market.blend_price_test` (walk-forward by season, flat 1u at the best price at >=3/4/5% over the price,
  date-clustered bootstrap CIs, `market` line-shopping and `model` standalone as controls) is wired into `nba.props.history_join.market_report` and
  `scripts/nhl_sog_market_eval.py`. **It has not been run on the real NBA/NHL consensus yet** (multi-hour builds); the c values in
  `mlb_core.risk.calibration.BLEND_C` are the table above, not a tradability result.
