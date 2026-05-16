# How Machine Learning Models Beat the Books

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

```
required_edge = (vig / 100) + market_accuracy_gap
```

At -110 odds, vig is 4.76%. So you need:
```
required_edge = 4.76% + (your_accuracy - market_accuracy)
```

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

```
clv = if (your_prob > closing_prob) then "positive" else "negative"
```

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
