# NFL drive-level Markov-chain Monte Carlo simulator -- walk-forward evidence (2026-11-05)

Branch `feat/nfl-drive-simulator`. Code: `nfl/sim/` (research tooling), tests `tests/test_nfl_sim.py`. **Not wired into serving; nothing deployed,
no job provisioned, nothing written to GCS or the bets table.** Every number below is ACCURACY evidence (log score / MAE / correlations on
realised outcomes); no price enters any validation, so none of it is edge. Every output stays `validated: false`.

## Bottom line

**Leave it off.** The simulator is a coherent joint-game engine and it reproduces the dependence structure of real games very well (QB passing
yards vs his top receiver's yards: simulated +0.46, empirical +0.46; the pass-rate response to the score state is reproduced to the point),
but as a PROP projection engine it is **not better than the serving EWM engine** on the stats where volume matters, and it is **worse** on
pass attempts / completions / rush attempts / receptions. Its only held-out gains are (i) rec_yds log score, which the hierarchical engine gets
identically (a distribution-shape effect, no mean information: MAE and Brier do not move), and (ii) `anytime_td`, which clears the repo's accuracy
bar against the serving engine and the hierarchical engine (details below) but has not been checked at real book lines. As a game-level engine it is
no better than the market it was compared with (its game total / margin MAE is worse than the closing line's) and it **under-produces the NFL key
margins** (|margin| = 3: 7.5% simulated vs 11.7% realised). The paper's headline premise is real but is not "12-16%": it is a second-half,
clock-dependent response of +30% to +140% that is about zero in the first half; the overall average (+13%) is what lands in the 12-16% range.

Headline numbers (held-out 2024, 2025, 2026 wks 1-8 = 3 seasons; paired per player-game on the SAME rows as the serving engine, ratings-only
simulator, 2,000 draws per game; d log score > 0 favours the simulator; standard errors in parentheses):

| stat | simulator minus serving, log score (se), z | MAE serving -> simulator | verdict (repo rule) |
|---|---|---|---|
| pass attempts | -0.345 (0.054), z -6.4 | 7.77 -> 8.15 | WORSE |
| pass completions | -0.322 (0.055), z -5.9 | 5.35 -> 5.53 | WORSE |
| pass yards | -0.032 (0.042), z -0.7 | 65.3 -> 66.5 | no clear difference |
| pass TDs | -0.044 (0.027), z -1.6 | 0.945 -> 0.918 | no clear difference |
| rush attempts | -0.075 (0.025), z -3.0 | 4.34 -> 4.41 | WORSE |
| rush yards | +0.048 (0.016), z +3.0 | 27.1 -> 28.2 | no clear gain (3/3 seasons better by log score; Brier and MAE worse) |
| receptions | -0.041 (0.008), z -5.0 | 1.78 -> 1.78 | WORSE |
| rec yards | +0.173 (0.009), z +19.0 | 25.24 -> 25.21 | no clear gain (Brier z +1.6, just under 1.645; hierarchical engine: +0.169, so sim minus hierarchical = +0.004, z +0.4) |
| anytime TD | +0.018 (0.0045), z +3.9; Brier z +2.3; 3/3 held-out seasons and 5/5 replayed | 0.491 -> 0.500 | ADOPT by the accuracy rule; NOT adopted (no real-line check, mean not better) |

## What was built

| module | job |
|---|---|
| `nfl/sim/pbp_prep.py` | nflverse pbp -> scrimmage-play / drive / field-goal / 4th-down / clock-gap tables; clock x score state buckets |
| `nfl/sim/script_state.py` | MEASURES pass rate over expected vs score differential and time remaining (CLI: `python -m nfl.sim.script_state`) |
| `nfl/sim/fit.py` | walk-forward MLE / empirical fit of every engine parameter from pbp of seasons < the evaluated season |
| `nfl/sim/ratings.py` | as-of team rating deltas (EWM, empirical-Bayes shrinkage) and as-of player usage shares |
| `nfl/sim/engine.py` | vectorised simulator: N coherent joint games per call, regulation only |
| `nfl/sim/derive.py` | team markets, prop probabilities and same-game joint probabilities read off the SAME draws |
| `nfl/sim/evaluate.py` | walk-forward replay vs the serving and hierarchical engines, diagnostics, leakage test (`run`, `report`, `league`, `leakage`) |

`nfl.props.engine_eval.replay_hierarchical` now also carries `player_id` / `game_id` / `team` on its rows (additive) so the third engine joins on the
same player-games.

### How the engine works

Per simulated game the state is possession, yardline_100, down, yards to go, game clock and both scores. Each loop iteration advances every unfinished
game by one action: drive start; 4th-down choice (softmax over go / field goal / punt); a scrimmage play whose pass probability comes from the fitted
call model (down, distance, field position AND 9 clock states x 9 score states); on a pass: sack -> target (QB share, receiver share) -> interception /
completion -> yards; on a run: rusher by carry share -> yards; clock from empirical snap-to-snap pools by (outcome, hurry class); drive ends (TD +
extra point / two-point, FG, punt, turnover with defensive-TD chance, downs, missed FG) with next field position from empirical pools; halftime flips
possession. Because each pass play updates the QB's attempts / yards / TDs AND the receiver's targets / yards / TDs in the same step, every player
stat of the game is a function of the same plays, so QB-receiver, pass-rush and total-margin dependence is produced by the mechanics, not imposed.

What is fitted from pbp of seasons strictly before the evaluated season S (2018..S-1; L2-ridged logistic / softmax by L-BFGS, or empirical):
the play-call logit (94 features incl. the 81 clock x score cells); the 4th-down softmax; field-goal make probability by kick distance; sack /
interception / completion frequencies per (field zone x distance bucket x script class) cell with hierarchical shrinkage; completion-yard and rush-yard
distributions per cell -- as **right-censored Kaplan-Meier pools**: a touchdown play's yards equal the distance to the goal line (it was capped), so a
plain resampling pool understated pass TDs by ~25% until the censoring was modelled; snap-to-snap and end-of-drive clock pools; next-drive field
position pools; extra-point / two-point rates; pick-six / fumble-return rates. Team rating deltas (sack, interception, completion on the logit scale;
yards per completion, per carry, seconds per play as ratios; pass-rate-over-expected as a Newton step) use EWM states (half-life 8 team games, 0.7
off-season carry) shrunk toward the AS-OF league rate with pseudo-counts estimated by method of moments from the between-team variance of
team-season rates (training seasons only: e.g. sack 217, completion 293, yards per completion 225, pace 151 plays). League level is tracked as-of:
each week a four-step fixed-point calibration makes an average-team matchup reproduce the points and plays per team-game of the league as-of that week
(results already played), so a league that scores more than the training seasons is followed from past games only.

**Constants that are design choices, not fitted** (chosen a priori or while debugging on development output -- see "Information and development
discipline"): team half-life 8 games, off-season carry 0.7, player half-life 6 games, QB half-life 3 games, QB share cubed with a 0.48-of-leader floor,
role-holder thresholds (4 targets / 8 carries per appearance), 10-game usage window, 6-game active window, usage prior weight 0.5 x 0.5 newcomer scale,
catch-rate / yards shrinkage (25 targets, 20 receptions), the week calibration's iteration count and clips, kernel-smoothing bandwidth (Silverman,
floors 1.0 yard / 0.25 count), FG range 55 yards, the 0.02 punt suppression inside the opponent 20, ridge strengths, minimum cell sizes (150) and
shrinkage pseudo-count (60). None was tuned on the held-out player-level log score.

## 1. The score-state response, MEASURED (nflverse pbp 2018-2025, regular season, regulation, 262k scrimmage plays)

Expected pass rate = a logistic model of P(pass) on down, distance and field position ONLY; PROE = observed minus expected per (clock x score) cell;
contrasts compare a trailing state with the matching leading state, game-clustered bootstrap (500 resamples of whole games).

### Pass rate over expected (PROE) by clock state x score state (offence's lead)

| clock \ score | <=-17 | -16..-9 | -8..-4 | -3..-1 | tied | +1..+3 | +4..+8 | +9..+16 | >=+17 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | -0.8 | -3.4 | -2.7 | -3.0 | -3.4 | -4.4 | -2.8 | -1.4 | -13.8 |
| Q2 >5m | +1.0 | -1.9 | -2.7 | -2.0 | -2.1 | -4.1 | -1.9 | -4.7 | -3.6 |
| Q2 5-2m | +5.6 | +4.2 | +0.0 | -0.1 | +0.3 | -2.6 | -3.5 | -3.6 | -1.6 |
| Q2 2-min | +26.2 | +23.6 | +19.9 | +19.1 | +18.8 | +17.7 | +18.2 | +17.5 | +15.4 |
| Q3 | +5.4 | +0.5 | -2.0 | -3.6 | -3.6 | -4.4 | -4.4 | -5.1 | -9.0 |
| Q4 >10m | +16.1 | +8.4 | +0.1 | -1.1 | -2.7 | -6.4 | -5.3 | -9.1 | -17.7 |
| Q4 10-5m | +18.3 | +18.5 | +4.7 | -0.2 | -2.6 | -7.2 | -10.0 | -20.1 | -31.4 |
| Q4 5-2m | +20.1 | +29.9 | +14.8 | +10.1 | -2.8 | -21.6 | -27.2 | -39.9 | -42.9 |
| Q4 last 2m | +12.7 | +30.8 | +26.2 | +20.7 | +9.2 | -44.5 | -48.8 | -55.7 | -53.0 |

(percentage points of pass rate above what down / distance / field position alone predict)

### Relative pass-rate response, trailing vs leading (game-clustered bootstrap 95% CI)

| contrast | n trail / lead | pass % trail / lead | raw relative | CI | vs-expected relative | CI |
|---|---|---|---:|---|---:|---|
| 2nd half, down 9+ vs up 9+ | 39,994 / 25,805 | 70.6 / 41.6 | +69.6% | [+66.0, +73.6] | +69.2% | [+65.6, +73.1] |
| 2nd half, down 1-8 vs up 1-8 | 31,592 / 26,114 | 62.9 / 48.6 | +29.5% | [+27.2, +31.8] | +29.6% | [+27.5, +31.9] |
| Q4, down 1-8 vs up 1-8 | 17,523 / 12,809 | 68.9 / 43.5 | +58.4% | [+54.5, +62.6] | +59.2% | [+55.4, +62.9] |
| Q4 last 10 min, down 1-8 vs up 1-8 | 12,893 / 8,468 | 72.9 / 39.1 | +86.3% | [+80.1, +92.2] | +88.7% | [+82.9, +94.1] |
| Q4, down 9+ vs up 9+ | 23,197 / 13,446 | 77.6 / 32.8 | +136.7% | [+129.8, +144.4] | +138.2% | [+131.1, +145.8] |
| 1st half, down 1-8 vs up 1-8 | 37,524 / 27,170 | 58.9 / 58.3 | +1.0% | [-0.4, +2.5] | +0.2% | [-1.1, +1.6] |
| 1st half, down 9+ vs up 9+ | 15,781 / 9,325 | 64.7 / 60.5 | +7.0% | [+4.5, +9.7] | +5.2% | [+3.0, +7.8] |
| all plays, down 1-8 vs up 1-8 | 69,116 / 53,284 | 60.7 / 53.5 | +13.4% | [+12.1, +14.8] | +13.0% | [+11.7, +14.3] |

### Efficiency by script class

| script | plays | pass % | cmp % | yds/att | sack % | int % | ypc |
|---|---:|---:|---:|---:|---:|---:|---:|
| normal | 196,401 | 57.8 | 65.1 | 7.22 | 6.5 | 2.17 | 4.53 |
| chasing (down 9+, 2nd half) | 39,994 | 70.6 | 63.7 | 6.84 | 7.5 | 2.93 | 4.71 |
| milking (up 9+, 2nd half) | 25,805 | 41.6 | 65.6 | 7.40 | 6.4 | 1.87 | 4.38 |

Team-game view (second half, mean lead <= -4 vs >= +4): pass rate 68.2% vs 44.7% (+52.5%); second-half attempts per team-game 22.1 vs 13.0 (1,802 / 1,387 team-games).

Reading it:

* The response is **almost entirely a second-half, clock-dependent effect**. First half, down 1-8 vs up 1-8: +1.0% (CI -0.4 to +2.5), i.e. none; down 9+ vs
  up 9+: +7.0%. Second half: +29.5% / +69.6%. Q4: +58.4% / +136.7%. Last 10 minutes, one score: +86.3%. A trailing team in the last five minutes passes
  ~30 points above what its down / distance / field position imply; a leading team runs ~40 points above.
* The paper's "12-16%" is the level you get by averaging over all plays (down 1-8 vs up 1-8, all plays: **+13.4%**, CI +12.1 to +14.8). It is not a
  constant: modelling it as a flat pass-rate multiplier would be wrong in both halves. The simulator therefore uses the fitted 81-cell state surface,
  not a coefficient.
* The two-minute drill adds ~+19 points of pass rate even when the game is tied (a clock effect, not a score effect).
* Efficiency also moves with the script: a chasing team completes fewer (63.7% vs 65.1%), gains fewer yards per attempt (6.84 vs 7.22), is sacked more
  (7.5% vs 6.5%) and throws more interceptions (2.93% vs 2.17%); a leading team runs less and gains slightly fewer yards per carry. The cell pools carry a
  script class for exactly this reason.
* Descriptive, not causal: teams that trail are on average the worse teams and the contrast is not adjusted for that.

The simulator reproduces the response it was fitted on, out of sample (neutral league-average teams, fitted on seasons before each test season; share of
plays that are passes):

| per team-game | 2022 real / sim | 2023 real / sim | 2024 real / sim | 2025 real / sim |
|---|---|---|---|---|
| plays | 61.55 / 61.83 | 61.91 / 61.60 | 60.96 / 62.02 | 59.97 / 61.04 |
| pass attempts | 32.92 / 33.35 | 33.39 / 32.91 | 32.47 / 33.09 | 31.72 / 32.42 |
| completions | 21.22 / 21.69 | 21.62 / 21.36 | 21.26 / 21.48 | 20.49 / 21.07 |
| pass yds | 232.23 / 243.80 | 235.31 / 239.83 | 232.03 / 239.17 | 223.31 / 234.67 |
| pass TD | 1.38 / 1.53 | 1.38 / 1.49 | 1.48 / 1.46 | 1.48 / 1.42 |
| interceptions | 0.76 / 0.77 | 0.79 / 0.75 | 0.71 / 0.76 | 0.69 / 0.74 |
| sacks | 2.37 / 2.30 | 2.58 / 2.27 | 2.40 / 2.36 | 2.35 / 2.31 |
| rush attempts | 26.26 / 26.19 | 25.94 / 26.43 | 26.09 / 26.58 | 25.90 / 26.31 |
| rush yds | 121.38 / 119.31 | 113.12 / 121.06 | 119.61 / 121.04 | 117.05 / 119.80 |
| rush TD | 0.89 / 0.81 | 0.86 / 0.83 | 0.93 / 0.82 | 0.93 / 0.83 |
| drives | 10.81 / 10.64 | 11.01 / 10.60 | 10.65 / 10.69 | 10.42 / 10.50 |
| points (regulation) | 21.75 / 21.31 | 21.65 / 21.23 | 22.77 / 20.96 | 22.85 / 20.73 |
| pass share, normal | 0.57 / 0.57 | 0.58 / 0.57 | 0.57 / 0.57 | 0.57 / 0.57 |
| pass share, chasing (down 9+, 2nd half) | 0.70 / 0.71 | 0.69 / 0.71 | 0.71 / 0.71 | 0.69 / 0.71 |
| pass share, milking (up 9+, 2nd half) | 0.41 / 0.40 | 0.42 / 0.40 | 0.40 / 0.40 | 0.40 / 0.39 |

(`pass share, *` rows: realised vs simulated pass share by script class, 2022-2025. The other rows are the neutral-league structural check: pass yards
run 2-5% high, rush TDs ~10% low, and points per team-game track the league only to the extent it matches the training seasons -- 2024 and 2025 scored
~9% more than 2018-2023 fitted, which is why the as-of league calibration exists.)

## 2. Protocol

* Rows: the 10 NFL prop stats of `engine_eval.HIER_STATS` on the serving engine's own eligible player-games (rotation-usage threshold), replayed by
  `engine_eval.replay_hierarchical` for the serving EWM engine and the hierarchical engine (41,335 rows in 2022-2026 after the join; the simulator
  scores 95-97% of the baselines' rows -- a row is skipped when the player is not in the simulator's as-of active set).
* Walk-forward: for evaluated season S the engine is fitted on 2018..S-1 only; ratings / usage / active sets for a game use only games strictly before
  (season, week). 2022-2025 full seasons plus 2026 weeks 1-8 (121 games). Held-out verdict seasons = 2024, 2025, 2026 (the hierarchical priors were
  tuned on 2021-23, so 2022-23 flatter the hierarchical engine, not the simulator).
* Metrics: per-stat paired log score of the 1-unit probability mass of the realised value (same floor 1e-4 for every engine; the simulator's mass is the
  Silverman-kernel-smoothed histogram of its draws, reflected at zero), Brier of P(over proxy line) with the proxy line = L5 mean rounded to x.5 (NOT a
  book line), MAE of the mean, per-season consistency, standard errors of paired differences. Adoption bar = the repo's: pooled z >= 2.81 (Bonferroni
  over 10 stats), Brier z >= 1.645, better in every held-out season (3).
* **Active-set honesty.** A game's skill players are those who appeared for the team in its last 6 games, are not on their latest team elsewhere, and
  are not reported Out / Doubtful that week (injury report). The first version took everyone with a `player_stats` row in the game itself; that is a
  LOOKAHEAD (a row exists only if a stat was recorded). Kept as a sensitivity (`--active oracle`, 2024): it flatters the simulator badly -- MAE of pass
  yards 67.6 -> 58.2 (as-of: 65.2 -> 68.0), pass attempts 8.15 -> 6.85 (as-of: 7.77 -> 8.45) -- so no headline uses it.
* Usage is availability-adjusted per team game: a healthy player who recorded nothing counts as a zero, an injured-Out week does not count, and a
  role-holder (>= 4 targets or >= 8 carries per appearance) is credited at his per-appearance usage so a returning starter is not erased by the
  games he missed.

### Information and development discipline (read before trusting the verdict)

* The closing spread / total are NOT used by the headline simulator (ratings only). A second variant anchors each offence so simulated points match the
  closing-line implied team totals (`--anchor lines`; results below); the serving engine's env calibration also uses those lines for rec_yds / anytime TD.
* **The held-out seasons were not untouched during development.** The simulator's first full run showed (a) team totals far below the market because the
  league had drifted from the training seasons (-> the as-of league calibration), and (b) QB attempts split across backups because per-appearance usage
  had a prior and no recency (-> availability-adjusted usage, QB exclusivity). Both were found from 2024 weeks 1-9 / 2026 output and from game-level
  totals of 2022-2026. The fixes are structural, were not tuned on log score, and the 2022-23 numbers (not inspected player-level before the final run)
  agree with 2024-26 in sign for every stat, but the 2024-26 verdict is not a clean confirmation. The final configuration was run once per season.
* Nothing tuned or selected on the replayed seasons' player-stat distributions; the hyperparameters in the list above were fixed before the final run.

## 3. Results: ratings-only simulator (headline)

Rows scored by all three engines: 41,335 (2022-2026); held-out seasons for the verdict: [2024, 2025, 2026].

Row coverage (a baseline row is skipped when the player is not in the simulator's as-of active set):

| stat | baseline rows | also scored by the simulator | coverage |
|---|---:|---:|---:|
| anytime_td | 9,853 | 9,578 | 97.2% |
| interceptions | 2,352 | 2,241 | 95.3% |
| pass_attempts | 2,352 | 2,241 | 95.3% |
| pass_completions | 2,352 | 2,241 | 95.3% |
| pass_tds | 2,352 | 2,241 | 95.3% |
| pass_yds | 2,352 | 2,241 | 95.3% |
| rec_yds | 7,543 | 7,336 | 97.3% |
| receptions | 7,543 | 7,336 | 97.3% |
| rush_attempts | 3,022 | 2,940 | 97.3% |
| rush_yds | 3,022 | 2,940 | 97.3% |

#### Simulator minus SERVING EWM engine (held out; + log score is better)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.3450 (0.0539) | -6.4 | +0.0250 (0.0073) | -3.4 | 7.771 -> 8.145 (+0.375, 0.197) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.3223 (0.0547) | -5.9 | +0.0199 (0.0072) | -2.8 | 5.346 -> 5.530 (+0.184, 0.127) | 0/3 | WORSE |
| pass_yds | 1,252 | -0.0315 (0.0423) | -0.7 | +0.0085 (0.0067) | -1.3 | 65.253 -> 66.485 (+1.233, 1.370) | 1/3 | NO CLEAR GAIN |
| pass_tds | 1,252 | -0.0435 (0.0270) | -1.6 | -0.0082 (0.0052) | +1.6 | 0.945 -> 0.918 (-0.027, 0.013) | 1/3 | NO CLEAR GAIN |
| interceptions | 1,252 | +0.0052 (0.0225) | +0.2 | -0.0085 (0.0049) | +1.7 | 0.683 -> 0.656 (-0.027, 0.010) | 2/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | -0.0751 (0.0249) | -3.0 | +0.0068 (0.0053) | -1.3 | 4.342 -> 4.405 (+0.063, 0.062) | 0/3 | WORSE |
| rush_yds | 1,555 | +0.0478 (0.0157) | +3.0 | +0.0072 (0.0040) | -1.8 | 27.086 -> 28.188 (+1.102, 0.351) | 3/3 | NO CLEAR GAIN |
| receptions | 3,948 | -0.0409 (0.0081) | -5.0 | +0.0047 (0.0020) | -2.4 | 1.782 -> 1.777 (-0.005, 0.011) | 0/3 | WORSE |
| rec_yds | 3,948 | +0.1731 (0.0091) | +19.0 | -0.0033 (0.0020) | +1.6 | 25.242 -> 25.210 (-0.032, 0.143) | 3/3 | NO CLEAR GAIN |
| anytime_td | 5,181 | +0.0177 (0.0045) | +3.9 | -0.0037 (0.0016) | +2.3 | 0.491 -> 0.500 (+0.009, 0.003) | 3/3 | ADOPT |

#### Simulator minus HIERARCHICAL engine (held out)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.2541 (0.0580) | -4.4 | +0.0250 (0.0073) | -3.4 | 7.760 -> 8.145 (+0.386, 0.199) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.2439 (0.0583) | -4.2 | +0.0153 (0.0070) | -2.2 | 5.355 -> 5.530 (+0.174, 0.128) | 0/3 | WORSE |
| pass_yds | 1,252 | +0.0086 (0.0422) | +0.2 | +0.0050 (0.0067) | -0.7 | 65.483 -> 66.485 (+1.002, 1.373) | 2/3 | NO CLEAR GAIN |
| pass_tds | 1,252 | -0.0714 (0.0271) | -2.6 | -0.0017 (0.0049) | +0.4 | 0.939 -> 0.918 (-0.021, 0.012) | 0/3 | NO CLEAR GAIN |
| interceptions | 1,252 | -0.0083 (0.0238) | -0.3 | -0.0007 (0.0044) | +0.1 | 0.674 -> 0.656 (-0.018, 0.009) | 1/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | -0.1151 (0.0234) | -4.9 | +0.0100 (0.0052) | -1.9 | 4.338 -> 4.405 (+0.067, 0.065) | 0/3 | WORSE |
| rush_yds | 1,555 | -0.0245 (0.0159) | -1.5 | +0.0117 (0.0044) | -2.6 | 26.923 -> 28.188 (+1.265, 0.354) | 0/3 | NO CLEAR GAIN |
| receptions | 3,948 | -0.0378 (0.0081) | -4.7 | +0.0030 (0.0020) | -1.5 | 1.789 -> 1.777 (-0.011, 0.011) | 0/3 | WORSE |
| rec_yds | 3,948 | +0.0039 (0.0097) | +0.4 | -0.0015 (0.0016) | +0.9 | 25.476 -> 25.210 (-0.266, 0.136) | 1/3 | NO CLEAR GAIN |
| anytime_td | 5,181 | +0.0183 (0.0044) | +4.1 | -0.0042 (0.0016) | +2.7 | 0.486 -> 0.500 (+0.014, 0.002) | 3/3 | ADOPT |

#### Hierarchical minus serving (same rows, for scale)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.0909 (0.0148) | -6.1 | +0.0001 (0.0020) | -0.0 | 7.771 -> 7.760 (-0.011, 0.039) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.0784 (0.0141) | -5.5 | +0.0046 (0.0021) | -2.2 | 5.346 -> 5.355 (+0.009, 0.028) | 0/3 | WORSE |
| pass_yds | 1,252 | -0.0401 (0.0118) | -3.4 | +0.0035 (0.0024) | -1.5 | 65.253 -> 65.483 (+0.230, 0.364) | 0/3 | WORSE |
| pass_tds | 1,252 | +0.0279 (0.0061) | +4.5 | -0.0064 (0.0020) | +3.3 | 0.945 -> 0.939 (-0.006, 0.006) | 3/3 | ADOPT |
| interceptions | 1,252 | +0.0135 (0.0107) | +1.3 | -0.0078 (0.0021) | +3.7 | 0.683 -> 0.674 (-0.009, 0.004) | 2/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | +0.0400 (0.0078) | +5.1 | -0.0032 (0.0019) | +1.7 | 4.342 -> 4.338 (-0.004, 0.025) | 3/3 | ADOPT |
| rush_yds | 1,555 | +0.0722 (0.0139) | +5.2 | -0.0045 (0.0022) | +2.0 | 27.086 -> 26.923 (-0.163, 0.138) | 3/3 | ADOPT |
| receptions | 3,948 | -0.0031 (0.0024) | -1.3 | +0.0017 (0.0008) | -2.0 | 1.782 -> 1.789 (+0.007, 0.005) | 0/3 | NO CLEAR GAIN |
| rec_yds | 3,948 | +0.1692 (0.0115) | +14.7 | -0.0018 (0.0015) | +1.2 | 25.242 -> 25.476 (+0.234, 0.076) | 3/3 | NO CLEAR GAIN |
| anytime_td | 5,181 | -0.0006 (0.0008) | -0.8 | +0.0005 (0.0003) | -1.5 | 0.491 -> 0.486 (-0.005, 0.001) | 1/3 | NO CLEAR GAIN |

#### Simulator minus serving, ALL replayed seasons 2022-2026

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 2,241 | -0.3122 (0.0395) | -7.9 | +0.0201 (0.0053) | -3.8 | 7.564 -> 7.933 (+0.369, 0.140) | 0/5 | WORSE |
| pass_completions | 2,241 | -0.3037 (0.0403) | -7.5 | +0.0163 (0.0052) | -3.1 | 5.258 -> 5.432 (+0.174, 0.089) | 0/5 | WORSE |
| pass_yds | 2,241 | -0.0284 (0.0309) | -0.9 | +0.0035 (0.0048) | -0.7 | 64.413 -> 65.242 (+0.829, 0.981) | 2/5 | NO CLEAR GAIN |
| pass_tds | 2,241 | -0.0417 (0.0183) | -2.3 | -0.0058 (0.0039) | +1.5 | 0.908 -> 0.892 (-0.016, 0.009) | 1/5 | NO CLEAR GAIN |
| interceptions | 2,241 | +0.0028 (0.0163) | +0.2 | -0.0085 (0.0037) | +2.3 | 0.701 -> 0.676 (-0.025, 0.007) | 2/5 | NO CLEAR GAIN |
| rush_attempts | 2,940 | -0.1256 (0.0204) | -6.2 | +0.0133 (0.0040) | -3.4 | 4.396 -> 4.474 (+0.078, 0.045) | 0/5 | WORSE |
| rush_yds | 2,940 | +0.0417 (0.0119) | +3.5 | +0.0068 (0.0030) | -2.2 | 26.865 -> 27.938 (+1.073, 0.252) | 5/5 | NO CLEAR GAIN |
| receptions | 7,336 | -0.0517 (0.0063) | -8.2 | +0.0063 (0.0015) | -4.2 | 1.795 -> 1.798 (+0.003, 0.008) | 0/5 | WORSE |
| rec_yds | 7,336 | +0.1630 (0.0069) | +23.7 | -0.0018 (0.0015) | +1.1 | 25.355 -> 25.458 (+0.103, 0.103) | 5/5 | NO CLEAR GAIN |
| anytime_td | 9,578 | +0.0204 (0.0033) | +6.2 | -0.0051 (0.0012) | +4.3 | 0.481 -> 0.489 (+0.009, 0.002) | 5/5 | ADOPT |

#### Per-season log-score difference, simulator minus serving

| stat | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|
| pass_attempts | -0.2875 | -0.2552 | -0.4340 | -0.2706 | -0.3091 |
| pass_completions | -0.2972 | -0.2642 | -0.4010 | -0.2126 | -0.3886 |
| pass_yds | -0.0694 | +0.0172 | -0.0558 | +0.0036 | -0.0546 |
| pass_tds | -0.0120 | -0.0652 | -0.0987 | -0.0123 | +0.0123 |
| interceptions | -0.0001 | -0.0005 | -0.0211 | +0.0170 | +0.0386 |
| rush_attempts | -0.2309 | -0.1341 | -0.0982 | -0.0018 | -0.1853 |
| rush_yds | +0.0221 | +0.0476 | +0.0103 | +0.0879 | +0.0445 |
| receptions | -0.0790 | -0.0500 | -0.0387 | -0.0376 | -0.0538 |
| rec_yds | +0.1512 | +0.1513 | +0.1892 | +0.1676 | +0.1488 |
| anytime_td | +0.0256 | +0.0217 | +0.0157 | +0.0229 | +0.0102 |

#### MAE and mean by engine (held-out seasons)

| stat | n | MAE serving | MAE hierarchical | MAE simulator | mean y | mean serving | mean hier | mean sim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pass_attempts | 1,252 | 7.771 | 7.760 | 8.145 | 29.930 | 30.460 | 30.396 | 27.812 |
| pass_completions | 1,252 | 5.346 | 5.355 | 5.530 | 19.484 | 19.771 | 19.767 | 18.016 |
| pass_yds | 1,252 | 65.253 | 65.483 | 66.485 | 215.217 | 218.078 | 217.301 | 210.521 |
| pass_tds | 1,252 | 0.945 | 0.939 | 0.918 | 1.428 | 1.405 | 1.481 | 1.384 |
| interceptions | 1,252 | 0.683 | 0.674 | 0.656 | 0.633 | 0.643 | 0.636 | 0.612 |
| rush_attempts | 1,555 | 4.342 | 4.338 | 4.405 | 13.027 | 13.417 | 13.501 | 12.710 |
| rush_yds | 1,555 | 27.086 | 26.923 | 28.188 | 57.308 | 56.823 | 57.530 | 62.357 |
| receptions | 3,948 | 1.782 | 1.789 | 1.777 | 3.979 | 3.957 | 3.967 | 3.749 |
| rec_yds | 3,948 | 25.242 | 25.476 | 25.210 | 46.795 | 46.478 | 46.837 | 45.951 |
| anytime_td | 5,181 | 0.491 | 0.486 | 0.500 | 0.397 | 0.387 | 0.366 | 0.389 |

#### Dependence: simulated vs empirical

| pair | simulated within-game corr (mean over games) | empirical corr of standardised residuals | n |
|---|---:|---:|---:|
| QB passing yds vs his top receiver's receiving yds | +0.461 | +0.457 | 1923 |
| team pass attempts vs rush attempts | -0.314 | -0.365 | 2416 |
| game total vs margin (home - away) | -0.000 | +0.011 | 1208 |

#### Team totals and cover probabilities

| quantity | simulator | closing line (context only) |
|---|---:|---:|
| MAE of game total | 10.61 | 10.24 |
| MAE of home margin | 10.17 | 9.50 |
| Brier of P(home covers) | 0.2559 | coin flip 0.2428 |
| Brier of P(over) | 0.2614 | coin flip 0.2483 |
| mean simulated total / actual / line | 44.48 | 44.92 / 44.17 |
| mean within-game sd of simulated total / sd of actual total | 12.57 | 13.71 |
| sd of standardised total residual (1.0 = calibrated spread) | 1.07 | |
| sd of standardised margin residual | 0.96 | |

(1208 games with both closing lines.)

#### Scoreboard: |regulation margin| at the key numbers

| |margin| | simulated | realised (regulation) | ratio | realised count / se |
|---|---:|---:|---:|---|
| 0 | 4.05% | 6.13% | 0.66 | 74 / 0.69pp |
| 1 | 5.59% | 4.64% | 1.21 | 56 / 0.60pp |
| 2 | 4.54% | 5.05% | 0.90 | 61 / 0.63pp |
| 3 | 7.52% | 11.67% | 0.64 | 141 / 0.92pp |
| 4 | 6.57% | 5.38% | 1.22 | 65 / 0.65pp |
| 5 | 4.10% | 4.97% | 0.83 | 60 / 0.63pp |
| 6 | 5.28% | 5.38% | 0.98 | 65 / 0.65pp |
| 7 | 6.23% | 7.86% | 0.79 | 95 / 0.77pp |
| 8 | 3.80% | 4.47% | 0.85 | 54 / 0.59pp |
| 9 | 3.82% | 1.49% | 2.56 | 18 / 0.35pp |
| 10 | 5.54% | 4.39% | 1.26 | 53 / 0.59pp |
| 13 | 3.66% | 2.15% | 1.70 | 26 / 0.42pp |
| 14 | 3.87% | 4.47% | 0.87 | 54 / 0.59pp |
| 17 | 3.42% | 4.55% | 0.75 | 55 / 0.60pp |

Key numbers (3, 7, 6, 10, 14) together: simulated 28.4% vs realised 33.8% of regulation margins (1208 games, 1208 simulated).
Mean |margin|: simulated 10.89, realised 10.55.

## 4. Results: simulator anchored to closing-line implied team totals

The same simulator, but each offence's yards are scaled until its simulated mean points equal the closing-line implied team total (3 fixed-point
iterations per game on pre-game numbers). It is a different, line-informed estimator; nothing is tuned on outcomes.

Rows scored by all three engines: 41,335 (2022-2026); held-out seasons for the verdict: [2024, 2025, 2026].

Row coverage (a baseline row is skipped when the player is not in the simulator's as-of active set):

| stat | baseline rows | also scored by the simulator | coverage |
|---|---:|---:|---:|
| anytime_td | 9,853 | 9,578 | 97.2% |
| interceptions | 2,352 | 2,241 | 95.3% |
| pass_attempts | 2,352 | 2,241 | 95.3% |
| pass_completions | 2,352 | 2,241 | 95.3% |
| pass_tds | 2,352 | 2,241 | 95.3% |
| pass_yds | 2,352 | 2,241 | 95.3% |
| rec_yds | 7,543 | 7,336 | 97.3% |
| receptions | 7,543 | 7,336 | 97.3% |
| rush_attempts | 3,022 | 2,940 | 97.3% |
| rush_yds | 3,022 | 2,940 | 97.3% |

#### Simulator minus SERVING EWM engine (held out; + log score is better)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.3446 (0.0540) | -6.4 | +0.0254 (0.0074) | -3.4 | 7.771 -> 8.154 (+0.383, 0.198) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.3321 (0.0549) | -6.1 | +0.0242 (0.0073) | -3.3 | 5.346 -> 5.564 (+0.218, 0.128) | 0/3 | WORSE |
| pass_yds | 1,252 | -0.0164 (0.0420) | -0.4 | +0.0052 (0.0066) | -0.8 | 65.253 -> 65.638 (+0.385, 1.357) | 1/3 | NO CLEAR GAIN |
| pass_tds | 1,252 | -0.0312 (0.0267) | -1.2 | -0.0124 (0.0051) | +2.4 | 0.945 -> 0.902 (-0.043, 0.013) | 2/3 | NO CLEAR GAIN |
| interceptions | 1,252 | +0.0090 (0.0225) | +0.4 | -0.0096 (0.0049) | +2.0 | 0.683 -> 0.653 (-0.030, 0.010) | 2/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | -0.0726 (0.0248) | -2.9 | +0.0055 (0.0052) | -1.1 | 4.342 -> 4.373 (+0.031, 0.060) | 1/3 | WORSE |
| rush_yds | 1,555 | +0.0564 (0.0155) | +3.6 | +0.0039 (0.0039) | -1.0 | 27.086 -> 27.925 (+0.839, 0.355) | 3/3 | NO CLEAR GAIN |
| receptions | 3,948 | -0.0439 (0.0082) | -5.3 | +0.0059 (0.0020) | -2.9 | 1.782 -> 1.783 (+0.001, 0.011) | 0/3 | WORSE |
| rec_yds | 3,948 | +0.1736 (0.0091) | +19.1 | -0.0045 (0.0020) | +2.2 | 25.242 -> 25.094 (-0.148, 0.138) | 3/3 | ADOPT |
| anytime_td | 5,181 | +0.0228 (0.0043) | +5.3 | -0.0062 (0.0016) | +3.9 | 0.491 -> 0.494 (+0.003, 0.003) | 3/3 | ADOPT |

#### Simulator minus HIERARCHICAL engine (held out)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.2537 (0.0578) | -4.4 | +0.0253 (0.0072) | -3.5 | 7.760 -> 8.154 (+0.394, 0.197) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.2536 (0.0582) | -4.4 | +0.0196 (0.0069) | -2.8 | 5.355 -> 5.564 (+0.209, 0.128) | 0/3 | WORSE |
| pass_yds | 1,252 | +0.0237 (0.0420) | +0.6 | +0.0017 (0.0066) | -0.3 | 65.483 -> 65.638 (+0.155, 1.374) | 2/3 | NO CLEAR GAIN |
| pass_tds | 1,252 | -0.0591 (0.0268) | -2.2 | -0.0060 (0.0048) | +1.2 | 0.939 -> 0.902 (-0.037, 0.011) | 1/3 | NO CLEAR GAIN |
| interceptions | 1,252 | -0.0045 (0.0238) | -0.2 | -0.0017 (0.0043) | +0.4 | 0.674 -> 0.653 (-0.021, 0.008) | 1/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | -0.1126 (0.0231) | -4.9 | +0.0087 (0.0049) | -1.8 | 4.338 -> 4.373 (+0.035, 0.061) | 0/3 | WORSE |
| rush_yds | 1,555 | -0.0158 (0.0152) | -1.0 | +0.0084 (0.0043) | -2.0 | 26.923 -> 27.925 (+1.002, 0.340) | 0/3 | NO CLEAR GAIN |
| receptions | 3,948 | -0.0407 (0.0080) | -5.1 | +0.0043 (0.0019) | -2.2 | 1.789 -> 1.783 (-0.005, 0.011) | 0/3 | WORSE |
| rec_yds | 3,948 | +0.0043 (0.0097) | +0.4 | -0.0026 (0.0016) | +1.6 | 25.476 -> 25.094 (-0.382, 0.134) | 1/3 | NO CLEAR GAIN |
| anytime_td | 5,181 | +0.0234 (0.0043) | +5.4 | -0.0067 (0.0016) | +4.3 | 0.486 -> 0.494 (+0.008, 0.002) | 3/3 | ADOPT |

#### Hierarchical minus serving (same rows, for scale)

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 1,252 | -0.0909 (0.0148) | -6.1 | +0.0001 (0.0020) | -0.0 | 7.771 -> 7.760 (-0.011, 0.039) | 0/3 | WORSE |
| pass_completions | 1,252 | -0.0784 (0.0141) | -5.5 | +0.0046 (0.0021) | -2.2 | 5.346 -> 5.355 (+0.009, 0.028) | 0/3 | WORSE |
| pass_yds | 1,252 | -0.0401 (0.0118) | -3.4 | +0.0035 (0.0024) | -1.5 | 65.253 -> 65.483 (+0.230, 0.364) | 0/3 | WORSE |
| pass_tds | 1,252 | +0.0279 (0.0061) | +4.5 | -0.0064 (0.0020) | +3.3 | 0.945 -> 0.939 (-0.006, 0.006) | 3/3 | ADOPT |
| interceptions | 1,252 | +0.0135 (0.0107) | +1.3 | -0.0078 (0.0021) | +3.7 | 0.683 -> 0.674 (-0.009, 0.004) | 2/3 | NO CLEAR GAIN |
| rush_attempts | 1,555 | +0.0400 (0.0078) | +5.1 | -0.0032 (0.0019) | +1.7 | 4.342 -> 4.338 (-0.004, 0.025) | 3/3 | ADOPT |
| rush_yds | 1,555 | +0.0722 (0.0139) | +5.2 | -0.0045 (0.0022) | +2.0 | 27.086 -> 26.923 (-0.163, 0.138) | 3/3 | ADOPT |
| receptions | 3,948 | -0.0031 (0.0024) | -1.3 | +0.0017 (0.0008) | -2.0 | 1.782 -> 1.789 (+0.007, 0.005) | 0/3 | NO CLEAR GAIN |
| rec_yds | 3,948 | +0.1692 (0.0115) | +14.7 | -0.0018 (0.0015) | +1.2 | 25.242 -> 25.476 (+0.234, 0.076) | 3/3 | NO CLEAR GAIN |
| anytime_td | 5,181 | -0.0006 (0.0008) | -0.8 | +0.0005 (0.0003) | -1.5 | 0.491 -> 0.486 (-0.005, 0.001) | 1/3 | NO CLEAR GAIN |

#### Simulator minus serving, ALL replayed seasons 2022-2026

| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| pass_attempts | 2,241 | -0.3170 (0.0395) | -8.0 | +0.0214 (0.0054) | -4.0 | 7.564 -> 7.964 (+0.400, 0.142) | 0/5 | WORSE |
| pass_completions | 2,241 | -0.3182 (0.0405) | -7.9 | +0.0212 (0.0053) | -4.0 | 5.258 -> 5.480 (+0.222, 0.091) | 0/5 | WORSE |
| pass_yds | 2,241 | -0.0129 (0.0307) | -0.4 | -0.0005 (0.0047) | +0.1 | 64.413 -> 64.408 (-0.005, 0.972) | 2/5 | NO CLEAR GAIN |
| pass_tds | 2,241 | -0.0268 (0.0180) | -1.5 | -0.0097 (0.0038) | +2.6 | 0.908 -> 0.875 (-0.032, 0.009) | 3/5 | NO CLEAR GAIN |
| interceptions | 2,241 | +0.0055 (0.0163) | +0.3 | -0.0093 (0.0037) | +2.5 | 0.701 -> 0.673 (-0.028, 0.007) | 3/5 | NO CLEAR GAIN |
| rush_attempts | 2,940 | -0.1191 (0.0201) | -5.9 | +0.0118 (0.0039) | -3.0 | 4.396 -> 4.447 (+0.052, 0.044) | 1/5 | WORSE |
| rush_yds | 2,940 | +0.0471 (0.0117) | +4.0 | +0.0053 (0.0030) | -1.8 | 26.865 -> 27.795 (+0.930, 0.252) | 5/5 | NO CLEAR GAIN |
| receptions | 7,336 | -0.0553 (0.0065) | -8.6 | +0.0075 (0.0015) | -4.9 | 1.795 -> 1.804 (+0.009, 0.008) | 0/5 | WORSE |
| rec_yds | 7,336 | +0.1651 (0.0069) | +24.0 | -0.0032 (0.0015) | +2.1 | 25.355 -> 25.296 (-0.058, 0.100) | 5/5 | ADOPT |
| anytime_td | 9,578 | +0.0251 (0.0032) | +7.8 | -0.0072 (0.0012) | +6.2 | 0.481 -> 0.483 (+0.002, 0.002) | 5/5 | ADOPT |

#### Per-season log-score difference, simulator minus serving

| stat | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|
| pass_attempts | -0.3026 | -0.2629 | -0.4291 | -0.2611 | -0.3393 |
| pass_completions | -0.3232 | -0.2798 | -0.4113 | -0.2132 | -0.4177 |
| pass_yds | -0.0520 | +0.0321 | -0.0478 | +0.0275 | -0.0431 |
| pass_tds | +0.0041 | -0.0447 | -0.0916 | +0.0019 | +0.0319 |
| interceptions | +0.0059 | -0.0032 | -0.0162 | +0.0191 | +0.0434 |
| rush_attempts | -0.2319 | -0.1110 | -0.0979 | +0.0009 | -0.1782 |
| rush_yds | +0.0067 | +0.0666 | +0.0271 | +0.0905 | +0.0478 |
| receptions | -0.0841 | -0.0536 | -0.0433 | -0.0384 | -0.0578 |
| rec_yds | +0.1522 | +0.1583 | +0.1894 | +0.1673 | +0.1516 |
| anytime_td | +0.0290 | +0.0267 | +0.0184 | +0.0277 | +0.0216 |

#### MAE and mean by engine (held-out seasons)

| stat | n | MAE serving | MAE hierarchical | MAE simulator | mean y | mean serving | mean hier | mean sim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pass_attempts | 1,252 | 7.771 | 7.760 | 8.154 | 29.930 | 30.460 | 30.396 | 27.688 |
| pass_completions | 1,252 | 5.346 | 5.355 | 5.564 | 19.484 | 19.771 | 19.767 | 17.932 |
| pass_yds | 1,252 | 65.253 | 65.483 | 65.638 | 215.217 | 218.078 | 217.301 | 210.717 |
| pass_tds | 1,252 | 0.945 | 0.939 | 0.902 | 1.428 | 1.405 | 1.481 | 1.401 |
| interceptions | 1,252 | 0.683 | 0.674 | 0.653 | 0.633 | 0.643 | 0.636 | 0.610 |
| rush_attempts | 1,555 | 4.342 | 4.338 | 4.373 | 13.027 | 13.417 | 13.501 | 12.727 |
| rush_yds | 1,555 | 27.086 | 26.923 | 27.925 | 57.308 | 56.823 | 57.530 | 62.979 |
| receptions | 3,948 | 1.782 | 1.789 | 1.783 | 3.979 | 3.957 | 3.967 | 3.742 |
| rec_yds | 3,948 | 25.242 | 25.476 | 25.094 | 46.795 | 46.478 | 46.837 | 45.829 |
| anytime_td | 5,181 | 0.491 | 0.486 | 0.494 | 0.397 | 0.387 | 0.366 | 0.393 |

#### Dependence: simulated vs empirical

| pair | simulated within-game corr (mean over games) | empirical corr of standardised residuals | n |
|---|---:|---:|---:|
| QB passing yds vs his top receiver's receiving yds | +0.463 | +0.448 | 1923 |
| team pass attempts vs rush attempts | -0.298 | -0.368 | 2416 |
| game total vs margin (home - away) | +0.024 | +0.015 | 1208 |

#### Team totals and cover probabilities

| quantity | simulator | closing line (context only) |
|---|---:|---:|
| MAE of game total | 10.24 | 10.24 |
| MAE of home margin | 9.51 | 9.50 |
| Brier of P(home covers) | 0.2423 | coin flip 0.2428 |
| Brier of P(over) | 0.2486 | coin flip 0.2483 |
| mean simulated total / actual / line | 44.18 | 44.92 / 44.17 |
| mean within-game sd of simulated total / sd of actual total | 12.49 | 13.71 |
| sd of standardised total residual (1.0 = calibrated spread) | 1.04 | |
| sd of standardised margin residual | 0.91 | |

(1208 games with both closing lines.)

#### Scoreboard: |regulation margin| at the key numbers

| |margin| | simulated | realised (regulation) | ratio | realised count / se |
|---|---:|---:|---:|---|
| 0 | 3.82% | 6.13% | 0.62 | 74 / 0.69pp |
| 1 | 5.22% | 4.64% | 1.13 | 56 / 0.60pp |
| 2 | 4.23% | 5.05% | 0.84 | 61 / 0.63pp |
| 3 | 7.09% | 11.67% | 0.61 | 141 / 0.92pp |
| 4 | 6.16% | 5.38% | 1.15 | 65 / 0.65pp |
| 5 | 3.83% | 4.97% | 0.77 | 60 / 0.63pp |
| 6 | 5.01% | 5.38% | 0.93 | 65 / 0.65pp |
| 7 | 5.94% | 7.86% | 0.76 | 95 / 0.77pp |
| 8 | 3.62% | 4.47% | 0.81 | 54 / 0.59pp |
| 9 | 3.63% | 1.49% | 2.44 | 18 / 0.35pp |
| 10 | 5.41% | 4.39% | 1.23 | 53 / 0.59pp |
| 13 | 3.66% | 2.15% | 1.70 | 26 / 0.42pp |
| 14 | 3.88% | 4.47% | 0.87 | 54 / 0.59pp |
| 17 | 3.53% | 4.55% | 0.78 | 55 / 0.60pp |

Key numbers (3, 7, 6, 10, 14) together: simulated 27.3% vs realised 33.8% of regulation margins (1208 games, 1208 simulated).
Mean |margin|: simulated 11.67, realised 10.55.

Things this anchoring does and does not change: game total / margin MAE and the cover Brier become the line's by construction; rec_yds additionally clears
the Brier guard (z +2.2) so it also reads ADOPT against the serving engine; passing and rushing volume stats stay WORSE; anytime TD strengthens
(+0.0228, z +5.3, Brier z +3.9). The scoreboard dispersion widens (mean |margin| 11.7 vs 10.55 realised) because the anchoring adds variance to the
efficiency of each side.

## 5. Leakage test

* `python -m nfl.sim.evaluate leakage --season 2024 --week 9`: every outcome column of every play and every player-game from week 9 onward is permuted
  (same-season weeks >= 9) and the three week-9 games are re-simulated with the same seeds: **draws, scores, ratings and usage weights bit-identical in
  3/3 games**. Negative control (the same permutation one week earlier, so week 8 data change): draws changed in 3/3 games, so the test has power.
  Output: `{"identical": [true, true, true], "control_changed": [true, true, true], "pass": true}`.
* Offline pytest (`tests/test_nfl_sim.py`): the parameter fit is invariant to later seasons it is not given; as-of team ratings for a game do not move
  when that game and everything after it is scrambled and DO move when earlier data are scrambled; the usage store only returns rows strictly before
  the game; plus unit tests for the buckets, MLE routines, the Kaplan-Meier censoring correction, simulator invariants (completions <= attempts,
  QB passing yards == receivers' receiving yards, pass TDs == receiving TDs), determinism, the QB-receiver dependence, the score-state response recovered
  from a synthetic pbp with a built-in effect, and the scoring helpers.
* Caveat: the leakage test certifies that nothing at or after the game enters the simulation; it cannot certify the active-set rule (the injury report
  is pre-game, the recent-appearance list is history) is exactly what was knowable -- it is a documented approximation.

## 6. What to conclude

1. **Premise measured.** The score-state response is real and large late in games, ~zero early; flat "12-16%" is an average over a very non-uniform
   effect. The simulator carries the measured surface and reproduces pass share by script class within about a point.
2. **Joint structure works.** One simulated game gives team totals, the cover probability and every player prop from the same draws, with
   QB-receiver dependence of +0.46 (empirical +0.46), pass-rush attempts -0.31 (empirical -0.37) and total-margin independence (0.00 vs +0.01).
   `derive.joint_over` prices a same-game combination straight from the draws (same-game dependence was previously an assumption in this repo; this is
   the first measured-by-construction version -- but still unvalidated against SGP prices).
3. **It does not improve the volume stats.** Pass attempts / completions / rush attempts / receptions are significantly worse and pass yards / pass TDs
   are not better. Mean volume is low (QB attempts 27.8 vs 29.9 realised, receptions 3.75 vs 3.98): the active-set share model spreads a fixed team
   volume over the whole as-of roster and the simulated team volume is a ratings-based quantity, while the serving engine keys on the player's own trailing
   volume, which already carries the coach's usage. The hierarchical engine had the same pattern (also WORSE on attempts / completions).
4. **Distribution shape is not information.** The rec_yds log-score gain (+0.17) is shared one-for-one with the hierarchical engine (+0.169) and with
   no MAE or Brier gain: both beat the serving engine's Normal on a zero-heavy, right-skewed quantity.
5. **anytime TD is the one candidate.** The simulator's TD probability (usage x team scoring environment x goal-line mechanics) beats the serving
   engine's `1 - exp(-mu)` and the hierarchical engine on log score and Brier in every season 2022-2026. Its mean is not more accurate (the MAE of a
   probability against 0/1 is not a proper score). Before this would be served it needs the real-line check (BettingPros consensus, as for the
   hierarchical allowlist) and a clean confirmation season; neither was run here.
6. **Game-level it is not a pricing engine.** Ratings-only: game-total MAE 10.61 vs the closing line's 10.24, margin MAE 10.17 vs 9.50, cover Brier worse
   than a coin flip (0.2559 vs 0.2428). **Key numbers are wrong**: |margin| = 3 is 7.5% simulated vs 11.7% realised (ratio 0.64), 0 is 4.1% vs 6.1%, 7 is
   6.2% vs 7.9%; the five key numbers (3, 6, 7, 10, 14) together 28.4% vs 33.8%. The engine has no end-of-game strategy (a team down 2 kicking a tying
   FG, kneels, two-point decisions late) and no coaching-driven tie-breaking behaviour, so a spread or total derived from it will misprice the numbers
   that matter most. (|margin| = 9 and 13 are over-represented, 2.6x and 1.7x -- small realised counts, 18 and 26 games.)

Recommendation: keep `nfl/sim` as research tooling, OFF. If it is revisited: (a) add late-game strategy and re-measure the key numbers before any
spread / total use; (b) fix QB volume (an explicit starter / snap model) before any passing use; (c) run the BettingPros real-line comparison for
anytime TD; (d) consider using only the simulator's TD-allocation prior inside the existing engines instead of replacing them.

## 7. Not verified

* Any price: no real-line (BettingPros) comparison, no CLV, no same-game-parlay price check; every result is accuracy only.
* Overtime (the simulator plays 60 minutes; realised player stats include overtime), two-point strategy, kneels / spikes, penalties as plays, safeties,
  weather, home-field effect, stadium / surface, player-specific red-zone roles beyond carry / target shares, in-game injuries, rookies and traded players
  (no usage history -> positional priors / skipped when outside the as-of active set).
* A 2026 confirmation beyond week 8 (121 games); the 2026 mirror lags the live season.
* That the injury-report-based active set equals what a bettor could know pre-game in every case (late scratches are not in it).
* Why exactly volume is low (the likely causes above were not isolated by ablation).
* The simulator's compute cost in production: 2,000 draws of one game take ~1-2 s single-threaded; not benchmarked for a full slate.

## Reproduce

```bash
# data: local copy of NFL/nflverse/{pbp,player_stats,schedules,injuries} (read-only from the bucket), MLB_BASE_DATA pointing at it
PYTHONPATH=. python -m nfl.sim.script_state --seasons 2018-2025 --boot 500
# baseline rows (serving + hierarchical engines), once
python - <<'EOF'   # see nfl.props.engine_eval.replay_hierarchical; seasons 2022-2026, rows to base_rows.parquet
EOF
for s in 2022 2023 2024 2025 2026; do PYTHONPATH=. python -m nfl.sim.evaluate run --season $s --base base_rows.parquet --out sim_$s --draws 2000; done
PYTHONPATH=. python -m nfl.sim.evaluate report --base base_rows.parquet --runs sim_2022 sim_2023 sim_2024 sim_2025 sim_2026 --heldout-from 2024
PYTHONPATH=. python -m nfl.sim.evaluate league --seasons 2022 2023 2024 2025
PYTHONPATH=. python -m nfl.sim.evaluate leakage --season 2024 --week 9
```
