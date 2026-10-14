# Do Kalshi NFL prop longshots reward buying NO? (analysis, 2026-10-14)

Question (owner): retail piles into high-strike ladder rungs (`80+`, `90+`, `100+` yds, long receptions) at cheap YES
prices. Is **buying NO** on those rungs (`yes_price < 0.20`, NO costs > 0.80, fee only ~0.7-1.4% of cost) a
statistically significant, exploitable favorite-longshot edge? Read-only, no new API credits: the same settled tape as
`market_making_2026-10-12.md` (78 games, Sep 9 - Oct 11; receiving yds, rushing yds, receptions, passing yds; 1.90M
trades), `scripts/mm_longshot.py` (accounting unit-tested in `tests/test_mm_longshot.py`), game-clustered bootstrap CIs.

## Verdict

**No. Buying NO on cheap rungs lost money, and the sign is the opposite of a fade.** Longshots resolved YES *more* often
than their price implies (a mild reverse favorite-longshot bias), so the NO side is the wrong side. The pre-specified
contract-weighted test is not significant (CI spans zero); the equal-weighted views are significantly *negative*.
Nothing here supports building a longshot-fade strategy, and the reverse (buying cheap YES) was not tested and is not
established -- see "What this does not show".

## Method (as pre-specified, before looking)

Per trade at yes price `p`, size `n`, outcome `y` (1 = settled YES): buying NO pays `p - y` per contract. **Taker NO**
(`taker_side == 'no'`, a real print at that price/size) is charged Kalshi's quadratic taker fee
`0.07 * (1-p) * p` (all four prop series: `fee_type quadratic`, multiplier 1, checked 2026-10-14); **maker NO** (a resting
NO bid hit by a YES taker) pays no fee. Pre-kick = before `occurrence - 3h`. Buckets by yes price:
`[0.02,0.10) [0.10,0.20) [0.20,0.35) [0.35,0.65) [0.65,0.98]`. One primary test: pooled pre-kick, `p < 0.20`, taker NO,
net of fee, contract-weighted. Everything else is descriptive.

## A flaw in the tape that matters (found mid-analysis)

The tape holds only markets whose **final** volume cleared a per-series cutoff (3,000-4,000 contracts). Final volume is
outcome-dependent: a longshot that resolves YES trades ~3.5x more in-game (median 6,940 vs 1,964 contracts), so YES
longshots clear the cutoff far more often than NO ones. Among longshot markets the YES rate rises **12.9% -> 36.3%**
across final-volume quintiles at an unchanged ~11-12% price. The raw tape therefore overstates the YES rate.

Fix used for every headline number: keep only markets whose **pre-kick** volume alone already clears their series
cutoff, so inclusion cannot depend on the outcome (5,256 of 8,755 markets, 81.6% of contracts). The raw-tape primary
(-7.42c) and the selection-safe one (-7.34c) are close because contract weighting is dominated by a few giant liquid
prints, but the equal-weighted views move materially (market-weighted -10.4c raw vs -5.8c safe).

## Results (selection-safe sample, pre-kick)

| View (yes price < 0.20) | n | YES rate vs price | edge, c/contract (95% game-cluster CI) |
|---|---|---|---|
| **PRIMARY** taker NO, net of fee, contract-weighted | 8,439 prints / 1.50M contracts / 76 games | 16.0% vs 9.2% | **-7.34 [-23.82, +2.86]** (n.s.) |
| same, gross of fee | | | -6.78 [-24.35, +3.48] |
| taker NO, every **print** weighted equally | 8,439 | 14.4% vs 10.6% | **-4.39 [-8.24, -0.97]** |
| taker NO, every **market** weighted equally | 1,173 markets | 16.5% vs 11.4% | **-5.80 [-9.29, -2.84]** |
| calibration, all longshot prints, per market | 1,795 markets | 15.4% vs 11.9% | **-3.46 [-6.25, -1.02]** |

Per bucket, taker NO net of fee (contract-weighted): `[0.02,0.10)` -10.68c, `[0.10,0.20)` -3.05c, `[0.20,0.35)` -2.12c,
`[0.35,0.65)` -3.54c, `[0.65,0.98]` +0.88c -- every CI spans zero. The passive side (resting NO) at `[0.20,0.35)` is
significantly negative (-6.37c), matching the market-making study.

Why the contract-weighted CI is so wide and its point estimate so large: the top 10 prints are 14% of contracts and 65%
of |gross P&L|. The largest single driver is one player's ladder (ticker `NYGILIKELY9`, receptions 7.5/8.5/9.5 on Oct 11): five
prints, ~79k contracts at 6-14c, every rung settled YES together -- rungs of one player are one correlated bet. Without
the biggest slate day the primary shrinks to -1.3c [-6.3, +1.2]. By slate day, all five 12-14 game Sundays are negative
(-2c to -25c) while the ten single-game days are mixed (7 positive at +0.5c to +7.7c, 3 negative; each is one game), i.e. the
sign tracks which slate we sampled, not a stable property of prices.

By series (market-weighted, all longshot prints): passing yds -6.1c [-13.7, +0.5], receptions -4.3c [-8.4, -0.7],
receiving yds -3.7c [-7.1, -0.3], rushing yds -0.5c [-4.7, +3.5]. The receptions series' alarming contract-weighted
figure (33.6% YES vs 8.2% price) is that one ladder; at the market level it looks like the others.

Ladder position (strike >= 1.35x the player's main line, main line proxied by the ladder rung priced nearest 0.5):
-7.76c [-28.1, +3.8] for high rungs vs -4.01c [-15.7, +3.9] for the rest -- not distinguishable.

## What this does not show

* **It does not establish buying cheap YES.** That direction was not pre-specified; the evidence is a market-weighted
  ~3.5c/contract on 1,795 correlated markets, from a selection-corrected but approximate sample, with strong slate-day
  heterogeneity. A real test needs an unbiased market universe (the settled-market list without a volume filter, plus
  pre-kick prices for every market) and an executability check.
* **No order-book model.** Taker prints are real fills at that price and size, but queue, size beyond the print, and
  price impact are not modelled; the per-order cent rounding of the fee is approximated per contract.
* **78 games** (13 slate days). Cluster CIs are by game, but one player's outcome moves every rung of his ladder and a
  slate shares conditions, so the effective sample is smaller than 78.
* The selection-safe cutoff is the series' minimum observed final volume (3,001 / 4,000 / 4,003 / 4,007), an inference
  about the fetch thresholds, not a recorded parameter.
* Many cells were printed; only the primary was pre-specified. Treat the starred equal-weighted results as suggestive.

## Reproduce

```
python scripts/mm_tape_fetch.py OUTDIR KXNFLRECYDS:3000,KXNFLREC:4000,KXNFLPASSYDS:4000,KXNFLRSHYDS:4000   # public API
python -I scripts/mm_longshot.py OUTDIR            # prints the selection diagnostic, the raw-tape contrast, then all tables
```
