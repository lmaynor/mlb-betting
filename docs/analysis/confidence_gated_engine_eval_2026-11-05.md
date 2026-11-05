# Confidence-gated engine evaluation: do the NFL / NBA game engines beat the close when they are confident? (2026-11-05)

Code and research only. Nothing deployed, nothing written to GCS or the bets table, no serving engine changed, no `nhl/` file touched.
Every number is an UNVALIDATED research output. Builds on `docs/analysis/game_line_harness_2026-11-04.md` (PR #183).

## Verdict (plain)
**No gate shows the engine beating the closing line. Not confirmed anywhere; on the evidence it is "no" for the NBA and "no evidence,
too little data to be sure" for the NFL.**
- Under the pre-registered rule the formal label is INCONCLUSIVE overall, because the small NFL gates (79-268 held-out games) cannot rule out
  a break-even-sized edge. The NBA, which has enough games, is NO (every gate's 95% upper bound on the log-loss gain is below the
  break-even size; the best, "NBA total, low Kalman uncertainty", is -0.0176 with upper bound +0.0006).
- 0 of 29 gate cells have a positive log-loss gain on the held-out seasons. Max-statistic adjusted p over the whole family: 1.000 for every cell.
- Being "confident" makes it WORSE, not better: the more the engine disagrees with the line, the larger its log-loss deficit (EDGE gate, held-out:
  NFL -0.116 / -0.080 / -0.148 on moneyline / spread / total, NBA -0.067 / -0.096 / -0.103, versus -0.04 / -0.03 / -0.04 and -0.02 / -0.03 / -0.02 ungated).
  That is the winner's-curse shape: the engine's large disagreements are mostly its own noise.
- Leakage: neither replay leaks. Both pass the garble test at two cut-offs each (0 changed predictions before the cut-off, many after). One false alarm on the first NFL run was a flaw in the garble itself (it moved missing scores onto games), fixed and re-run clean; see "Leakage test".
- What would settle it: not any historical backtest. Detecting even a +3% ROI at 80% power needs about 4,000-16,000 GATED games; at a 20-30% gate
  that is roughly 13,000-80,000 games (NBA 11-65 seasons, NFL 50-290 seasons). The break-even-sized log-loss gain (0.001 nats) needs 0.3-1.9 million games.
  The only feasible test is forward: freeze one gate now, log picks with timestamps, and compare to the close until a few thousand gated games exist.

## Protocol (pre-registered in code before any outcome was scored)
Fixed in `mlb_core/eval/gating.py` and committed (`c7cfbbd7`) before the dev or held-out results were computed. The held-out stage refuses to run
without `frozen.json`, refuses a second look without `--allow-rescore` (flagged in the report), and checks `frozen.json` was not edited.

**Splits (chronological).** NFL dev 2021-2023, held-out 2024-2025. NBA (season = start year) dev 2021-22..2023-24, held-out 2024-25 and 2025-26.
The NBA 2026-27 games (in progress) are excluded. Disclosure: I had already seen the UNGATED all-season results of #183 (engines lose overall), so the
held-out seasons were not blind in aggregate; they were blind to every gated number.

**Confidence = six gates, ONE threshold each, no tuning.** Thresholds are dev-split quantiles of FEATURES (the outcome column is never read; a test pins that).
| gate | rule |
|---|---|
| EDGE | `|p_model - p_mkt|` in the top quartile of the dev distribution (per sport and market) |
| SD | Kalman rating uncertainty (sd of the margin / total mean) in the lowest dev tercile |
| AGREE | Kalman-only, feature-side and blended margin (or total) all on the same side of the line as the bet. Not applicable to the NFL total (one variant) |
| PHASE | games already played this season: NFL week >= 6, NBA both teams >= 20 (constants, not fitted) |
| DQ | no box-score game dropped from either team's last 10. Degenerate on every market (see below), so not tried |
| COMBO | EDGE and PHASE and (AGREE where applicable) |

The NBA data-quality gate is degenerate: after the player-box backfill merged in #183, `nan_stats` drops are 0 of 17,730 team-games, so there is nothing to gate on.
The NFL replay carries no injury / weather inputs (see Limits), so it has no data-quality flag.

**Number of gate variants tried: 6 designs x 3 markets x 2 sports = 36 slots, 29 applicable cells** (6 DQ cells degenerate, NFL total x AGREE not applicable).
One threshold per gate; no threshold was varied after seeing outcomes. "Agreement across engine variants" uses the two components the engine really blends
(Kalman 55-60% and feature 40-45%); engine versions / retrains do not exist to compare.

**Screen and test.**
1. Dev: a cell advances only if dev log-loss gain > 0, dev blend c > 0 and n >= 100. **0 of 29 advanced.** So the confirmatory family is empty.
2. Held-out is scored once for every cell as an exhibit. Confirmation would have required: survivor, Holm p < 0.05 within the survivors, blend c z > 1.645, ROI at close > 0.
3. Whole-family penalty for all 29 forks: a max-statistic sign-flip (one random sign per game day shared across cells, date-clustered z, 4,000 flips). A cell's adjusted p is the share of
   flips whose maximum z over all 29 cells reaches its observed z.
4. Verdict rule: YES = a survivor is confirmed. NO = nothing confirmed and every gate's 95% upper bound on the gain is below 0.001 nats (about break-even against a ~4.5% two-way vig).
   INCONCLUSIVE = otherwise. The per-sport version of the same rule is NBA = NO, NFL = INCONCLUSIVE.

**Per gate we report**: coverage, date-clustered paired log loss vs the close (positive = engine better) with 95% CI, blend weight c (market coefficient fixed at 1, so it is identified on spread /
total where p_mkt is about 0.5) with cluster-robust z, minimum detectable effect (one-sided 5%, 80% power), edge-tercile monotonicity inside the gate, flat-stake ROI of the engine's side at the close
(median) price with a clustered 95% CI and at the best available price, and the no-model control (both sides of everything at the best price). NFL has one price per game, so best = close there.

## Held-out results (every cell, scored once)
Positive LL gain = engine better than the close. `ctl best` = ROI of betting both sides of every game in that subset at the best price (the vig a no-model bettor pays).

### NFL (held-out 2024-2025)

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
| spread (home covers) | SD | 165 | 31% | -0.0067 | [-0.0524, +0.0391] | -0.29 | 0.0580 | +0.42 (+1.7) | +7.8% [-5.9%, +21.5%] | +7.8% | -4.3% | +0.50 | 1.000 | - |
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

### NBA (held-out 2024-25, 2025-26)

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

Reading the table:
- No cell has a positive LL gain. The least bad are the low-uncertainty gates: NFL spread -0.007 [-0.052, +0.039], NFL total -0.024, NBA total -0.018 [-0.036, +0.001].
- Blend c: no cell reaches z > 1.645 (max NFL spread SD, c = +0.42, z = +1.67, one of 29 cells; about 1.5 cells would cross 1.645 by chance alone). On dev the best c was NBA total SD at z +1.9, and that cell still lost on log loss (-0.016).
- ROI looks better than the log loss in a few cells (NFL spread EDGE +7.5%, SD +7.8%, COMBO +10.1% at the close; control -4.4%) but every 95% CI includes 0 and spans -7% to +27%, n is 96-165, and the same
  cells lose on log loss. 7 of 29 cells have a positive point ROI and 0 have a positive lower bound; with 29 cells x 2 prices that is what noise produces. NFL ROI at the close is the only price there is.
- Edge-tercile monotonicity cannot be read inside a gate (3 buckets, 30-250 games each; rho is +-1 or +-0.5 by construction). The cross-gate dose-response above (EDGE worse than ungated in 6 of 6 held-out markets) is the usable signal, and it points the wrong way.
- Line shopping: on NBA the best price is worth several ROI points over the close price (compare the two ROI columns) and the no-model control already earns that, so any NBA best-price ROI must be read against `ctl best`, as in #183.

## Power: what these samples can and cannot tell us
Minimum detectable true log-loss gain at 80% power: ungated NBA 0.012-0.013, ungated NFL 0.030-0.034, gated NBA 0.014-0.065, gated NFL 0.037-0.15 nats per game. Minimum detectable ROI: NBA 5-17%, NFL 11-33%.
Realistic edges are far smaller than any of these (the break-even log-loss gain is about 0.001 nats, a 3% ROI is 0.0006-0.0012). So:
- The NFL gates (79-268 games) are too small to conclude anything about a modest real edge; they can only say there is no large one.
- The NBA gates (386-1,800 games) each exclude any log-loss gain above about +0.0007 (every NBA 95% upper bound is below that). They do not exclude a +3% ROI (MDE 5-17%), because ROI is a much noisier statistic than log loss.
- Games needed to detect a true +3% ROI (same coverage): NBA 6,000-16,000 gated games, NFL 4,000-11,000 gated games (from the held-out standard errors).

## Leakage test (garble)
`mlb_core/eval/garble.py`, `nfl/game/leak_check.py`, `nba/game/leak_check.py`. For a cut-off day T, every post-kickoff field of every game on or after T is scrambled (NFL: all play-by-play columns except
ids / dates / teams, and the schedule's final scores and result; NBA: every numeric box-score statistic of the team box AND the player box that backfills rebounds / turnovers). The replay is re-run and the engine's
predictions for every game on or before T must be identical. A power check requires predictions after T to change. Injected-leak tests (an oracle that reads the game's own final margin) prove the test catches a real leak in both replays.

**Result.** Real data, seed 0.

| replay | cut-off T | games on / before T | predictions changed before T | predictions changed after T (power) |
|---|---|---|---|---|
| NBA (2019-2026 history) | 2023-12-01 | 5,005 | 0 | 3,810 |
| NBA | 2025-12-01 | 7,671 | 0 | 1,144 |
| NFL (2018-2025 history) | 2024-10-20 | 105 | 0 | 437 |
| NFL | 2025-10-26 | 392 | 0 | 151 |

**No leak found in either replay loop.** The predictions for a game do not depend on its own result, on same-day results, or on any later game.
That covers the Kalman updates (post-game, per team), the PBP / four-factor rolling profiles (strictly earlier week or date), the league z-scores, and the player-box backfill.

**A false alarm worth recording.** The first real-data NFL run reported 3 and 1 changed predictions before the cut-off. They were not value changes: those games were MISSING from the garbled run.
The first garble permuted the schedule's final-score column including its missing values, which moved a NaN score onto those games, and the replay (correctly) skips games without a final score.
`garble_columns` now permutes only non-null cells so missing stays missing, a unit test pins that, and the re-run above is clean. Nothing in the replay or the engine changed.
What the garble does not cover: closing lines are treated as pre-game inputs (they are what the engine is priced at and scored against).

## Limits and unverified items
- The NFL replay passes no schedule / injury / weather inputs (`compute_matchup_pbp_features` is called without `schedules`, as in #183), so bye, short-week, divisional, wind and QB terms sit at defaults. That is not the engine as the live board runs it. The NBA replay passes no tip times (8,865 games used the UTC master date), so rest / back-to-back are approximate, and no injury report.
- NBA coverage now spans 2021-22..2025-26 because #183's player-box backfill merged; the numbers are therefore not comparable to #183's 2025-26-only NBA table. NFL reproduces #183 exactly (n 1,355 / -0.0404 / z -5.82 for the moneyline).
- Closing lines are BettingPros last-quote medians (NBA) and nflverse closes with an undocumented book (NFL); neither is Pinnacle. No CLV: single-side opening quotes do not give an open-to-close pair.
- The gate family is mine. The user's other candidates (injury flags, engine variants across retrains) are not testable here because the harness has no injury input and no second engine version. A different family would be a new experiment with its own multiplicity.
- The dev screen (gain > 0 and c > 0) is a deliberately low bar, so the empty survivor set is itself informative: not one gate was even directionally right on 2021-2023.
- Post-protocol code changes (none alter a gate, threshold, split or statistic): `verdict_by_sport` (same rule applied per sport) was added after the held-out stage, so `held_report.md` shows the overall verdict only and the per-sport labels here were computed from `held_results.json`; `garble_columns` was fixed as described; `nfl.game.market_eval.to_events` was made tolerant of record files lacking the new columns.
- Single replay per sport, parameters as shipped. SESOI 0.001 nats is derived from a ~4.5% vig, not from this data.

## Reproduce
    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025 --records-csv nfl_rec.csv
    gcloud storage cp -r gs://<bucket>/Odds/bettingpros_nba_games/{moneyline,spread,total} nba_q/
    GCS_BUCKET=<bucket> python -m nba.game.market_eval --local-dir nba_q --records-pkl nba_rec.pkl
    python -m mlb_core.eval.gated_run dev  --nfl-records nfl_rec.csv --nba-records nba_rec.pkl --out-dir docs/analysis/confidence_gated_engine_eval_2026-11-05
    python -m mlb_core.eval.gated_run held --nfl-records nfl_rec.csv --nba-records nba_rec.pkl --out-dir docs/analysis/confidence_gated_engine_eval_2026-11-05
    GCS_BUCKET=<bucket> python -m nfl.game.leak_check --seasons 2024-2025 --cutoffs 2024-10-20,2025-10-26
    GCS_BUCKET=<bucket> python -m nba.game.leak_check --cutoffs 2023-12-01,2025-12-01
Committed outputs in `docs/analysis/confidence_gated_engine_eval_2026-11-05/`: `frozen.json` (thresholds, survivors, input hashes), `dev_report.md`, `dev_results.json`, `held_report.md`, `held_results.json`.
The NBA run is slow (about 10 minutes for the replay, the same again per garble re-run); the NFL replay about 10 minutes.
