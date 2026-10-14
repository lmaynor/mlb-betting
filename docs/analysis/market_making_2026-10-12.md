# Should we make markets? -- Kalshi and Novig NFL props (analysis, 2026-10-12)

Question (owner): should the project become a market maker (the `~/market-maker` repo has Kalshi MM work), instead
of / in addition to taking mispriced contracts? Method: measure what the PASSIVE side of every real trade earned,
simulate a small queue-limited maker on the real tape, and size the revenue pool. Everything here is read-only,
reproducible from `scripts/mm_tape_fetch.py`, `scripts/mm_analysis.py`, `scripts/mm_sim.py` (accounting unit-tested in
`tests/test_mm_sim.py`) and public Kalshi/Novig data.

## Verdict

**No -- not as a primary strategy, and not on the evidence we have.** On Kalshi NFL player props the passive side's
realized return is statistically indistinguishable from zero pre-kick (point estimate -1.6c) and significantly negative in-game (-1.2c); the spread a small
maker could actually capture (about 0.5c per fill) is an order of magnitude smaller than the noise (standard error
1.5c per contract after 78 games) and than the adverse selection we measured. The structure also works against a new
entrant: the spread is already at Kalshi's 1-cent tick floor, so you cannot undercut, and you join a queue thousands of
contracts deep. There is one narrow structure worth a forward test (hedged two-venue making, below); nothing here
justifies building a market-making engine now.

## 1. Prior evidence in this repo: the weather market maker

`~/market-maker` (TypeScript, Kalshi weather, demo-validated, maker fee $0 per ADR 0003) ran a shadow-mode test with
pre-registered go/no-go criteria (ADR 0004). Its own summary (`data/shadow-summary.json`, 2026-10-12): 448 simulated
fills (conservative) / 490 (optimistic) across 132 markets -- **realized (round-trip) P&L +$21 / +$29, but total P&L
-$238 / -$435, kill switch tripped on both.** The spread-capture mechanism worked; inventory and adverse selection
overwhelmed it. Criterion 1 (positive total P&L) fails. That is a different market and a weather model, but it is the
same failure shape the tape analysis below finds in NFL props.

## 2. Market structure (what a new maker faces)

| | Kalshi NFL props | Novig NFL props |
|---|---|---|
| Spread | **1c for 94% of quotes, <=2c for 100%** (463 markets, 6,911 pre-kick rows, Oct 4) -- the tick floor | median ~2c on matched two-sided props, wide tails (median 26c on thin ones early); tick 0.5c mid-range so price improvement is possible |
| Depth ahead of you | top ask median **4,841 contracts** (p10 553, p90 21,814) | 100k+ cent-contracts typical per level |
| Flow | median market **777 contracts / 24h**, p90 6,434 | $10.4M NFL prop notional on Sun Oct 11 (all-in, incl. live), 3,217 markets, median trade $43 |
| Fees | prop series `fee_type: quadratic` => maker fee 0 (game series `quadratic_with_maker_fees`: ~0.4c/contract maker fee) | maker pays 0; taker fee only while LIVE; **no maker credit pre-game**; resting orders are voided at go-live |
| Competition | formal Market Maker Agreement participants exist (Kalshi rules, Novig program) | same; MMAs excluded from public maker credits |

Consequences: at the 1c floor the only way to be first in line is to be early; typical queue-turn time is days
(4,841 ahead / 777 per day), so a late entrant fills mainly in the pre-kick surge -- i.e. exactly when informed flow
arrives.

## 3. What the passive side of real trades earned (Kalshi tape, settled, 78 games Sep 9 - Oct 11)

For each trade: maker P&L = (price - outcome) if the taker bought YES, (outcome - price) if the taker bought NO,
held to settlement. This is the realized, adverse-selection-INCLUSIVE return of providing liquidity on that trade.
Kickoff = `occurrence_datetime` - 3h (verified on MNF). 95% CIs are bootstrapped by GAME (outcomes cluster within a
game). Series: receiving yards, rushing yards, receptions, passing yards (2.02M trades, 78 games).

| Slice | Contracts | Maker edge (c/contract) | 95% CI |
|---|---|---|---|
| Receiving yds, pre-kick | 35.1M | -2.09 | [-6.37, +2.05] |
| Rushing yds, pre-kick | 23.1M | +0.12 | [-3.37, +4.63] |
| Receptions, pre-kick | 20.8M | -2.09 | [-5.74, +1.42] |
| Passing yds, pre-kick | 12.3M | -2.87 | [-9.08, +4.26] |
| **POOLED pre-kick** | **91.2M** | **-1.64** | **[-4.74, +1.56]** |
| **POOLED in-game** | 84.3M | **-1.21** | **[-2.36, -0.05] (significant)** |
| pooled pre-kick, last 60 min | 35.4M | -1.14 | [-4.49, +2.34] |
| pooled pre-kick, >60 min | 55.8M | -1.95 | [-4.90, +1.02] |
| pooled pre-kick, size >= 1,000 | 42.8M | -2.25 | [-5.35, +1.07] |
| pooled pre-kick, size < 100 | 12.6M | -0.68 | [-3.99, +2.55] |
| receptions, in-game | 10.9M | -2.49 | [-4.58, -0.77] (significant) |
| receiving yds, in-game | 25.6M | -1.91 | [-3.51, -0.27] (significant) |

- **No positive edge anywhere with a CI that excludes zero; the only significant slices are negative (in-game).**
- The sign is unstable: September receiving yards alone looked like +1.4c; adding October flipped it to -2.1c.
  That is noise dominating, not a regime to trade.
- Statistical power: standard error 1.47c/contract at 78 games; resolving even a 0.5c edge at 1 SE needs ~670 games
  (about 2.5 full NFL seasons of every game).
- Robustness: calibration of the sample is sound (volume-weighted price 0.405 vs realized yes-rate 0.426, so no
  look-ahead skew) and restricting to markets by PRE-kick volume (>=3k/10k/30k contracts) gives -1.3 / -1.7 / -1.6c.
- Mean pre-kick flow is 1.17M contracts per game across these four series; the all-flow maker P&L per game has mean
  -$19.1k and sd $161k -- the variance of simply being the passive side is enormous.

Sampling caveat I caught and corrected: ranking game-line markets (moneyline / spread / total) by TOTAL volume is
look-ahead -- "wins by over N" markets that resolve YES close early and trade less, so a top-volume sample is skewed to
NO outcomes and makes makers look profitable (+8c on spreads: an artifact, volume-weighted price 0.49 vs yes-rate
0.29). Those numbers are discarded; game lines are re-sampled at random (section 6).

## 4. Simulated small maker (queue-limited, held to settlement, maker fee 0)

Model: quotes sit at the touch; we receive a fraction `phi` of each passive-side trade (queue share, random luck),
net-inventory cap `L` contracts per market, skew rule (no fills that add to a large inventory), quoting stops 15 min
pre-kick. Optional extra adverse selection `shock` (cents/contract on our fills only).

3 prop series, 78 games (`scripts/mm_sim.py`):

| phi | L | shock | fills/game | edge c/contract | mean $/game | sd $/game | 95% CI mean | P(game<0) | worst day |
|---|---|---|---|---|---|---|---|---|---|
| 0.5% | 200 | 0 | 3,489 | -1.27 | -$44 | $344 | [-123, +29] | 0.47 | -$1,233 |
| 1% | 200 | 0 | 5,845 | -1.13 | -$66 | $463 | [-169, +35] | 0.51 | -$1,361 |
| 1% | 200 | 0.5c | 5,845 | -1.63 | -$95 | $470 | [-201, +6] | 0.53 | -$1,546 |
| 1% | 1000 | 0 | 8,066 | -1.52 | -$122 | $970 | [-342, +83] | 0.45 | -$4,009 |
| 2% | 1000 | 0 | 14,607 | -1.28 | -$188 | $1,511 | [-531, +114] | 0.47 | -$5,634 |
| 5% | 200 | 0.5c | 15,599 | -1.19 | -$185 | $732 | [-348, -22] | 0.50 | -$3,522 |
| 5% | 1000 | 0.5c | 29,227 | -1.63 | -$477 | $2,350 | [-1,000, +23] | 0.53 | -$7,728 |

All 16 configurations (phi 0.5-5%, L 200/1000, shock 0/0.5c) are negative in expectation (-0.7c to -2.2c per
contract); in 15 the CI includes zero and in one it is entirely negative; about half of games lose, and a bad day costs
10-40x the average gross spread earned. Total over the 78 games ranges from -$3.5k to -$37k.

Revenue ceiling: ~1.0M pre-kick contracts per game across the three simulated series (1.17M with passing yards) x ~0.5c half-spread = ~$5k gross pool
per game for ALL makers (~$80k per 16-game week). A 1% share is ~$800/week gross -- against a point-estimate adverse
selection of -1.4c on those contracts (~-$2.3k/week) with a weekly sd in the thousands. Capital is not the constraint;
edge is.

## 4b. Markout (price move after the fill) and queue position -- the "pennies" test

Sections 3-4 hold fills to settlement, which adds outcome noise. A maker normally judges fills by MARKOUT: P&L marked
to the market a few minutes after the fill (reference = volume-weighted trade price in the 5 minutes after the delay,
which mixes bid- and ask-side trades so bid/ask bounce cancels). `scripts/mm_markout.py` (time-unit bug found and fixed;
test added). Passive side, pre-kick, 4 NFL prop series, 78 games, estimates are TIGHT (+/-0.1c):

| Horizon | contracts | maker markout (c/contract) | 95% CI |
|---|---|---|---|
| +1 min | 42.1M | -0.16 | [-0.27, -0.09] |
| +5 min | 35.3M | -0.20 | [-0.34, -0.12] |
| +15 min | 27.0M | -0.23 | [-0.42, -0.11] |
| +30 min | 20.1M | -0.32 | [-0.59, -0.14] |

The half-spread is ~0.5c, so **adverse selection after a fill is ~0.7c per contract -- larger than the spread earned**.
Who hits you: trades under 10 contracts: +0.18c [+0.13,+0.24]; 10-100: +0.08c; 100-1000: -0.05c; **>= 1,000 contracts:
-0.42c [-0.65,-0.27]**. By time to kickoff: >24h -1.07c; 6-24h -0.21c; 1-6h -0.25c; 20-60m -0.02c. Small (retail-sized)
trades pay the maker; large ones are informed.

Queue position (our quote = 100 contracts at the touch; a trade of size n fills us min(100, max(0, n - Q)) where Q is the
queue ahead of us):

| Q ahead | fills (contracts) | markout (c/contract) | 95% CI | $/game |
|---|---|---|---|---|
| 0 (front) | 10.8M | **+0.02** | [-0.04, +0.06] | +$22 |
| 100 | 4.1M | -0.08 | [-0.18, -0.01] | -$42 |
| 500 | 1.2M | -0.20 | [-0.35, -0.10] | -$30 |
| 1,000 | 0.58M | -0.30 | [-0.50, -0.16] | -$22 |
| 2,500 | 0.18M | -0.56 | [-0.86, -0.34] | -$13 |
| 5,000 (the median Kalshi top-of-book) | 59k | **-0.71** | [-1.17, -0.40] | -$5 |

Reading: the spread is real but it is exactly the compensation for informed flow (Glosten-Milgrom): even at the front
of the queue the net markout is ~0, not positive. The deeper you sit, the more your fills are the large sweeps that
move the price (-0.7c at the median queue). Inventory also keeps drifting against makers to settlement (-0.3c at 30 min
-> -1.6c held to settlement), so laying off by crossing the spread (another ~0.5c) does not rescue it. Unmodelled and
what real makers do: cancel quotes ahead of informed flow using a fast outside reference. Our sportsbook feed arrives
every 15 minutes, which is far too slow for that.

## 5. Ideas that might rescue it, and what we can actually say

1. **Quote only where our sportsbook consensus agrees (informed maker).** Not testable yet: only 26 historical trades
   had a fresh >=3-book consensus (7 markets), because only ~21% of Kalshi prop rows have one. Inconclusive, not
   negative.
2. **Hedged two-venue making: rest on Kalshi (maker, fee 0), hedge by taking on Novig (pre-game taker fee 0).**
   Profit per hedged contract = (Kalshi ask on side X) - (Novig ask on the same outcome). Snapshot over 376 matched
   props (MNF, 19:xx UTC): median -0.50c per leg, best-of-two median 0.0c, positive in 29%, >=1c in 10%, max +2.5c.
   That is the only structure with a positive tail -- but a resting bid is filled exactly when its price has gone stale
   (selection effect this snapshot cannot capture), Novig has no idempotency, and the hedge leg can move. Worth a
   forward shadow test, not a build.
3. **Illiquid markets with wide spreads** (Kalshi NHL player props: 5-7c spreads, thin open interest; NBA props once
   listed). The structural conditions for a maker exist there, but the flow is tiny (see section 7).
4. **Novig 0.5c tick.** You can improve a 2c spread by a half cent and jump the queue -- but Novig's public data has
   no settlement outcomes, so passive-side adverse selection there cannot be measured historically; only a forward
   paper test can.

## 6. Game lines (the "sharp" markets): not established either way

Capped, bounded samples (the last 12 pages of trades, up to 6h pre-kick, 100 random settled markets per series).
- **Moneyline** (calibrated: volume-weighted price 0.490 vs realized yes-rate 0.516; 87.8M contracts, 71 games):
  passive-side edge **-2.35c/contract, 95% CI [-10.2, +5.8]** -- unbiased but uninformative (SE ~3.8c). Kalshi
  moneyline series are `quadratic_with_maker_fees` (~0.38c average maker fee here), so net is worse by that amount.
- **Spread and total: no usable result.** Even a random sample conditioned on total volume >= 2,000 retained a No-skew
  (spread price 0.45 vs yes-rate 0.38; total 0.52 vs 0.28), because "wins by over N" / "over N" markets that resolve YES
  close early and trade less, so any ex-post volume filter drops them. Reported spread/total maker edges (+16c / +1c)
  are artifacts and are not used. A proper test would select strikes by PRE-kick information (e.g. all strikes within
  +/-X of the closing line) and trade through the whole pre-kick window -- not done.
- Structure instead of outcomes: the moneyline pool is far larger (~2.8M contracts per game in the capped window alone)
  but so is the competition (professional makers with agreements), and our own MLB result (n=73, +1.5% net, t=0.12)
  and the first-week NFL prop results (median net EV -4%) say game-level prices track the books. Nothing here supports
  a maker edge in game lines; it simply is not measured.

## 7. NHL player props (wide spreads, almost no flow)

Kalshi NHL goal-scorer props (2,488 settled markets, 79 games, Oct 1-12): spreads 5-7c and thin open interest at the
Oct 3 snapshot -- the structural conditions for a maker (wide spread, little competition) exist here. But the flow is
tiny: **58.6k pre-kick contracts per game** (vs ~1.17M per game for the four NFL prop series), median trade 13
contracts. Passive-side edge pre-kick +2.1c per contract, 95% CI [-1.3, +5.6] (not significant; in-game +1.9c
[-0.2, +4.0]). All-flow maker P&L per game: mean $1.2k, sd $9.5k. The gross pool (58.6k contracts x ~3c half-spread)
is ~$1.8k per game for ALL makers; a 10% share (very ambitious) is ~$180 per game before adverse selection, with a
standard error on the edge of 1.8c/contract (~1,000 games to resolve 0.5c). Interesting as a niche, too small to
matter, and the sample is a few weeks old.

## 8. Costs and risks of actually doing it
- Always-on process, WebSocket book maintenance, sub-second requoting, kill switches (the TS repo has OMS/risk/WS for
  Kalshi weather; props/Novig would need porting). Not a Cloud Run Job shape.
- Correlated inventory: a game's QB/WR/RB props are driven by the same game script (hence the game-clustered CIs).
- Positions cannot be exited at kickoff on Novig (orders void at go-live) and Kalshi props trade in-game against
  faster, better-informed flow; inventory is held to settlement.
- Professional makers with agreements have queue/fee advantages we cannot get.
- Opportunity cost: the same engineering would improve the sweep/consensus coverage, which is the binding constraint
  for the +EV-taker strategy (only ~21% of rows had >=3 books).

## 9. Recommendation and pre-registered tests (modelled on market-maker ADR 0004)

Do NOT build a maker. If we want to keep the option alive, run only cheap forward shadow tests, with go/no-go fixed
before looking:
1. **Hedged cross-venue scalp, shadow only** (Kalshi resting bid + Novig taker hedge, priced from public books each
   pass, fills simulated conservatively with queue ahead = displayed size): GO only if, over >=40 games, net P&L after
   modelled slippage is positive, >=100 simulated fills, no single day >50% of profit, and the 30-minute markout after
   fills is not adverse. Otherwise stop.
2. **Novig paper making** (needs a paper trading key): 2-week test quoting 0.5c inside the spread on liquid props,
   pre-kick only; record fills, markout at 5/30 minutes, and queue-position outcomes. GO only if markout-adjusted edge
   is positive with the lower CI bound above zero on >=500 fills.
3. Revisit only if (a) consensus coverage exceeds ~60% of liquid props, (b) a venue lists wide-spread, adequately
   liquid markets (NBA props in season), or (c) a hedge venue is added that lets inventory be laid off.

## Appendix: data, limits
- Kalshi tape: public `/markets?status=settled` + `/markets/trades` (price, size, taker side, time); results from the
  market `result` field. Props: markets with total volume >= 3-4k contracts (checked not to bias; see section 3).
- Limits: no historical order-book snapshots, so quote simulation uses the tape's own prices as the touch; queue
  position is modelled, not observed; one NFL season-in-progress (5 weeks); kickoff is inferred; Novig public data
  lacks outcomes and player identities.
- Files: `scripts/mm_tape_fetch.py`, `scripts/mm_analysis.py`, `scripts/mm_sim.py`, `tests/test_mm_sim.py`.
