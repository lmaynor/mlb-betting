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
- **The walk-forward comparison has not been run on real data** (no mirrored data in this environment). The harness is tested on synthetic leagues only.
  Run: `PYTHONPATH=. python -m nfl.props.engine_eval --seasons 2024-2025 --hierarchical --hier-context --hier-cache /tmp/h.parquet`.
- Nothing ran against live GCS, the nflverse mirror, Open-Meteo or the real participation schema (column names are from nflreadr's dictionary:
  `defense_coverage_type`, `defense_man_zone_type`, `was_pressure`; unverified against a downloaded file).
- Cost of `LiveContext.load` (2 seasons of pbp per `daily-projections` run) was not measured; `NFL_HIER_CONTEXT=0` skips it, `NFL_WEATHER_FETCH=0` skips the forecast.
- Tests ran on a fresh venv (Python 3.14, pandas 3.x, scipy 1.18), not the pinned requirements.

## Open / next
- If the replay clears the bar for only some stats, add a per-stat allowlist (today the engine is all-or-nothing per NFL row).
- vs a same-moment Normal the gamma is lighter just above the mean and heavier only ~1.5 sd out (rush 100.5 lighter, 120.5+ heavier): the alt-ladder
  claim needs the real comparison, not the argument.
- Re-run `engine_eval --write-calibration` is unaffected; the hierarchical engine has no calibration of its own beyond the shipped scale.
