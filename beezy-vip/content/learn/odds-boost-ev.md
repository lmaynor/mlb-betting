# Odds Boosts: How to Tell If a Boost Is Actually +EV

## Why boosts are different

Every normal price a sportsbook posts carries vig -- typically 5-12% on props. To profit against those prices you need a genuine informational edge, which is hard.

An **odds boost** flips the arithmetic. The book takes a market price, improves it as a marketing expense, and caps your stake so it cannot be exploited at scale. When the boosted price exceeds the market's fair price, the bet is +EV *by construction* -- no model, no handicapping skill, no inside information required. It is the single most dependable source of positive expected value available to a recreational bettor.

The catch: not every boost clears the bar. A boost from -150 to -135 on a market whose fair price is -190 is a winning bet, but the same boost on a market whose fair price is -125 is a losing one. You have to do the math every time, or use the free Boost EV Calculator at /tools/boost-ev.

## The three-step calculation

**Step 1 -- Find the fair probability.** Take the market's normal two-sided prices (your side and the opposite side) and remove the vig (the full method is in [how to remove vig](/learn/how-to-remove-vig-odds)). Convert both to implied probability, then divide your side by the total:

> implied(-120) = 120/220 = 54.5%
> implied(+100) = 100/200 = 50.0%
> fair prob = 54.5 / (54.5 + 50.0) = 52.2%

**Step 2 -- Convert the boosted price to decimal.** A +150 boost = 2.50 decimal.

**Step 3 -- Compute EV.**

> EV per $1 = fair prob x boosted decimal - 1 = 0.522 x 2.50 - 1 = +30.5%

A +30% EV bet is enormous -- for reference, professional bettors grind out careers on 2-4% edges. This is why sharp bettors max out every qualifying boost they see: the per-bet dollar amounts are capped, but the edge per dollar is often 10x anything obtainable at normal prices.

Our [Boost EV calculator](/tools/boost-ev) does all three steps -- enter the boosted price and the market's normal two-sided prices, and it returns the true EV plus a [Kelly-sized](/learn/kelly-criterion-sports-betting) stake.

## Rules of thumb

- **Boost the longshot side when EV is equal.** Boosting +400 to +500 adds more EV than boosting -200 to -170, because the boost percentage compounds on the larger payout.
- **Always max the stake cap** on a confirmed +EV boost. The cap IS the offer; betting less leaves free money.
- **Beware parlay boosts.** Multi-leg boosts multiply vig across legs before applying the boost; most remain -EV after the math. Run the numbers per leg.
- **Track them like real bets.** Boost EV is still an expectation, not a guarantee -- you will lose plenty of individual boosts. The edge shows up in the aggregate.

## Why books keep offering them

Boosts are customer acquisition priced as marketing. Books know the average user cannot or will not compute fair value, taps the shiny number, and then gives back the EV on -EV bets later the same session. Refuse the second half of that trade and the boost program becomes a small, steady subsidy from the book to you.

Free money is rare in betting, and boosts are the closest thing. Remember that accounts that only take boosts can be limited, stake caps apply, and none of this is financial advice. 21+. If gambling is a problem, call 1-800-GAMBLER.
