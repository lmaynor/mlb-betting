# How to Remove Vig from Sports Betting Odds

## What Is Vig (Juice)?

**Vig** is the sportsbook's commission. When you see -110 odds on both sides of a bet, the book is saying:

- Side A: 52.38% probability (implied)
- Side B: 52.38% probability (implied)
- Total: 104.76%

That extra 4.76% is pure profit for the house—the vig.

Removing vig lets you see what the *true* market probability is, stripped of the house's edge. This is critical for identifying which side is mispriced.

## Why Remove Vig?

Imagine you're comparing two books:

**Book 1:**
- Side A: -110 (52.38% implied)
- Side B: -110 (52.38% implied)

**Book 2:**
- Side A: -105 (51.22% implied)
- Side B: -105 (51.22% implied)

Without removing vig, you might think both are saying the same thing. But Book 2 has *lower* vig, meaning less of a house margin and more efficient pricing.

When you remove vig, you can compare the *true* probabilities the books are assigning—not just the vig-laden numbers.

## The Math: Vig-Free Probability

```
vig_total = implied_prob_a + implied_prob_b
vig_factor = vig_total / 1.0

vig_free_prob_a = implied_prob_a / vig_factor
vig_free_prob_b = implied_prob_b / vig_factor
```

### Example 1: -110 on Both Sides

- Side A implied: 110 / (110 + 100) = 0.5238
- Side B implied: 110 / (110 + 100) = 0.5238
- Total: 1.0476
- vig_factor = 1.0476

Vig-free:
- Side A: 0.5238 / 1.0476 = 0.5000 (50%)
- Side B: 0.5238 / 1.0476 = 0.5000 (50%)

This makes sense: both sides should be 50-50 fair.

### Example 2: Asymmetric Odds

A book prices:
- Favorite: -150 (60.0% implied)
- Underdog: +120 (45.45% implied)
- Total: 105.45%
- vig_factor = 1.0545

Vig-free:
- Favorite: 0.60 / 1.0545 = 0.5689 (56.89%)
- Underdog: 0.4545 / 1.0545 = 0.4311 (43.11%)

So the book's *true* assessment (vig-removed) is the favorite has 56.89% and the underdog 43.11%. That's a 13.78 percentage point gap.

The original -150/-120 odds had the same gap (60% - 45.45% = 14.55%), but that difference was obscured by vig.

## When Books Disagree

This is where vig removal becomes powerful:

**Book A:**
- Home team: -110 (52.38% implied)
- Away team: -110 (52.38% implied)

**Book B:**
- Home team: -120 (54.55% implied)
- Away team: +100 (50.00% implied)

Which book is more confident in the home team?

Remove vig from Book A:
- Home: 52.38% / 1.0476 = 50%

Remove vig from Book B:
- Home: 54.55% / 1.0545 = 51.71%

**Book B is more confident in the home team** (51.71% vs 50%). The -120 odds aren't just worse for you—they indicate the book actually thinks the home team is more likely.

## The CRA Method (Closing Line Algorithm)

Professional bettors use vig removal to track **closing line value** (CLV)—how your win probability compares to where the market settled.

```
1. Get vig-free probability at time of bet
2. Get vig-free probability at closing line (game time)
3. If you bet higher than closing, you have CLV
```

Example:
- You bet home team at -110 (52.38% implied, 50% vig-free)
- Market closes at -120 (54.55% implied, 51.71% vig-free)
- Your estimated prob: 52% (0.52 vig-free)
- You bet at 50% vig-free, market closed at 51.71% vig-free
- You got worse odds than the market eventually settled on—no CLV

## Quick Vig Removal for -110 Both Sides

When you see -110 on both sides, the vig-free breakdown is **50-50**.

Just remember: **At -110/-110, remove 4.76% from each side.**

- 52.38% → 50%
- Any other odds: use the full formula above

## Vig Removal for Moneyline Pairs

DraftKings and FanDuel always show a sportsbook moneyline. Here's the fast way:

**If the odds are close to -110/-110:**
- Just split the difference. If it's -110/-110, vig-free is 50-50.
- If it's -115/-115, vig-free is roughly 49.5-50.5 (slightly higher vig).

**If the odds are asymmetric (e.g., -150/+120):**
- Use the formula above. Don't skip this—asymmetric odds can hide real market disagreement.

## The Closing Line Value (CLV) Concept

Track your bets against closing line:

```
if vig_free_opening > vig_free_closing:
    you_have_positive_clv = True
```

Pros with consistent positive CLV are the ones who beat the market long-term. Your closing line value (not just hit rate) is the real measure of edge.

Example:
- You bet Pitcher K Over at -120 (54.55% implied, 51.71% vig-free)
- You estimate 56% true probability
- Market closes at -130 (56.86% implied, 54.0% vig-free)
- You got a better deal than the market eventually agreed to
- Positive CLV

## The Caveat: Vig Removal Is Additive

Vig removal assumes the book's probabilities are proportional. In reality, books sometimes misprice both sides (e.g., if a prop is illiquid, they might quote -120/-120 instead of -110/-110).

Vig removal smooths this out *as if* both sides were mispriced equally. It's not perfect, but it's the industry standard.

## Tools to Help

Most modern sports betting apps show implied probability already. If you want to remove vig:

1. Calculate implied probability for each side
2. Add them together
3. Divide each by the total
4. Multiply by 100 to get percentages

Or use an online calculator (search "vig removal calculator").

## The Bottom Line

Removing vig lets you compare how different books value the same bet. A book pricing -120 isn't just offering worse odds—it's saying the outcome is 1.71 percentage points more likely (in vig-free terms).

For serious bettors:
- **Remove vig from every pair of odds you compare**
- **Track closing line value, not just hit rate**
- **Books that consistently offer lower vig or edge-favoring odds are worth your time**

The sharps do this automatically. You should too.
