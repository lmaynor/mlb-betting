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
    description: "How the Kelly Criterion sizes a bet from your win probability and the price, with a worked example and why most bettors use a fraction of it.",
    content:     `# Kelly Criterion for Sports Betting

## What it is

The **Kelly Criterion** is a formula for what fraction of your bankroll to bet when you believe you have an edge. It maximizes long-run growth without risking ruin, but only if your win probability is right.

\`\`\`
f = (p x d - 1) / (d - 1)
\`\`\`

Where:
- **f** = the fraction of your bankroll to bet
- **p** = your estimated probability that the bet wins
- **d** = the decimal odds (-110 is 1.909, +150 is 2.50, +100 is 2.00)

If p x d is 1 or less, f is zero or negative and the answer is simply: do not bet.

## Worked example: 55% at -110

- Decimal odds: 1.909
- p x d = 0.55 x 1.909 = 1.050
- f = (1.050 - 1) / (1.909 - 1) = 0.055

Full Kelly says bet 5.5% of your bankroll, which is $55 on $1,000. The book's price implies 52.4% (that is the break-even win rate at -110), so a 55% estimate is an edge of about 2.6 points.

## More examples

- 52% at -110: p x d is 0.993, so f is -0.8%. No bet. A 52% win rate does not beat -110.
- 55% at -110: f = 5.5%.
- 60% at -110: f = 16.0%. This is why overestimating your probability is dangerous: five points of error triples the stake.
- 45% at +150: f = 8.3%.
- 55% at +100: f = 10.0%.

## Why most people bet a fraction

Full Kelly is the optimum only when your probability is exactly right. If you overestimate it, you overbet, and bankroll swings get large fast. Most bettors use a fraction, usually half or a quarter. At quarter Kelly the 55% / -110 example is 1.4%, or about $14 on $1,000. The +EV Terminal on this site sizes at quarter Kelly with a 5% cap per bet.

## Pitfalls

- **Your probability is the whole game.** The formula is only as good as p. A model that is overconfident at high edges will oversize exactly the bets it is wrong about.
- **Bets are not independent.** The same prop at three books, or several lines on one player, is one idea. Do not Kelly-size each separately.
- **Stake limits.** Books limit winning accounts, so the Kelly number is sometimes more than you can place.
- **Price matters as much as probability.** The same 55% is a 5.5% bet at -110 and a 10% bet at +100.

## Try it

The free Kelly Calculator at /tools/kelly-calculator does this math for any bankroll, price and win probability. Everything here is educational and paper-traded; it is not financial advice. 21+. If gambling is a problem, call 1-800-GAMBLER.
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

A simple rule: if the vig-free probability at the close is higher than the probability implied by the price you took, you beat the close. See the CLV guide for more.

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

Or let the free Odds & Vig Calculator at /tools/odds-calculator do it.

## The bottom line

Removing vig lets you compare how different books value the same bet. A book pricing the home team at -120 / +100 is saying the home team is 2.17 points more likely than a book at -110 / -110.

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
    title:       "Why Most Betting Models Lose to the Closing Line",
    category:    "Theory",
    keyword:     "machine learning",
    description: "Imagine a sports betting model trained on 5 years of data. It sees patterns humans miss\u2014park effects, weather correlations, pitcher fatigue cycles. It outputs p",
    content:     `# Why Most Betting Models Lose to the Closing Line

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

## A Hypothetical Example: A Strikeout Model

The numbers in this section are illustrative, not Beezy results. For our real, settled numbers see the Results and Models pages. Here is how a careful strikeout model might be built and judged:

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

**Illustrative results (hypothetical):**
- Backtest accuracy: 53%
- Hold-out accuracy: 52.2%, which is below the 52.4% needed to break even at -110, so it would not be profitable on its own
- CLV: a positive average means you took better prices than the close; it is a price gain, not a profit figure

A model like this is only worth trusting if:
1. Validation against realistic future data
2. Conservative Kelly sizing
3. Focus on a niche market (strikeouts) with less competition
4. Continuous retraining (quarterly)
5. Tracking CLV, not just accuracy

## The Bottom Line

Machine learning models can sometimes find an edge, but most do not, and it requires:

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

Closing line value compares the price you took with the price at the close. It is a better early read on skill than a short win-loss record.

\`\`\`
CLV: positive when the closing price is worse for a bettor than the price you took
\`\`\`

If you bet the Dodgers at -110 and the line closes at -130:
- You took -110 when the market finished at -130
- The market moved toward your side, so you got the better price
- Positive CLV

If you bet the Giants at +130 and the line closes at +112:
- You took +130 when the market finished at +112
- The market moved toward your side, so you got the better price
- Positive CLV

If you bet the Dodgers at -130 and the line closes at -110, the market moved away from you: negative CLV.

**Hit rate alone is noisy.** Over a few hundred bets a 52% win rate can be luck. At -110 the break-even win rate is 52.38%, so 52% actually loses about 0.8% per bet. A bettor who consistently takes prices better than the close is doing something repeatable, which is why CLV is tracked alongside results. See the CLV guide and the CLV Tracker at /tools/clv-tracker.

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
  {
    slug:        "closing-line-value-clv",
    title:       "Closing Line Value: A Better Test Than ROI",
    category:    "Theory",
    keyword:     "closing line value",
    description: "A positive ROI over a few hundred bets can be pure luck -- or a soft-line artifact. Closing Line Value (CLV) is the leakage-proof test of whether you are actually beating the market.",
    content:     `# Closing Line Value: A Better Test Than ROI

## The problem with ROI

Imagine a model that shows +10% ROI over 400 backtested bets. Sounds like edge. Now ask one question: **could you have actually gotten those prices?**

Historical odds databases are full of stale quotes -- lines captured once a day, hours before they corrected. A model can look brilliant against a soft, out-of-date price and be worthless against the price that actually existed at bet time. ROI measured against stale lines is not edge; it is an artifact of the data.

Sample size does not save you either. Betting outcomes have enormous variance: at typical -110 prop pricing, a bettor with ZERO edge will show +5% ROI or better over 200 bets roughly one time in four. Most "hot streaks" you see posted online are exactly this.

## What CLV measures

**Closing Line Value** compares the price you bet to the price the market closed at:

> CLV % = (your decimal odds / closing decimal odds) - 1

If you bet a home run prop at +450 and it closes at +400, you beat the close by about 11%. If you bet +450 and it closes +500, the market moved against you.

Why does this matter more than whether the bet won? Because the closing line is the sharpest number in the market -- it has absorbed every injury report, lineup card, weather update, and every dollar of informed money. Beating the close is widely used as a test of skill: **bettors who consistently take better prices than the close tend to be profitable over time**, whatever their recent win-loss record says.

## Why CLV is leakage-proof

ROI can be inflated by accident: train a model on data that overlaps the backtest window and it will "predict" games it has already seen. CLV is much harder to fool this way, because it is measured on prices that moved after you bet. Either the line moved toward your bet, or it did not. It requires no settled outcomes, resolves within hours, and reaches statistical significance far faster than win-loss records -- a meaningful CLV signal shows up in about 100 bets, where ROI needs 1,000+.

That is why CLV matters to us. Today a Beezy system needs at least 200 settled bets, a positive ROI and good calibration before it can leave paper mode, and we are moving toward a CLV requirement as well. A system that shows positive ROI but flat-or-negative CLV may be beating a soft historical line rather than the market, so we treat it as unproven. Track it yourself in the CLV Tracker at /tools/clv-tracker.

## How to use CLV yourself

1. **Record the price at bet time.** Screenshot or log the exact odds you got.
2. **Record the close.** Capture the same market's final pre-game price at your book or a sharp reference.
3. **Compute CLV per bet and average it.** A sustained average above +1-2% with a decent sample is real evidence of edge. An average near zero means your wins are variance.
4. **Diagnose by segment.** Positive CLV on strikeout props but negative on moneylines tells you where your process actually works.

One warning: CLV against ONE soft book's close can flatter you (their close may itself be off-market). The gold standard is CLV against the sharpest available close -- Pinnacle where quoted, or a multi-book consensus.

## The takeaway

Win rate tells you what happened. CLV tells you whether it was skill. If you track one number about your own betting, make it this one.
`,
  },
  {
    slug:        "odds-boost-ev",
    title:       "Odds Boosts: The Most Reliable +EV Bet You Will Ever Make",
    category:    "Strategy",
    keyword:     "odds boost EV",
    description: "Profit boosts and promo odds are the one place a sportsbook voluntarily hands you positive expected value. Here is how to compute exactly how much a boost is worth before you tap it.",
    content:     `# Odds Boosts: The Most Reliable +EV Bet You Will Ever Make

## Why boosts are different

Every normal price a sportsbook posts carries vig -- typically 5-12% on props. To profit against those prices you need a genuine informational edge, which is hard.

An **odds boost** flips the arithmetic. The book takes a market price, improves it as a marketing expense, and caps your stake so it cannot be exploited at scale. When the boosted price exceeds the market's fair price, the bet is +EV *by construction* -- no model, no handicapping skill, no inside information required. It is the single most dependable source of positive expected value available to a recreational bettor.

The catch: not every boost clears the bar. A boost from -150 to -135 on a market whose fair price is -190 is a winning bet, but the same boost on a market whose fair price is -125 is a losing one. You have to do the math every time, or use the free Boost EV Calculator at /tools/boost-ev.

## The three-step calculation

**Step 1 -- Find the fair probability.** Take the market's normal two-sided prices (your side and the opposite side) and remove the vig. Convert both to implied probability, then divide your side by the total:

> implied(-120) = 120/220 = 54.5%
> implied(+100) = 100/200 = 50.0%
> fair prob = 54.5 / (54.5 + 50.0) = 52.2%

**Step 2 -- Convert the boosted price to decimal.** A +150 boost = 2.50 decimal.

**Step 3 -- Compute EV.**

> EV per $1 = fair prob x boosted decimal - 1 = 0.522 x 2.50 - 1 = +30.5%

A +30% EV bet is enormous -- for reference, professional bettors grind out careers on 2-4% edges. This is why sharp bettors max out every qualifying boost they see: the per-bet dollar amounts are capped, but the edge per dollar is often 10x anything obtainable at normal prices.

Our [Boost EV calculator](/tools/boost-ev) does all three steps -- enter the boosted price and the market's normal two-sided prices, and it returns the true EV plus a Kelly-sized stake.

## Rules of thumb

- **Boost the longshot side when EV is equal.** Boosting +400 to +500 adds more EV than boosting -200 to -170, because the boost percentage compounds on the larger payout.
- **Always max the stake cap** on a confirmed +EV boost. The cap IS the offer; betting less leaves free money.
- **Beware parlay boosts.** Multi-leg boosts multiply vig across legs before applying the boost; most remain -EV after the math. Run the numbers per leg.
- **Track them like real bets.** Boost EV is still an expectation, not a guarantee -- you will lose plenty of individual boosts. The edge shows up in the aggregate.

## Why books keep offering them

Boosts are customer acquisition priced as marketing. Books know the average user cannot or will not compute fair value, taps the shiny number, and then gives back the EV on -EV bets later the same session. Refuse the second half of that trade and the boost program becomes a small, steady subsidy from the book to you.

Free money is rare in betting, and boosts are the closest thing. Remember that accounts that only take boosts can be limited, stake caps apply, and none of this is financial advice. 21+. If gambling is a problem, call 1-800-GAMBLER.
`,
  },
]

export function getArticle(slug: string): Article | undefined {
  return ARTICLES.find(a => a.slug === slug)
}
