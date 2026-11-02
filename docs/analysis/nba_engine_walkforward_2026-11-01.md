# NBA serving engine: walk-forward evidence (2026-11-01)

Branch `claude/nba-props-quantitative-upgrade-816ba8` (PR #147) introduced the hierarchical NBA projection engine; the
follow-up branch (`claude/nba-props-walkforward-dvp-hurdle`) adds the replay harness (`nba/props/engine_eval.py`), four more
candidate features and the tuning below. **This measures ACCURACY, not edge**: the box-score master has no prop lines, so there is no
CLV or ROI here.

## Method
- Data: the NBA player box-score master, 2019-10 .. 2026-11 (318k rows). Eligible population = the board's: average minutes
  over the last 10 games >= 20 (16,483 player-games in 2023-24; 34,169 in 2024-25 + 2025-26).
- Replay: the engine's own functions (`project_mu`, `GameLogs.context`, `_nba_projection`, `p_over`) per player-game, using
  only rows strictly before that game; league context (opponent factors, dispersion) rebuilt each calendar month from earlier
  rows; history window = current + previous season (as production).
- Selection discipline: every parameter was SELECTED on 2023-24 and only then scored on 2024-25 and 2025-26. Adopt bar:
  paired log-score gain over the baseline with z >= 2.81 (player-clustered SE, Bonferroni over 10 stats) AND an MAE gain > 0.
- Metrics: MAE, bias, mean log score of the projection's own distribution (discretised Normal / NegBin / ZINB), Brier of
  P(over) at a PROXY line (floor(L10 mean) + 0.5; not a book line).

## Limits that bound every number below
1. **Absences are ORACLE**: a rotation regular (L10 >= 12 mpg) the box score lists as not playing is treated as Out with
   probability 1. Live there is only the injury report (Questionable = a probability), so absence-driven gains are an UPPER BOUND.
   The old engine got the same oracle absences (rotation-only), which favours it.
2. **No spreads** in the master: the blowout trim and the overtime term are NOT exercised (spread = None), so they remain
   unvalidated spec priors.
3. Accuracy only, one data source, two held-out seasons. Magnitudes of the adopted pool/shot-style factors are tiny.

## What the evidence said
| Candidate | Tune 2023-24 | Held-out 2024-26 | Decision |
|---|---|---|---|
| PR #147 engine vs the old engine | better on points / threes / all combos, worse on steals / blocks | same: points z=+2.75, rebounds +4.1, assists +3.5, threes +6.9, pr/pa/ra/pra z=+10.6..+20.5; steals -5.4, blocks -4.4 | PR #147 stands, but see boost strength |
| Absence boost at the spec's elasticities (0.55-0.65) | made MAE WORSE on every stat vs no boost (points -0.125 MAE) even with oracle absences; best-fit scale ~0.25 | n/a | too strong |
| Boost scale 0.4 of spec, steals / blocks excluded (`eta_0.4`) | best summed log score of {0.15, 0.25, 0.4, 0.6} | beats the spec-strength boost on EVERY stat (z = +4.2..+14.0); points MAE -0.13 | **ADOPTED** |
| Stale-absence discount (absent teammate counts only for the share of the player's last 10 games he played) | motivated by the first smoke run | in every number above | **kept** (fixes double counting) |
| Team-makes factor for assists (`shot_0.5`) | z +0.9 (assists) | on top of eta_0.4: assists z=+3.01, pa +3.17, pra +2.93 (ra +2.59) | **ADOPTED** (tiny: +0.0006 log score) |
| Missed-shot-pool rebounds (`pool_0.5`) | z ~ 0 | on top of eta_0.4: rebounds z=+2.42, ra +2.59, pr +1.18 | **REJECTED** (below the bar once the boost is right-sized) |
| Positional defense-vs-position (`dvp_k` 75 / 150 / 300) | no gain (z -2.2..+0.8) | not run | **REJECTED** |
| Zero-inflated steals / blocks | worse (steals z -4.0) | not run | **REJECTED** |

Overall (final shipped configuration vs the old engine, 2024-26): log-score gains on all 10 stats, z = +5.6 (points) .. +23.0
(`ra`); MAE better on all 10 (points -0.17, `pra` -0.29). Because the shipped config is `eta_0.4 + shot_0.5` and the confirmation
run scored `eta_0.4 + pool_0.5 + shot_0.5`, rebounds / `pr` / `ra` / `pra` for the shipped config sit between the `eta_0.4`-only
and the three-factor rows (both in the appendices); assists and `pa` are identical to the confirmed rows.

## Not changed by this evidence (still unvalidated spec priors)
Blowout curve and starter trim, back-to-back trim, foul-trouble weight, overtime regime (variance only), shot-diet points
variance scale, correlation priors for the combos, `BLEND_C_BY_SPORT_STAT` (display-only), the opt-in `--coherent-combos`
switch in `nba/props/score.py` (trained-model pipeline; not run against prop lines).

## Reproduce
```
python -m nba.props.engine_eval --master PLAYER_BOXSCORES_MASTER.csv --phase tune --seasons 2023 --out tune.md
python -m nba.props.engine_eval --master PLAYER_BOXSCORES_MASTER.csv --phase test --seasons 2024,2025 \
    --chosen "eta_0.4,eta_0.4+pool_0.5+shot_0.5" --out test.md
```
Appendices (full per-stat tables): `docs/analysis/nba_engine_walkforward/`.
