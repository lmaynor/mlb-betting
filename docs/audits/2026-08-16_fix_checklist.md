# Fix checklist — 2026-08-16 audit

Working branch: `fix/audit-2026-08-16-criticality-pass`. Source: `docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md`
(finding IDs referenced below, e.g. A1, C3.3, map 1:1 to that doc).

Legend: `[ ]` not started · `[~]` code fixed, needs a retrain/deploy/infra step to take live
effect (noted inline) · `[x]` fixed and effective immediately on merge.

## P0 — fix first

- [x] **A1** Kelly stake formula uses wrong probability basis (`mlb_core/odds/utils.py`) — `28bb4f0`
- [x] **A2** `ODDS_PRIMARY` unsafe defaults — `deploy/deploy_service.sh` + `snapshot_odds.py` — `964a479`
- [x] **A3** BATTER_TB + 1I have zero calibration/EDGE_CAP/gate defense — `f6c7028`
- [x] **A4** GAME builder home/away pitcher inversion *(code fixed, `431a5c7` — rebuild+retrain still needed to take live effect, not run from here)*
- [x] **A5** No auth on state-changing `main.py` routes / service open to `allUsers` — `d072e2c`
- [~] **A6** Single gunicorn worker shares process with long admin routes *(auth fix in d072e2c reduces exposure; the async-Jobs conversion itself not yet done — deferred to a future pass)*
- [x] **A7** Two deploy scripts collide on `/run` scheduler jobs; `RUNBOOKS.md` points at the stale one — `6477158` *(docs-update commit `6a3f377` missed ticking this box even though the code fix landed first; corrected retroactively)*
- [x] **A8** `registry.py` OUTS `model_artifact` points at K's model — `6477158`
- [x] **A9** PITCHER_ER suppression gate structurally can never fire — `6477158` *(added `force_gate="on"` + a belt-and-suspenders `PITCHER_ER_LOG_ONLY` flag in `run_k.py` given it's a brand-new, unproven system)*
- [x] **A10** F5/K IL-skip regressed to the twice-already-fixed dual-condition check — `6477158`
- [x] **A11** Banned XGBoost `iteration_range=None` pattern (`run_k.py` OUTS path) — `6477158`

## P1 — high value, safe

- [x] **A12 / C2.2** K/OUTS starter-selection `idxmax` groupby bug — `2428a79` *(code fixed; rebuild+retrain still needed to take live effect)*
- [x] **A13 / C2** HR `is_outdoor` chaos (3 conventions + merge collision) — `2428a79` *(rebuild+retrain still needed)*
- [x] **A14 / C2** HR same-game feature leakage past denylist — `2428a79` *(retrain still needed)*
- [x] **C2.3** BATTER_HITS/BATTER_TB `is_home` constant-zero — `2428a79` *(rebuild+retrain still needed)*
- [x] **B1.1-B1.5** Missing `usecols` (HR/NRFI/K/BATTER_TB) + F5 duplicate statcast read *(rebuild still needed to realize the memory/cost savings; behavior-neutral otherwise)*
- [x] **C3.1/C3.2** NRFI + HR walk-forward CV leaks test fold into early stopping *(shared `XGBModel.train()` fix covers NRFI's v17/v18 diagnostic path; HR has its own separate inline CV loop, fixed to match; both carve an internal val slice from train's own tail instead of watching dtest directly)*
- [x] **C3.3** Hardcoded `CV_FOLDS=[2023,2024,2025]` frozen across 5 systems *(K/OUTS/BATTER_HITS/BATTER_TB/GAME each got a `_cv_folds(df, n=3)` helper deriving the last 3 years actually present in the data)*
- [x] **C3.4** `retrain_nrfi_v18.py` never computes `feature_dists` *(added per-sub-model, matching feature_means/feature_stds' existing per-sub-model shape; also deleted an unrelated exact-duplicate `feature_dists` compute-block found while fixing this in the sibling v17 file)*
- [x] **C3.5** F5 target-column mismatch (`tune_hyperparams.py` + `registry.py` say `home_win`, real col is `home_wins_f5`)
- [x] **C3.6/C3.7** BATTER_TB `nb_alpha` clip deviation + missing `prop_1` in 3 systems
- [x] **C4.1** Kalshi never excluded from `backtest_market.OFFSHORE`
- [x] **C4.3** `verdict()` never checks high-edge-bucket CLV *(Rule 3 added; `hi_clv`/`hi_n` surfaced in `bakeoff_report.py` EVIDENCE_COLS + both bakeoff scripts' scorecard rows)*
- [x] **C4.4** `odds_history.write_partition` overwrite collision (bettingpros/parlayapi ingest) *(both call sites now pass `append=True`)*
- [x] **B4.1** `mlb-bakeoff` job's HR leg never gets `--resume` *(new `--create-if-missing` flag + deploy script keys HR_RUN_ID off Cloud Run Jobs' `CLOUD_RUN_EXECUTION`, stable across a retry, different across executions)*

## P2 — medium

- [x] **C5.2** Doubleheader team-pair dict collisions (NRFI/F5/GAME/1I) *(F5 turned out to have 2 independent collision sites, not 1 -- both fixed; no dedicated test, disproportionate mocking cost for a rare edge case vs. compile-check + review)*
- [x] **C5.4** `event_id` validation fail-open in BATTER_HITS/BATTER_TB
- [x] **C5.8** K/OUTS share one exposure-cap accumulator *(no dedicated test, same proportionality call as C5.2 -- `_build_predictions` needs GCS model/odds/feature mocking disproportionate to the risk already ruled out by a dangling-reference grep + compile-check)*
- [x] **C5.7** Sub-`min_edge` rows dropped before logging (F1H/PITCHER_ER/1I)
- [x] **C5.1** `settle_bets.py` GAME void threshold `<8` vs documented `<5`
- [x] **C5.5/C5.6** `capture_closing_lines.py`: HR team-name mismatch + missing GAME/F1H branches
- [x] **C6.1** Suppression gate self-clears via zero-stake window dilution *(same fix also applied to `_season_stats`, byte-identical bug pattern, not in the audit's own line citation)*
- [x] **C6.2** NRFI AUC alert measures market, not model *(fixed for every system, not just NRFI/1IOU -- market auc is never the right signal for this alert)*
- [x] **C6.3** Capped alerts marked "notified" without posting (`fast_alert_loop.py` + `kalshi_alert.py`) *(both halves of the fix: dedup state now comes from `posted`; overflow persisted to a new `deferred.parquet` and given first priority next run)*
- [x] **C6.4** `monitor_drift.py` missing BATTER_HITS/BATTER_TB/GAME *(CONTEXT.md s6 "adding a new system" checklist also updated)*
- [x] **C6.8** Dead/stale `SCHEDULER_JOBS` list in `monitor_ops.py` *(deleted; CONTEXT.md s9 + the s18 quick-reference table both corrected)*
- [x] **C6.9** `odds_alert.py` never posts to Discord *(freshness failures -> #ops-alerts; resolution scorecard -> #performance, only on runs with new alerts to avoid reposting an unchanged cumulative summary)*
- [x] **C6.6/C6.14** `public_api.py`: `get_today_picks` missing CLV cols + `get_picks` unbounded limit

## P3 — cloud-cost mechanical + cleanup

- [ ] **B3.1** `id_resolver` caches not GCS-backed (rebuilt every cold start)
- [ ] **B3.2/B3.3** `bet_tracker` one-shot migration re-run forever + non-unique dedup index
- [ ] **B3.5** Raw GCS client bypass (weather/umpires/scoring-backfill)
- [ ] **B3.6** Unpaced 30-team IL roster loop
- [ ] **B3.8/B3.9** `track_bettingpros` true call volume + credit-ledger month-boundary bug
- [ ] **E1-E9** Doc drift + duplicated feature lists + deprecated deploy scripts

---

*Deferred (needs a human decision, not auto-fixed):* **C5.9** (`LOG_ONLY=False` on GAME/BATTER_HITS
— can't verify the 200-bet gate criteria without DB access; don't want to silently undo a real
promotion). **A5's OIDC enforcement** ships code-complete but disabled by default — flip only
after verifying against a live Scheduler-signed request.
