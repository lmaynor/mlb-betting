---
title: "Changing the NFL projection engine requires a held-out walk-forward replay of the serving code"
module: ev/daily_projections.py, nfl/props/engine_eval.py, nfl/props/env_adjust.py, nfl/props/projection_calibration.json
tags: [nfl, projections, walk-forward, calibration, evidence, conventions]
problem_type: convention
category: conventions
date: 2026-10-16
---

## Why this exists

The board served at `/api/public/projections` runs `ev.daily_projections` (EWM + usage + opponent factor), not the
XGBoost research models in `nfl/models/`. Until 2026-10-16 NO accuracy figure in this repo described that code; every
number belonged to a different model, and its parameters were hand-set.

## The rule

1. Measure the SERVING functions, not a re-implementation: `python -m nfl.props.engine_eval --seasons 2021-2025
   --tune-through 2023 --calibrate` replays `project_mu` / `GameLogs.context` / `p_over` as of every regular-season week
   using only earlier games and the production two-season window (2 warm-up seasons are replayed before the first test season).
2. Parameters are selected on the early seasons only and judged on later seasons nothing selected on. A variant is adopted
   only if the held-out paired log-score gain is >= `ADOPT_Z` standard errors (Bonferroni over the 6 stats = 2.64) AND the
   secondary guard agrees (MAE; Brier for `anytime_td`, because MAE of a probability against a 0/1 outcome is not a
   proper score and rewards hugging 0).
3. Calibrations (below) are fitted walk-forward (season S on seasons < S) and shipped only for stats that clear their bar.
   That bar was fixed AFTER an exploratory look showed the over-projection bias is near-universal, so what it demands is
   replication: the primary metric must improve >= `ADOPT_Z` standard errors pooled AND in >= 4 of the 5 test seasons,
   with log score not significantly worse. Treat the calibration evidence as "exploratory, replicated across 5 seasons".
4. Overrides live in `STAT_PARAMS` (ev/daily_projections.py) and `nfl/props/projection_calibration.json`, each with its
   evidence; the engine id is bumped when behaviour changes (now `empirical_ewm_v2`). Never tune on the seasons you report.
5. This is ACCURACY evidence only. There are no historical NFL prop lines in the repo: no number from this replay is edge
   against a price, and every board row stays `validated: false` until CLV against banked closes says otherwise.

## What the first replay found (2021-2025, 36,826 player-stat rows, paired, standard errors in parentheses)

- **The shipped engine beats the trailing baselines.** vs the L5 mean, log score +0.144 (0.041) pass yds, +0.179 (0.074)
  rush yds, +0.191 (0.024) rec yds, +0.039 (0.003) receptions, +0.038 (0.004) pass TDs, +0.043 (0.003) anytime TD; MAE
  -2.7 / -1.2 / -1.2 yds, -0.067 rec, -0.028 TD. (Season mean baseline: same direction.)
- **The opponent factor is worth almost nothing.** Removing it moves log score by -0.0003 to +0.0028 (largest: rec_yds,
  z ~ 2.3). Kept because it is harmless and strongly shrunk, but nothing should be built that assumes it carries signal.
- **Parameter variants** (selected on 2021-23, scored on 2024-25; the same pick re-found under a 2021-22 / 2023-25 split):
  `rec_yds` prefers a long memory and no usage blend (half-life 7, weight 1.0): held-out log score +0.027 (0.007), MAE
  -0.23 yds. `anytime_td` prefers half-life 7 / weight 0.3: log score +0.012 (0.002), Brier -0.0033 (0.0008). `pass_tds`
  pointed the same way but missed the multiplicity bar (z = 2.5). Adopted: `rec_yds`, `anytime_td`.
- **Every trailing-average projection over-projects, in every season.** Sum of outcomes / sum of projections is 0.93-0.99
  for all six stats in all five seasons -- for the plain L5 mean too: the quoted pool is selected on high trailing usage
  and regresses. Walk-forward scale `k` (fitted on earlier seasons): rush yds 0.96, rec yds 0.96, receptions 0.96, anytime
  TD 0.96, pass TDs 0.99 (immaterial), pass yds 0.98 (not shipped: 4/5 seasons, z 1.9). MAE improves 5/5 seasons for rush
  yds (-0.31 yds), rec yds (-0.37), receptions (-0.023), anytime TD (Brier -0.0003).
- **The normal family's spread must stay tied to the UNCALIBRATED mean.** Scaling the mean down and the spread with it makes
  log score worse (rec yds -0.003); keeping the spread gives +0.002. `cv` was measured against raw trailing means.
- **The spread/total game term adds to plain scaling for two stats** (5/5 seasons): `rec_yds` (MAE -0.08 yds, Brier
  -0.0009; b1 +0.24 on ln(implied total/22.5), b2 -0.016 on spread/7) and `anytime_td` (Brier -0.0005; b1 +0.27).
  Higher implied team total -> more receiving production, as it should. It did not add for rush yds, receptions, pass TDs or
  pass yds.
- **nflfastR-style play-by-play context features** (neutral pass rate, PROE, pace, EPA, explosive / sack rates, deep and
  red-zone usage) added to the XGBoost research models gave NO measurable out-of-sample gain (paired ablation 2021-2025: all
  six stats inconclusive, `anytime_td` slightly worse at z ~ -2.5). They remain available but off
  (`include_pbp_ctx`; `python -m nfl.models.ablation`).
