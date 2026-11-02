# NHL hierarchical prop engine: what changed and what the replay shows (2026-11-01)

Code: `nhl/props/hierarchical.py`, `ev/daily_projections.py` (`project_nhl`, as-of `GameLogs`), `nhl/data/powerplay.py`.
Replay: `python -m scripts.nhl_engine_replay --test-seasons 2024,2025 --date-stride 2` (raw numbers in
`nhl_hierarchical_engine_2026-11-01_results.json`). ACCURACY evidence only: there are no historical NHL prop lines in this
path, so nothing here is edge against a price and every board row stays `validated: false`.

## Method

Serving functions called as of each game date, with history, league contexts (alpha / cv / opponent factors), team shot
pace and league save % all cut to games strictly before the date. Legacy = the engine this replaced (generic EWM + usage +
opponent factor, plus the old goals / points finishing regression), scored on the same player-games. Score = mean log
P(actual) under each engine's own distribution (NegBin for counts; Normal vs the new two-regime mixture for saves, +-0.5
window), paired, standard error of the paired difference. Goalies are scored on starts only (the board prices probable
starters; relief appearances made the first run look 1 save biased). Parameters were chosen on 2023 only; 2024-25 were never
used for selection.

## Result, 2024 + 2025 (every other game date, rotation-level players)

| stat | n | log score new / legacy | diff (z) | MAE new / legacy |
|---|---|---|---|---|
| sog | 37,327 | -1.6287 / -1.6287 | 0.0000 | 1.0974 / 1.0974 |
| goals | 37,327 | -0.4962 / -0.5158 | **+0.0196 (10.8)** | 0.2907 / 0.2892 |
| assists | 37,327 | -0.7391 / -0.7435 | **+0.0043 (17.3)** | 0.4538 / 0.4536 |
| points | 37,327 | -0.9264 / -0.9325 | **+0.0061 (7.3)** | 0.5727 / 0.5732 |
| pp_points | 37,327 | -0.3442 / -0.3590 | **+0.0147 (6.2)** | 0.2030 / 0.1984 |
| saves | 2,848 | -3.3925 / -3.4148 | **+0.0223 (5.1)** | 5.48 / 5.66 |
| blocked_shots (spec pace factor, REVERTED) | 37,327 | -1.2309 / -1.2301 | **-0.0008 (-2.6)** | 0.7969 / 0.7966 |

- Log score is the proper score here. MAE of a mean against a sparse count is not (goals / pp_points MAE gets slightly worse
  while the whole distribution gets better: the shrunk means stop over-projecting streaky players).
- `legacy pp_points` = the same estimator with no shrinkage, i.e. there was no legacy market; the comparison isolates the shrinkage.
- SOG is unchanged by construction: same estimator, no absence data in the replay.
- **Blocked shots got very slightly WORSE** with the spec's opponent shot-pace factor than with the generic opponent factor
  (-0.0008 log score per row, 2.6 SE, MAE flat), so the factor was REVERTED: blocks use the opponent-allowed factor, i.e. the
  legacy path, and the shipped engine ties legacy on this stat by construction. The row above is the measured spec variant
  (kept in the results JSON). `hierarchical.pace_factor` still feeds the saves row's displayed `opp_factor`.
- Saves: bias +0.23 (legacy) -> -0.06; the gain comes from the measured start share and the rolling league save %.

## Absent-teammate redistribution (hindsight upper bound)

`--oracle-absences` feeds the game's ACTUAL non-dressers (information the live engine does not have):
sog +0.0004 log score (z 1.3, MAE worse), assists +0.0014 over the no-absence run (0.0057 vs 0.0043), goals / points
unchanged. Even with perfect knowledge the mechanism is marginal, and the live version only sees roster membership (which
flagged exactly one top-line player, Larkin, in a 2026-11-01 probe; the rest were fringe call-ups). Treat it as plumbing for
a future injury feed (`extra_absent`), not as an edge.

## Where the spec's numbers were replaced by measured ones (2021-23, never the report seasons)

| constant | spec | used | measured |
|---|---|---|---|
| starter share of team shots against | 0.965 | 0.970 | 0.967 / 0.974 / 0.970 (2021-23); 0.976 in 2024-25 |
| league save % prior | fixed .903 | rolling league rate | .908 (2021) -> .897 (2025) -> .891 (2026 so far) |
| P(starter plays < 55 min) | 0.08 | 0.066 | 7.2% / 6.3% / 6.2% |
| saves of such a starter | 16, sd 4.5 | 13.8, sd 7.3 | 14.5 / 14.2 / 12.6, sd ~7 |
| PP points / game, forwards / D | n/a | 0.180 / 0.077 | 2022-23, TOI >= 14 |
| pp_points half-life, prior strength | n/a | 30 games, 6 games | best of 8/15/30 x 6/12/24/48 on 2023 |

Kept as specified (not tuned, spec values): shooting-% prior K = 85 and 0.112 / 0.055, goals blend 0.55 / 0.45, assists
half-life 10, saves EB K = 400, total-cv floor 0.24, vacate damp 0.50 and the 16.0 / 18.5 TOI thresholds.

## Not built, because the data is not here

- Power-play / even-strength TOI splits, PP1 vs PP2 deployment: the boxscore has total TOI only (needs shift charts).
- Corsi / Fenwick per player in the live path (shots master has attempts; wiring it needs its own evidence run).
- Confirmed starting goalie, back-to-back goalie flag, empty-net-goal term for `goals`, injuries.
- The spec's `powerPlayPoints` boxscore field does not exist: see `docs/solutions/integration-issues/nhl-boxscore-has-no-power-play-points.md`.
- `BLEND_C_BY_SPORT_STAT` sog 0.30 is the coefficient measured for the TRAINED SOG model (c = 0.31, t = +4.9), applied to this
  empirical engine as a display-only prior (it feeds neither edge, confluence nor Beezy Score).
