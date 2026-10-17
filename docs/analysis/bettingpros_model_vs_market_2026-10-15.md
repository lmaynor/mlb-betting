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
  before start). A short grace window (about 10 minutes) would recover most of them -- **tested 2026-10-16: it does, 36% -> 2% dropped for SOG; see the follow-up at the bottom.**
- **NFL scored only 6-20% of consensus rows in 2022-24 and about 0 in 2025** (`no_model_row` dominates 2025; cause not
  diagnosed). NFL results below are therefore thin and should not be read as a verdict.
- NHL: 41% of consensus rows matched (36% postgame_quote, 17% no_game). **Follow-up 2026-10-16: the `no_game` rows were the `LA/NJ/SJ/TB` and ARI/UTA code mismatches (0 after the fix); coverage is now 98%.**
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
- ~~Test a 10-minute grace window for the postgame filter~~ (done 2026-10-16, follow-up below).
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

## Follow-up 2026-10-16: NHL SOG re-run with the grace window, team-code fix and full-history names

Branch `analysis/sog-bettingpros-slack-rerun` (stacked on PR #70). The coverage caveats above had three causes, all now fixed and
measured on the SAME consensus (74,222 rows, identical to the first run):
(1) the strict `pregame` flag (`updated_max <= scheduled`) dropped 27,028 rows (36%): near-close quotes, not stale ones;
(2) BettingPros `LA/NJ/SJ/TB` did not match the API's `LAK/NJD/SJS/TBL`, and Arizona is `ARI` in our game log until 2023-24 but `UTA` on the
quote side (the earlier 17% `no_game`; it is 0 after the fix); (3) names were resolved through `players_master.csv`, which holds only the
~790 CURRENT roster players (4,905 rows unresolved); `--names-from-masters` uses every skater who ever dressed (5,051 names).
Change: `study.bettingpros_model_vs_market(..., slack_min=10)` (default `None` = old behaviour), game-side team mapping, and the
result frame now carries the best / Novig prices so the fragile price re-join in the script is not needed for the new path. See
`docs/solutions/integration-issues/bettingpros-nhl-pregame-flag-and-team-codes.md`.

| config | consensus rows | scored | % | postgame_quote | unresolved | ambiguous | void | no_game | pooled n | log-loss model / market | blend c (t) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A. strict flag, current-roster names (original setup + team-code fix) | 74,222 | 41,525 | 56% | 27,028 | 4,905 | 238 | 486 | 0 | 41,525 | 0.6830 / 0.6822 | +0.32 (+5.8) |
| B. strict flag, full-history names | 74,222 | 46,222 | 62% | 27,028 | 45 | 280 | 605 | 0 | 46,222 | 0.6828 / 0.6822 | +0.30 (+5.7) |
| C. 10-minute grace window, full-history names | 74,222 | 72,485 | 98% | 100 | 83 | 403 | 1,015 | 0 | 72,485 | 0.6832 / 0.6824 | +0.34 (+8.2) |

A is the first run's setup PLUS the team-code fix (so its 56% already includes the 17% `no_game` recovered); the original run scored 41%.

Per season, config C. 10-minute grace window, full-history names:

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 72,485 | 0.490 | 0.507 | 0.483 | 0.6832 / 0.6824 | 0.34 (8.2) | +2.2% (22,356) | +5.9% (9.0) | -0.6% (-1.4) |
| 2022 | 12,136 | 0.487 | 0.499 | 0.484 | 0.6833 / 0.6829 | 0.41 (4.0) | +1.0% (3,377) | +5.2% (3.1) | -1.5% (-1.6) |
| 2023 | 18,845 | 0.483 | 0.510 | 0.486 | 0.6814 / 0.6815 | 0.29 (3.2) | +2.8% (5,575) | +6.9% (5.3) | +1.2% (1.5) |
| 2024 | 19,096 | 0.487 | 0.509 | 0.477 | 0.6845 / 0.6830 | 0.25 (3.3) | +2.4% (7,184) | +6.3% (5.5) | +0.5% (0.6) |
| 2025 | 20,758 | 0.502 | 0.507 | 0.487 | 0.6831 / 0.6819 | 0.40 (5.0) | +1.8% (5,548) | +4.9% (3.7) | -2.6% (-3.6) |
| 2026 | 1,650 | 0.480 | 0.492 | 0.472 | 0.6909 / 0.6876 | 0.31 (1.8) | +3.0% (672) | +5.2% (1.4) | -0.0% (-0.0) |

(Season labels are the START year. Under the strict flag 2025-26 has only 68 scored props, so the first note's yearly table could not
speak for it; with the grace window it has 20,758.) "c" is the blend coefficient: > 0 means the model adds information beyond the
market. "vs-ref" is the 5pp-disagreement ROI minus betting BOTH sides of the same props at the same prices.

Disagreement bets, all seasons pooled (gap = model P(over) - consensus P(over)):

| gap | price | bets | ROI (t) | win rate | no-skill ROI (both sides, same props) | vs no-skill (t) |
|---|---|---|---|---|---|---|
| 3pp | best | 38,643 | +1.3% (+2.6) | 52.8% | -3.7% | +5.1% (+10.0) |
| 3pp | novig | 10,331 | -1.5% (-1.6) | 52.3% | -5.3% | +3.8% (+4.0) |
| 5pp | best | 22,356 | +2.2% (+3.3) | 53.3% | -3.7% | +5.9% (+9.0) |
| 5pp | novig | 5,613 | -1.0% (-0.8) | 52.6% | -5.2% | +4.2% (+3.3) |
| 8pp | best | 8,352 | +2.6% (+2.3) | 53.4% | -3.6% | +6.1% (+5.7) |
| 8pp | novig | 1,987 | -0.3% (-0.1) | 52.9% | -4.5% | +4.3% (+1.9) |

### Reading it
- **Information beyond the market: yes, small, consistent.** Blend c = +0.34 (t = 8.2) pooled and positive in all five seasons
  (+0.25 to +0.41; t from 1.8 in the 1,650-prop 2026-27 start to 5.0). The model alone is slightly worse than the market in log-loss
  (0.6832 vs 0.6824), as in the first note. The 2026-10-14 SOG study found c = +0.10 +- 0.11 for a model trained on ~3 months of
  2025-26; its section 3 ("longer-history model check") was marked NOT RUN. This is that check: with 2019+ training the information
  content rises to c = +0.34, so that study's NO_EDGE was substantially a short-training-history effect on information, not a statement
  about the market.
- **Profitability is not established.** At the BEST book price the disagreement bets return +1.3% / +2.2% / +2.6% at 3 / 5 / 8pp (t 2.6 /
  3.3 / 2.3, clustered by game), monotone in the gap and positive in every season (t above 2 in two of five). At Novig's price (an exchange
  quote; the loader notes Novig only starts quoting in 2025, so this is a thinner, later sample: 5.6k bets at 5pp) they return -1.0% (t = -0.8): break-even. The no-skill reference is -3.7% at best price, so side
  selection is worth about +4 to +6pp, but the "best price" is a line-shopped, optimistic fill and the hold is what it eats.
- **Blind sides remain ~0:** blind Under at best price -0.6% (t = -1.4) pooled (first note: -0.08%); it flips sign by season (+1.2% in 2023, -2.6% in 2025).
- **Status: UNDETERMINED, not promotable.** Unchanged rule: CLV, which this one-snapshot-per-day feed cannot give. Of the NHL markets scored so far
  (SOG here; saves / goals / assists / points in PR #70) SOG is the only one whose pooled best-price disagreement ROI is positive with t > 3
  AND positive in every season; goals / assists / points were ~0% or negative and saves flipped negative in 2025-26. (The information
  signal itself is comparable on goals and assists, c +0.38 / +0.34.) Looks here: 3 gaps x 2 prices x 6 slices, so single cells below |t| ~ 3 are noise; the pooled 5pp best-price cell
  (t = 3.3) was not pre-registered.

Reproduce (needs local copies of the masters and the BettingPros partitions; use `gcloud storage cp`, not `gsutil -m`):
```
PYTHONPATH=. python scripts/nhl_sog_market_eval.py --bp-dir <dir containing shots/*.parquet> --masters-dir <dir with NHL masters + shots/> \
    --names-from-masters --slack-min 10 --json out.json      # 15-30 min: 49 monthly refits
```
