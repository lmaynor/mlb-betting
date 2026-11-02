---
title: "The hierarchical NFL prop engine ships OFF; turning it on needs the held-out comparison"
module: nfl/models/hierarchical_props.py, nfl/models/hierarchical_live.py, nfl/props/engine_eval.py, ev/daily_projections.py
tags: [nfl, projections, hierarchical, walk-forward, adoption, conventions]
problem_type: convention
category: conventions
date: 2026-11-01
---

## What it is

`nfl.models.hierarchical_props` prices the ten NFL stats as one coherent generative process (attempts -> completions -> yards,
carries -> yards, targets -> receptions -> yards, team-TD-anchored anytime TD). `nfl.models.hierarchical_live` feeds it from the serving
engine's game-log rows. `ev.daily_projections` uses it for NFL only when `NFL_ENGINE=hierarchical` (default `ewm`) AND only for the stats listed in
`nfl/props/hierarchical_allowlist.json` (written by `engine_eval --write-allowlist`; missing / unreadable file = empty list = nothing served).
The board payload's `engine` is then `empirical_ewm_v2+hierarchical_v1`; every row carries its own `engine`. `NFL_HIER_STATS=all|none|a,b,c`
overrides the file for experiments only (production uses the file).

## Why it is off

Every coefficient is a hand-set prior (`hierarchical_props.PRIORS`). The repo's rule
(`nfl-projection-engine-changes-need-walkforward-evidence.md`) is that a change to what the board serves needs held-out evidence, and no
such evidence existed when this was written. The pbp-context features earlier gave NO gain to the XGBoost models, and the legacy engine's
opponent factor is worth ~0, so "more structure" is not evidence of "more accuracy".

## How to decide

```bash
PYTHONPATH=. python -m nfl.props.engine_eval --seasons 2024-2025 --hierarchical [--hier-context] [--hier-cache rows.parquet] --out report.md
```

Replays both engines on the same player-games using only earlier weeks and the schedule's closing lines. Reports, per stat, the paired
log score (1-unit probability mass, identical binning for both engines), Brier of P(over proxy line), CRPS, MAE, and tail calibration at the
fair +200 / +400 rungs (set from the baseline's own quantile). ADOPT needs pooled z >= 2.81 (Bonferroni over 10 stats) on log score, Brier
agreeing, and a better log score in >= 4 of 5 seasons. Selected per stat: a stat that does not clear it should be left on the legacy price
(the per-row fallback in `make_row` is the hook; a per-stat allowlist is the natural next step and is not built).

`--hier-context` also loads injuries / pbp / participation / snap_counts (large). Without it the replay uses game logs + schedule lines only,
so it under-states what the live path can see (no teammate absences, no coverage shells, no OL / pressure).

## Gotchas

- `team_spread`: hierarchical = NEGATIVE favoured; env_adjust = POSITIVE favoured. Convert at the boundary.
- `net_pressure_mismatch` is a ratio (1.0 = league average), not the spec's rate-unit expression.
- The serving calibration multiplies the VOLUME input; TD stats take its scale only.
- Rows stay `validated: false` whatever the replay says: it is accuracy evidence, not edge against a price.

## The allowlist (2026-11-01)

```bash
PYTHONPATH=. python -m nfl.props.engine_eval --seasons 2024-2025 --hierarchical --hier-cache nocontext.parquet \
  --hier-cache-extra context.parquet --hier-confirm season2026.parquet --write-allowlist nfl/props/hierarchical_allowlist.json
```
A stat is listed only if it is ADOPT (strict rule: log-score z >= 2.81, Brier z >= 1.645, better in the required seasons) on the no-context
run AND on every `--hier-cache-extra` run (the live path uses context when it loads), and is not contradicted on `--hier-confirm`
seasons nothing was tuned or selected on (point estimate > 0, not significantly negative). Shipped: **pass_tds, rush_yds** (2024-25 held out,
priors tuned on 2021-23 only; 2026 weeks 1-8 confirm: +0.026 and +0.102 log score). Not listed: rush_attempts (ADOPT only with context),
rec_yds (log score +0.17 in every period but the Brier gain at the proxy line is not significant: needs a real-line check), everything passing.
The 2024-25 verdicts also SELECTED these stats, so 2026 is the only clean check, and it is 8 weeks (208-264 rows per stat). Re-run
each offseason and when the priors change; the file records the evidence it was written from.
