# NBA props: does the model beat real book prices? (BettingPros history) -- 2026-10-23

**Verdict: INSUFFICIENT. No tradable edge is shown; at executable prices the evidence leans NO_EDGE.**
The model carries a small, real piece of information the market lacks (calibration), but the profit that looks large at the
"best price" is overwhelmingly a best-price / last-quote artifact: a no-model line-shopping control earns MORE than the blend.
At median-book prices the blend is +1.0% (CI -0.2..+2.1%), negative in 2024, and non-monotone in the edge threshold.
CLV, the repo's go/no-go, cannot be computed from this one-snapshot-per-day feed. Read-only analysis; no deploys, schedulers,
credits or prod writes. Full output tables: `nba_props_bettingpros_price_test_2026-10-23_tables.md`.

## Setup
- Consensus rebuilt 2026-10-23 from `Odds/bettingpros_nba` (383,285 rows, 5 markets, >=3 two-sided real books; identical row
  counts per market to the extras rebuild). The "slow" build is GCS latency: the whole tree is only 161 MB, so
  `gcloud storage cp -r` to a local dir makes any further pass take about a minute.
- Model rows and identity join = `nba.props.evaluate` / `history_join.join_stat` (fail-closed). Test seasons 2022-2025 (season = start
  year); the blend `p = sigmoid(a + b*logit(p_mkt) + c*(logit(p_model) - logit(p_mkt)))` is fitted per stat on EARLIER seasons only, so
  2022 is never scored: **scored = 2023-24, 2024-25, 2025-26 (n = 239,315 props, grace +10 min)**.
- Flat 1 unit; edge = side probability minus implied probability of that side's price; CIs = date-clustered bootstrap (1,000).
- The strict-filter run reproduces the 2026-10-15 numbers exactly (210,886 scored, Brier model 0.2520 / market 0.2461, c = +0.14, t = +9.8).

## 1. Information signal: real but small
Out-of-sample (walk-forward) Brier: market 0.24654, blend 0.24618, model alone 0.25276. Paired dBrier(blend - market) = -0.00036
[-0.00046, -0.00026]; log-loss 0.68615 -> 0.68541. Fitted c by stat (earlier seasons only): rebounds +0.18..0.27, threes +0.26..0.30,
assists +0.17..0.20, points +0.04..0.07, PRA ~0..+0.04. Consistent with the earlier note: a few hundredths of Brier, in the
market-anchored blend only; the model alone is worse than the market.

## 2. Profit at the BEST price looks large and is not credible
| edge >= | blend ROI [95% CI] (n) | line shopping only, NO model (n) | blend vs betting both sides |
|---|---|---|---|
| 0% | +3.2% [+2.4, +4.0] (109,593) | +2.1% (63,524) | +4.3% |
| 3% | +10.6% [+8.7, +12.8] (24,706) | **+17.1%** (9,579) | +8.0% |
| 5% | +18.5% [+13.9, +23.3] (9,079) | **+35.8%** (3,656) | +10.8% |
| 10% | +89.7% [+63.2, +120.8] (1,273) | +104.1% (1,044) | +44.5% |

Reasons not to believe it (each is measured here):
1. **The control beats the model.** Picking whichever book's price beats the consensus fair price, with no model at all, earns +17% at
   3% and +36% at 5%. No efficient market pays that; it is the best-price selection, not skill.
2. **Best-price overround is +3.2% on average but 9.0% of props show an arbitrage-looking best pair** (implied_over + implied_under < 1): the
   best price is frequently a stale or outlier quote. ROI also rises with market depth (3-5 books +3.9%, 6-9 +11.3%, 10+ +17.4% at 3%): a
   max over more books is a more optimistic fill.
3. **Not an edge ladder.** Blend ROI by edge bucket 0-2% +0.6%, 2-4% +3.5%, 4-6% +7.6%, 6-10% +6.6% (non-monotone), then +89.7% at 10%+ --
   the "only the extreme bucket pays" signature in `docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md`.
4. **Look-ahead through quote age.** Each row is the LAST quote of the day; a book's best price can be a quote that was last updated
   hours earlier, which the later consensus then makes look like value. Where stamps are genuine (2023; 2024 mostly) blend ROI
   at edge >= 3% is +6.9% (2023) / +4.0% (2024) when the best quote is < 30 min old and +5% .. +22% when it is 0.5-10+ h old (3-10 h bucket: +11.5% / +16.2%; line shopping +19% / +23%): older quotes pay more, but the pattern is noisy. In 2025-26, 88% of selected quotes are stamped at/after the
   scheduled start (a freeze stamp, per the 2026-10-16 loader notes), so quote age is unknowable exactly where the headline ROI is highest
   (+16% at 3%, +28% at 5%, line shopping +22% / +42%).
5. The walk-forward threshold choice (section E in the tables) gives +41% pooled out-of-sample (+71% in 2025 at an 8% threshold, n=1,528). It
   is the same artifact: higher thresholds select more outlier prices, so "choose the best threshold on earlier seasons" only picks the
   most extreme price filter. Not evidence.

## 3. Executable prices: what is left
Same blend rule, selection and payout at the stated price (edge >= 3% / 5%):
| price used | edge >= 3% | edge >= 5% | always Under | always Over |
|---|---|---|---|---|
| best | +10.6% [+8.8, +12.7] (24,706) | +18.5% (9,079) | -0.9% | -5.0% |
| second-best book | +6.9% [+5.1, +8.9] (10,656) | +8.1% [+4.3, +11.6] (2,913) | -2.7% | -7.0% |
| median book | +3.3% [+0.2, +6.3] (3,502) | -14.5% (211, noise) | -4.5% | -8.7% |

The no-model line-shopping control still earns +9.0% at second-best price (n=1,822), so even the second-best fill is contaminated by the same
effect. The cleanest read of the model's own side selection is the median-book sweep:

| edge >= | blend ROI at median-book price [CI] (n) | 2023 / 2024 / 2025 |
|---|---|---|
| 0% | +1.0% [-0.2, +2.1] (23,818) | +2.3 / -1.0 / +2.2 |
| 1% | +1.8% [+0.0, +3.4] (12,028) | +4.1 / -0.5 / +1.2 |
| 2% | +2.4% [+0.2, +4.5] (6,442) | +4.2 / +0.2 / +2.9 |
| 3% | +3.3% [+0.3, +6.2] (3,502) | +3.1 / +3.5 / +3.3 |
| 4% | -0.0% [-4.5, +4.2] (1,845) | -1.0 / +0.5 / +1.5 |

Small, mostly positive, CIs graze zero, 2024 is flat/negative, and the 4% cell vanishes. About 5 thresholds x 3 seasons were looked at, so a single
cell at t ~ 2 is not distinguishable from noise. The median book price also overstates what one account can get on each prop, and does not
price limits, stake caps or price movement between the quote and the bet.

## 4. Baselines (blind sides at best price, all scored props)
Always Over -5.0% [-5.5, -4.5]; always Under -0.9% [-1.4, -0.4]. By stat, Under: rebounds +1.6% [+0.6, +2.5], threes +0.3% [-0.6, +1.3],
points -1.5%, assists -2.4%, PRA -2.6%. By season, Under: 2023 -1.1%, 2024 -2.1%, 2025 +0.6% [-0.4, +1.5]. Over rate 0.484 vs mean consensus
P(over) 0.495: the market's 1.1pp over-lean is eaten by the vig, as for NHL SOG (-0.08%). The only slice where blind Under is positive with a CI
excluding 0 (rebounds) is a best-price result and flips sign by season in the tables, so it is not promoted.

## 5. Grace window for the postgame filter (10 minutes): recommended, no in-play signature
| | consensus rows | scored | postgame_quote dropped |
|---|---|---|---|
| strict (updated <= start) | 383,285 | 210,886 (55%) | 109,863 |
| +10 min | 383,285 | 300,341 (78%) | 843 |

Recovered rows (updated 0-10 min after the scheduled start) are not sharper than pre-start rows, which is what in-play contamination would look
like: market Brier 0.2471 recovered (0-2 min 0.2471, 2-5 min 0.2471, 5-10 min 0.2457) vs 0.2461 before the start; blend c +0.12 (t +5.6) vs +0.14. The grace
therefore adds coverage without an information leak. It does NOT fix the best-price artifact, and recovered rows are 2025-26-heavy where stamps are
a freeze stamp. Treat "+10 min grace" as safe for calibration work but not as proof that a quote was available before the start.
The strict-filter ROI (best price, edge >= 3%) is +7.1% [+5.3, +8.9] (n=13,095), vs +14.6% on the recovered rows alone.

## 6. What cannot be concluded
- **Tradability.** BettingPros keeps one last snapshot per day: no open-to-close series, no CLV, no time of each quote. ROI vs a last-quote best price
  is exactly the soft-line artifact documented in the repo; here it is quantified (control beats model).
- **Price at the time of bet.** Whether any quote was available when a bettor could have acted is unknown, especially in 2025-26.
- **Model-availability gap.** The NBA frame trains on actual inactives; live only has the Out list (upper bound on the information signal).
- Selection of the evaluated props: conditional on the player playing (DNP is void at the book, so this matches settlement).
- The prior (every MLB count model NO_EDGE; NHL SOG UNDETERMINED) stands.

## 7. What would change the verdict
Forward CLV from banked ParlayAPI snapshots (`nba.props.forward`, pre-registered) with open and close prices per book and a quote timestamp, restricted to
the median-or-better price. A confirmed positive CLV on >= several thousand props with the line-shopping control subtracted would move this to PROMISING.
Until then, do not size money off the NBA model.

## Reproduce
```
cd <worktree>; PY=/Users/lmaynor/mlb-betting/.venv/bin/python; S=<scratch dir>; export GCS_BUCKET=concrete-crow-445205-m4-mlb-data
mkdir -p $S/bp $S/cons $S/model && (cd $S/bp && gcloud storage cp -r 'gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nba/*' .)
for m in pts reb ast threes pra; do $PY scripts/join_bettingpros_history.py --sport nba --markets $m --grace-minutes 10 --out $S/cons/nba_cons_$m.parquet & done; wait   # ~30-60 min, GCS-bound
$PY -m nba.props.evaluate --out $S/model/eval_base.md --preds-cache $S/model/nba_preds.parquet --no-oracle                          # walk-forward preds (~15 min)
$PY scripts/nba_bp_price_extras.py --bp-dir $S/bp --consensus $S/cons/nba_cons_{pts,reb,ast,threes,pra}.parquet --out $S/extras.parquet   # ~1 min
$PY scripts/nba_bettingpros_price_test.py --consensus $S/cons/nba_cons_{pts,reb,ast,threes,pra}.parquet --extras $S/extras.parquet \
    --preds-cache $S/model/nba_preds.parquet --out-json $S/res.json --out-md $S/tables.md                                          # ~7 min
```
Code: `scripts/nba_bettingpros_price_test.py` (sweeps, controls, grace test), `scripts/nba_bp_price_extras.py` (second-best / median price, quote age).
