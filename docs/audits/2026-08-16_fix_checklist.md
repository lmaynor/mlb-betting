# Fix checklist — 2026-08-16 audit

Working branch: `fix/audit-2026-08-16-criticality-pass`. Source: `docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md`
(finding IDs referenced below, e.g. A1, C3.3, map 1:1 to that doc).

Legend: `[ ]` not started · `[~]` code fixed, needs a retrain/deploy/infra step to take live
effect (noted inline) · `[x]` fixed and effective immediately on merge.

## P0 — fix first

- [ ] **A1** Kelly stake formula uses wrong probability basis (`mlb_core/odds/utils.py`)
- [ ] **A2** `ODDS_PRIMARY` unsafe defaults — `deploy/deploy_service.sh` + `snapshot_odds.py`
- [ ] **A3** BATTER_TB + 1I have zero calibration/EDGE_CAP/gate defense
- [ ] **A4** GAME builder home/away pitcher inversion *(needs rebuild+retrain to take effect)*
- [ ] **A5** No auth on state-changing `main.py` routes / service open to `allUsers`
- [ ] **A6** Single gunicorn worker shares process with long admin routes (folded into A5's fix)
- [ ] **A7** Two deploy scripts collide on `/run` scheduler jobs; `RUNBOOKS.md` points at the stale one
- [ ] **A8** `registry.py` OUTS `model_artifact` points at K's model
- [ ] **A9** PITCHER_ER suppression gate structurally can never fire
- [ ] **A10** F5/K IL-skip regressed to the twice-already-fixed dual-condition check
- [ ] **A11** Banned XGBoost `iteration_range=None` pattern (`run_k.py` OUTS path)

## P1 — high value, safe

- [ ] **A12 / C2.2** K/OUTS starter-selection `idxmax` groupby bug *(needs rebuild+retrain)*
- [ ] **A13 / C2** HR `is_outdoor` chaos (3 conventions + merge collision) *(needs rebuild+retrain)*
- [ ] **A14 / C2** HR same-game feature leakage past denylist *(needs retrain)*
- [ ] **C2.3** BATTER_HITS/BATTER_TB `is_home` constant-zero *(needs rebuild+retrain)*
- [ ] **B1.1-B1.5** Missing `usecols` (HR/NRFI/K/BATTER_TB) + F5 duplicate statcast read
- [ ] **C3.1/C3.2** NRFI + HR walk-forward CV leaks test fold into early stopping
- [ ] **C3.3** Hardcoded `CV_FOLDS=[2023,2024,2025]` frozen across 5 systems
- [ ] **C3.4** `retrain_nrfi_v18.py` never computes `feature_dists`
- [ ] **C3.5** F5 target-column mismatch (`tune_hyperparams.py` + `registry.py` say `home_win`, real col is `home_wins_f5`)
- [ ] **C3.6/C3.7** BATTER_TB `nb_alpha` clip deviation + missing `prop_1` in 3 systems
- [ ] **C4.1** Kalshi never excluded from `backtest_market.OFFSHORE`
- [ ] **C4.3** `verdict()` never checks high-edge-bucket CLV
- [ ] **C4.4** `odds_history.write_partition` overwrite collision (bettingpros/parlayapi ingest)
- [ ] **B4.1** `mlb-bakeoff` job's HR leg never gets `--resume`

## P2 — medium

- [ ] **C5.2** Doubleheader team-pair dict collisions (NRFI/F5/GAME/1I)
- [ ] **C5.4** `event_id` validation fail-open in BATTER_HITS/BATTER_TB
- [ ] **C5.8** K/OUTS share one exposure-cap accumulator
- [ ] **C5.7** Sub-`min_edge` rows dropped before logging (F1H/PITCHER_ER/1I)
- [ ] **C5.1** `settle_bets.py` GAME void threshold `<8` vs documented `<5`
- [ ] **C5.5/C5.6** `capture_closing_lines.py`: HR team-name mismatch + missing GAME/F1H branches
- [ ] **C6.1** Suppression gate self-clears via zero-stake window dilution
- [ ] **C6.2** NRFI AUC alert measures market, not model
- [ ] **C6.3** Capped alerts marked "notified" without posting (`fast_alert_loop.py` + `kalshi_alert.py`)
- [ ] **C6.4** `monitor_drift.py` missing BATTER_HITS/BATTER_TB/GAME
- [ ] **C6.8** Dead/stale `SCHEDULER_JOBS` list in `monitor_ops.py`
- [ ] **C6.9** `odds_alert.py` never posts to Discord
- [ ] **C6.6/C6.14** `public_api.py`: `get_today_picks` missing CLV cols + `get_picks` unbounded limit

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
