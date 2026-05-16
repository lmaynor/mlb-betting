# Kelly Criterion for Sports Betting

## What Is It?

The **Kelly Criterion** is a mathematical formula that tells you what *fraction* of your bankroll to wager on a bet, given your edge and the odds.

```
kelly_fraction = (edge × odds_ratio - 1) / (odds_ratio - 1)
```

Where:
- **edge** = your true win probability minus the implied probability from the odds
- **odds_ratio** = (your payout on a win + stake) / stake

In plain English: Kelly sizes your bet so your bankroll grows as fast as possible *without* risking ruin.

## Why It Matters

The Kelly Criterion isn't just a formula—it's the **optimal growth rate** for repeated bets with edge. Bet too small and you're leaving money on the table. Bet too big and you risk catastrophic losses from normal variance.

Underfunding good bets costs you money. Overfunding any bet—even with positive edge—can wipe you out in a bad streak.

## The Math (Simple Version)

Let's say you have a $1,000 bankroll and you find a bet where:
- **You think it's 55% to hit** (your analysis)
- **The book prices it at 52%** (the odds imply this)
- **Odds are -110** (American format)

First, convert to odds ratio:
- At -110, you risk $1.10 to win $1
- odds_ratio = 2.10 / 1.10 ≈ 1.909

Your edge in decimal:
- 0.55 - 0.52 = 0.03 (3 percentage points)

Kelly fraction:
```
(0.03 × 1.909 - 1) / (1.909 - 1)
= (0.0572 - 1) / 0.909
= -0.943 / 0.909
≈ -1.04
```

Wait—that's *negative*. That means **don't bet**. Your edge isn't large enough to overcome -110 juice.

Now let's say you find a 55% edge at -120 (better odds):
- odds_ratio = 2.20 / 1.20 ≈ 1.833
- edge = 0.05

Kelly:
```
(0.05 × 1.833 - 1) / (1.833 - 1)
= (0.0917 - 1) / 0.833
= -0.908 / 0.833
≈ -1.09
```

Still negative. You need a *bigger* edge or *better* odds.

One more: 57% confidence, -110 odds:
- edge = 0.57 - 0.52 = 0.05
- odds_ratio = 1.909
- Kelly = (0.05 × 1.909 - 1) / 0.909 = -0.9045 / 0.909 ≈ -0.995

*Still* negative.

Finally: 60% confidence, -110 odds:
- edge = 0.60 - 0.52 = 0.08
- odds_ratio = 1.909
- Kelly = (0.08 × 1.909 - 1) / 0.909 = 0.0572 / 0.909 ≈ 0.063

**6.3% of bankroll.** On $1,000, that's $63 per bet.

## The Insights

### 1. High Juice Kills Small Edges
At -110 odds, you need roughly 5–6% edge to make Kelly positive. This is why casual bettors *feel* like they need huge edges—they do, because of the vig.

At -105 (where sharp books operate), you need 4–5%. At -100 (a fair market with no vig), you need just over 2%.

### 2. Kelly Scales with Bankroll
If you have $10,000 instead of $1,000, Kelly tells you to bet $630 instead of $63. The *percentage* stays the same (6.3%), but the dollar amount grows with bankroll.

### 3. You Can Never Overbett Kelly
If Kelly says 6.3% and you bet 6.3%, the math guarantees the maximum expected value growth. If you bet 7%, you're slightly suboptimal but still profitable (if your edge is real). If you bet 10%, you're taking on more risk than necessary and your expected long-term growth *decreases*.

## Full Kelly vs. Half Kelly vs. Quarter Kelly

Pro bettors rarely use *full Kelly*. Here's why:

- **Full Kelly (100%):** Theoretically optimal, but requires perfect edge estimation. One miscalibration and you can lose 25%+ of bankroll in a bad streak.
- **Half Kelly (50%):** The practical choice. You keep most of Kelly's growth (roughly 75% of the optimal growth rate) while cutting max drawdown in half.
- **Quarter Kelly (25%):** Conservative. For bettors new to a market or unsure of their model.

Most professional bettors use **half Kelly** or **Kelly / 1.5** and adjust down further when uncertainty is high.

## The Bankroll Growth Chart

With full Kelly and consistent edge:

| Bets Won | Bankroll Growth (full Kelly) | Bankroll Growth (half Kelly) |
|---|---|---|
| 100 | 1.4x | 1.2x |
| 500 | 3.8x | 2.1x |
| 1,000 | 14.4x | 4.6x |

The exponential curve is why Kelly wins long-term. But the *downside* is dramatic too: a bad 100-bet stretch with miscalibrated edge can cut your bankroll 30%.

## Common Mistakes

### Mistake 1: Using Model Confidence as Edge
Your model says 60% confidence. That's *not* your edge—that's your prediction. Your edge is how much better you are than the market.

If the book also predicts 60% (at +100 odds), your edge is 0%. Kelly says bet 0.

### Mistake 2: Overstating Edge
Most bettors think they have 5–10% edge. Statistically, 85% of them don't. Be conservative. If you're new to a market, assume half the edge you think you have.

### Mistake 3: Not Accounting for Model Error
Kelly assumes your probability estimate is accurate. But models have calibration error (you say 60%, actual hit rate is 56%). If your model is off by 2%, your Kelly fraction should be cut in half.

### Mistake 4: Forgetting Vig
The house takes juice. At -110, that's 4.5% of the market's total probability mass. This is *already* in the odds. Don't "double-count" it.

## The Real-World Formula

For sports bettors, this is the practical version:

```
kelly_fraction = (your_hit_rate - implied_hit_rate) / odds_ratio
```

Then apply a haircut:

```
bet_fraction = kelly_fraction / 2  (for half Kelly)
```

If the result is negative, don't bet.

## Bankroll Sizing Rule of Thumb

- **If you're unsure of your edge:** Use 1–2% Kelly (very conservative).
- **If you have 500+ bets of backtested data:** Use 2–4% Kelly.
- **If you have 1000+ bets and your edge is proven:** Use 4–6% Kelly.
- **If you're a professional with institutional capital:** Use 5–10% Kelly.

Most retail bettors should never exceed 3% Kelly on a single bet.

## The Bottom Line

Kelly Criterion is the **mathematically optimal bet sizing formula**. But it only works if:
1. You know your true edge (most people don't)
2. You apply a conservative haircut (half Kelly minimum)
3. You have a large sample of bets (100+) to validate your model

If you're just starting out: **Use 1% Kelly.** On a $1,000 bankroll with a 60% edge at -110 odds, that's $0.63 per bet. Boring, but you'll still be in business after 100 bets.

Once you've logged 500+ bets and proven your edge, you can scale up. The pros who blow up their bankroll are usually the ones who skipped this step.
