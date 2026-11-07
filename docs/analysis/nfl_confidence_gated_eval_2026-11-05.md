# NFL game engine: does it beat the close when it is confident? (2026-11-05)

Research only. Nothing deployed, no serving engine changed, nothing written to GCS or the bets table. Every number is UNVALIDATED.
Shared protocol: `docs/analysis/gated_eval_methodology_2026-11-05.md` (PR `feat/gated-eval-core`). Baseline: `docs/analysis/game_line_harness_2026-11-04.md` (#183).
Superseded combined run: #196 (NFL and NBA in one PR); the NBA half is its own PR and doc.

## Verdict (NFL)
**INCONCLUSIVE, with no evidence for any gate.**
- 0 of 14 gate cells have a positive held-out log-loss gain versus the close; 0 of 14 passed the dev screen, so the confirmatory family was empty. Max-T adjusted p over the 14 cells: min 0.991.
- Confidence makes it worse: the EDGE gate (top quartile of |model - market|) is -0.116 / -0.080 / -0.148 on moneyline / spread / total, against -0.040 / -0.033 / -0.042 ungated.
- The label is INCONCLUSIVE rather than NO only because the gates are small (79-268 held-out games, minimum detectable gain 0.037-0.15 nats per game): they can say there is no LARGE edge, not that there is no modest one.
- Settling it by backtest is not feasible: detecting a true +3% ROI at 80% power needs 4,000-11,000 gated games (about 50-280 NFL seasons at a 15-30% gate). Only a forward test with one frozen gate and timestamped picks can.

## What was run
- Replay: `nfl.game.market_eval.replay` (Kalman ratings + PBP features + key-number matrix, game by game from 2018; state for a game uses only earlier games), scored against nflverse closing spread / total / moneyline and their prices (one price per game, undocumented book, so best price = close price).
- Splits (fixed first): dev 2021-2023, held-out 2024-2025. Gates (one threshold each, dev-quantile, outcomes never read): EDGE, SD (Kalman rating sd), AGREE (Kalman-only margin, PBP-feature margin and the blend on the same side of the line as the bet; not applicable to the total, which has one variant), PHASE (week >= 6), COMBO (EDGE and PHASE and AGREE where applicable). The DQ gate does not exist for the NFL replay (no data-quality flag), so 3 markets x 6 gate slots = 18, 14 applicable.
- Number of gate variants tried: **14 cells**; max-T sign-flip penalty is over all 14, Holm over survivors (none).
- Provenance note: the held-out split was first scored inside #196 jointly with the NBA (family of 29). This directory is the per-sport re-run from the same records file and the same frozen thresholds (the dev freeze reproduces); every cell statistic is identical to the digit, only the max-T family changed (14 cells, min p 0.991 vs 1.000 over 29). The disclosure that matters: I had already seen the UNGATED all-season results of #183 (the engine loses overall), so the held-out seasons were not blind in aggregate, only to every gated number.

## Held-out results (every cell)
Positive LL gain = engine better than the close. `ctl best` = ROI of both sides of everything in that subset at the best price (the vig a no-model bettor pays).

| market | gate | n | cover | LL gain | 95% CI | z | MDE80 | c (z) | ROI close [95% CI] | ROI best | ctl best | gap rho | p maxT | p Holm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| moneyline (home wins) | NONE | 543 | 100% | -0.0398 | [-0.0638, -0.0158] | -3.25 | 0.0304 | +0.02 (+0.1) | -5.8% [-14.6%, +3.1%] | -5.8% | -6.5% | -0.50 | - | - |
| moneyline (home wins) | EDGE | 157 | 29% | -0.1160 | [-0.1945, -0.0375] | -2.89 | 0.0996 | -0.01 (-0.1) | -8.2% [-28.3%, +11.9%] | -8.2% | -5.2% | +1.00 | 1.000 | - |
| moneyline (home wins) | SD | 166 | 31% | -0.0256 | [-0.0592, +0.0080] | -1.50 | 0.0426 | +0.18 (+0.9) | -1.5% [-15.4%, +12.4%] | -1.5% | -2.6% | +1.00 | 1.000 | - |
| moneyline (home wins) | AGREE | 268 | 49% | -0.0699 | [-0.1087, -0.0310] | -3.52 | 0.0493 | -0.01 (-0.1) | -11.2% [-23.0%, +0.5%] | -11.2% | -8.4% | +0.50 | 1.000 | - |
| moneyline (home wins) | PHASE | 388 | 71% | -0.0507 | [-0.0799, -0.0215] | -3.41 | 0.0370 | -0.14 (-0.8) | -10.8% [-21.6%, +0.1%] | -10.8% | -8.7% | -0.50 | 1.000 | - |
| moneyline (home wins) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| moneyline (home wins) | COMBO | 94 | 17% | -0.1469 | [-0.2467, -0.0471] | -2.89 | 0.1266 | -0.12 (-0.5) | -17.3% [-43.2%, +8.5%] | -17.3% | -9.5% | +0.50 | 1.000 | - |
| spread (home covers) | NONE | 539 | 100% | -0.0326 | [-0.0591, -0.0061] | -2.41 | 0.0336 | +0.18 (+1.5) | +1.2% [-6.6%, +8.9%] | +1.2% | -4.4% | +0.50 | - | - |
| spread (home covers) | EDGE | 155 | 29% | -0.0798 | [-0.1655, +0.0059] | -1.82 | 0.1087 | +0.21 (+1.5) | +7.5% [-7.5%, +22.5%] | +7.5% | -4.4% | +0.50 | 1.000 | - |
| spread (home covers) | SD | 165 | 31% | -0.0067 | [-0.0524, +0.0391] | -0.29 | 0.0580 | +0.42 (+1.7) | +7.8% [-5.9%, +21.5%] | +7.8% | -4.3% | +0.50 | 0.991 | - |
| spread (home covers) | AGREE | 267 | 50% | -0.0573 | [-0.1048, -0.0099] | -2.37 | 0.0602 | +0.17 (+1.3) | -0.4% [-9.9%, +9.0%] | -0.4% | -4.4% | +1.00 | 1.000 | - |
| spread (home covers) | PHASE | 384 | 71% | -0.0443 | [-0.0754, -0.0133] | -2.80 | 0.0394 | +0.08 (+0.6) | -2.3% [-11.6%, +7.0%] | -2.3% | -4.5% | +0.50 | 1.000 | - |
| spread (home covers) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| spread (home covers) | COMBO | 96 | 18% | -0.0958 | [-0.2059, +0.0143] | -1.70 | 0.1397 | +0.19 (+1.2) | +10.1% [-7.1%, +27.2%] | +10.1% | -4.4% | -0.50 | 1.000 | - |
| total (over) | NONE | 541 | 100% | -0.0418 | [-0.0679, -0.0158] | -3.15 | 0.0330 | +0.11 (+0.9) | -1.0% [-9.2%, +7.2%] | -1.0% | -4.5% | +0.50 | - | - |
| total (over) | EDGE | 104 | 19% | -0.1483 | [-0.2479, -0.0487] | -2.92 | 0.1264 | +0.07 (+0.5) | +1.0% [-14.0%, +16.0%] | +1.0% | -4.6% | +0.50 | 1.000 | - |
| total (over) | SD | 166 | 31% | -0.0240 | [-0.0747, +0.0266] | -0.93 | 0.0643 | +0.26 (+1.1) | -2.0% [-19.6%, +15.7%] | -2.0% | -4.4% | +1.00 | 1.000 | - |
| total (over) | AGREE | - | not applicable / degenerate | | | | | | | | | | | |
| total (over) | PHASE | 385 | 71% | -0.0432 | [-0.0743, -0.0121] | -2.72 | 0.0395 | +0.11 (+0.8) | +0.3% [-8.5%, +9.1%] | +0.3% | -4.5% | -0.50 | 1.000 | - |
| total (over) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| total (over) | COMBO | 79 | 15% | -0.1446 | [-0.2640, -0.0252] | -2.37 | 0.1515 | +0.08 (+0.5) | +1.7% [-16.0%, +19.5%] | +1.7% | -4.5% | n/a | 1.000 | - |

- No cell has a positive LL gain. Least bad: spread SD -0.007 [-0.052, +0.039], total SD -0.024, moneyline SD -0.026.
- Blend c: no cell reaches z > 1.645 (max spread SD c = +0.42, z +1.67; about 0.7 of 14 cells would cross 1.645 by chance).
- Spread ROI at the close looks positive in three cells (EDGE +7.5%, SD +7.8%, COMBO +10.1%; control -4.4%) but every 95% CI spans roughly -7% to +27% on 96-165 games and the same cells lose on log loss.
- Inside a gate the edge terciles have 30-90 games each, so the monotonicity rho is not interpretable; the cross-gate dose-response (EDGE worse than ungated in 3 of 3 markets) is the usable signal and points the wrong way.

## Power
Minimum detectable log-loss gain: ungated 0.030-0.034, gated 0.037-0.15 nats per game. Minimum detectable ROI at the close 11-33%. A break-even-sized gain (0.001 nats) needs 0.3-1.9 million games.

## Leakage garble test (NFL replay)
`nfl/game/leak_check.py`: scramble every play-by-play column except ids / dates / teams, and the schedule's final scores, for every game on or after the cut-off; re-run the replay; predictions on or before the cut-off must be identical.

| cut-off | games on or before | predictions changed before | predictions changed after (power) |
|---|---|---|---|
| 2024-10-20 | 105 | 0 | 437 |
| 2025-10-26 | 392 | 0 | 151 |

**No leak.** Injected-leak unit test (an oracle reading the game's own final margin) is caught. The first real-data run reported 3 and 1 changed predictions before the cut-off; they were games MISSING from the garbled run, because the garble had permuted the schedule's final scores including missing ones and moved a NaN onto a played game. `garble_columns` now permutes only non-null cells (core PR), and the re-run above is clean. Results: `nfl_confidence_gated_eval_2026-11-05/garble_results.json` (re-run from this branch's own code after the `engine` -> `nfl_engine` rename: identical counts).

## Limits
- The replay calls `compute_matchup_pbp_features` without `schedules`, so bye / short-week / divisional / wind and QB terms sit at defaults: not the engine as the live board runs it.
- Closing lines are nflverse closes with an undocumented source book; no CLV (no open-to-close pair); no Pinnacle.
- The gate family is mine. Injury flags and engine versions across retrains are not testable here (no injury input, no second version).
- Single replay, parameters as shipped; the SESOI (0.001 nats) comes from a ~4.5% vig, not from this data.

## Reproduce
    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025 --records-csv nfl_rec.csv
    python -m nfl.game.gated_eval dev  --records nfl_rec.csv --out-dir docs/analysis/nfl_confidence_gated_eval_2026-11-05
    python -m nfl.game.gated_eval held --records nfl_rec.csv --out-dir docs/analysis/nfl_confidence_gated_eval_2026-11-05
    GCS_BUCKET=<bucket> python -m nfl.game.leak_check --seasons 2024-2025 --cutoffs 2024-10-20,2025-10-26
Committed outputs in `docs/analysis/nfl_confidence_gated_eval_2026-11-05/`: `frozen.json`, `dev_report.md`, `dev_results.json`, `held_report.md`, `held_results.json`, `garble_results.json`.
