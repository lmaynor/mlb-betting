# Confidence-gated evaluation: shared methodology (2026-11-05)

Infra only: `mlb_core/eval/gating.py`, `gated_run.py`, `garble.py`. Sport-agnostic, no sport package imported, nothing deployed, UNVALIDATED research tooling.
Each sport's adapter, replay glue, results and verdict live in that sport's own PR / doc (`docs/analysis/nfl_confidence_gated_eval_2026-11-05.md`,
`docs/analysis/nba_confidence_gated_eval_2026-11-05.md`). Pointer only: the pre-registered outcome of the first runs was NBA = NO, NFL = INCONCLUSIVE.

## Question
"The model does not need to beat the market on every game, only when it is confident." Legitimate and testable, and a classic garden of forking paths, so the
protocol is fixed in code before any outcome is scored.

## Event frame (what an adapter supplies)
One row per (game, market) event: `date, p_model, p_mkt, y, season`, price columns `dec_{a,b}_{med,best}` (decimal odds of the event / its opposite at the median and best
price), and pre-game confidence inputs `c_kal c_feat c_mu` (component and blended expected margin or total on the line's scale), `c_line` (the market line on that scale),
`c_sd` (rating uncertainty), `c_phase` (games played this season), `c_dq` (1 = hole in recent data; NaN if the sport has no such flag). Scoring itself is
`mlb_core.eval.market_scoring` (date-clustered paired log loss, de-vigged close).

## Gates (one threshold each; thresholds are never tuned on outcomes)
| gate | rule |
|---|---|
| EDGE | `abs(p_model - p_mkt)` in the top quartile of the DEV distribution, per market |
| SD | `c_sd` in the lowest DEV tercile, per market |
| AGREE | `c_kal`, `c_feat` and `c_mu` on the same side of `c_line` as the bet. Not applicable where `c_feat` is missing |
| PHASE | `c_phase >= phase_min`, a constant the adapter fixes up front |
| DQ | `c_dq == 0`; declared degenerate (not tried) when it keeps more than 98% of games |
| COMBO | EDGE and PHASE and (AGREE where applicable) |

## Pre-registration
1. `freeze_thresholds` reads DEV-split features only (a test pins that the outcome column is never read) and returns the frozen dict.
2. DEV screen: a cell advances only if its dev log-loss gain > 0, dev blend c > 0 and n >= 100. Held-out is scored once, for every cell (exhibit) and, as the confirmatory
   family, for the survivors (Holm).
3. `gated_run.run_held` refuses to run without an unedited `frozen.json` made from the same records file and the same season split, and refuses a second look
   unless `allow_rescore` is passed (the report then says "RE-SCORED").
4. Chronological splits only (`split_by_season` rejects overlap). Commit the protocol before computing results, and say what you had already seen.

## Multiplicity
Holm over the dev survivors, plus a max-statistic sign-flip over EVERY cell tried (the honest penalty for the forks): one random sign per game day shared across cells
(overlapping gates stay dependent), date-clustered z recomputed per flip, 4,000 flips; a cell's adjusted p is the share of flips whose maximum z reaches its observed z.
Null: paired log-loss differences are symmetric about 0 within a day. The family is one sport's cells (each sport is siloed).

## Per-cell report
Coverage; paired log loss vs the close with 95% CI (positive = model better); blend c with the market coefficient fixed at 1 (identified on spread / total where `p_mkt` is
about 0.5) and robust z; minimum detectable effect (one-sided 5%, 80% power) and games needed to reach a break-even-sized gain (0.001 nats, about a 4.5% vig); edge-tercile
monotonicity inside the gate; flat-stake ROI of the model's side at the close (median) and best price with clustered 95% CI; the no-model both-sides control.
Verdict rule: YES = a survivor is confirmed (Holm p < .05, c z > 1.645, ROI at close > 0); NO = nothing confirmed and every cell's 95% upper bound on the gain is below
0.001 nats; INCONCLUSIVE otherwise.

## Leakage garble test (`garble.py`)
For a cut-off day T, scramble every post-kickoff field of every game on or after T (final scores, box / play-by-play statistics; never ids, dates, teams or lines), re-run the
replay, and require the predictions for every game on or before T to be identical. A power check requires predictions after T to change. `garble_columns` permutes only
non-null cells: moving a missing score onto a game made a replay skip it and masqueraded as a leak on the first real NFL run. Each sport's adapter supplies the replay and
the list of columns to scramble, and should include an injected-leak test (an oracle reading the game's own result) to show the test has power.
