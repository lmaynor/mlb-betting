# NBA correlated game simulator: what it does and what the held-out box scores say (2026-11-05)

Branch `feat/nba-joint-game-simulator`. Code: `nba/sim/` (`joint_sim.py` simulator + CLI, `fit.py` calibration of the structural constants,
`adapter.py` engine -> simulator inputs, `evaluate.py` walk-forward / correlation / pair-event harness, `params.json` the committed fit).
Tests: `tests/test_nba_joint_sim.py` (21, offline). **Offline research tool. Not wired into the picks board, the SGP proxy builder, an alert or
any Discord path. Every output is UNVALIDATED. This measures ACCURACY, not edge: no price, prop line or spread is used anywhere.**

## Verdict (read this first)

| Question | Answer |
|---|---|
| Does one simulated game give coherent props? | Yes, by construction and by test: combos are sums of the SAME draws; player points + a rest-of-roster bucket equal the team total in every draw; points >= 3 x threes in every draw. |
| (1) Does the simulated marginal P(over) match the analytic hierarchical marginal? | **Structural mode: rebounds / assists / threes yes (mean abs gap 0.006, about one Monte-Carlo SE); points NO (bias -0.025, 71% of rows beyond 3 SE).** The points gap is a shape difference, and the walk-forward below says the simulation's shape is the better-calibrated one. **Anchored mode: yes for every single leg (mean abs gap 0.0001).** |
| (2) Do the simulated within-game correlations match the box scores? | **Partly.** Same-player, opposing-player and player-vs-margin pairs are within about 0.03 of the box scores; RMS gap over 19 pair types 0.039 against 0.194 for "everything independent". Three systematic misses, listed below (teammate points-points too negative by 0.04, teammate points-assists too low by 0.045, player-vs-game-total too low by 0.06). |
| (3) Are simulated pra / pr / pa / ra at least as well calibrated as today's coherent-from-marginals ones on held-out seasons? | **Yes in both modes, but read why.** Structural: log-score gain +0.0040..+0.0057, ECE 0.044-0.049 -> 0.007-0.015, z = 8..25 on 2024-25 and 2025-26 separately. But about all of that comes from the POINTS marginal shape (the board prices points as a Normal; the simulation's skewed shape is better calibrated: points alone +0.0047 / +0.0071). Anchored (exact analytic marginals, simulated dependence only): +0.0002..+0.0009 for pra / pr, ~0 for pa, +0.0043 for ra. The dependence structure on its own is worth little for the combos today. |
| Recommendation | **Leave it off.** Nothing here is an edge claim and the dependence has known misses. If it is used at all, use `marginals="anchored"` for SGP pair probabilities (leg probabilities then equal the board's), keep the UNVALIDATED label, and treat the structural-mode calibration result as a finding about the points Normal rather than a reason to serve the simulator. |

## What a draw is

Everything in one draw comes from one latent game (module docstring of `nba/sim/joint_sim.py` has the full list):

1. **Game script**: a possession factor shared by both teams, game total T (loaded on the possession factor, fitted corr 0.455, Student-t 12) and
   margin M (Student-t 10), so team points are integers P_home = (T+M)/2, P_away = (T-M)/2.
2. **Team pies** given the script: team threes, assists and rebounds are linear in the team's own points (and, for assists, threes; for rebounds,
   pace, the opponent's points and threes) with fitted residual noise. Fitted on 2019-2023 team games against an EX-ANTE expectation (EWM of each
   team's earlier games), so "more team points -> more threes and assists" is measured, not assumed.
3. **Minutes**: usual minutes x (mean fraction at this |margin|, by role: a 35+ mpg starter plays 0.84 of his usual minutes at |margin| >= 25, a 12-20 mpg
   bench player 1.05) + a draw from the empirical distribution of what is left (foul trouble, injury exits, overtime), Gaussian-copula correlated within a
   team (0.06) and across the game (0.03). Garbage time is therefore a consequence of the simulated margin, not a separate coin flip.
4. **Shares**: each pie is split by multinomial sampling among the modeled players plus a "rest of roster" bucket, weights = usual per-minute production x
   tonight's minutes x (pie size / expected pie)^gamma x a log-normal factor with a shared per-player "form" term. Threes are their own pie and points =
   3 x threes + a share of the team's remaining points, which is what makes the points constraint exact and coherent with threes.
5. **Props**: points / rebounds / assists / threes are the draws, pra / pr / pa / ra are sums of the same draws.

**Marginal consistency.** For every (player, pie) the simulator solves a multiplicative weight (mean) and a noise scale (variance) on a pilot of the SAME
random numbers (common random numbers, 7 passes, the pass closest to the targets is kept) so the simulated mean and sd equal the engine's. The shape is
whatever the mechanism produces. `marginals="anchored"` additionally replaces each stat's draws by the analytic quantile of the draw's rank (ties broken at
random, deterministically): single-leg P(over) equals the board's, the dependence is the simulator's, the exact sum constraint is lost (tested).

**API**: `simulate_game(GameInput, n=10000, seed=..., marginals="structural"|"anchored") -> JointDraws` with `p_over(player, stat, line)`, `p_under`,
`joint(leg_a, leg_b)` (legs are `(player, stat, line[, "over"|"under"])`; returns p_a, p_b, p_joint, p_indep, lift and the Monte-Carlo SE), `joint_all(legs)`,
`corr(...)`, `series(...)`, game-level legs under the player name `"GAME"` (`total`, `margin`, `home_points`, `away_points`). Deterministic in (game, n, seed, params,
marginals). About 0.9 s per 16-player game at 10,000 draws in one process on a laptop. `nba/sim/adapter.py::engine_player_input` builds the inputs from the serving engine's own
`_nba_component` / `_nba_projection`. **CLI**: `python -m nba.sim.joint_sim --game spec.json --n 10000 --seed 1 --marginals anchored --prop "h0:pra:35.5"
--joint "h0:points:23.5+a0:points:23.5"` (spec format: `game_from_dict`).

## Data discipline

* Structural constants (`fit.py`): fitted on `season_year <= 2023` box scores only. Player-level block (`params["player"]`: form amplitude, pie-size elasticity,
  rest-bucket noise, pie wobble): tuned on 388 games of 2023-24 (Dec 2023 + Feb 2024) with `evaluate.py --set`, never on 2024 or 2025.
* Held-out replay: 2024-25 (1,317 of 1,321 games) and 2025-26 (1,322 of 1,322), 34,429 eligible player-games (last-10 minutes >= 20, the board's population).
  For each game every rotation player's marginals come from the serving engine's own functions on history strictly before that game; league context rebuilt
  monthly; the baseline got exactly the same inputs. 4,000 draws per game (the default is 10,000; at 4,000 the Monte-Carlo noise adds about 1e-4 to the
  simulation's log score and none to the analytic baseline, so it biases AGAINST the simulator).
* Tuning grid on the 388-game subsample (RMS gap over the 19 correlation rows, lower = better): first-pass defaults (elasticity 0.5 / 0.3) 0.0349; no pie-size elasticity 0.0354;
  elasticity 1.0 / 0.7 0.0345; rest-bucket noise 0.4 / 0.8: 0.0343 / 0.0342; pie wobble 0.12: 0.0346; form amplitude 0.3 on every stat 0.0508 (worse);
  shipped (elasticity 1.0 / 0.7, rest noise 0.4, wobble 0.07, form amplitude rebounds 0.22 / assists 0.12): **0.0296**. The pie wobble is what removes the
  negative teammate rebound / assist correlation (-0.034 -> -0.014 against -0.011 measured): a multinomial split of a total that is less dispersed than a
  Poisson count makes teammates look negatively correlated (Cov = p_i p_j (Var N - E N)).

## 1. Consistency with the analytic marginals (held-out, pooled)

Gap = simulated minus analytic P(over) over every (player, stat, line) row (the L10 proxy line plus model-centred lines at z = -1, 0, +1); MC SE at 4,000 draws is
0.007. Full table: `docs/analysis/nba_joint_simulator/heldout_2024_25_pooled.md`.

| stat | structural bias | structural mean abs gap | structural rows > 3 SE | anchored mean abs gap | anchored rows > 3 SE |
|---|---|---|---|---|---|
| points | -0.0253 | 0.0356 | 71% | 0.0001 | 0% |
| rebounds | -0.0014 | 0.0066 | 6% | 0.0001 | 0% |
| assists | -0.0009 | 0.0065 | 3% | 0.0001 | 0% |
| threes | -0.0011 | 0.0060 | 3% | 0.0001 | 0% |
| pra / pr / pa | -0.021 / -0.022 / -0.023 | 0.029 / 0.032 / 0.032 | 67-69% | 0.0085 / 0.0066 / 0.0050 | 6% / 3% / 1% |
| ra | -0.020 | 0.026 | 60% | 0.0254 | 59% |

Reading it: the count stats are consistent in structural mode; points are not, and the walk-forward in section 3 shows that is the simulation being right and the
Normal being wrong (the board's points Normal has ECE 0.047 at the proxy line). Anchored combos are NOT forced to equal the board's Normal combos (they are sums of
anchored legs), and for `ra` they differ by 0.025 because the board's `ra` Normal uses a correlation prior (0.22) that is lower than the measured one (0.24 residual
correlation, and a different shape).

## 2. Simulated vs empirical within-game correlation (held-out, pooled 2024-25 + 2025-26)

Pooled Pearson correlation of residuals (actual minus the engine's mean, scaled by the engine's sd, clipped at +/-6) over every pair of the type, all rotation players
(last-10 minutes >= 12; 2,639 games). Empirical = box scores, simulated = structural draws, SE = game bootstrap. Residual form removes the engine's own bias, which
matters: the engine's team-total input overshoots by 5.95 points a game (the oracle-absence boost documented in `nba_engine_walkforward_2026-11-01.md`).

| pair type | pairs | empirical | +/- SE | simulated | gap |
|---|---|---|---|---|---|
| teammates points-points | 192,819 | -0.004 | 0.002 | -0.046 | **-0.042** |
| teammates rebounds-rebounds | 192,819 | -0.000 | 0.003 | -0.020 | -0.019 |
| teammates assists-assists | 192,819 | +0.001 | 0.002 | -0.010 | -0.011 |
| teammates threes-threes | 192,819 | -0.008 | 0.002 | +0.001 | +0.009 |
| teammates points-assists (A pts, B ast) | 385,638 | +0.049 | 0.002 | +0.004 | **-0.045** |
| teammates points-rebounds (A pts, B reb) | 385,638 | +0.011 | 0.002 | -0.032 | **-0.043** |
| teammates rebounds-assists (A reb, B ast) | 385,638 | +0.004 | 0.002 | -0.023 | -0.027 |
| opponents points-points | 211,642 | +0.017 | 0.002 | +0.010 | -0.007 |
| opponents rebounds-rebounds | 211,642 | +0.016 | 0.002 | +0.002 | -0.014 |
| opponents assists-assists | 211,642 | +0.005 | 0.002 | +0.007 | +0.002 |
| opponents points-assists (A pts, B ast) | 423,284 | +0.009 | 0.002 | +0.008 | -0.001 |
| same player points-assists | 47,222 | +0.226 | 0.005 | +0.253 | +0.027 |
| same player points-rebounds | 47,222 | +0.337 | 0.005 | +0.323 | -0.014 |
| same player rebounds-assists | 47,222 | +0.248 | 0.005 | +0.225 | -0.023 |
| same player points-threes | 47,222 | +0.640 | 0.003 | +0.683 | +0.043 |
| player points vs game total | 47,222 | +0.207 | 0.004 | +0.146 | **-0.062** |
| player rebounds vs game total | 47,222 | +0.068 | 0.005 | -0.045 | **-0.113** |
| player assists vs game total | 47,222 | +0.160 | 0.004 | +0.120 | -0.040 |
| player points vs own team margin | 47,222 | +0.079 | 0.004 | +0.108 | +0.030 |

RMS gap 0.039; the same RMS for "all independent" (simulated = 0 everywhere) is 0.194. The 2024-25 and 2025-26 seasons separately agree to within 0.005 on every row
(`heldout_2024_season.md`, `heldout_2025_season.md`).

**Where it is wrong and why (do not skip):**
* *Teammates compete too hard.* Teammate points are -0.046 simulated against -0.004 measured. The pie (team points) is drawn first and then split, so a hot night by one
  player has to come out of someone else; in reality a hot night also RAISES the team total. The bottom-up version (individual deviations summing into the team total)
  is the fix and was not built. Same cause for teammate points-assists (+0.049 measured, +0.004 simulated) and points-rebounds. For SGP: simulated teammate
  points-points over/over probabilities are too low, and teammate points + assists too low.
* *Pace / total link too weak.* Player points vs game total 0.146 against 0.207, rebounds the wrong sign (-0.045 against +0.068). The game script's pace-to-rebounds
  path comes from team-level regressions that net to a negative own-pie effect.
* *Same-player points-threes* 0.683 against 0.640: the simulator ties them slightly too tightly (a player's threes are part of his points by construction).
* Opposing players and the player-vs-margin pair are close.

## 3. Walk-forward calibration (held-out, pooled; accuracy only)

`d LL` = analytic minus simulated mean log loss (positive = the simulation is better), z = game-clustered. "Analytic" = what the board computes today (components:
the engine marginal; combos: Normal with moment-matched variance from the shrunk within-player correlation). Rows: the L10 proxy line (floor(L10 mean) + 0.5, as in
`engine_eval.py`; 34,429 rows per stat) and model-centred lines (z = -1, 0, +1; 103,287 rows per stat).

| stat | lines | LL analytic | LL structural | d LL (z) | LL anchored | d LL (z) | ECE analytic -> structural -> anchored |
|---|---|---|---|---|---|---|---|
| points | proxy | 0.6796 | 0.6749 | +0.0047 (+9.0) | 0.6796 | -0.0000 (-0.2) | 0.047 -> 0.005 -> 0.047 |
| points | model-centred | 0.5190 | 0.5119 | +0.0071 (+25.3) | 0.5190 | +0.0000 (+1.0) | 0.026 -> 0.011 -> 0.026 |
| rebounds / assists / threes | both | 0.4574-0.6675 | same | -0.0004..+0.0000 (|z| <= 3.7) | same | 0.0000 (|z| <= 1.7) | unchanged (0.006-0.018) |
| pra | proxy | 0.6771 | 0.6732 | +0.0040 (+8.7) | 0.6762 | +0.0009 (+7.2) | 0.044 -> 0.007 -> 0.036 |
| pra | model-centred | 0.5152 | 0.5103 | +0.0050 (+21.4) | 0.5151 | +0.0002 (+2.2) | 0.027 -> 0.014 -> 0.025 |
| pr | proxy | 0.6792 | 0.6745 | +0.0047 (+9.9) | 0.6785 | +0.0007 (+7.2) | 0.048 -> 0.008 -> 0.042 |
| pr | model-centred | 0.5151 | 0.5093 | +0.0057 (+23.5) | 0.5148 | +0.0003 (+5.2) | 0.027 -> 0.013 -> 0.025 |
| pa | proxy | 0.6781 | 0.6740 | +0.0040 (+7.8) | 0.6778 | +0.0003 (+4.3) | 0.044 -> 0.005 -> 0.041 |
| pa | model-centred | 0.5177 | 0.5120 | +0.0057 (+21.9) | 0.5178 | -0.0001 (-1.5) | 0.025 -> 0.011 -> 0.024 |
| ra | proxy | 0.6761 | 0.6719 | +0.0043 (+9.9) | 0.6718 | +0.0043 (+10.2) | 0.049 -> 0.011 -> 0.011 |
| ra | model-centred | 0.5049 | 0.5026 | +0.0023 (+12.5) | 0.5028 | +0.0021 (+12.3) | 0.029 -> 0.015 -> 0.013 |

Both seasons separately have the same signs and similar sizes (2025-26 alone: points +0.0052 / +0.0071, pra +0.0036 / +0.0046, ra +0.0040 / +0.0023; 2024-25 alone in
`heldout_2024_season.md`).

**Decomposition (the honest reading).** Anchored mode keeps the analytic marginals exactly and changes only the dependence between a player's own legs; it gains
+0.0002..+0.0009 on pra / pr, nothing on pa, and +0.0043 on `ra` (where the board's correlation prior is the weak part). Structural mode additionally changes the SHAPE of
the points marginal (right skew; the board uses a Normal with sd = cv x mu), and that is where the +0.004..+0.007 log-score and the ECE collapse (0.047 -> 0.005 at the
proxy line) come from: points alone gain +0.0047 / +0.0071. So the finding is "the points Normal is miscalibrated" far more than "the joint simulation prices combos
better". The same gain should be available from a skewed points marginal without any simulator.

## 4. SGP-style pair events (accuracy of P(A and B); no prices)

Two over-legs at model-centred lines (floor(mu) + 0.5), eligible players only; pooled 2024-25 + 2025-26. `d LL vs independence` = log loss of the product of the analytic
marginals minus that of the simulated joint (positive = the joint is better). `dependence only` compares the structural joint with the product of the structural
simulation's OWN marginals, so the (better-calibrated) points shape cancels out and only the dependence is scored.

| pair type | pairs | d LL vs independence, structural (z) | anchored (z) | dependence only, structural (z) |
|---|---|---|---|---|
| same player points-rebounds | 34,429 | +0.0003 (+1.1) | -0.0015 (-2.6) | **+0.0043 (+7.2)** |
| same player rebounds-assists | 34,429 | +0.0020 (+4.2) | +0.0022 (+4.6) | +0.0024 (+5.0) |
| same player points-assists | 34,429 | -0.0002 (-0.8) | -0.0028 (-5.7) | +0.0018 (+3.4) |
| teammates points-points | 100,102 | +0.0112 (+13.0) | +0.0038 (+20.0) | +0.0008 (+3.7) |
| teammates rebounds-rebounds | 100,102 | +0.0007 (+6.7) | +0.0008 (+8.6) | +0.0008 (+9.0) |
| teammates points-assists (A pts, B ast) | 200,204 | +0.0017 (+6.2) | -0.0001 (-3.7) | +0.0001 (+2.1) |
| opponents points-points | 112,566 | +0.0067 (+10.1) | -0.0005 (-14.4) | +0.0000 (+0.6) |
| player points over x game total over | 34,429 | +0.0021 (+16.5) | -0.0067 (-9.4) | **-0.0036 (-4.6)** |
| player assists over x game total over | 34,429 | -0.0044 (-7.5) | -0.0042 (-7.2) | **-0.0042 (-7.1)** |
| player rebounds over x game total over | 34,429 | +0.0016 (+7.2) | +0.0017 (+8.6) | +0.0017 (+8.5) |

Reading it: the dependence adds real but small accuracy where the correlations are real and the simulator has them right (same-player points-rebounds and
rebounds-assists, teammate rebounds). The anchored negatives on points pairs are the analytic points marginal's calibration problem showing through, not the dependence
(the same pair with calibrated marginals, last column, is positive). The two **player-vs-game-total** rows where the dependence column is NEGATIVE are not a clean test:
the total leg's line is floor(mu_T) + 0.5 with mu_T the sum of the engine's player means, which overshoots the real total by 5.95 points a game, so the total leg is
badly miscalibrated for every method; do not read those two rows as evidence either way. Remember the correlation misses in section 2 when using the teammate
points-points and points-assists pairs: the structural joint is too low for the first (the simulated correlation is too negative) and sits near independence for the
second although the box scores show a positive link.

## What was NOT verified

* **No prices.** No prop line, no SGP quote, no CLV, no ROI. "Better calibrated at a proxy line" is not an edge, and the repo's own rule is CLV, not log score.
* **Oracle absences**: players who did not play are excluded and the engine's role boosts use the real DNP list (exactly as `engine_eval.py`); the live board only has an
  injury report with probabilities. The baseline got the same oracle inputs, so the comparison is fair, but the absolute numbers are an upper bound.
* **No spreads in the replay**: the engine's blowout trim is therefore off, and the game script's margin mean is a simple EX-ANTE team-rating regression
  (`margin = 2.12 + 0.77 x (rating_home - rating_away)`), not the Kalman game engine's `PricedGame`. Production would pass `mu_margin` / `sd_margin` / `sd_total` from
  `nba/game/model.py`; that wiring was not built or tested.
* **Team-total inputs are biased** by the engine's own oracle-boost bias (+5.95 points a game over actual; total residual sd 31.6 against the 18.9 the simulator assumes).
  The simulator inherits the player means it is given. That is why correlations are scored on residuals and the game-total pair rows are discounted.
* Rotation = last-10 minutes >= 12; players below that are the rest-of-roster bucket (about 5% of points). Players with no threes projection are in the points / rebounds /
  assists pies only. Steals, blocks, turnovers and minutes props are not simulated as props.
* 4,000 draws in the replay (default 10,000); one seed per game; the 2026-27 season (a few weeks, SportsBlaze has no 2026-27 file and the mirror was not used here)
  was not replayed.
* Tuned constants are tuned on 388 games and not re-validated on a second tuning sample; the simple grid is in this note and the tuning is cheap to redo.

## Reproduce

    # pull a copy of the masters (read-only)
    gcloud storage cp gs://<bucket>/NBA/player_boxscores_master.csv gs://<bucket>/NBA/team_boxscores_master.csv .
    # structural constants, fitted before the held-out seasons (the tuned "player" block in params.json is kept)
    python -m nba.sim.fit --player-master player_boxscores_master.csv --team-master team_boxscores_master.csv \
        --fit-through 2023 --out nba/sim/params.json
    # held-out replay (4,000 draws, 9 workers; about 6 minutes a season)
    python -m nba.sim.evaluate --player-master player_boxscores_master.csv --team-master team_boxscores_master.csv \
        --params nba/sim/params.json --seasons 2024 --n 4000 --workers 9 --out-dir out_2024
    python -m nba.sim.evaluate --report out_2024 out_2025       # the markdown tables above
    # tuning on a subsample: add --seasons 2023 --months 2023-12,2024-02 --set player.tau.rebounds=0.07 ...
