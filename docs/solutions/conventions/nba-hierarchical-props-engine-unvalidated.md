---
title: "NBA hierarchical prop engine (hier_v1): what it changes, what is NOT validated, and the UTC as_of trap"
module: nba/props/hierarchical.py, ev/daily_projections.py
tags: [nba, projections, props, minutes, combos, leakage, evidence, conventions]
problem_type: convention
category: conventions
date: 2026-11-01
---

## What changed (NBA rows only; NFL / NHL rows are untouched except the display-only blend weight)

`ev.daily_projections.make_row` routes NBA through `_nba_projection` -> `nba.props.hierarchical`:

- Role-weighted teammate absence: replaces the flat `1 + 0.04 * sum(absence prob)` (applied to every stat alike). An absent
  playmaker lifts the backup guard's assists, an absent big lifts the remaining big's rebounds / blocks. Per-stat elasticity
  0.55-0.65, affinity ~ minutes * (per-minute rate)^3, one-stat boost capped at x1.40.
- Minutes: EWM minutes trimmed for blowout risk (starters, `|spread|` from the banked game-line snapshot) and back-to-backs;
  a 3-regime mixture supplies EXTRA relative variance only above what the history already holds (zero with no spread info).
- Threes: EWM(3PA) x empirical-Bayes 3P% (K=150 attempts toward .362, 60-game window). Falls back to the old path if the
  master carries no attempts column.
- Points: sd scaled by the player's three-point / free-throw scoring mix.
- pra / pr / pa / ra: never projected alone. Component (points, rebounds, assists) means are rounded to the 3 decimals the
  board shows and SUMMED, so the published means add up exactly; variance uses the player's last-30-game correlation matrix
  shrunk 50% toward (0.20, 0.18, 0.22).
- `BLEND_C_BY_SPORT_STAT` replaces the flat 0.10 in the displayed `p_over_blend`.

## NOT validated

No walk-forward run backs any constant here (elasticities, blowout curve, foul weight, shot-diet coefficients, correlation
priors); they are the specification's priors. The 2026-10-15 BettingPros study measured blend `c` for the TRAINED research
models on points / rebounds / assists / threes / pra and NHL SOG only (`mlb_core/risk/calibration.py`). Nothing was measured for
this empirical engine, for pr / pa / ra / steals / blocks, or for any NFL stat, so those entries of `BLEND_C_BY_SPORT_STAT` are
priors, and pra (0.12) and points (0.10) sit ABOVE the measured 0.05 / 0.07. The blend is display-only: it feeds neither the
edge, the confluence flag nor the Beezy Score. Do not read the combo coherence as edge: it makes the board internally
consistent; whether it beats the old projection is an open walk-forward question (`nfl.props.engine_eval` is the NFL template).
Not implemented from the spec: missed-shot-pool rebounds, teammate-eFG assists, ZIP / hurdle steals and blocks, an overtime
term, positional DvP, tracking-data joins, and wiring into the trained-model scorer `nba/props/score.py`.

## Gotcha: the master's `_date` is the UTC date of the tip

`NBA/player_boxscores_master.csv` labels each game with the UTC date of its tip (docs/solutions/integration-issues/
sportsblaze-master-date-is-utc.md), so for an evening game tonight's ET day equals the UTC date of LAST night's late game.
A strict `_date < ET game day` history cut would silently drop yesterday's game from every live projection. Therefore:

- live serving passes `as_of=None` (the master holds only finished games); `GameLogs.replay = True` switches on a cutoff at the
  tip's UTC day (replays of past boards; an afternoon tip also loses the previous evening's game -- stale, never leaking);
- `rest_days` is computed between the newest prior row and the tip's UTC day, floored at 0 (it can miss a back-to-back across
  the UTC/ET labelling, never invent one);
- league-level context (`StatContext`: opponent factors, dispersion) is NOT as-of filtered -- pre-existing, and harmless live.
  `tests/test_nba_props_hierarchical.py` pins it from clean logs when it proves bit-for-bit leakage freedom.
