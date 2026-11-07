# How to Remove Vig from Sports Betting Odds

## What Is Vig (Juice)?

**Vig** is the sportsbook's commission. When you see -110 odds on both sides of a bet, the book is saying:

- Side A: 52.38% probability (implied)
- Side B: 52.38% probability (implied)
- Total: 104.76%

That extra 4.76% is pure profit for the house—the vig.

Removing vig lets you see what the *true* market probability is, stripped of the house's edge. This is critical for identifying which side is mispriced. It starts from implied probability; if that conversion is new, read [how to calculate implied probability](/learn/implied-probability-calculator) first.

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
- Total: 52.38% + 52.38% = 104.76%
- Home: 52.38% / 1.0476 = 50.00%

Remove vig from Book B:
- Total: 54.55% + 50.00% = 104.55%
- Home: 54.55% / 1.0455 = 52.17%

**Book B is more confident in the home team** (52.17% vs 50.00%, a gap of 2.17 points). The -120 price is not just worse for a home bettor; it says the book thinks the home team is more likely to win.

## Using vig-free prices to measure closing line value

Vig-free probabilities are how you compare a price you took with where the market finished.

- You bet the home team when the market was -110 / -110 (50.00% vig-free).
- The market closes at -120 / +100 (52.17% vig-free for the home team).
- The market moved toward your side, so the price you took was better than the close. That is **positive closing line value**.

A simple rule: if the vig-free probability at the close is higher than the probability implied by the price you took, you beat the close. See the [CLV guide](/learn/closing-line-value-clv) for more.

## Quick vig removal for -110 on both sides

When you see -110 on both sides, the vig-free breakdown is **50-50**. The two implied probabilities add to 104.76%, so about 2.38 points of vig sits on each side.

- 52.38% becomes 50.00%
- Any other pair of odds: use the full formula above

## Vig removal for moneyline pairs

**If the odds are symmetric (-115 / -115):** the fair price is still 50-50, and the vig is larger (the two sides add to 106.98%).

**If the odds are asymmetric (for example -150 / +120):** use the formula. 60.00% + 45.45% = 105.45%, so the fair split is 56.90% / 43.10%. Do not skip this: asymmetric odds can hide real market disagreement.

## The caveat: proportional removal is a simplification

Dividing each side by the total assumes the book spread its margin in proportion to each side's probability. Books often shade favorites and longshots differently, and thin markets (props) can be mispriced on both sides at once. Other methods exist, but proportional removal is the common default.

## Tools to help

1. Calculate the implied probability for each side
2. Add them together
3. Divide each by the total
4. Multiply by 100 to get percentages

Or let the free [Odds & Vig Calculator](/tools/odds-calculator) do it.

## The bottom line

Removing vig lets you compare how different books value the same bet. A book pricing the home team at -120 / +100 is saying the home team is 2.17 points more likely than a book at -110 / -110.

For serious bettors:
- **Remove vig from every pair of odds you compare**
- **Track closing line value, not just hit rate**
- **Books that consistently offer lower vig or edge-favoring odds are worth your time**

The sharps do this automatically. You should too.
