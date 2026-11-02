# Handoff -- 2026-11-01 -- hierarchical NFL prop engine (own PR, shipped OFF)

Branch `claude/nfl-props-generative-model-dafff0`. **Nothing deployed, nothing written to GCS / the bets table, no scheduler or job changed.**
The serving board is unchanged until someone sets `NFL_ENGINE=hierarchical` on the `daily-projections` job.

## What was built
- `nfl/models/hierarchical_props.py` -- pure pricers (numpy / scipy, no I/O). 10 stats as one process: attempts (3-regime script mixture) ->
  completions (Beta-Binomial over the attempts pmf, P(C > A) = 0) -> pass yds; carries -> rush yds; targets -> receptions -> rec yds
  (coverage-shell target multiplier, aDOT-driven spread); pass TDs / INTs; anytime TD (3-zone, team-TD anchored). Compound yards are hurdle-gamma
  by exact total-variance moments. All coefficients live in `PRIORS` and are hand-set.
- `nfl/models/hierarchical_live.py` -- adapter from the engine's game-log rows (+ optional `LiveContext`: injuries, pbp, participation, snaps)
  to the pricers; `price_row` returns None (legacy price kept) on thin history or any failure.
- `nfl/features/context_gaps.py` -- coverage-shell rates, receiver / passer profiles, trench + OL health, `net_pressure_mismatch`, practice-weighted
  absences, role-based redistribution. Every as-of function filters to games strictly before the cutoff week first.
- `nfl/data/weather.py` -- 32-stadium table, closed roofs neutral (no network), cached Open-Meteo kickoff forecast (2 s timeout, never raises).
- `ev/daily_projections.py` -- `engine_id()`, `--nfl-engine`, `NFL_ENGINE`, per-row branch in `make_row`; `EnvTuple` now carries season / week / home;
  `env_adjust.game_env_table` gained `home_team`; `cockpit_form._load_nfl` reads 5 optional extra player_stats columns (air yards, YAC, CPOE).
- `nfl/props/engine_eval.py` -- `--hierarchical`: paired replay vs the shipped baseline (log score on identical 1-unit bins, Brier, CRPS, MAE, tail
  calibration at fair +200 / +400, per-season consistency, ADOPT verdict).
- `nfl/features/props_features.py` -- `team_missing_*_l5` now status/practice weighted (+ rz-touch and air-yards columns). Experimental, not in
  default model features.
- Tests: `tests/test_nfl_props_hierarchical.py` (46) plus updates to `tests/test_nfl_features.py` for the new weights.
- Docs: CONTEXT s20 entry, `docs/solutions/conventions/nfl-hierarchical-engine-adoption.md`.

## Decisions I made (reverse any of them)
1. **Default OFF.** The spec said to wire it into the live board; the repo's own rule requires held-out evidence for engine changes and there is none.
2. **`team_spread` sign:** hierarchical = negative favoured (spec); the engine's env tuple = positive favoured. Converted at the boundary.
3. **`net_pressure_mismatch` is a ratio (1.0 = league)**, not the spec's literal rate-unit expression, because the interception / completion terms are written for a ratio.
4. **Spec's "hard conservation" vs "0.65 / 0.35 shrinkage":** they conflict; implemented the shrinkage (`anchor_w = 0.35`), hard conservation is `anchor_w=1.0`.
5. **Questionable with no practice report = 0.25** (`Q_UNKNOWN_PRACTICE`), an assumption the spec does not cover.
6. **Live path has no red-zone split** in the game logs; vacated touches stand in for vacated red-zone share. `ftn_charting` is registered but unused.
7. The calibration scale is applied to the VOLUME input (TD stats: scale only).
8. Retractable-roof and canopy stadiums (ATL, DAL, ARI, HOU, IND, LAR, LAC) are treated as neutral per the spec unless the schedule says `open`.

## NOT verified
- ~~The comparison has not been run~~ -- it HAS now: see "Real-data replay" below. Still not run: live GCS service path, Open-Meteo, the daily-projections job.
- Nothing ran against live GCS, the nflverse mirror, Open-Meteo or the real participation schema (column names are from nflreadr's dictionary:
  `defense_coverage_type`, `defense_man_zone_type`, `was_pressure`; unverified against a downloaded file).
- Cost of `LiveContext.load` (2 seasons of pbp per `daily-projections` run) was not measured; `NFL_HIER_CONTEXT=0` skips it, `NFL_WEATHER_FETCH=0` skips the forecast.
- Tests ran on a fresh venv (Python 3.14, pandas 3.x, scipy 1.18), not the pinned requirements.

## Open / next
- If the replay clears the bar for only some stats, add a per-stat allowlist (today the engine is all-or-nothing per NFL row).
- vs a same-moment Normal the gamma is lighter just above the mean and heavier only ~1.5 sd out (rush 100.5 lighter, 120.5+ heavier): the alt-ladder
  claim needs the real comparison, not the argument.
- Re-run `engine_eval --write-calibration` is unaffected; the hierarchical engine has no calibration of its own beyond the shipped scale.

## Real-data replay (2024-2025 held out, 18,992 scored player-stat rows; GCS mirror; FINAL numbers)
`python -m nfl.props.engine_eval --seasons 2024-2025 --hierarchical [--hier-context]`; baseline = the shipped engine incl. calibration.
Priors were tuned on 2021-2023 ONLY (7 + 5 parallel replays, paired log score); 2024-2025 was never used for selection. Log score,
hierarchical minus baseline (se):

| stat | no context | with context | verdict (strict rule) |
|---|---|---|---|
| pass_attempts | -0.109 (0.017) | same | WORSE |
| pass_completions | -0.085 (0.016) | -0.085 | WORSE |
| pass_yds | -0.039 (0.013) | -0.043 | WORSE |
| pass_tds | +0.024 (0.007) | +0.024 | **ADOPT** (Brier z 1.9) |
| interceptions | +0.018 (0.011) | +0.017 | no clear gain |
| rush_attempts | +0.037 (0.008) | +0.042 (0.009) | ADOPT only with context (Brier z 1.9) |
| rush_yds | +0.066 (0.015) | +0.071 (0.016) | **ADOPT** (CRPS -0.30 / -0.42) |
| receptions | -0.003 (0.003) | -0.010 (0.004) | no clear gain; context hurts |
| rec_yds | +0.177 (0.013) | +0.171 (0.013) | no clear gain: log score huge, but Brier z only ~1.3 / 0.8 |
| anytime_td | -0.000 (0.001) | +0.002 (0.001) | parity |

- **Two harness artifacts inflated the first report and are fixed**: anytime_td was scored as a count pmf for one engine and a 0/1 indicator for the
  other; and the baseline's log score floors mu at 0.05 while the hierarchical side had no floor (rows with ~0 projected TDs that scored cost -20 nats).
  The first "anytime_td WORSE" and the "wider attempt spread is better" tuning signal were both artifacts of those.
- MAE is still essentially unchanged on every stat (rec_yds with context is worse: 25.84 vs 25.14): any gain is DISTRIBUTION SHAPE, not a better mean.
- **Context (injuries, coverage, OL) still does not help.** After the absence discount (a teammate's share is scaled by the fraction of the team's recent games
  he has NOT already missed) the over-projection shrank, but receptions / rec_yds MAE and tail calibration (rec_yds +200: 0.296 vs realised 0.276) are still
  worse with context than without it. Coverage-shell and OL terms are untested in isolation.
- Passing (attempts / completions / yards) is genuinely worse than the baseline's Normal / NegBin; widening the attempts distribution did NOT help once
  scoring was fair (default width 1.0 was best on train; narrower 0.65 / 0.8 not significantly different).
- Adopted-by-selection (train only): comp_kappa 15 (edge of grid, +0.012 +/- 0.005 on pass_completions, provisional), pass_td anchor 0.15.
- Bugs real data found and fixed: blank-string coverage tags diluted `def_man_rate`; `redistribute` divided by zero; role mismatch without air-yards.

## Per-stat allowlist (built)
`nfl/props/hierarchical_allowlist.json` = `pass_tds`, `rush_yds` (generated by `engine_eval --write-allowlist`: ADOPT on the 2024-25 no-context AND
context runs, not contradicted on 2026 weeks 1-8). `stat_enabled(stat) = NFL_ENGINE=hierarchical and stat in allowlist`; `NFL_HIER_STATS` overrides
for experiments. **2026 clean confirmation (8 weeks, 208-658 rows per stat; nothing tuned or selected on it):** pass_tds +0.026 (0.014), rush_yds +0.102
(0.034), rush_attempts +0.052 (0.023), rec_yds +0.171 (0.028); pass_completions -0.107 (0.030) WORSE, pass_yds -0.054, pass_attempts -0.094,
anytime_td -0.001. Brier z is below 1.645 on all four (n is small). The 2024-25 verdicts both selected the allowlist and tested it, so 2026 is the
only clean evidence and it is thin.
To turn it on: separate PR (stacked on #148) sets `NFL_ENGINE=hierarchical` + `NFL_HIER_CONTEXT=0` in `deploy/setup_daily_projections_job.sh`; it takes effect only when that script is re-run against an image containing this code. NOT done: nothing was built, tagged or deployed. WARNING: any rebuild from a tree containing main's #149 also ships the NHL hierarchical engine (ungated), so the image build is the owner's call, not part of the NFL work.

## Next (not done)
rec_yds: check Brier / CRPS at REAL book lines (the replay's proxy line is the L5 mean). rush_attempts: ADOPT only with context. Isolate which
context layer hurts receptions. Re-run the allowlist each offseason. Everything remains accuracy evidence, not edge.
