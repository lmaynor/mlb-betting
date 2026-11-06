# Kelly Criterion for Sports Betting

## What it is

The **Kelly Criterion** is a formula for what fraction of your bankroll to bet when you believe you have an edge. It maximizes long-run growth without risking ruin, but only if your win probability is right.

```
f = (p x d - 1) / (d - 1)
```

Where:
- **f** = the fraction of your bankroll to bet
- **p** = your estimated probability that the bet wins
- **d** = the decimal odds (-110 is 1.909, +150 is 2.50, +100 is 2.00)

If p x d is 1 or less, f is zero or negative and the answer is simply: do not bet.

Everything depends on p, your own probability. If converting a price into a probability is new, start with [implied probability](/learn/implied-probability-calculator).

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

Full Kelly is the optimum only when your probability is exactly right. If you overestimate it, you overbet, and bankroll swings get large fast. Most bettors use a fraction, usually half or a quarter. At quarter Kelly the 55% / -110 example is 1.4%, or about $14 on $1,000. The [Odds Terminal](/ev) on this site sizes at quarter Kelly with a 5% cap per bet.

## Pitfalls

- **Your probability is the whole game.** The formula is only as good as p. A model that is overconfident at high edges will oversize exactly the bets it is wrong about.
- **Bets are not independent.** The same prop at three books, or several lines on one player, is one idea. Do not Kelly-size each separately.
- **Stake limits.** Books limit winning accounts, so the Kelly number is sometimes more than you can place.
- **Price matters as much as probability.** The same 55% is a 5.5% bet at -110 and a 10% bet at +100.

## Try it

The free [Kelly Calculator](/tools/kelly-calculator) does this math for any bankroll, price and win probability. Everything here is educational and paper-traded; it is not financial advice. 21+. If gambling is a problem, call 1-800-GAMBLER.
