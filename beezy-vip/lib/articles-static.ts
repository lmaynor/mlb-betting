// Auto-generated -- do not edit manually
export interface Article {
  slug: string
  title: string
  category: string
  keyword: string
  description: string
  content: string
}

export const ARTICLES: Article[] = [
  {
    slug:        "what-is-nrfi-betting",
    title:       "What is NRFI Betting? Complete Guide",
    category:    "Fundamentals",
    keyword:     "nrfi betting",
    description: "**NRFI** stands for \"No Run First Inning.\" It's a bet on whether both teams will fail to score in the first inning of an MLB game. YRFI is the opposite\u2014\"Yes Run",
    content:     `# What is NRFI Betting? Complete Guide

## The Basics

**NRFI** stands for "No Run First Inning." It's a bet on whether both teams will fail to score in the first inning of an MLB game. YRFI is the opposite—"Yes Run First Inning"—a bet that at least one run *will* score in that first inning.

These are moneyline bets, not totals. You're not predicting how many runs will score—just whether the scoreboard will move at all in the opening frame.

## Why It Matters

First inning runs are relatively rare. Across all MLB games, the first inning goes scoreless roughly 45–55% of the time. This creates two things bettors love: **low-probability outcomes** (where the odds offer edge) and **quick resolution** (the bet settles within 15 minutes of first pitch).

NRFI has become one of DraftKings' most popular prop markets, second only to player props like strikeouts and home runs.

## The Odds

Typical NRFI odds range from -110 to -130 (implying 52–57% probability). YRFI odds are usually -120 to -150 (implying 54–60% probability). This isn't a market where books are giving you free money—both sides are heavily traded.

The key to edge here is finding **when the consensus gets it wrong**. A pitcher known for a slow start might face low NRFI odds even when the data suggests scoreless is more likely. Conversely, a high-velocity reliever pitching the opening inning might overshoot in YRFI prices.

## What Affects First Inning Scoring

### Pitcher Quality
- **Starter velocity and control.** Hard-throwing starters with low walks allowed almost always post scoreless first innings. The median first-inning K-to-batter-faced ratio for a top-tier starter is roughly 0.25–0.30.
- **First-inning specific stats.** Some starters are slower to warm up (high first-inning ERA) while others establish dominance immediately. This is signal, not noise.

### Lineup Strength
- **Leadoff hitter discipline.** Teams with high on-base percentage at the top of the order are less likely to go scoreless. A lineup with three patient hitters has maybe 2–3% higher YRFI odds than the book implies.
- **Home field.** The home team bats second, giving it an advantage (down 0, they can still score). YRFI odds are usually 3–5% higher for home teams.

### Weather & Park
- **Wind direction.** Outfield wind suppresses home runs and deep fly balls, increasing NRFI odds slightly. Infield wind doesn't matter much for first inning (no time to accumulate baserunners yet).
- **Park factor.** Smaller parks (Yankee Stadium) see slightly higher first-inning run rates. Larger parks (Oakland Coliseum) suppress them.

### Game Context
- **Rivalry games.** High-stakes matchups sometimes see more aggressive early at-bats, slightly favoring YRFI.
- **Revenge narrative.** A team that lost 1-0 the night before might be more aggressive. This is real but small effect.

## Betting Strategy

### Find the Edges
The best NRFI bets come from **starter quality mismatches** paired with **lineup fatigue** or **injury news**. If a team's leadoff hitter is out and they're starting a bench player, NRFI odds often don't adjust enough.

Example: Team A starts a 95+ mph, 2.8 BB/9 pitcher. Team B has a .330 OBP leadoff hitter but he's in a 2-for-25 slump and the book prices YRFI at -130. The bet here is NRFI at -110, laying 1.1 units to win 1.

### Avoid the Traps
- **Primacy bias.** A pitcher's *last start* often gets too much weight. Look at rolling averages (last 5 starts) instead.
- **Overweighting recent blowouts.** One 8-run first inning doesn't overturn months of solid data.
- **Forgetting the home team.** Home teams do score in the first more often. Adjust NRFI odds down by 2–3% if you're betting against the home team.

### Bankroll Management
NRFI has a lower hit rate (55–60%) than some other props, but the odds are usually fair. Use Kelly sizing:

\`\`\`
kelly_pct = (hit_rate * odds_ratio - 1) / (odds_ratio - 1)
\`\`\`

For a 57% edge at -110 odds, you're looking at roughly 2–3% Kelly. Don't overbett just because the market is liquid.

## The Data

Our models track first-inning runs across:
- 2,000+ games per season
- Per-pitcher first-inning ERA and K rate
- Per-lineup home/away first-inning run rates
- Weather, park, and injury adjustments

The current baseline: **NRFI hits 55% of the time** when odds are fair. Systems that identify 2–5% edge over this baseline are profitable.

## Quick Checklist for NRFI Bets

- [ ] Is the starting pitcher a velocity guy or a control guy?
- [ ] How has the opponent's lineup performed in the first inning this season (not last game)?
- [ ] Is there a key injury affecting the top of the order?
- [ ] What's the wind direction?
- [ ] Am I getting at least -110 or better?

## The Bottom Line

NRFI/YRFI is a high-liquidity, relatively efficient market. You won't find +200 NRFI bets regularly—but you *can* find 2–3% edges if you focus on **pitcher quality**, **lineup fatigue**, and **weather context** rather than narrative.

Start with 50–100 bets tracked in a spreadsheet. If you're hitting 55%+ at fair odds, you have an edge. If you're hitting 50%, you're even with the market. Most casual bettors hit 47–49% because they chase narrative instead of data.
`,
  },
  {
    slug:        "kelly-criterion-sports-betting",
    title:       "Kelly Criterion for Sports Betting",
    category:    "Theory",
    keyword:     "kelly criterion",
    description: "The **Kelly Criterion** is a mathematical formula that tells you what *fraction* of your bankroll to wager on a bet, given your edge and the odds.",
    content:     `# Kelly Criterion for Sports Betting

## What Is It?

The **Kelly Criterion** is a mathematical formula that tells you what *fraction* of your bankroll to wager on a bet, given your edge and the odds.

\`\`\`
kelly_fraction = (edge × odds_ratio - 1) / (odds_ratio - 1)
\`\`\`

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
\`\`\`
(0.03 × 1.909 - 1) / (1.909 - 1)
= (0.0572 - 1) / 0.909
= -0.943 / 0.909
≈ -1.04
\`\`\`

Wait—that's *negative*. That means **don't bet**. Your edge isn't large enough to overcome -110 juice.

Now let's say you find a 55% edge at -120 (better odds):
- odds_ratio = 2.20 / 1.20 ≈ 1.833
- edge = 0.05

Kelly:
\`\`\`
(0.05 × 1.833 - 1) / (1.833 - 1)
= (0.0917 - 1) / 0.833
= -0.908 / 0.833
≈ -1.09
\`\`\`

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

\`\`\`
kelly_fraction = (your_hit_rate - implied_hit_rate) / odds_ratio
\`\`\`

Then apply a haircut:

\`\`\`
bet_fraction = kelly_fraction / 2  (for half Kelly)
\`\`\`

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
`,
  },
  {
    slug:        "implied-probability-calculator",
    title:       "How to Calculate Implied Probability from Odds",
    category:    "Fundamentals",
    keyword:     "implied probability",
    description: "**Implied probability** is the probability the sportsbook is *pricing in* when it sets odds. If DraftKings prices a pitcher strikeout prop at -110, that means t",
    content:     `# How to Calculate Implied Probability from Odds

## The Core Idea

**Implied probability** is the probability the sportsbook is *pricing in* when it sets odds. If DraftKings prices a pitcher strikeout prop at -110, that means the book thinks there's a 52.38% chance it hits.

Converting odds to implied probability is the foundation of edge analysis. If you think the actual probability is higher, you have an edge and should bet.

## The Three Odds Formats

### American Odds (Moneyline)
Used in the US. Can be positive or negative.

**Positive odds** (e.g., +200):
\`\`\`
implied_probability = 100 / (odds + 100)
\`\`\`

Example: +200 odds
- 100 / (200 + 100) = 100 / 300 = 0.333 = 33.3%

**Negative odds** (e.g., -110):
\`\`\`
implied_probability = odds / (odds + 100)
\`\`\`

Note: You have to negate the odds first.

Example: -110 odds
- abs(-110) / (abs(-110) + 100) = 110 / 210 = 0.5238 = 52.38%

Example: -200 odds
- 200 / (200 + 100) = 200 / 300 = 0.667 = 66.7%

### Decimal Odds (European)
Used internationally. Just one formula.

\`\`\`
implied_probability = 1 / decimal_odds
\`\`\`

Example: 1.91 decimal odds
- 1 / 1.91 = 0.523 = 52.3%

Example: 2.50 decimal odds
- 1 / 2.50 = 0.40 = 40%

### Fractional Odds (UK)
Used in British betting. Expressed as "5/1" (five-to-one).

\`\`\`
implied_probability = denominator / (numerator + denominator)
\`\`\`

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

\`\`\`
edge = your_probability - implied_probability
\`\`\`

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

\`\`\`
fair_probability = implied_probability / vig_factor
\`\`\`

Where vig_factor = (prob_a + prob_b) / 1.0

Example:
- Both sides are -110: 52.38% + 52.38% = 104.76% total
- vig_factor = 1.0476
- Fair prob for each side: 52.38% / 1.0476 = 50%

This tells you the market thinks it's dead even (vig-adjusted).

## The Shortcut for -110 Odds

If you're seeing a lot of -110 odds and want a quick mental math trick:

\`\`\`
At -110, the vig-free fair probability is (implied_probability - 0.0238) / 0.9762
\`\`\`

But honestly, just remember: **-110 means the book thinks 52.4%**. Everything else is just decimal places.

## Bottom Line

Implied probability is the bridge between what you think and what the market thinks. Master this calculation and you can:
- Spot mispricings instantly
- Calculate your edge accurately
- Know whether a bet is worth taking

Print the reference table above and keep it handy. After a few weeks, you'll have it memorized.
`,
  },
  {
    slug:        "how-to-remove-vig-odds",
    title:       "How to Remove Vig from Sports Betting Odds",
    category:    "Theory",
    keyword:     "remove vig",
    description: "**Vig** is the sportsbook's commission. When you see -110 odds on both sides of a bet, the book is saying:",
    content:     `# How to Remove Vig from Sports Betting Odds

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

\`\`\`
vig_total = implied_prob_a + implied_prob_b
vig_factor = vig_total / 1.0

vig_free_prob_a = implied_prob_a / vig_factor
vig_free_prob_b = implied_prob_b / vig_factor
\`\`\`

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

\`\`\`
1. Get vig-free probability at time of bet
2. Get vig-free probability at closing line (game time)
3. If you bet higher than closing, you have CLV
\`\`\`

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

\`\`\`
if vig_free_opening > vig_free_closing:
    you_have_positive_clv = True
\`\`\`

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
`,
  },
  {
    slug:        "mlb-strikeout-props-guide",
    title:       "MLB Strikeout Props Betting Guide",
    category:    "Markets",
    keyword:     "strikeout props",
    description: "A **strikeout prop** on DraftKings is a bet on how many strikeouts a pitcher will record in a single game. You're betting Over/Under a specific line\u2014like \"Will ",
    content:     `# MLB Strikeout Props Betting Guide

## The Basics

A **strikeout prop** on DraftKings is a bet on how many strikeouts a pitcher will record in a single game. You're betting Over/Under a specific line—like "Will the pitcher strike out 7.5 or more batters?"

Strikeout props are the most liquid prop market in baseball, second only to moneylines. This liquidity creates both opportunity (tight vig) and competition (sharp prices).

## How Strikeouts Work

A strikeout is recorded when:
- A pitcher throws three strikes to a batter
- The batter swings and misses
- The batter doesn't put the ball in play after three strikes

**Key:** It doesn't matter if the pitcher is the one throwing the pitch when strike three is recorded. Relief pitchers get credit for K's they inherit.

Innings pitched doesn't equal strikeouts. A pitcher can throw 7 innings with 3 K's or 7 innings with 12 K's—it depends entirely on the pitching style and opponent.

## The Data

Across MLB:
- **Starter average:** 8–10 strikeouts per game (7–8 IP)
- **Median K/9:** 8.5 strikeouts per 9 innings
- **Elite guys (top 10%):** 11+ K per game
- **Weak pitchers (bottom 10%):** 5–6 K per game

The current market has tight pricing—sharp books price K props within 0.5 strikeouts of the true expected value.

## Factors That Affect Strikeouts

### 1. Pitcher Velocity and Swing-and-Miss Rate
The single best predictor. A 96 mph fastball with elite spin gets more swings and misses than a 91 mph sinker.

- **High velo + high whiff %:** 10+ K per game likely
- **Low velo + low whiff %:** 6–8 K per game likely
- **The outliers:** A control pitcher (low K) who happens to face a high-strikeout team can exceed projections

### 2. Opponent Strikeout Rate
Some teams strike out a lot (high K%), others don't. The 2023 Oakland Athletics had the highest K% (26%+ of plate appearances). The Houston Astros had the lowest (17%).

Facing an 18% K rate team vs. a 24% K rate team is a 3 strikeout swing on average.

### 3. Bullpen Usage
This is the silent variable most bettors ignore.

If a pitcher is projected for:
- **Game script:** Blowout win likely → pulled after 5 innings → fewer batters faced → fewer K's
- **Game script:** Close game → stays in 7+ innings → more K opportunities

Preseason analysis is bad at predicting game script. Real-time updates (before game time) are better.

### 4. Recent Form
A pitcher's **last 5 starts** matter more than the season average. A guy with a 8.5 K/9 average but 11 K's in each of his last three starts is trending hot.

Avoid the primacy trap: one 15-K game doesn't overturn months of data. But two or three consecutive high-K performances *are* signal.

### 5. Weather and Park
- **Humidity and air density:** Low humidity = farther fly balls, fewer K's (batters put more balls in play). High humidity = denser air, more weak contact, more K's.
- **Park:** High-elevation parks (Colorado, Mexico City) have more strikeouts because the thin air helps fastballs move more. Humidors suppress this slightly.
- **Wind:** Wind direction doesn't directly affect K's (they're all air contact) but can affect a pitcher's confidence (more or less aggressive).

### 6. Umpire Tightness
Some umps have tight strike zones (fewer close calls = fewer strikeouts). Others have wide zones (more strikeouts).

Umpire data is available (umpscorecards.com) but is small-sample. Don't overweight unless the umpire has a strong reputation.

## The Betting Strategy

### Finding Edge: The 1–2 K Advantage

The market prices most starters at 8.0–9.0 K's. A 5% edge would be finding a starter you think is a 9.5 K pitcher being priced at 8.0.

Edges usually come from:

**Scenario 1: Weaker Opponent**
- Your pitcher is priced at 8.5 K's (fair)
- Opponent is unusually bad at K avoidance (23% K rate vs. league 20%)
- True expectation: 9.2 K's
- If the book priced at 8.5, OVER 8.5 has edge

**Scenario 2: Unusual Bullpen Usage**
- Your pitcher is expected to go 7 innings due to team rest patterns
- Book assumes 6 innings (standard)
- One more inning ≈ 1 more K (rough estimate)
- True: 9.5 K's, priced at 8.5

**Scenario 3: Recent Hot Streak**
- Average: 8.2 K/9
- Last 5 starts: 11.4, 10.1, 9.8, 11.2, 10.6 K's (trending hot)
- Book prices at 8.5 (using season average, not recent)
- True: 10.0 K's, priced at 8.5

### Avoiding the Traps

**Trap 1: One Big Game**
A pitcher throws 14 K's once. Book doesn't move. Don't chase the outlier.

**Trap 2: Playing Bullshit Justifications**
"This pitcher hates the sun" or "opponent bats right-handed so more K's." Margin is <1% if it exists at all.

**Trap 3: Ignoring Game Script**
If your team is -300 (heavy favorite), plan for a blowout. Pitcher pulled after 5. Fewer K opportunities.

**Trap 4: Fading Popular Bets**
When everyone is betting OVER 8.5, books sometimes shade the line up 9.0. Don't fade just because it's popular—only if the data says so.

## The Numbers to Track

Keep a spreadsheet:

| Date | Pitcher | Opp | Line | Over/Under | Your Edge | Result | Actual K | Hit |
|---|---|---|---|---|---|---|---|---|
| 2026-05-16 | DeGrom | Yankees | 8.5 | OVER | +0.8 | OVER | 9 | W |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

After 50 bets, calculate:
- **Accuracy:** How often OVER/UNDER hit as predicted
- **Profit:** dollars won / dollars wagered
- **ROI:** profit / total amount wagered

If you're hitting 52–55% at -110 odds, you have an edge. If you're at 50%, you're even. If you're below 50%, you're losing money and should stop or recalibrate.

## Market Efficiency

Strikeout props are one of the *most* efficient markets in sports betting. The vig is usually -110 or better, and sharp books price within 0.5 strikeouts of fair value.

This means:
- Huge edges (3+ K's) are rare
- Small edges (0.5–1.5 K's) are where the real money is
- Most casual bettors will struggle to beat this market
- Consistency beats chasing home runs

## Quick Checklist for K Props

- [ ] Is the pitcher a velocity guy or control guy? (Velo matters)
- [ ] How does the opponent rank in K%? (vs. league average)
- [ ] What's the game script? (favored/underdog = more/less usage)
- [ ] How has the pitcher performed in the last 5 starts? (recent form)
- [ ] Is there any injury news or bullpen usage changes?
- [ ] Am I getting at least -110 or better?

## The Bottom Line

Strikeout props are a great market to learn edge-finding because:
1. The data is public and reliable
2. The market is liquid (tight vig)
3. Edges are small but exploitable
4. One pitcher per day means you can track accuracy easily

Start with 50–100 bets on paper (don't bet real money yet). If you're consistently hitting 52%+ at -110, you've found an edge. Scale up.

Most professional bettors start here because it teaches patience and calibration. You won't get rich on K props alone, but you can build a repeatable, profitable system.
`,
  },
  {
    slug:        "f5-betting-mlb",
    title:       "First 5 Innings (F5) Betting Explained",
    category:    "Markets",
    keyword:     "f5 betting",
    description: "**F5** stands for \"First 5 Innings.\" It's a moneyline bet on who will be winning after the first 5 complete innings of an MLB game.",
    content:     `# First 5 Innings (F5) Betting Explained

## What Is F5?

**F5** stands for "First 5 Innings." It's a moneyline bet on who will be winning after the first 5 complete innings of an MLB game.

Unlike the full-game moneyline, F5 ignores everything that happens in innings 6–9. The game could end 2-0 in 5 innings (bet settles), or it could end 2-7 in the 9th (still 2-0 at the F5 mark, bet wins).

F5 markets exist on most DraftKings games during the regular season. The odds are usually close to, but slightly different from, the full-game moneyline.

## Why It Matters

F5 is interesting because it:
- **Isolates starter performance** — it's almost entirely determined by the two starting pitchers. By inning 6, relievers are entering.
- **Reduces variance** — fewer innings = less opportunity for late-inning chaos
- **Has cleaner data** — you can model starter matchups directly

For that reason, sharp bettors often find edge here that doesn't exist in full-game lines.

## The Difference Between F5 and Full Game

Example matchup:

**Full Game:** Dodgers -150 vs. Giants +130
**F5:** Dodgers -110 vs. Giants -110

Why the big difference? The Dodgers are favored full-game because:
- Better starter
- Better bullpen
- Better team overall

But in the F5 window (5 innings), **bullpen quality doesn't matter yet**. Both teams are using their top pitchers. This can narrow the gap.

If the Giants' starter is decent but their bullpen is bad, the full-game line is -150 (heavy Dodgers). The F5 line might be closer to -120 because the Giants can't blow it in the first 5.

## Factors Affecting F5 Outcomes

### 1. Starter Quality (Dominant)
This is 70–80% of the F5 result.

- **Starter W-L and ERA** — reliable indicators
- **Recent form** — last 5 starts matter more than season average
- **K/9 and BB/9** — strikeouts and walks are predictive of runs allowed

A starter with 2.8 ERA facing a lineup striking out 21% of the time will likely go scoreless or low-run F5.

### 2. Home Team Advantage (Real but Subtle)
The home team bats last in every inning. This means:
- Down 1-0 in the 5th, they still get their chance
- Down 0-1 in the 1st, they can tie it immediately after

Studies show home teams win F5 roughly 52–54% of the time on average. This is baked into the odds already, but it's a real structural advantage.

### 3. Opponent Lineup Quality
In the F5 window, you're seeing the opponent's top batters twice and maybe early third-AB against your starter.

- **High-OBP team** (think Astros, Dodgers) — more baserunners, higher chance of a run in 5 innings
- **Low-OBP team** (like A's) — fewer baserunners, higher NRFI likelihood, might not score at all

### 4. Bullpen Stability (Small Effect)
Here's the weird part: even though bullpen doesn't pitch in F5, **the knowledge that your bullpen is weak affects how aggressively your team bats**.

If you're down 1-0 with a trash bullpen, you'll swing more aggressively in the 4th/5th. This can lead to more runs for or against you. It's small but real.

### 5. Weather and Park
Wind and weather affect early-inning scoring the same way they do full game, but the effect is slightly muted (only 5 innings vs. 9).

- **Cold weather** → fewer home runs, harder contact, slightly more F5 favorites win
- **Wind blowing in** → fly balls die, fewer runs overall, slightly more shutouts/low-run scores
- **Humid weather** → more strikeouts, fewer balls in play, could go either way

### 6. Day Game Effect
Pitchers are sometimes slower to warm up in day games (especially after night games). This can suppress early-inning runs.

Evidence is mixed, but day games after night games (like an afternoon game after an evening contest) see slightly lower early-run totals.

## Finding Edge in F5

F5 is **less efficient** than full-game moneylines because:
- Less trade volume (smaller market)
- Fewer casual bettors understand starter matchups
- Books sometimes misprice based on team strength rather than pitcher matchups

This creates edge opportunities:

**Scenario 1: Better Starter, Worse Overall Team**
- Cleveland has a Cy Young contender
- Facing Texas, a strong team overall
- Full game: Texas -120 (team is stronger)
- F5: Cleveland -110 (better starter)
- Bet: Cleveland F5

**Scenario 2: Weak Opponent Lineup**
- Your team's mediocre starter vs. Oakland (24% K rate, weak lineup)
- Full game: 50-50 odds or slight underdog
- F5: Should be slight favorite (5 innings of weak opposition)
- Bet: Your team F5

**Scenario 3: Recent Form Divergence**
- Yankees starter has 4.2 ERA but is 3-0 last 5 starts with 2.15 ERA (hot)
- Blue Jays starter has 2.8 ERA but 0-2 last 5 starts with 4.80 ERA (cold)
- Full game: Blue Jays slight favorite (team strength)
- F5: Yankees should be favored (hot starter vs. cold starter)
- Bet: Yankees F5

## The Math: Win Probability by Inning

In the first 5 innings:
- **Teams score in roughly 70% of games** (avg 1.8 runs per team)
- **Scoreless games in first 5: ~15%** (usually happens when both starters are dominant)
- **One team leads by 3+ at F5 mark: ~30% of games** (big early leads often win)

This is why F5 moneylines are usually close to -110/-110 even when full-game lines are -150/+130. The starters are more evenly matched than the overall teams.

## Bankroll Management

F5 bets have a higher win rate than full-game (roughly 52–54% for both sides combined, vs 50%). But the odds are tighter (-110 usually).

Use Kelly sizing:
- 2–3% Kelly for F5 bets with edge
- Avoid the temptation to bet big because "fewer innings = safer"
- Variance is still meaningful (a 5-inning sample is small)

## Common Mistakes

**Mistake 1: Using Full-Game Projections**
"The Dodgers are +250 to win the World Series, so they should beat the Giants in F5." Wrong. F5 is about starters, not team strength.

**Mistake 2: Forgetting Warm-Up Time**
A pitcher who is "supposed to be good" but has been unavailable (injury, rest) might be rusty in the first inning. His recent-start data is more important than his ERA.

**Mistake 3: Overweighting One Big Game**
A starter allows 6 runs in the 1st inning of one game. That's an outlier. Look at the median, not the maximum.

**Mistake 4: Ignoring Day Games After Night Games**
Pitchers and batters are sometimes groggy in afternoon games after evening games. Data suggests slight suppression in early-inning runs (roughly 2–3%). It's small but worth remembering.

## Quick Checklist for F5 Bets

- [ ] Which starter is better? (ERA, last 5 starts, K/9)
- [ ] Which lineup is weaker? (K%, OBP, runs scored per game)
- [ ] Is this a day game after a night game? (slight fatigue factor)
- [ ] What's the weather? (wind, temperature, humidity)
- [ ] How much better is the full-game moneyline's team? (if it's close, F5 is likely 50-50)
- [ ] Am I getting -110 or better?

## The Bottom Line

F5 is the **best market for isolating pitcher quality** without the noise of bullpen strength, late-inning atmosphere, or team momentum.

If you can model pitcher performance, F5 is where you'll find consistent edge. The market is less efficient than full-game lines, vig is tight, and starter data is reliable.

Start with 30–50 F5 bets. If you're hitting 52%+ at -110, you've found an edge. That's a 2% ROI per bet—enough to be profitable long-term if you scale carefully.

The pros start with moneyline betting (full game or F5) before moving to prop props because it teaches market structure and variance. Master this first.
`,
  },
  {
    slug:        "mlb-home-run-props",
    title:       "How to Bet MLB Home Run Props",
    category:    "Markets",
    keyword:     "home run props",
    description: "A **home run prop** on DraftKings is a yes/no bet on whether a specific batter will hit a home run in a single game.",
    content:     `# How to Bet MLB Home Run Props

## What Is a Home Run Prop?

A **home run prop** on DraftKings is a yes/no bet on whether a specific batter will hit a home run in a single game.

Example: "Will Shohei Ohtani hit a home run tonight? Yes (-120) or No (-120)?"

If the batter hits a home run at any point in the game (before game ends), the yes side wins. It doesn't matter if it's a solo shot or a grand slam, infield or outfield—one home run = win.

## Why Home Run Props Are Popular

1. **High variance** — home runs are rare and explosive, so odds can be favorable (+150 to +300)
2. **Visual appeal** — everyone watches for home runs. It's engaging.
3. **Available every day** — 15 games with 2–3 major hitters each = 30–45 prop options daily
4. **Bankroll leverage** — if you find a +200 shot that you think is 20% (implied 33%), you're risking 1 unit to win 2

The downside: **home run props are one of the least efficient markets** because casual bettors love the upside and chase favorites they recognize.

## How Often Do Home Runs Happen?

Across MLB:
- **Average home runs per game:** 1.3 per team (1.3 × 2 = 2.6 total per game)
- **Average home runs per batter per season:** 0.3 (for qualified hitters)
- **Translation:** A league-average position player hits a home run once every 3.3 games

But this varies wildly:
- **Elite sluggers** (Judge, Ohtani, Trout): 0.6–0.8 HR per game
- **Above-average hitters**: 0.3–0.5 HR per game
- **Average hitters:** 0.15–0.3 HR per game
- **Weak hitters:** 0.05–0.15 HR per game

A batter with a 15% home run rate (about one every 6–7 games) is a below-average power hitter.

## Factors Affecting Home Run Probability

### 1. Batter Power (Dominant)
This is 60–70% of the result.

- **Slugging percentage** — tells you power production
- **ISO (Isolated Power)** — slugging minus batting average, pure power metric
- **Exit velocity** — how hard the batter hits the ball (50th percentile ≈ 88 mph, 90th ≈ 93 mph)
- **Barrel rate** — percentage of hard-hit balls that are "barrels" (optimal launch angle + exit velo for home runs)

A batter with:
- High barrel rate (12%+)
- High exit velo (91+ mph avg)
- Good season slugging (.500+)

...has roughly 25–30% chance of a home run per game.

### 2. Pitcher's Fly Ball Rate and HR/9
Some pitchers give up more home runs. This is partly because they're worse, but also because of their pitch selection.

- **Fly ball pitchers** give up more home runs
- **Ground ball pitchers** give up fewer
- **HR/9 rate** is the best predictor (e.g., 1.2 HR/9 vs. 0.8 HR/9)

Facing a pitcher who allows 1.5 HR/9 is dramatically different from facing one who allows 0.7 HR/9.

### 3. Park Factor
**This is huge and often ignored.**

Different parks suppress or amplify home runs:

- **Most HR-friendly:** Yankee Stadium, Globe Life (Rangers), Coors Field
- **Most HR-suppressing:** Comerica Park (Tigers), Oakland Coliseum, Petco (Padres)

A 90 mph line drive that's a home run in Yankee Stadium is a double in Oakland. DK usually prices this in, but soft-book lines sometimes don't.

Reference: [Here's a list of park factors](https://www.baseball-reference.com/play-index/). Anything above 1.0 is HR-friendly. Below 1.0 suppresses HRs.

### 4. Handedness (L vs. R)
Some batters hit better against certain pitcher types:
- **Righty batter vs. LHP:** Often better contact rates, similar HR rate
- **Righty batter vs. RHP:** Sometimes exploited by reverse splits

Check **splits data** (Baseball Reference) before betting. A batter with a .400+ slugging against LHP but .300 against RHP is a "splits guy."

Example: A righty with 25% HR rate vs. LHP but 12% vs. RHP. If he's facing an RHP, don't bet his yes side at +150.

### 5. Recent Form (Secondary Signal)
A batter with a hot streak (2+ home runs in last 5 games) is slightly more likely to continue. But this is a small effect (2–3% bump).

Avoid chasing after one big game. Use rolling averages.

### 6. Weather
- **Temperature:** Cold suppresses HRs (ball doesn't carry as far)
- **Wind:** Outfield wind direction matters
- **Air density:** Low humidity (dry air) carries better

A 400-foot fly ball in 75°F humid weather is a home run. In 55°F with 40% humidity, it's a warning-track out.

DK sometimes prices this in, sometimes doesn't. Sharp bettors monitor weather updates before game time and fade bets in cold weather.

## Home Run Prop Pricing

**Typical odds:**
- **Superstar slugger** (Judge, Ohtani, Trout): -130 to -150 (56–60% implied)
- **Star player** (Machado, Soto): -110 to -120 (52–55% implied)
- **Above-average hitter**: -110 (52% implied)
- **Average hitter**: +100 to +130 (50–57% implied)
- **Weak hitter**: +200 to +600 (33% implied or less)

Books use lazy pricing: "This guy hit 0.30 HR/game last season, so -110 implied 52% is close enough."

But the sharp books (Pinnacle) will price:
- Cold-weather adjustment
- Pitcher-specific factors
- Park factor
- Recent form

This is where edge lives: finding mispricings the soft books (DK) miss.

## Finding Edge in HR Props

### Scenario 1: Cold Weather Underdog
- Batter normally 0.25 HR/game (implied 25% per game)
- It's 45°F and windy
- Book still prices at -110 (52% implied)
- True probability: maybe 15% in cold weather
- **Bet: No side** (odds favor it)

### Scenario 2: Pitcher Factor
- Batter has 0.30 HR/game on the season
- Facing a pitcher with 1.8 HR/9 (well above average)
- Book prices at -110 (52% implied)
- True probability: 35–40%
- **Bet: Yes side** (underpriced)

### Scenario 3: Park Factor Mispricing
- Batter has 0.25 HR/game on the season
- Playing at Yankee Stadium (1.15 park factor)
- Book prices at -110 (52% implied)
- True probability with park factor: 30–35%
- **Bet: Yes side** (underpriced)

### Scenario 4: Revenge Narrative (Ignore)
- "Player was traded and plays old team tonight"
- "Player hit 2 HRs last night, on a heater"
- Books often price narratives
- **Reality:** Single-game correlation is tiny (1–2% at most)
- Don't bet based on narrative

## The Danger: Overestimating HR Probability

This is the most common mistake.

A batter with a 25% season HR rate does *not* have a 25% chance every game. That's an *average*. On any given night:

- He has maybe 20–30% depending on factors above
- Some games it's 15% (cold, bad pitcher matchup, park factor)
- Some games it's 40% (warm, soft pitcher, HR-friendly park)

Don't just use season average. Adjust for:
1. Temperature
2. Pitcher HR/9 vs. league average
3. Park factor
4. Handedness matchup

## Quick Checklist for HR Props

- [ ] What's the batter's season HR rate? (home runs per game)
- [ ] What's the pitcher's HR/9 vs. league average?
- [ ] What's the park factor? (HR-friendly or suppressing?)
- [ ] What's the temperature? (if <55°F, suppress HR odds 3–5%)
- [ ] Are there handedness splits I should check?
- [ ] Has the batter had recent home runs? (small signal, not dominant)
- [ ] What's the implied probability from the odds?
- [ ] Am I getting better odds than my estimate?

## Bankroll Strategy

HR props are high-variance. A batter with a 25% HR rate has long stretches (10+ games) without a home run.

Use conservative Kelly:
- **1–2% Kelly per bet** (even if you have edge)
- Track 50+ bets before scaling up
- Expect 20–30% drawdown variance

The house hit rate on HR props is around 45–50% on average. If you're consistently hitting 50%+, you have edge.

## Tools to Help

- **Baseball Reference** — season splits, park factors, pitcher HR/9
- **Weather.com** — check game-time temperatures
- **Savant** — exit velo, barrel rate, player home run distances
- **Vegas lines** — track closing lines and compare to DK prices

## The Bottom Line

HR props are **high-variance, low-efficiency markets** where casual bettors blow money on favorites.

If you can systematically adjust for park factor, pitcher quality, and weather, you can find 2–3% edges.

But don't chase the upside. A 25% HR guy at +150 looks tempting, but you need to be 27–28% confident to break even. Most bettors aren't that precise.

Start with 100 bets tracked in a spreadsheet. If you're hitting 50%+, you have an edge. If you're hitting 48%, stop and recalibrate.

The pros treat HR props as a side market, not their primary focus. They're useful for portfolio diversification and leverage, but edge is harder to find than in pitcher strikeouts or moneylines.
`,
  },
  {
    slug:        "machine-learning-sports-betting",
    title:       "How Machine Learning Models Beat the Books",
    category:    "Theory",
    keyword:     "machine learning",
    description: "Imagine a sports betting model trained on 5 years of data. It sees patterns humans miss\u2014park effects, weather correlations, pitcher fatigue cycles. It outputs p",
    content:     `# How Machine Learning Models Beat the Books

## The Promise and the Reality

Imagine a sports betting model trained on 5 years of data. It sees patterns humans miss—park effects, weather correlations, pitcher fatigue cycles. It outputs predictions with 55% accuracy.

"55% accuracy times a $1000 bankroll at -110 odds = exponential growth," the story goes.

Reality: Most models don't beat the books. Here's why, and how the ones that do stay ahead.

## How the Books Price

Modern sportsbooks don't rely on opinions anymore. They use algorithms too.

DraftKings and FanDuel employ:
1. **Historical databases** — 20+ years of outcomes
2. **Regression models** — team strength, recent form, injuries
3. **Market pricing** — where the money is flowing
4. **Sharp bettors' actions** — early signal from respected players

A book's opening line on a moneyline is usually 51.5% vs. 51.5% (both sides 52% vig-included). This is extraordinarily efficient.

By the time you see the line 24 hours before game time, 100+ professional bettors have already traded on it. The market has priced in almost all public information.

## Why Machine Learning Can Still Win

The key insight: **The market prices the *average* outcome. ML models can find *edges* in variance.**

Three specific ways:

### 1. Injury Information Before Market Reacts
If a star player is ruled out 2 hours before game time, the market hasn't adjusted. Your model, if it updates in real-time, can.

Example:
- Ohtani ruled out 90 minutes before game time
- Book's opener: Dodgers -110
- Sharp bettors immediately fade Dodgers (no Ohtani)
- By game time: Dodgers -130 (massive shift)

A model that **ingests injury news and recalculates every 30 minutes** can catch the 2-hour window when Dodgers are still -110 but should be -150.

### 2. Micro-Patterns the Market Doesn't Price
The market prices:
- Pitcher ERA
- Team win%
- Recent form (last 5 games)

Models can price:
- Pitcher ERA in *day games* vs. night games (real effect: ~0.3 ERA difference)
- Team win% *after a loss* (momentum is small but real)
- Specific pitcher-batter handedness matchups
- Day games after travel (sleep deprivation effect)
- Home-field fatigue in 3rd straight home game

None of these are huge edges (1–2% each). But **combine 5 small edges and you have a 5–10% edge total.**

### 3. Market Inefficiency in Niche Props
The markets the books *really* care about (full-game moneylines, spreads) are sharp and efficient.

But niche props (NRFI, F5, pitcher outs) have:
- Lower trade volume
- Fewer sharp bettors
- More casual money (soft-book inflated favorites)

A model that focuses on low-volume markets can find bigger edges (3–5%) because there's less competition.

## What Separates Winners from Losers

### Loser Model:
1. Train on 3 years of historical data
2. XGBoost with 30 features
3. 55% accuracy on test set
4. Launch with full Kelly
5. First 100 bets: 49% accuracy (worse than 50%)
6. Bankroll cut in half in 3 months

What went wrong: Overfitting to historical data. Market has shifted. Model didn't account for structural changes (rule changes, different teams, player aging).

### Winner Model:
1. Train on 5 years, but with recent-data weight (2x weight on last 2 years)
2. Validate with **walk-forward testing** (2019 data to predict 2020, 2020 data to predict 2021, etc.)
3. Track **closing line value** not just accuracy (did you beat the eventual market?)
4. Use conservative Kelly (half Kelly, or even quarter Kelly)
5. First 100 bets: 51.5% accuracy, 2% ROI (solid)
6. Scale gradually and monitor for drift

The difference: Winners validate against **future data and market prices**, not just historical accuracy.

## The Four Types of Edge

### Edge Type 1: Information Edge
You know something the market doesn't. Example: Injury that hasn't been announced yet.

**Lifetime:** 1–24 hours (until announcement)
**Expected value per bet:** 2–10%
**How to exploit:** Real-time data feeds, injury news aggregators, connections to teams

This is the edge most models *can't* exploit (no data access).

### Edge Type 2: Analytical Edge
You calculate probabilities *better* than the market. Example: A pitcher's true K/9 adjusted for opponent quality.

**Lifetime:** Days to weeks (until the market converses)
**Expected value per bet:** 1–3%
**How to exploit:** Better feature engineering, better validation

This is what most ML models attempt. Problem: it's slow and competition is fierce.

### Edge Type 3: Market Inefficiency Edge
The market prices based on mass behavior. Casual bettors chase favorites. Sharp bettors fade the public. In niche markets, the public moves too much.

**Lifetime:** Weeks to months (until sharps notice and trade in)
**Expected value per bet:** 2–5%
**How to exploit:** Focus on low-volume markets, track public betting percentages, fade the public

This is reliable but requires market monitoring.

### Edge Type 4: Model Calibration Edge
Your model predicts 60% but the true win rate on those predictions is 61%. You're slightly better calibrated than the market's model.

**Lifetime:** Season (until market recalibrates)
**Expected value per bet:** 0.5–2%
**How to exploit:** Bet large when confident, small when uncertain; track calibration

This is the "boring" edge that wins long-term. It's also the hardest to find and the slowest to exploit.

## The Math: When Does ML Beat Books?

A model needs to be better than the market by:

\`\`\`
required_edge = (vig / 100) + market_accuracy_gap
\`\`\`

At -110 odds, vig is 4.76%. So you need:
\`\`\`
required_edge = 4.76% + (your_accuracy - market_accuracy)
\`\`\`

If the market is 51% accurate (typical for efficient markets):
- You need 55.76% accuracy to break even
- 53% accuracy loses money
- 56% accuracy makes 1% ROI

This is **much harder** than it sounds. Most models achieve 50–52% on test data, which loses money against -110 juice.

## How to Validate a Model Before Betting Real Money

### Step 1: Backtest on Historical Data
Train your model on years 1–3. Test on year 4. Did you get 52%+ accuracy?

**Gotcha:** This tests if your model *understands the past*, not if it can predict the future.

### Step 2: Walk-Forward Validation
Train on Jan–Apr. Test on May. Train on Jan–May. Test on Jun. Etc.

Does your accuracy stay 52%+ or drop to 50%?

### Step 3: Simulate Real-Time
Take your model's predictions *as they were made* (not with hindsight). Compare to the *closing line* (odds at game time, not opening).

Did your edge persist against the sharpened market?

### Step 4: Track Closing Line Value (CLV)
For every bet, log:
- Your prediction probability
- Opening odds (implied probability)
- Closing odds (implied probability)
- Actual result

Calculate: Did you consistently get better odds than where the market ended up?

\`\`\`
clv = if (your_prob > closing_prob) then "positive" else "negative"
\`\`\`

**Real edge shows positive CLV over 100+ bets.** If your backtest shows 55% but CLV is breakeven, your edge is fake.

### Step 5: Paper Trade First
Before risking real money, place 50–100 "paper" bets. Track them in a spreadsheet for 2 weeks. 

Did you hit 52%? Did you have positive CLV? Great, now risk real money.

If you hit 49–50%, stop and recalibrate. Your model isn't ready.

## Common ML Pitfalls

### Pitfall 1: Overfitting to Juice
The market prices both sides at 52% (vig-included). If your model predicts 51% and 51%, you're not beating anything—you're just replicating the market.

**Fix:** Validate against closing lines, not season accuracy.

### Pitfall 2: Feature Leakage
You accidentally train on data from the same game (e.g., you include "home team scored first" as a feature for predicting home team win). Your model looks great on test data but fails in production.

**Fix:** Strict separation of training/test data. Do not use *any* same-game data as features.

### Pitfall 3: Market Drift
Your model trained on 2020–2022 data. It's now 2024 and the game has changed (analytics, rule changes, player talent). Your 52% accuracy has degraded to 50.5%.

**Fix:** Retrain quarterly. Weight recent data 2x. Track in-season accuracy continuously.

### Pitfall 4: Underfitting to Bankroll Risk
You found a 2% edge. You bet 10% Kelly on every game. After 100 bets, you hit a 15-bet losing streak (normal variance) and your bankroll is down 30%.

**Fix:** Use half Kelly (1% per bet). This cuts growth to 75% of optimal but also cuts drawdown risk in half.

## Real-World Example: The Strikeout Model

Here's how a winner model beats the market on pitcher strikeouts:

**Data sources:**
- Pitching stats (Statcast, 5 years)
- Lineup data (Statcast, 5 years)
- Umpire data (umpscorecards.com)
- Weather (Open-Meteo archive)

**Features:**
- Pitcher K/9, recent form (K/9 last 5 games)
- Opponent K% (how often they strike out)
- Weather (cold suppresses K's, dry air inflates them)
- Umpire tightness (affects K rate ~1%)
- Park factor (indirectly, through ump zone variance)

**Validation:**
- Train on 2019–2022
- Test on 2023 (hold out)
- Walk-forward: Train on every month, test next month
- Compare to DK opening and closing odds

**Results:**
- Backtest accuracy: 53%
- 2023 hold-out accuracy: 52.2%
- CLV: +0.8% (for every 100 bets, bet 1 unit, get 0.8 units profit)
- ROI at half Kelly: +1.6%

This model works because:
1. Validation against realistic future data
2. Conservative Kelly sizing
3. Focus on a niche market (strikeouts) with less competition
4. Continuous retraining (quarterly)
5. Tracking CLV, not just accuracy

## The Bottom Line

Machine learning models *can* beat the books, but it requires:

1. **Better data than the market** — real-time injury feeds, detailed umpire data
2. **Or better analysis** — smarter feature engineering and validation
3. **Or niche focus** — smaller markets with less competition
4. **And ruthless validation** — paper trading, walk-forward testing, CLV tracking
5. **And conservative sizing** — half Kelly minimum, not full Kelly

The models that succeed focus on **long-term calibration and edge retention**, not flashy 55% accuracy claims.

If your model shows 55% on historical data but doesn't beat closing lines, it's not ready. If it beats closing lines but you bet full Kelly and blow your bankroll to variance, it won't matter.

Start small, validate religiously, and scale gradually. The boring, data-driven approach beats the overconfident "I found the secret" model every time.
`,
  },
  {
    slug:        "nrfi-betting-strategy",
    title:       "NRFI Betting Strategy: A Data-Driven Approach",
    category:    "Strategy",
    keyword:     "nrfi strategy",
    description: "You now know what NRFI is (no run first inning). You know the baseline (55% of games go scoreless). You know the factors (pitcher, lineup, weather).",
    content:     `# NRFI Betting Strategy: A Data-Driven Approach

## Beyond the Basics

You now know what NRFI is (no run first inning). You know the baseline (55% of games go scoreless). You know the factors (pitcher, lineup, weather).

Now comes the hard part: **how do you actually find edge?**

NRFI is one of the most traded props on DraftKings. Books have years of data, sharp bettors place significant volume, and the market is shaded heavily.

Casual bettors lose money here because they chase narrative ("favorite pitchers" or "this team never scores early"). Winners find **tiny statistical edges** and bet them consistently.

## The Cascade Model: Three Levels of Decision

### Level 1: Is NRFI Even Possible?
Some matchups are dominated by high-velocity starters and weak opposition lineups. Others are weak pitchers vs. aggressive lineups.

Before considering edge, ask: **Is this a >45% or <55% probability matchup?**

If both teams are well-matched (50-50 starters, neutral lineups), the book will price it -110/-110. You can't find edge here—vig will kill you.

**Filter:** Only bet NRFI if the true probability is outside the 48-52% range. If you think it's 45%, bet NRFI. If you think 53%, skip it (edge too small).

### Level 2: Pitcher Quality (Dominant Factor)
Narrow to pure pitcher analysis.

- **Velocity and spin rate:** Elite velo (96+) with good spin = NRFI favorite. Soft-tossing control guys = YRFI slight edge.
- **First inning specifically:** Some pitchers have high first-inning ERA (slow starters). Others dominate early. Use first-inning K/BB splits, not season splits.
- **Bullpen connection:** If your starting pitcher has allowed runs in the first, is the team still confident? Or do they play timid? (Small effect but real.)

### Level 3: Lineup Weakness (Secondary Factor)
Given the pitcher quality, how bad is the opponent?

- **K% (strikeout rate):** High K% teams (23%+) go scoreless more often. Low K% teams (20%-) make more contact, higher YRFI odds.
- **OBP (on-base percentage):** Teams with low OBP get fewer baserunners. Fewer runners = lower YRFI probability.
- **Leadoff quality:** A patient, high-OBP leadoff hitter is dangerous. A slap hitter with high K% is not.

## The NRFI Scoring System

Use this framework to quantify edge:

\`\`\`
nrfi_score = (pitcher_velocity_factor × 0.40) +
             (opponent_k_factor × 0.35) +
             (weather_factor × 0.15) +
             (home_field_factor × 0.10)
\`\`\`

### Pitcher Velocity Factor
- **96+ mph average, low walk rate:** 1.2 (elite, NRFI favored)
- **94-95 mph, average walks:** 1.0 (neutral)
- **92-93 mph:** 0.95
- **<92 mph:** 0.85 (soft toss, NRFI disfavored)

### Opponent K Factor
- **23%+ K rate:** 1.15 (lots of strikeouts, NRFI favored)
- **21-22% K rate:** 1.05
- **20-21% K rate:** 1.0 (neutral)
- **19-20% K rate:** 0.95
- **<19% K rate:** 0.85 (good contact, YRFI favored)

### Weather Factor
- **<50°F or strong wind:** 1.1 (cold suppresses runs, NRFI favored)
- **50-65°F:** 1.0 (neutral)
- **>75°F with wind blowing out:** 0.85 (heat favors power, YRFI favored)

### Home Field Factor
- **Betting NRFI for the home team:** 0.95 (home team has slight bat-last advantage)
- **Betting NRFI for the away team:** 1.05 (away team bats first, slight disadvantage)

### Score Interpretation
- **Score > 1.1:** NRFI is underpriced; strong bet
- **Score 1.0–1.1:** NRFI has slight edge
- **Score 0.95–1.0:** Too close to vig; skip
- **Score < 0.95:** YRFI has edge

## Real-World Application

**Example 1: High-Velocity Starter vs. Bad Lineup**

- Starter: 96.2 mph average, 2.1 BB/9 (excellent)
- Opponent: 24% K rate, .310 OBP (weak offensive team)
- Weather: 48°F, wind in
- NRFI pitched for the away team

Scoring:
- Pitcher velocity: 1.2 × 0.40 = 0.48
- Opponent K: 1.15 × 0.35 = 0.40
- Weather: 1.1 × 0.15 = 0.165
- Home field: 1.05 × 0.10 = 0.105
- **Total score: 1.15** ← NRFI has edge

Book prices NRFI at -110 (52.4% implied). True probability: maybe 60%. **Bet NRFI.**

**Example 2: Weak Starter vs. Strong Lineup**

- Starter: 91.8 mph average, 3.8 BB/9 (walks batters)
- Opponent: 19% K rate, .330 OBP (strong offensive team)
- Weather: 72°F, humid, wind blowing out
- NRFI pitched for the home team

Scoring:
- Pitcher velocity: 0.85 × 0.40 = 0.34
- Opponent K: 0.85 × 0.35 = 0.30
- Weather: 0.85 × 0.15 = 0.13
- Home field: 0.95 × 0.10 = 0.095
- **Total score: 0.86** ← YRFI has edge

Book prices NRFI -120 (54.5% implied). True probability: maybe 45%. **Bet YRFI at even odds.**

## The Bankroll Strategy

NRFI has a win rate around 55% at fair odds. This is *lower* than pitcher K's (53% accuracy for you might mean 50% hit rate due to vig). Be conservative.

### Progressive Betting:
- **Bets 1–20:** 1% Kelly (learn the market)
- **Bets 20–100:** 1.5% Kelly if hitting 55%+
- **Bets 100–500:** 2% Kelly if sustained 55%+
- **Bets 500+:** 2–3% Kelly only if edge >3%

### Stop-Loss:
If you go 0 for 20 (0% hit rate), your model is broken. Stop and recalibrate.

If you go 8 for 20 (40% hit rate), you're losing money. Drop back to 0.5% Kelly or stop.

## Common NRFI Mistakes (and How to Avoid Them)

### Mistake 1: Ignoring Recent Starter Performance
You see a pitcher with a 3.2 ERA. That's average. But his **last 5 games: 1.8 ERA, 3 scoreless in first innings.**

His recent trend is better than season average. This matters. Weight recent form 2x in your analysis.

### Mistake 2: Overweighting One Game
A pitcher allowed 2 runs in the first inning last start. Now every model says YRFI.

This is an outlier. Look at the median, not the maximum.

### Mistake 3: Forgetting the Home Team Batting Second
Home teams are slightly favored in NRFI (they bat last). If you're fading NRFI and you're betting against the home team, you're also fighting the structural edge of batting last. Account for this.

### Mistake 4: Chasing Recent News
"This pitcher was just called up. He's excited to pitch in the majors." This is pure narrative. Data says nothing about "excitement" predicting first-inning runs.

Skip the narrative bets.

### Mistake 5: No Umpire Data
Some umpires have tight strike zones (fewer balls, fewer HBP, potentially fewer runs). Some have wide zones.

This is a small effect (1–2%) but worth tracking. If you can get umpire data (umpscorecards.com), do it.

## Tracking Your Edge

Keep a simple spreadsheet:

| Date | Pitcher (Away/Home) | Opp | NRFI/YRFI | Line | Your Edge | Result | Hit | Profit |
|---|---|---|---|---|---|---|---|---|
| 5/20 | Rodon (SF) / Gallo (NYY) | NRFI | -120 | +3% | NRFI | Y | +$1.1 |

After 50 bets, calculate:
- **Hit rate:** (wins / total)
- **ROI:** (profit / total wagered)
- **Win rate:** Should be 52–55% at -110

If you're at 50%, you're even with vig. If you're at 48%, you have a leak. Recalibrate.

## Advanced: Line Shopping

DraftKings isn't the only book. FanDuel, Caesars, and Pinnacle might have different NRFI lines.

- DK: NRFI -110
- FanDuel: NRFI -120
- Pinnacle: NRFI -108

If your edge is +3%, taking -108 instead of -110 increases your ROI by 2 points.

**Always shop lines.** Use an arbitrage tool like Action Network or covers.com to compare.

## Seasonal Variance

NRFI is less efficient in:
- **April-May:** Cold weather (more NRFI)
- **July-August:** Hot weather (more YRFI)
- **September:** Weak teams in playoffs, strong hitting (more YRFI)

The book struggles to price seasonal variance. Sharp bettors exploit this. You can too.

## The Bottom Line

NRFI strategy isn't about finding massive edges. It's about:
1. **Filtering to favorable matchups** (pitcher velocity + opponent K%)
2. **Sizing conservatively** (1–2% Kelly)
3. **Tracking results religiously** (spreadsheet)
4. **Adjusting for weather, umpires, home-field**
5. **Shopping lines** (getting -108 instead of -110)

Combine these five and you're better than 85% of casual bettors.

Start with the scoring system above. Bet only when score > 1.1. Track 50 bets. If you're hitting 54%+, you've found an edge.

Scale gradually. The market is efficient, but efficiency means small edges are available *everywhere* for disciplined bettors.

That's the NRFI game.
`,
  },
  {
    slug:        "line-movement-sports-betting",
    title:       "Understanding Line Movement in Sports Betting",
    category:    "Theory",
    keyword:     "line movement",
    description: "**Line movement** is when sportsbook odds change between opening and closing.",
    content:     `# Understanding Line Movement in Sports Betting

## What Is Line Movement?

**Line movement** is when sportsbook odds change between opening and closing.

Example:
- **Opening (24 hours before):** Dodgers -110
- **Closing (5 minutes before):** Dodgers -125

The line moved 15 cents (in American odds terms). This signals **where the money and sharpness went**.

Understanding line movement is how professional bettors spot:
- Injury news not yet public
- Sharp money flowing to one side
- Public money chasing overvalued favorites
- Opportunities to get better odds than sharps eventually forced

## Why Lines Move

### Reason 1: New Information
An injury announcement, weather change, or roster move happens. The market reprices instantly.

Example: Star pitcher ruled out 3 hours before game time.
- Opening: Dodgers -110
- 3 hours later: Dodgers -130 (moved 20 cents)
- This is information-driven movement

### Reason 2: Money Flow
Money is wagered on one side. The book adjusts to balance liability.

Example: Sharp bettors put $500K on Dodgers in first 6 hours.
- Opening: Even odds
- After sharp money: Dodgers -130
- The book moved the line to attract action on the other side

### Reason 3: Closing Price Discovery
The true market consensus emerges as closing approaches. Amateurs fade, sharps place final bets.

Example: For 20 hours, both sides see equal volume.
- By 30 min before game: Sharps reveal their hand
- Closing line: Dodgers -125 (reflects real consensus)

## Sharp Money vs. Public Money

This is the core of line movement strategy.

### Sharp Money Characteristics:
- Early action (first 6–12 hours)
- Small volume, high confidence
- Moves the line 5–15 cents
- Often gets counter-action from the public

### Public Money Characteristics:
- Later action (last 12–6 hours)
- Large volume, lower confidence
- Pushes lines in the direction of favorites
- Often moves the line *opposite* to sharp direction

### The Dynamic:
1. Sharps bet early with conviction → line moves
2. Public sees the moved line and fades it ("opposite of where the smart money is")
3. Public bets the other side
4. Book adjusts again to balance, but sharps have better odds

Example timeline:
- 09:00 AM: Opens Dodgers -110
- 09:30 AM: Sharp $100K on Giants → Dodgers moves to -120
- 02:00 PM: Public fades the sharp move, $200K on Dodgers → Dodgers moves back to -115
- 05:55 PM (5 min before): Final market consensus clears at Dodgers -112

Sharps got -120 on Giants. Public got -115 on Dodgers. Closing settled -112.

## How to Read Line Movement

### Type 1: Sharp Movement
**Characteristic:** Early, small-volume, narrow range.

- Opens: -110 even
- 2 hours later: -120 (single sharp bet)
- Stays at -120 all day

**Signal:** Sharps are highly confident. Respect this. If sharps believe in +130 Giants, that's information.

### Type 2: Public Rejection (Reverse Movement)
**Characteristic:** Sharp move, then counter-move of larger magnitude.

- Opens: -110
- Sharps move to: -130 Dodgers (small volume)
- Public bets Giants hard: Line moves to -115 Dodgers (large volume)

**Signal:** Public disagrees with sharps. This often means the public is wrong. Track these and grade.

### Type 3: Late Sharp Movement
**Characteristic:** Last-minute flip, usually before major matchup info.

- Steady at -120 all day
- 3 minutes before: Suddenly -130 (sharp reversal)
- Game time: -128

**Signal:** New injury info, umpire announcement, or sharps reaching consensus. These moves are usually directionally correct.

## Closing Line Value (CLV)

This is the **true measure of betting skill**.

\`\`\`
clv = (your_opening_odds - closing_odds)
\`\`\`

If you bet Dodgers at -110 opening and closing is -130:
- You got -110 when the market eventually said -130
- You got a *worse* deal than the sharps' consensus
- Negative CLV

If you bet Giants at +130 opening and closing is +112:
- You got +130 when the market eventually said +112
- You got a *better* deal than consensus
- Positive CLV

**Professionals track CLV, not just hit rate.**

Why? Because hit rate can be 50% but CLV positive (you're beating the market). Or 52% but CLV negative (you're getting bad odds).

### The Math:
Over 100 bets:
- Hit rate: 52% (you're winning 52 out of 100)
- Closing line value: +1.5% (on average, you get 1.5% better odds than closing)
- ROI: 52% × average_odds - 100% ≈ 2–3%

That's professional territory.

Without CLV:
- Hit rate: 52%
- Opening odds: You bet -110
- Closing line: You got -115 (worse)
- Effective ROI: nearly 0%

Same hit rate, different skill.

## Line Movement Signals

### Signal 1: Heavy Public Fade
**Pattern:** Opens -110. Sharp bets one side. Public bets opposite. Line reverses.

**What to do:** The first move (sharp movement) is usually right. When public counter-moves, track which side hit more often. If sharps are 60% right, bet their direction against public counter-movement.

### Signal 2: Late Sharp Consensus
**Pattern:** Line sits steady all day. 5 minutes before, moves 10 cents sharply.

**What to do:** This is information. Don't fade it. Late sharp movement is often injury news or umpire assignments.

### Signal 3: Massive Public Skew with Sharp Counter
**Pattern:** -150 on public favorite despite 70% public money on that side.

**What to do:** Books don't move this much without reason. If public is 70% on one side but line favors the other, sharps are big on the other side. Track which side hits. Sharps usually win here.

### Signal 4: No Movement (Consensus)
**Pattern:** Opens -110, closes -110. Flat line.

**What to do:** Both sides are valued equally. No edge from line movement. Source your edge elsewhere (analytical edge, not market signal).

## How to Track Line Movement

Tools to use:
1. **Action Network** — shows opening, closing, and line movement history
2. **Covers.com** — historical odds from all books
3. **BookMaker.eu** — European odds archive (sharper than US lines)
4. **Your own spreadsheet** — track opening and closing for every bet

**Spreadsheet columns:**
- Date
- Game
- Your bet
- Opening odds
- Closing odds
- Line movement
- Money direction (sharp vs. public)
- Result
- CLV

After 50 bets, grade:
- Did you consistently get opening or closing odds?
- If opening: Did sharp movement predict results?
- If closing: How often did you match market consensus?

## Real-World Example: Reading the Tape

**Game: Dodgers vs. Giants, 5/20/2026**

- **09:00 AM:** Opens Dodgers -110 (52.4% implied), Giants +100 (50.0% implied)
- **09:15 AM:** Sharp $200K on Giants → Dodgers -130 (56.5% implied)
- **12:00 PM:** Public fades and bets Dodgers → Dodgers -120 (54.5% implied)
- **04:00 PM:** No new info, line stable at -120
- **05:55 PM:** Final market → Dodgers -115 (53.5% implied)

**Signal interpretation:**
- Sharps believed in Giants underdog (wanted +130)
- Public disagreed and chased Dodgers
- Market consensus landed at Dodgers -115 (slightly favored but not as heavily as sharps initially pushed)

**Lessons:**
1. Sharps wanted Giants badly (small volume, big conviction)
2. Public disagreed (larger volume, fading sharp thesis)
3. Reality landed between them (Giants slightly undervalued, Dodgers slightly overvalued vs. opening)

If **Giants actually won**, this is a "sharp vs. public" matchup where sharps were right. If **Dodgers won**, sharp thesis was wrong but the reversal delay cost them better odds.

## The Key Insight: Getting the Right Price

The *best* bettors don't just pick winners. They:
1. **Identify edges** (analytical or informational)
2. **Wait for the right price** (exploit line movement)
3. **Bet both sides if needed** (sharp on one side of a reversal, fading public on the other)

Example:
- You think it's 52% Giants
- Opens +100 (50% implied) → Bet Giants
- By closing, if it's -130 Dodgers (+100 Giants), you got a great price earlier
- If closing is -110 (52.4%), you got a slightly better price than fair
- Both are positive CLV situations

## The Bottom Line

Line movement reveals:
- **Where sharp money flows** (early, confident, small volume)
- **Where public money chases** (late, reactive, large volume)
- **What the market consensus is** (closing line = truth)

Master three things:
1. **Track CLV, not just hit rate** — you can be right 50% but beat the market by getting better prices
2. **Respect late sharp movement** — it's usually informational
3. **Fade obvious public biases** — when 70% of money is on one side despite unfavorable line, the other side has edge

Start by opening Action Network before every game. Note opening and closing odds. After 20 games, you'll spot patterns in how lines move and where opportunity lives.

The pros don't see line movement as noise. They see it as the market confessing what it really believes.
`,
  },
]

export function getArticle(slug: string): Article | undefined {
  return ARTICLES.find(a => a.slug === slug)
}
