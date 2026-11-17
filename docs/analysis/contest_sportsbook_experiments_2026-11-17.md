# Contest sportsbook features: what the data supports (2026-11-17)

Status: research for the Bankroll Challenge (play money, bragging rights only). Nothing here is a claim of betting edge. Every number
feeding the contest is labelled UNVALIDATED in code: one season of college football, two weeks of college basketball, closing lines only.

Reproduce: `python3 scripts/contest_history_pull.py --league cfb ...` then
`python3 scripts/contest_sportsbook_research.py ~/contest_history/cfb_2025.jsonl` (and `cbb_sample.jsonl --label "college basketball"
--teaser-points 4 4.5 5`). The script is deterministic (fixed bootstrap seed) and prints every table quoted below.

## Data

| set | games with closing lines | span | source |
|---|---|---|---|
| college football | 943 (15 completed games had no line and are dropped) | 2025-08-23 to 2026-01-19 | ESPN scoreboard plus the ESPN core odds API (provider lines, DraftKings / ESPN BET) |
| college basketball | 700 | 2026-01-05 to 2026-01-19 (stopped at the 700 game cap) | same |

One pull bug mattered: ESPN returns a placeholder `event.date` of 1993-01-31 on most past events, so the first football run was ordered
by a meaningless date and its 60/40 split was invalid. The puller now stamps each game with the day it was queried. Everything below is
from the repaired caches.

## Findings

### 1. Volatility (sigma for cash out and alt lines)

| sport | spread error | total error |
|---|---|---|
| football | sigma 15.0, 95% 14.4 to 15.6 (mean +0.8, se 0.5) | sigma 15.5, 95% 14.8 to 16.1 (mean +0.3) |
| basketball | sigma 11.1, 95% 10.6 to 11.7 (mean -0.9, se 0.4) | sigma 17.2, 95% 16.2 to 18.3 (mean +1.3, se 0.7) |

The placeholders were 13.5 / 13.0 (football) and 11.0 / 15.0 (basketball): the football ones were about 10 percent too tight and the
basketball total about 13 percent too tight. Residuals are close to normal (football: 69.6 and 94.9 percent inside 1 and 2 sigma against
68.3 and 95.4 for a normal curve). Shipped: `contest/rules.py` `_DEFAULT_SIGMAS`.

### 2. Alt lines: does a normal model price them? (60/40 split, scored on the later 40 percent)

Football, fitted on 565 games, scored on 378: spreads match to within 2 points of probability at every shift tested (14, 10, 7, 3, 1
points either way; largest gap 1.9 points, no |z| above 0.8). Totals are worse in the test window: the model over-states the over by
1.3 to 5.5 points of probability at 8 of 10 shifts (shift -1: model 0.473, actual 0.418, z -2.1; shift +1: 0.524 against 0.474, z -2.0).
That is 20 comparisons, so two |z| near 2 is not strong evidence on its own, but 8 of the 10 total shifts lean the same way. Reading: late-season
and bowl totals closed too high (the overall mean total error is only +0.3 points). Consequence: contest-priced OVER alts are slightly
generous to the over in the late-season window. Not corrected, because the correction would be fitted on the same 378 games.

### 3. Key numbers (football)

Share of games whose final margin lands exactly on k, against the neighbours' average: 3 is 3.8x, 7 is 3.0x, 10 is 2.5x, 14 is 2.4x,
17 is 2.4x, 1 is 2.7x; 2, 4, 8 and 15 are 0.4x to 0.5x. In weights (observed share over the normal share at each game's own line,
shrunk toward 1 by eight pseudo-games), fitted on all 943 games: 3 -> 2.51, 7 -> 1.92.

Does weighting the margin model by these help out of sample? Fitted on the first 60 percent, scored on the last 40 percent over 20 alt
shifts per game (log loss, lower is better):

| sport | plain normal | weighted | improvement | 95% bootstrap |
|---|---|---|---|---|
| football | 0.6572 | 0.6560 | +0.0012 | -0.0012 to +0.0035 |
| basketball | 0.6335 | 0.6328 | +0.0008 | -0.0006 to +0.0021 |

Null result: the gain is inside the noise for both sports. Variants tried: 1 weight scheme (all margins 1 to 40, shrink 8), then the
choice to ship only the 3 and 7 weights for football and none for basketball (basketball weights run 0.8 to 1.3 across the board, which is
noise). That is 2 choices on the same data, so treat the shipped 2.5 / 1.9 as a judgment call rather than an estimate. Why ship them
anyway: they cost nothing when wrong by this much, and without them a player could buy through the 3 and the 7 at the normal model's
price (one point across the 3, from -3.5 to -2.5, is worth about 5.9 points of probability with the shipped weights against 2.7 on a plain normal curve). Basketball
ships with no key weights.

### 4. Same-game parlays

| pair (football, 940 games without a push) | independent | actual | lift | copula rho |
|---|---|---|---|---|
| favorite covers + over | 0.254 | 0.282 | 1.11x | +0.17 |
| favorite covers + under | 0.260 | 0.232 | 0.89x | -0.17 |
| underdog covers + over | 0.240 | 0.213 | 0.88x | -0.17 |
| underdog covers + under | 0.246 | 0.273 | 1.11x | +0.17 |
| moneyline favorite wins + over | 0.380 | 0.388 | 1.02x (95% 0.358 to 0.420) | about +0.05 |

The spread error and the total error correlate at +0.134 (95% +0.073 to +0.194). Basketball: +0.049 (95% -0.022 to +0.118), every lift
within 1 percent of independent, so basketball ships with rho 0.02 and moneyline x total 0. The football lifts are real but modest: a
book that priced these pairs as independent parlays would give about 11 percent too much on "favorite + over". The contest prices the
pair with a Gaussian copula at the measured rho and then takes 7 percent once.

### 5. Teasers: the placeholder table is generous to the house

Break-even leg win rate for the placeholder tables, all legs equal: 73.9 percent for the 2-team -120, 75.2 for -130, 76.4 for -140,
72.7 for the 3-team +160, 72.6 for the 4-team +260. Measured teased-leg win rates, football: favorites at 3 to 3.5 points 74 to 79
percent (n 78 to 82; the classic "through the 3 and the 7" spot), favorites at 4 to 6.5 only 63 to 65 percent, underdogs at 3 to 6.5 points 61 to 67
percent, heavy favorites 14+ 69 to 72 percent. Basketball legs win 51 to 67 percent, mostly 59 to 63. So at the placeholder prices a random teaser loses
money for the bettor in both sports, and only the favorites-through-the-3 spot comes near break-even (n about 80, interval about 64 to
87 percent: not distinguishable from it). The table has not been changed: it is the owner's rulebook decision, and the contest is play
money.

### 6. Vig in the closing lines

Football: spread 4.67 percent, total 4.70, moneyline 4.32 (n 943 / 943 / 814). Basketball: 4.70, 4.74, 4.25. The contest margin on
alt lines (5 percent on the implied probability, about -110 at a coin flip) and on cash out (5 percent off fair value) sits at this
level; the same-game parlay margin (7 percent) is higher because the correlation is itself uncertain.

## What shipped from this (all UNVALIDATED, all behind rules fields)

| piece | where | note |
|---|---|---|
| sigmas 15.0 / 15.5 and 11.1 / 17.2 | `contest/rules.py` `_DEFAULT_SIGMAS` | basketball is a two-week sample and not March |
| key weights 3 -> 2.5, 7 -> 1.9 (football only) | `_DEFAULT_KEY_WEIGHTS` | out-of-sample gain within noise |
| same-game parlay rho +/-0.17 and 0.05 (football), 0.02 and 0 (basketball) | `_DEFAULT_SGP_RHO_BP` | sign by favorite / dog and over / under |
| alt lines, margin 5 percent, reach 14 (football) / 10 (basketball) | `contest/altlines.py` | 0.5 point grid; never in a teaser or a same-game parlay |
| cash out uses the same model | `contest/cashout.py` | so an alt bought and cashed out immediately cannot beat the margin |

## What this does not show

- No forward evidence. Every number is an in-sample fit or a single 60/40 split of one season. Re-fit the basketball numbers on a full
  March sample before the tournament contest, and re-check football on the next bowl slate.
- Closing lines only. The contest board refreshes on a schedule, so a bettor sees earlier lines; closing-line value for them is not
  measured here.
- The alt-line model is the contest's own and is not a replica of any sportsbook's ladder. Real ladders also shade for key numbers and
  for total-versus-side relationships we do not model.
- Same-game parlay correlation is measured for the main spread x total and moneyline x total pairs only. Props and three-leg
  combinations are not offered.
