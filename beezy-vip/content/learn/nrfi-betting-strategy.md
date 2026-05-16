# NRFI Betting Strategy: A Data-Driven Approach

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

```
nrfi_score = (pitcher_velocity_factor × 0.40) +
             (opponent_k_factor × 0.35) +
             (weather_factor × 0.15) +
             (home_field_factor × 0.10)
```

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
