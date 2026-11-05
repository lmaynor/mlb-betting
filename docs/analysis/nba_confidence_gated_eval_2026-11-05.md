# NBA game engine: does it beat the close when it is confident? (2026-11-05)

Research only. Nothing deployed, no serving engine changed, nothing written to GCS or the bets table. Every number is UNVALIDATED.
Shared protocol: `docs/analysis/gated_eval_methodology_2026-11-05.md` (PR `feat/gated-eval-core`). Baseline: `docs/analysis/game_line_harness_2026-11-04.md` (#183).
Superseded combined run: #196 (NFL and NBA in one PR); the NFL half is its own PR and doc.

## Verdict (NBA)
**NO. No gate beats the closing line, and the data are large enough to say so.**
- 0 of 15 gate cells have a positive held-out log-loss gain; 0 of 15 passed the dev screen, so the confirmatory family was empty. Max-T adjusted p over the 15 cells: min 1.000.
- Every gate's 95% upper bound on the log-loss gain is at most +0.0007 nats per game (best: total with low Kalman uncertainty, -0.0176 [-0.0358, +0.0006]); a break-even-sized edge is about +0.001. Under the pre-registered rule that is NO.
- Confidence makes it worse: the EDGE gate (top quartile of |model - market|) is -0.067 / -0.096 / -0.103 on moneyline / spread / total, against -0.024 / -0.031 / -0.025 ungated. The engine's large disagreements with the line are mostly its own noise.
- What this does NOT rule out: a +3% ROI. ROI is far noisier than log loss (minimum detectable ROI 5-17% here); detecting a true +3% needs 6,000-16,000 gated games (about 16-67 NBA seasons at a 20-30% gate). Only a forward test with one frozen gate and timestamped picks could add information.

## What was run
- Replay: `nba.game.evaluate.evaluate_walk_forward` on the team-game history (2019-20 onward; the 2019-24 rebounds / turnovers backfilled from the player box, so `nan_stats` drops are 0 of 17,730 team-games), scored against BettingPros closing quotes (fresh books only, >= 3 books on the same main line, Consensus excluded, PHO/NOR/UTH mapped; median across books is the close, best across books the best price).
- Splits (fixed first; season = start year): dev 2021-22 .. 2023-24, held-out 2024-25 and 2025-26. NBA 2026-27 games (in progress) are excluded.
- Gates (one threshold each, dev-quantile, outcomes never read): EDGE, SD (Kalman rating sd), AGREE (Kalman-only, feature-side and blended margin / total on the same side of the line as the bet), PHASE (both teams >= 20 games played this season), COMBO. The DQ gate (no dropped box-score game in either team's last 10) is degenerate on every market (nothing is dropped after the backfill), so it is not tried: 3 markets x 6 slots = 18, **15 cells tried**.
- Max-T sign-flip penalty over all 15 cells; Holm over survivors (none).
- Provenance note: the held-out split was first scored inside #196 jointly with the NFL (family of 29). This directory is the per-sport re-run from the same records file and the same frozen thresholds; every cell statistic is identical to the digit, only the max-T family changed (15 cells). I had already seen the UNGATED all-season results of #183, so the held-out seasons were not blind in aggregate, only to every gated number.

## Held-out results (every cell)
Positive LL gain = engine better than the close. `ctl best` = ROI of both sides of everything in that subset at the best price (what line shopping alone earns a no-model bettor).

| market | gate | n | cover | LL gain | 95% CI | z | MDE80 | c (z) | ROI close [95% CI] | ROI best | ctl best | gap rho | p maxT | p Holm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| moneyline (home wins) | NONE | 2641 | 100% | -0.0242 | [-0.0339, -0.0144] | -4.86 | 0.0124 | +0.08 (+0.9) | -4.9% [-10.2%, +0.4%] | -1.2% | -0.5% | +1.00 | - | - |
| moneyline (home wins) | EDGE | 665 | 25% | -0.0668 | [-0.0992, -0.0343] | -4.03 | 0.0412 | +0.11 (+1.1) | -0.8% [-12.5%, +11.0%] | +3.5% | +4.8% | +1.00 | 1.000 | - |
| moneyline (home wins) | SD | 838 | 32% | -0.0295 | [-0.0480, -0.0110] | -3.12 | 0.0235 | +0.09 (+0.7) | -11.0% [-21.0%, -1.0%] | -7.5% | -4.6% | +1.00 | 1.000 | - |
| moneyline (home wins) | AGREE | 1772 | 67% | -0.0343 | [-0.0480, -0.0205] | -4.89 | 0.0174 | +0.06 (+0.7) | -4.9% [-11.5%, +1.8%] | -1.0% | +0.1% | +0.50 | 1.000 | - |
| moneyline (home wins) | PHASE | 2021 | 77% | -0.0323 | [-0.0434, -0.0213] | -5.74 | 0.0140 | -0.04 (-0.4) | -7.8% [-14.0%, -1.6%] | -4.2% | -0.0% | +1.00 | 1.000 | - |
| moneyline (home wins) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| moneyline (home wins) | COMBO | 500 | 19% | -0.0895 | [-0.1257, -0.0533] | -4.85 | 0.0459 | -0.01 (-0.1) | -8.9% [-22.0%, +4.1%] | -5.5% | +3.9% | +0.50 | 1.000 | - |
| spread (home covers) | NONE | 2556 | 100% | -0.0305 | [-0.0409, -0.0201] | -5.74 | 0.0132 | +0.06 (+0.8) | -4.5% [-8.1%, -0.9%] | -3.3% | -3.4% | +1.00 | - | - |
| spread (home covers) | EDGE | 677 | 26% | -0.0955 | [-0.1293, -0.0618] | -5.55 | 0.0428 | +0.02 (+0.2) | -4.7% [-11.9%, +2.6%] | -3.4% | -3.4% | -0.50 | 1.000 | - |
| spread (home covers) | SD | 822 | 32% | -0.0387 | [-0.0595, -0.0178] | -3.64 | 0.0265 | +0.05 (+0.5) | -6.1% [-13.0%, +0.8%] | -4.9% | -3.3% | +1.00 | 1.000 | - |
| spread (home covers) | AGREE | 1788 | 70% | -0.0434 | [-0.0579, -0.0288] | -5.85 | 0.0185 | +0.04 (+0.5) | -4.6% [-9.1%, -0.2%] | -3.4% | -3.4% | +0.50 | 1.000 | - |
| spread (home covers) | PHASE | 1957 | 77% | -0.0381 | [-0.0504, -0.0258] | -6.08 | 0.0156 | -0.02 (-0.2) | -6.7% [-11.0%, -2.4%] | -5.4% | -3.4% | +0.50 | 1.000 | - |
| spread (home covers) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| spread (home covers) | COMBO | 536 | 21% | -0.1121 | [-0.1504, -0.0739] | -5.75 | 0.0485 | -0.04 (-0.5) | -7.0% [-15.0%, +1.1%] | -5.8% | -3.4% | -0.50 | 1.000 | - |
| total (over) | NONE | 2416 | 100% | -0.0247 | [-0.0352, -0.0142] | -4.62 | 0.0133 | +0.08 (+0.9) | -2.4% [-6.2%, +1.4%] | -1.5% | -3.7% | +0.50 | - | - |
| total (over) | EDGE | 497 | 21% | -0.1034 | [-0.1460, -0.0609] | -4.77 | 0.0540 | -0.00 (-0.0) | -4.9% [-13.5%, +3.8%] | -3.8% | -3.6% | -0.50 | 1.000 | - |
| total (over) | SD | 766 | 32% | -0.0176 | [-0.0358, +0.0006] | -1.89 | 0.0231 | +0.21 (+1.4) | +0.1% [-6.5%, +6.8%] | +1.1% | -3.7% | +1.00 | 1.000 | - |
| total (over) | AGREE | 1544 | 64% | -0.0383 | [-0.0542, -0.0223] | -4.70 | 0.0202 | +0.05 (+0.6) | -2.7% [-7.6%, +2.2%] | -1.8% | -3.7% | +0.50 | 1.000 | - |
| total (over) | PHASE | 1838 | 76% | -0.0260 | [-0.0387, -0.0134] | -4.03 | 0.0161 | +0.09 (+0.9) | -2.0% [-6.5%, +2.4%] | -1.1% | -3.7% | +0.50 | 1.000 | - |
| total (over) | DQ | - | not applicable / degenerate | | | | | | | | | | | |
| total (over) | COMBO | 386 | 16% | -0.1064 | [-0.1574, -0.0553] | -4.08 | 0.0648 | +0.02 (+0.2) | -3.1% [-13.1%, +6.8%] | -2.1% | -3.6% | -0.50 | 1.000 | - |

- No cell has a positive LL gain. Least bad: total SD -0.018, total ungated -0.025, moneyline SD -0.030.
- Blend c: no cell reaches z > 1.645 (max total SD, c = +0.21, z +1.4). On dev the best was the same cell at z +1.9, and it still lost on log loss (-0.016).
- ROI at the close is negative or indistinguishable from the no-model control in every cell (0 of 15 have a positive lower CI bound); several NBA cells look better at the best price, but the both-sides control earns that too (`ctl best`), so best-price ROI is mostly line shopping.
- Inside a gate the edge terciles have 130-290 games each, but rho takes only the values +-1 / +-0.5 with 3 buckets; the cross-gate dose-response (EDGE worse than ungated in 3 of 3 markets) is the usable signal and points the wrong way.

## Power
Minimum detectable log-loss gain: ungated 0.012-0.013, gated 0.014-0.065 nats per game; minimum detectable ROI 5-17%. A break-even-sized gain (0.001 nats) needs about 0.4-1.6 million games.

## Leakage garble test (NBA replay)
`nba/game/leak_check.py`: scramble every numeric box-score statistic of the team box AND the player box (which backfills rebounds / turnovers) for every game on or after the cut-off; re-run `build_team_game_stats_from_raw` + `evaluate_walk_forward`; predictions on or before the cut-off must be identical.

| cut-off | games on or before | predictions changed before | predictions changed after (power) |
|---|---|---|---|
| 2023-12-01 | 5,005 | 0 | 3,810 |
| 2025-12-01 | 7,671 | 0 | 1,144 |

**No leak.** Injected-leak unit test (an oracle reading the game's own final margin) is caught. Results: `nba_confidence_gated_eval_2026-11-05/garble_results.json` (generated before the identifier rename `engine` -> `nba_engine`, which changed no behaviour; the unit-level garble tests were re-run on this branch).

## Limits
- The replay passes no tip times (8,865 games used the UTC master date, so rest / back-to-back are approximate) and no injury report; not the engine as the live board runs it.
- Closes are BettingPros last-quote medians (updated within 4 h before / 15 min after the scheduled tip), not Pinnacle; no CLV (single-side opening quotes give no open-to-close pair).
- NBA coverage now spans 2021-22..2025-26 because #183's player-box backfill merged, so these numbers are not comparable to #183's 2025-26-only NBA table.
- The gate family is mine. Injury flags and engine versions across retrains are not testable here (no injury input, no second version).
- Single replay, parameters as shipped; the SESOI (0.001 nats) comes from a ~4.5% vig, not from this data.
- Also in this PR: `nba/game/market_eval.py` replaced an O(N^2) away-game lookup with a dict (the replay is much faster), and `evaluate.py` records the confidence inputs (additive).

## Reproduce
    GCS_BUCKET=<bucket> gcloud storage cp -r gs://<bucket>/Odds/bettingpros_nba_games/{moneyline,spread,total} nba_q/
    GCS_BUCKET=<bucket> python -m nba.game.market_eval --local-dir nba_q --records-pkl nba_rec.pkl
    python -m nba.game.gated_eval dev  --records nba_rec.pkl --out-dir docs/analysis/nba_confidence_gated_eval_2026-11-05
    python -m nba.game.gated_eval held --records nba_rec.pkl --out-dir docs/analysis/nba_confidence_gated_eval_2026-11-05
    GCS_BUCKET=<bucket> python -m nba.game.leak_check --cutoffs 2023-12-01,2025-12-01
Committed outputs in `docs/analysis/nba_confidence_gated_eval_2026-11-05/`: `frozen.json`, `dev_report.md`, `dev_results.json`, `held_report.md`, `held_results.json`, `garble_results.json`.
The replay takes about 10 minutes on a laptop.
