---
title: "NBA hierarchical prop engine (hier_v1): what it changes, what is NOT validated, and the UTC as_of trap"
module: nba/props/hierarchical.py, ev/daily_projections.py
tags: [nba, projections, props, minutes, combos, leakage, evidence, conventions]
problem_type: convention
category: conventions
date: 2026-11-01
---

## What changed (NBA rows only; NFL and NHL are untouched)

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
- NBA entries are added to `BLEND_C_BY_SPORT_STAT` (the displayed `p_over_blend`; the flat 0.10 is the fallback).

## Follow-up evidence (2026-11-01, `docs/analysis/nba_engine_walkforward_2026-11-01.md`)

A walk-forward replay (`nba/props/engine_eval.py`; tuned on 2023-24, scored on 2024-25 / 2025-26; accuracy only, ORACLE absences,
no spreads) found: the spec's absence elasticities were ~4x too strong (shipped: `eta_scale` 0.4, steals / blocks excluded), a
stale-absence double count (fixed), a small team-makes assist factor worth keeping (`shot_gamma` 0.5), and no gain from positional
DvP, missed-shot-pool rebounds or zero-inflated steals / blocks (all left OFF in `NBA_FEATURES`). The engine beats the pre-#147
one on all ten stats on the held-out seasons. Everything below that the replay could not exercise stays unvalidated.

## NOT validated (the replay could not test these: no spreads, no prop lines)

The walk-forward above tested the absence elasticities and the extra factors only. No run backs the blowout curve, foul weight,
overtime regime, shot-diet coefficients or correlation priors; they are the specification's priors. The 2026-10-15 BettingPros study measured blend `c` for the TRAINED research
models on points / rebounds / assists / threes / pra and NHL SOG only (`mlb_core/risk/calibration.py`). Nothing was measured for
this empirical engine or for pr / pa / ra / steals / blocks, so those NBA entries of `BLEND_C_BY_SPORT_STAT` are priors, and pra (0.12) and points (0.10) sit ABOVE the measured 0.05 / 0.07. The blend is display-only: it feeds neither the
edge, the confluence flag nor the Beezy Score. Do not read the combo coherence as edge: it makes the board internally
consistent; the replay (accuracy, not edge) shows it beats the old projection on the held-out seasons.
Built in the follow-up but left OFF because the replay rejected them: positional DvP, missed-shot-pool rebounds, zero-inflated
steals / blocks, and the prior-season tracking factors (`nba/data/tracking.py`: potential-assist / rebound-chance luck removal from the
unlicensed community mirror gabriel1200/site_Data; season aggregates only, so prior season only; rebound-chance files stop at 2022-23).
Overtime is a variance-only regime (untestable without spreads). `nba/props/score.py` has an opt-in `--coherent-combos` PRA (default off, unvalidated).

## Gotcha: the master's `_date` is the UTC date of the tip

`NBA/player_boxscores_master.csv` labels each game with the UTC date of its tip (docs/solutions/integration-issues/
sportsblaze-master-date-is-utc.md), so for an evening game tonight's ET day equals the UTC date of LAST night's late game.
A strict `_date < ET game day` history cut would silently drop yesterday's game from every live projection. Therefore:

- live serving passes `as_of=None` (the master holds only finished games); `GameLogs.nba_replay = True` switches on a cutoff at the
  tip's UTC day (replays of past boards; an afternoon tip also loses the previous evening's game -- stale, never leaking);
- `rest_days` is computed between the newest prior row and the tip's UTC day, floored at 0 (it can miss a back-to-back across
  the UTC/ET labelling, never invent one);
- league-level context (`StatContext`: opponent factors, dispersion) is NOT as-of filtered -- pre-existing, and harmless live.
  `tests/test_nba_props_hierarchical.py` pins it from clean logs when it proves bit-for-bit leakage freedom.
