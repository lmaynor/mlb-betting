# Roadmap -- 2026-06-29 -- Cross-system historical odds + ROI optimization

A multi-session program to take the NRFI breakthrough and make it the standard
operating procedure for ALL 10 systems: a unified historical-odds store, a
reusable per-system backtest playbook, the metric correction wired into live
gating, real CLV, and portfolio-level ROI optimization.

This roadmap is the single source of truth for the odds/backtest work so the
analysis track and the parallel BettingPros track (`feat/bettingpros-markets`)
build from one plan. It supersedes the odds-specific parts of
`roadmap_2026-06-28_model_improvement.md` and builds on
`handoff_2026-06-29_nrfi_misdiagnosis.md`.

---

## 0. Why this exists (the NRFI lesson, generalized)

The NRFI investigation (handoff_2026-06-29_nrfi_misdiagnosis.md) found that the
"NRFI is dead, concept drift" verdict was a METRIC ARTIFACT. Judged on global AUC
(washed to ~0.50 by the indifferent mid-slate), NRFI looked dead. Judged on
tail/edge-bucket ROI against REAL historical lines, it returns +8.1% at consensus
over 879 out-of-sample bets, model Brier < market. Leakage was ruled out (the
feature builder is point-in-time by construction).

Three things made that possible and must become standard:
1. A way to score the production model over history (`gen_nrfi_preds.py`).
2. Real historical odds joined to those scores (`yrfi_master.csv` + the SGO
   snapshot extractor).
3. The right metric (edge-bucket ROI on the model's own time-split holdout, not
   global AUC).

Right now (1)-(3) exist only for NRFI, as one-off scripts. This roadmap turns them
into shared infrastructure that covers every system.

### Verified vs proposed (intellectual honesty)
- VERIFIED: all 7 feature builders use the same `shift(1)`-after-date-sort leakage
  discipline (HR 7, K 6, F5 8, GAME 18, BATTER_HITS 8 occurrences). So the
  point-in-time guarantee very likely generalizes -- but each must still be
  spot-audited the way NRFI was.
- VERIFIED: the `bets` table already has `closing_odds`, `closing_implied_prob`,
  `clv_pct`, `morning_odds`, `line_move_pct`. CLV is a CAPTURE-COVERAGE problem,
  not a schema problem.
- VERIFIED: `monitor_performance.py` now computes per-edge-bucket ROI + dead-band
  detection for every system in CANONICAL_ORDER, and placed-bet AUC no longer
  alerts on its own (commit 68457cc). The metric correction is already live for
  all 10 systems.
- PROPOSED (unproven): "other systems have similar dead bands / are similarly
  salvageable." This is a HYPOTHESIS the playbook will confirm or refute per
  system. Do not assume NRFI's numbers transfer.

---

## 1. System inventory (market shape drives the adapter)

The backtest math differs by market shape. The toolkit needs one adapter per shape.

| System (registry key) | bet_type | Market shape | De-vig | Settlement field | Historical odds source |
|---|---|---|---|---|---|
| HR | HR | one-sided yes/no | `devig_unilateral` | batters[].home_runs | BettingPros (live) |
| NRFI / 1IOU | NRFI/YRFI | two-way O/U 0.5 | `shin` | innings[0] runs | SGO snapshots + yrfi_master/BettingPros |
| F5 | HOME/AWAY | two-way moneyline | `shin`/`proportional` | innings[0:5] | SGO + BettingPros |
| F1H | F1H_* | two-way moneyline | `shin` | innings[0:4] | SGO |
| GAME | GAME_* | two-way moneyline | `shin` | all innings | SGO + BettingPros |
| K | K_{SIDE}_{LINE} | count O/U (NegBin CDF) | per-line two-way | pitchers[].strikeouts | BettingPros pitcher props |
| OUTS | OUTS_{SIDE}_{LINE} | count O/U | per-line two-way | pitchers[].outs | BettingPros |
| BATTER_HITS | BATTER_HITS_* | count O/U | per-line two-way | batters[].hits | BettingPros |
| BATTER_TB | BATTER_TB_* | count O/U | per-line two-way | batters[].total_bases | BettingPros |
| PITCHER_ER | PITCHER_ER_* | count O/U | per-line two-way | pitchers[].earned_runs | BettingPros |

Implication: build THREE market adapters -- (a) one-sided prop, (b) two-way
moneyline/O/U-0.5, (c) count O/U with per-line book matching (reuse
`_best_book_odds_for_line` in sgo.py to avoid cross-line mixing). NRFI used (b).

---

## 2. Target architecture

```
   SGO snapshots (GCS JSON, 4x/day, 2026+)        BettingPros backfill (historical)
   Odds/sgo/{date}/snapshot_{HHMM}.json           scripts/bettingpros_*.py -> CSV
              |                                              |
              v   sgo_snapshots_to_parquet.py               v  bettingpros_to_parquet.py
              +----------------------+----------------------+
                                     v
                      odds_history (Parquet in GCS, partitioned by date)
                      Odds/history/market=<m>/date=YYYY-MM-DD/part-*.parquet
                                     |
            +------------------------+------------------------+
            v                                                 v
   gen_preds(system)  ->  per-system model probs       cl_capture: pair each live
   (Cloud Shell; reuses production scorers)            bet to its 2330 close -> CLV
            |                                                 |
            v                                                 v
   backtest_vs_lines(market_adapter)  ----------->  bets table (Postgres, live)
   tail/edge-bucket ROI on model holdout                     |
            |                                                 v
            v                                       monitor_performance.py
   per-system promotion decision (T17)              (edge-bucket ROI + dead-band,
   recalibrate or skip-band; un-pause via            already system-agnostic)
   registry.force_gate                              portfolio sizing / digest
```

Two stores, two jobs:
- **`bets` (Postgres)** -- LIVE bets only. Unchanged. The monitor reads it; CLV
  columns get filled by the closing-line capture job.
- **`odds_history` (Parquet in GCS)** -- the ANALYTICS store for backtesting. New.

---

## 3. odds_history schema (normalized, one row per quote)

Parquet, partitioned `market` then `date`. One row = one (selection, book, snapshot).

| column | type | notes |
|---|---|---|
| sport | str | "mlb" (NBA later) |
| market | str | canonical: nrfi_ou, hr_yn, k_ou, outs_ou, bhits_ou, btb_ou, per_ou, f5_ml, f1h_ml, game_ml |
| system | str | registry key (HR, 1IOU, ...) |
| game_pk | int (nullable) | resolved via norm_team/game_key bridge |
| game_date | date | PARTITION KEY (ET game day = snapshot folder date) |
| event_id | str | source event id (SGO eventID / BettingPros id) |
| away_team / home_team | str | canonical 3-letter (norm_team) |
| player_id | int (nullable) | MLBAM id for props |
| selection | str | YRFI/NRFI/OVER/UNDER/YES/HOME/AWAY |
| line | float (nullable) | O/U line; NULL for ML/yn |
| book | str | canonical onshore (draftkings, fanduel, ...) |
| american | int | American odds |
| decimal | float | derived (`american_to_decimal`) |
| implied_prob | float | vig-inclusive (`american_to_implied_prob`) |
| fair_prob | float (nullable) | de-vigged where the pair exists (method tagged) |
| snapshot_ts | timestamp | from snapshot filename (1555/1900/2155/2330) + date |
| is_open | bool | first snapshot of the day for this market/selection |
| is_closing | bool | the 2330 (pregame) snapshot |
| source | str | "sgo" or "bettingpros" |
| ingested_at | timestamp | passed in via args (Date.now is unavailable in some envs) |

De-dup key: `(market, game_pk, selection, line, book, snapshot_ts, source)`.
Source precedence on overlap: SGO for 2026+ closing; BettingPros for history.

CRITICAL discipline (the "1 priced 2026 game" trap): every loader writes a
**coverage report** `Odds/history/_coverage/{market}.json` = games priced per
season. Backtests refuse to run / loudly warn when coverage for the requested
window is below a threshold. Never let a backtest silently run on thin data.

---

## 4. Phases and tasks

Notation: [LOCAL] = code-only, doable on the edit-only Mac. [CLOUD] = needs
xgboost / GCS / Cloud SQL (Cloud Shell or Cloud Run). Each task lists files +
acceptance.

### PHASE 0 -- Foundations: the odds_history store + SGO ETL

P0.1 [LOCAL] Define schema + IO helpers.
- Files: `mlb/analysis/odds_history.py` (schema constants, `write_partition()`,
  `read_history(market, since, until)`, `coverage_report()`).
- Reuse `mlb_core.storage` for GCS IO; Parquet via pandas `to_parquet`/`read_parquet`
  (confirm pyarrow is in requirements.txt; add if missing -- watch build speed,
  CONTEXT s6).
- Acceptance: round-trip a synthetic frame to Parquet and back; coverage_report
  returns per-season counts.

P0.2 [CLOUD] SGO snapshot -> Parquet ETL.
- File: `mlb/analysis/sgo_snapshots_to_parquet.py`. Generalize
  `extract_2026_nrfi_odds.py`: iterate `list_keys("Odds/sgo/")`, for each event run
  the per-market extractors already in `mlb_core/odds/sgo.py`
  (`extract_nrfi_odds`, `extract_hr_props`, `extract_k_odds`, `extract_outs_odds`,
  `extract_f5_ml_odds`, `extract_batter_hits_odds`, `extract_batter_tb_odds`,
  `extract_pitcher_er_odds`, `extract_1i_3way_odds`), flatten to odds_history rows,
  tag `snapshot_ts`/`is_open`/`is_closing`, de-vig two-way markets.
- Acceptance: writes all markets for the full 2026 snapshot archive; coverage
  report matches the ~633 NRFI games we already validated; no unmapped teams.

P0.3 [CLOUD] BettingPros -> Parquet loader.
- File: `mlb/analysis/bettingpros_to_parquet.py`. Normalize the
  `feat/bettingpros-markets` outputs (built on `scripts/bettingpros_api.py`) into
  the SAME schema; map player/team -> game_pk via norm_team + MLB schedule.
- Acceptance: HR (already pulled) + at least one pitcher and one batter prop market
  loaded with >1 season coverage; de-dup vs SGO clean.

P0.4 [LOCAL] Coverage gating in the backtest entrypoint.
- Backtests read `odds_history` + a coverage check; refuse/warn under threshold.

### PHASE 1 -- Generalize the scorer + backtest adapters

P1.1 [CLOUD] `gen_preds(system)` -- parametrized scorer.
- File: `mlb/analysis/gen_preds.py`. Factor out of `gen_nrfi_preds.py`: load the
  system's model+meta+calibrator from the registry, score its `model_features.csv`,
  emit `game_key,p_<market>,realized,game_date`. For count models emit the lambda /
  per-line probabilities needed for the O/U adapter.
- Reuse the production scoring path per system (run_<sys> loaders) so preds == live.
- Acceptance: reproduces `gen_nrfi_preds` output for NRFI; emits preds for >=2 other
  systems.

P1.2 [LOCAL] Market adapters in the backtest.
- File: extend `mlb/analysis/nrfi_market.py` -> rename concept to
  `market_backtest.py` (keep a shim) with three adapters: one-sided prop, two-way
  ML/O/U-0.5, count O/U (per-line book match). Each yields (p_model, p_fair, odds,
  won) so `backtest_vs_lines` / `bettable_slate_metrics` / `validate_skip_band` work
  unchanged downstream.
- Acceptance: NRFI path byte-identical; HR (one-sided) and K (count O/U) backtests
  run end to end.

P1.3 [LOCAL] Tests: golden-file parser tests per market; team-norm coverage test
  (fail on unmapped name); backtest determinism test (fixed seed -> fixed ROI).

### PHASE 2 -- Per-system backtest + decision

P2.1 [CLOUD] Run the playbook for each system (HR, K, OUTS, F5, F1H, GAME,
  BATTER_HITS, BATTER_TB, PITCHER_ER): holdout edge-bucket ROI, line-shop decomp,
  dead-band check, fit/test split validation (`validate_skip_band` generalized).
- Acceptance: a one-page result per system (ROI, bucket table, OOS verdict),
  appended to a results doc.

P2.2 [CLOUD] Per-builder point-in-time leakage spot-audit (esp. GAME's 18 rolling
  features). Confirm the `shift(1)` pattern holds where it matters.

P2.3 [LOCAL] Decision per system: recalibrate mid-range (preferred -- isotonic on
  the bettable band, less overfit) vs hard skip-band vs leave. Record rationale.

### PHASE 3 -- Real CLV + promotion

P3.1 [CLOUD] Closing-line capture job. Pair each settled bet to its `2330` close
  from odds_history; fill `bets.closing_odds`/`closing_implied_prob`/`clv_pct`
  (`clv_pct_from_prices`). Extend the existing 00:00 `mlb-capture-closing` job or
  add `mlb/runners/capture_closing.py`. Backfill historical bets from odds_history.
- Acceptance: clv_pct populated for >90% of settled 2026 bets; monitor CLV stats
  become trustworthy.

P3.2 [LOCAL] T17 promotion gate on the corrected metric. Update the paper->live
  criteria (CONTEXT s6) to use tail/edge-bucket ROI + mean CLV >= +2% (t>2, n>=100)
  + hit-rate-within-3pts IN THE BETTABLE BUCKETS + PSI<0.25. Wire into
  `monitor_performance` promotion tagging (already has a PROMOTE-READY hook).

### PHASE 4 -- Portfolio ROI optimization

P4.1 Cross-system correlation + portfolio Kelly. Today exposure caps are per-system
  (CONTEXT s5). Add a portfolio view sizing across correlated systems (HR-in-1st ~
  YRFI; F5 ~ GAME ~ F1H). Likely a `mlb_core/risk/portfolio.py`.
P4.2 Universal best-line enforcement: verify EVERY runner takes best-onshore (NRFI
  does via `_best_book_odds_int`); the ~3-4% line-shopping lever is universal.

---

## 5. Cleanup checklist
- [ ] Merge `analysis/nrfi-historical-odds` (PR) and reconcile with
  `feat/bettingpros-markets` -- the odds work must live in one place.
- [ ] Promote one-offs: `yrfi_master.csv` + `extract_2026_nrfi_odds.py` become the
  first loaders of odds_history, not a bespoke path.
- [ ] Generalize `gen_nrfi_preds.py` -> `gen_preds.py` (retire the NRFI-only copy or
  make it a thin wrapper).
- [ ] Add `pyarrow` to requirements.txt if absent (mind build speed, CONTEXT s6).
- [ ] Run analysis sessions in dedicated `git worktree`s -- the branch-thrash this
  session (3 forced checkouts off the working branch by a parallel session) cost
  real time and nearly clobbered uncommitted work.
- [ ] Update CONTEXT.md s2/s3 with `mlb/analysis/`, `Odds/history/`, and the
  odds_history contract once Phase 0 lands.

## 6. Testing checklist
- [ ] Golden-file odds parser tests, one per market shape.
- [ ] Team-normalization coverage test (asserts no unmapped name silently passes).
- [ ] Point-in-time leakage spot-audit per builder (NRFI done).
- [ ] Backtest determinism (seed -> stable ROI).
- [ ] Coverage-gate test (thin window -> refuse/warn, not silent run).
- [ ] monitor edge-bucket tests already shipped (tests/test_monitor_edge_buckets.py);
  extend to count-O/U systems once adapters land.

## 7. Risks / gotchas
- Player-prop historical depth is the hard part (same wall that blocked NBA props).
  BettingPros is the bet; log coverage so thin markets are obvious.
- Cross-line mixing in count O/U: use `_best_book_odds_for_line`, not
  `_best_book_odds_int`, or you compare u0.5 against u1.5.
- Source overlap / double-count: de-dup on the documented key; tag `source`.
- Do NOT add AUC-based suppression anywhere. Suppression stays ROI-only
  (monitor `_gate_condition_met`); AUC/calibration are observability-only.
- Overfit skip-bands: always validate on a fit/test split (NRFI 10-15% bucket was
  +32% fit / +0.2% test -- unstable; we adopted skip[5,12), not [5,15)).
- Date.now()/random unavailable in some envs -- pass timestamps via args.

## 8. Open questions
- odds_history store: Parquet-in-GCS (recommended) vs a Postgres `odds_history`
  table? Parquet is cheaper for analytics scans; Postgres simpler to join live.
  Decision needed before P0.1.
- How far back does BettingPros go per market? Determines backtest power per system.
- Recalibration vs skip-band as the default remedy -- decide after P2 evidence.

## 9. Pointers
- NRFI evidence + un-pause proposal: `handoff_2026-06-29_nrfi_misdiagnosis.md`.
- Prior program (superseded in part): `roadmap_2026-06-28_model_improvement.md`.
- Toolkit (branch analysis/nrfi-historical-odds): `mlb/analysis/`
  {nrfi_market.py, gen_nrfi_preds.py, extract_2026_nrfi_odds.py, validate_skip_band.py}.
- Live gating: `mlb/runners/monitor_performance.py` (edge buckets + dead band),
  `mlb/runners/diagnose_nrfi_drift.py` (tail_auc), `mlb_core/risk/`
  {calibration.py, gates.py, clv.py}, `mlb_core/registry.py` (force_gate).
- Odds parsers: `mlb_core/odds/sgo.py` (per-market extractors), `mlb_core/odds/utils.py`
  (devig_two_way / clv_pct_from_prices / kelly).
- BettingPros track: `feat/bettingpros-markets` (scripts/bettingpros_*.py).
