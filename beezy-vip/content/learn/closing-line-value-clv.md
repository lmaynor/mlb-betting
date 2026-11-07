# Closing Line Value: A Better Test Than ROI

## The problem with ROI

Imagine a model that shows +10% ROI over 400 backtested bets. Sounds like edge. Now ask one question: **could you have actually gotten those prices?**

Historical odds databases are full of stale quotes -- lines captured once a day, hours before they corrected. A model can look brilliant against a soft, out-of-date price and be worthless against the price that actually existed at bet time. ROI measured against stale lines is not edge; it is an artifact of the data.

Sample size does not save you either. Betting outcomes have enormous variance: at typical -110 prop pricing, a bettor with ZERO edge will show +5% ROI or better over 200 bets roughly one time in four. Most "hot streaks" you see posted online are exactly this.

## What CLV measures

**Closing Line Value** compares the price you bet to the price the market closed at:

> CLV % = (your decimal odds / closing decimal odds) - 1

If you bet a home run prop at +450 and it closes at +400, you beat the close by about 11%. If you bet +450 and it closes +500, the market moved against you.

Why does this matter more than whether the bet won? Because the closing line is the sharpest number in the market -- it has absorbed every injury report, lineup card, weather update, and every dollar of informed money. Beating the close is widely used as a test of skill: **bettors who consistently take better prices than the close tend to be profitable over time**, whatever their recent win-loss record says. The moves themselves (sharp money, public money, late information) are covered in the [line movement guide](/learn/line-movement-sports-betting).

## Why CLV is leakage-proof

ROI can be inflated by accident: train a model on data that overlaps the backtest window and it will "predict" games it has already seen. CLV is much harder to fool this way, because it is measured on prices that moved after you bet. Either the line moved toward your bet, or it did not. It requires no settled outcomes, resolves within hours, and reaches statistical significance far faster than win-loss records -- a meaningful CLV signal shows up in about 100 bets, where ROI needs 1,000+.

That is why CLV matters to us. Today a Beezy system needs at least 200 settled bets, a positive ROI and good calibration before it can leave paper mode (the [Models](/models) page shows each system's sample and status), and we are moving toward a CLV requirement as well. A system that shows positive ROI but flat-or-negative CLV may be beating a soft historical line rather than the market, so we treat it as unproven. Track it yourself in the [CLV Tracker](/tools/clv-tracker).

## How to use CLV yourself

1. **Record the price at bet time.** Screenshot or log the exact odds you got.
2. **Record the close.** Capture the same market's final pre-game price at your book or a sharp reference.
3. **Compute CLV per bet and average it.** A sustained average above +1-2% with a decent sample is real evidence of edge. An average near zero means your wins are variance.
4. **Diagnose by segment.** Positive CLV on strikeout props but negative on moneylines tells you where your process actually works.

One warning: CLV against ONE soft book's close can flatter you (their close may itself be off-market). The gold standard is CLV against the sharpest available close -- Pinnacle where quoted, or a multi-book consensus.

## The takeaway

Win rate tells you what happened. CLV tells you whether it was skill. If you track one number about your own betting, make it this one.
