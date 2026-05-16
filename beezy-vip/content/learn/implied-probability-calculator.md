# How to Calculate Implied Probability from Odds

## The Core Idea

**Implied probability** is the probability the sportsbook is *pricing in* when it sets odds. If DraftKings prices a pitcher strikeout prop at -110, that means the book thinks there's a 52.38% chance it hits.

Converting odds to implied probability is the foundation of edge analysis. If you think the actual probability is higher, you have an edge and should bet.

## The Three Odds Formats

### American Odds (Moneyline)
Used in the US. Can be positive or negative.

**Positive odds** (e.g., +200):
```
implied_probability = 100 / (odds + 100)
```

Example: +200 odds
- 100 / (200 + 100) = 100 / 300 = 0.333 = 33.3%

**Negative odds** (e.g., -110):
```
implied_probability = odds / (odds + 100)
```

Note: You have to negate the odds first.

Example: -110 odds
- abs(-110) / (abs(-110) + 100) = 110 / 210 = 0.5238 = 52.38%

Example: -200 odds
- 200 / (200 + 100) = 200 / 300 = 0.667 = 66.7%

### Decimal Odds (European)
Used internationally. Just one formula.

```
implied_probability = 1 / decimal_odds
```

Example: 1.91 decimal odds
- 1 / 1.91 = 0.523 = 52.3%

Example: 2.50 decimal odds
- 1 / 2.50 = 0.40 = 40%

### Fractional Odds (UK)
Used in British betting. Expressed as "5/1" (five-to-one).

```
implied_probability = denominator / (numerator + denominator)
```

Example: 5/1 odds
- 1 / (5 + 1) = 1 / 6 = 0.167 = 16.7%

Example: 1/2 odds (the favorite)
- 2 / (1 + 2) = 2 / 3 = 0.667 = 66.7%

## Why These Numbers Don't Add to 100%

Here's a crucial insight: if you add up the implied probabilities of both sides of a bet, they *always* exceed 100%.

Example: -110 on each side of a moneyline
- Side A: 52.38% implied
- Side B: 52.38% implied
- Total: 104.76%

That extra 4.76% is the **vig** (juice). It's the house's profit margin.

On a fair market with no edge, the book sets odds so that roughly equal money is wagered on both sides. The vig ensures the book makes money regardless of the outcome.

## Quick Reference Table

| American | Decimal | Fractional | Implied % |
|---|---|---|---|
| -110 | 1.91 | 10/11 | 52.4% |
| -120 | 1.833 | 5/6 | 54.5% |
| -130 | 1.769 | 8/11 | 56.8% |
| -150 | 1.667 | 2/3 | 60.0% |
| -200 | 1.50 | 1/2 | 66.7% |
| +100 | 2.00 | 1/1 | 50.0% |
| +150 | 2.50 | 3/2 | 40.0% |
| +200 | 3.00 | 2/1 | 33.3% |
| +300 | 4.00 | 3/1 | 25.0% |

## Finding Your Edge

Once you can convert odds to implied probability, edge is simple:

```
edge = your_probability - implied_probability
```

Example:
- You think strikeouts have 56% probability
- The book prices it at 52.4% (-110 odds)
- Edge = 0.56 - 0.524 = 0.036 = 3.6%

If your edge is consistently positive across a large sample of bets, you have a profitable strategy.

## The Vig Hurts Small Edges

At -110 odds, the implied probability is 52.38%. To break even, you need to hit 52.38% of your bets on average.

But most casual bettors think of it as "even money"—and they expect to hit 50% and break even. This is a common trap.

Here's what happens:
- You hit 50% of your -110 bets
- Expected profit per 100 bets: 50 wins × $1 - 50 losses × $1.10 = $50 - $55 = -$5
- You're losing 5% of your handle

At -120 odds, you need 54.5% to break even. The better the odds you can find, the lower your win rate needs to be to profit.

## Market Inefficiencies

Sharp books (Pinnacle, sharp DK) price with minimal vig and high efficiency. The implied probability is very close to the true probability.

Softer books sometimes misprice, especially on:
- **Longshots** (casual bettors chase +300 bets, inflating the odds)
- **Favorites** (people overweight recent performance, depressing favorite odds)
- **Niche props** (less trading volume, more room for mistakes)

Your job as a bettor is to find these mispricings before the sharps do.

## How to Check Your Work

Use an online implied probability calculator to verify your math:
1. Enter the American odds
2. Calculator shows implied probability
3. Compare to your estimate
4. If yours is higher, you have potential edge

Most sports betting apps (FanDuel, DraftKings, Caesars) now show implied probability directly—look for it on the prop page.

## Advanced: Removing Vig to Find Fair Odds

Sometimes you want to know what the *fair* probability is (the vig-free version).

```
fair_probability = implied_probability / vig_factor
```

Where vig_factor = (prob_a + prob_b) / 1.0

Example:
- Both sides are -110: 52.38% + 52.38% = 104.76% total
- vig_factor = 1.0476
- Fair prob for each side: 52.38% / 1.0476 = 50%

This tells you the market thinks it's dead even (vig-adjusted).

## The Shortcut for -110 Odds

If you're seeing a lot of -110 odds and want a quick mental math trick:

```
At -110, the vig-free fair probability is (implied_probability - 0.0238) / 0.9762
```

But honestly, just remember: **-110 means the book thinks 52.4%**. Everything else is just decimal places.

## Bottom Line

Implied probability is the bridge between what you think and what the market thinks. Master this calculation and you can:
- Spot mispricings instantly
- Calculate your edge accurately
- Know whether a bet is worth taking

Print the reference table above and keep it handy. After a few weeks, you'll have it memorized.
