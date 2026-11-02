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

## The allowlist (2026-11-01, updated after the real-line check)

```bash
PYTHONPATH=. python -m nfl.props.engine_eval --seasons 2024-2025 --hierarchical --hier-cache nocontext.parquet \
  --hier-market --hier-market-cache market.parquet --hier-confirm season2026.parquet --write-allowlist nfl/props/hierarchical_allowlist.json
```
A stat is listed only if: log-score z >= 2.81 and better in the required seasons (2024-25 held out, priors tuned on 2021-23 only); its Brier z
>= 1.645 **at the book's own line** when >= 300 real-line rows exist (else at the L5 proxy line); and it is not contradicted on `--hier-confirm`
seasons nothing was tuned or selected on. Shipped: **pass_tds, rec_yds, rush_yds**. Not listed: rush_attempts (Brier at real lines z +0.1), receptions,
pass_attempts / completions / yards (worse), interceptions, anytime_td (parity).

**Real-line check (BettingPros consensus, 2024-25, 6,817 scored props after screening; 53% of consensus rows were dropped as post-start quotes and
3,865 for unresolved names, so it is a selected sample):** hierarchical vs shipped engine Brier z: rec_yds +2.3, rush_yds +3.0, pass_tds +2.7,
rush_attempts +0.1, receptions -1.4, pass_yds -1.2. The MARKET beats both engines on every stat (e.g. rec_yds Brier market 0.2498 / shipped 0.2759 /
hierarchical 0.2694), and the engine's blend coefficient on top of the market is only significant for rush_yds (c = +0.25, t 2.9; calibration, not edge). The 2026 real-line
sample is empty (5 rows: BettingPros quotes for 2026 are stamped after kickoff), so 2026 is confirmed on the proxy line only.

**Served configuration = the tested one.** No context layers (`NFL_HIER_LAYERS`, default `none`) and no weather (`NFL_HIER_WEATHER`, default off).
Ablation on 2021-23 (paired log score vs no context): teammate-absence layer HURTS receptions / rec_yds (-0.0066, z -3.5; it over-projects) and is
non-significantly positive for rush; coverage shell ~0; YAC / stuff factors hurt pass_yds (-0.005, z -2.9); OL / pressure ~0. The replay used to feed
the engine the schedule's REALISED game-day wind (a mild lookahead for wind-sensitive stats); that is removed (`price_row(use_weather=False)` ignores
env wind / temp and any weather dict) and every number above was re-run without it.

The 2024-25 verdicts both selected the allowlist and tested it, so 2026 weeks 1-8 (208-658 rows per stat) is the only clean check and it is thin.
Re-run each offseason and when the priors change; the file records the evidence it was written from.
