# Handoff -- 2026-10-14 -- NHL SOG props model pillar (validation study + production-shape code)

Branch `feat/nhl-sog-props-model`, one DRAFT PR. **Nothing is deployed, no job/scheduler/GCS object was created or written, no
Discord post, no ParlayAPI credit spent, no api-web.nhle.com call.**

## Verdict
**NO_EDGE** for the SOG count model; **INSUFFICIENT_N** for Sink A (60 qualifying quotes). PROMOTE_CANDIDATE is impossible on the
available history: it has one snapshot per quote (~184 min pre-start), so there is no closing line and no CLV. Full study:
`docs/analysis/nhl_sog_validation_2026-10-14.md` (pre-registered before outcomes were joined; frozen numbers in
`..._results_db_only.json`).

Key numbers (63 days, 482 games, 46,606 graded quotes): market log-loss 0.6816 vs model 0.6872; blend coefficient c = 0.10 +- 0.11
(t 0.9); model-disagreement bets -3.6% (best price) / -5.4% (median price) vs a no-skill -4.5% / -6.5%; Sink A +1.5% on n = 60
(t 0.1); the sog-predictor "+14% BetMGM unders" strategy reproduces as -5% to -7%. The market is well calibrated. A +3% edge would
need ~8,000 independent bets to detect; this data has ~480 games, so "no edge" means "none visible", not "none exists".

## What was built (all under `nhl/props/`, none of it wired to a job)
| File | Purpose |
|---|---|
| `gamelog.py` | canonical skater game log; loaders for our masters (`from_masters`, shots -> attempts / PP attempts) and for `nhl_data.db` |
| `features.py` | leakage-safe rolling features (`shift(1)` before every window); upcoming-game placeholders go through the SAME code, so train/serve parity is tested |
| `train.py` | XGBoost `count:poisson` + NegBin alpha; artifact `xgb_nhl_sog_v1.json` + `model_meta_nhl_sog_v1.json` (CONTEXT s5 contract); LOCAL output by default, `--gcs` is explicit opt-in |
| `score.py` | banked snapshot -> `parse_event_props` -> model P(over) -> edge vs consensus/best price; refuses stale snapshots (reuses `ev.sweep_props_alerts.read_latest_props`); LOG-ONLY: no Discord, no `bets` table; local CSV by default, `--gcs` explicit |
| `history.py`, `market.py`, `study.py`, `negbin.py` | the validation harness (quote pairing, grading, consensus, Sink A, cluster-robust stats, blend regression). `negbin.p_over_vec` is pinned equal to `mlb.analysis.gen_preds.p_over` |
Tests: `tests/test_nhl_props_{features,history,model,score}.py` (36 tests). Full suite: 1,313 passed (venv python 3.14 unpinned wheels).
No shared file was edited (no mlb_core/ev/main/registry/bet_tracker/settle_bets/CONTEXT change).

## Gotchas found
- `nhl_data.db` `game_date` is the SNAPSHOT date, not the game date; Utah is spelled out; one line per player per book; no close.
  Written up in `docs/solutions/logic-errors/nhl-sog-history-db-game-date-is-snapshot-date.md`.
- Test-caught parity bug: a naive league-environment feature gave an upcoming game different values than the same game had after it
  was played. Fixed by taking "last data date strictly before the game's date" explicitly.
- The live ParlayAPI SOG shape (one real fixture event) is two-sided only from a DFS app plus one-sided `Yes` ladders. If banked
  snapshots look like that, consensus needs >= 3 two-sided REAL books and will rarely exist: the scorer will log NO_CONSENSUS and
  never flag; Sink A would not fire either. Check `OddsAccum/icehockey_nhl/` after a few days.
- Era drift: team SOG/game ~31 in 2019-20 vs ~28 in 2025-26; the `lg_sf_l30` feature exists for that, but any 2019+ training should
  be checked by season.

## Not done / open
- Long-history (2019+) re-evaluation against the quote window: the backfill job was still running (see study section 3 for status
  and the one-line command, `study --history-dir`).
- Goalie saves market (optional in the brief): not started. Anytime goal/assist/points are one-sided: out of scope.
- To promote anything: forward-banked snapshots with opens AND closes (the `odds-bank-*` jobs), then run the scorer's evidence rows
  through `mlb_core.risk.clv.clv_verdict`. Until then keep this out of the registry and off Discord.

## Decisions for the user
1. Keep the pillar as code-only (recommended) until banked data with closing lines exists, or drop `nhl/props` from further work.
2. Whether to spend effort on a retrain job/scheduler for this at all given NO_EDGE (not provisioned; `deploy/` untouched).
