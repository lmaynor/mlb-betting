# Handoff -- 2026-06-24 (Model-health investigation: calibration coverage + no-edge retirement)

Session goal: investigate why some systems are consistently losing. Pulled ops
data via `/model-health`, `/edge-analysis`, `Gates/model_gates.json`, Cloud Run
logs, and direct `bets`-table queries (Cloud SQL proxy from Cloud Shell).

## Root cause found

The losses were dominated by **model overconfidence on large "edges"** (the
documented adverse-selection curve). The acute, fixable part: **two live systems
bypassed the prediction-calibration + EDGE_CAP layer entirely.**

- `mlb_core.risk.calibration.apply` was wired into only 7 runners; **PITCHER_ER
  (run_k) and F1H (run_f5) were never calibrated and never edge-capped.**
- Evidence: post-06-11, PITCHER_ER scored 65 `>0.20`-edge bets and PLACED 55
  (overconfidence +0.129, n=190); F1H placed 3/3 (+0.113, n=95). Every WIRED
  system suppressed 0/N big-edge bets -- proving the layer works, just wasn't
  installed on these two.
- Calibration confirmed working where wired: post-06-11 overconfidence was tiny
  (HR +0.034, OUTS +0.015).

Secondary findings:
- **All prediction calibrators were 13 days stale** (fit 06-11; NRFI model
  retrained 06-22) -- a retrain does NOT refit the prediction calibrator.
- **HR never logged `book`** (scored row dropped `bookmaker`) -> per-book
  profiling impossible.

## Weekly-trend reclassification (the key nuance)

The rolling-30 gate can't tell a consistent loser from one bad week. The
05-05..06-22 weekly ROI trend (Q1) showed:
- **Consistent no-edge losers (AUC ~0.50, negative most/every week): 1IOU, F5, F1H.**
- **Volatile / one-bad-week (do NOT retrain reactively): OUTS** (positive 5 of 7
  weeks; the -46% rolling is the single 06-22 week, n=10), **HR** (longshot
  variance, AUC 0.627 discriminates), **K** and **BATTER_HITS** (healthy, one off week).

## Shipped + deployed this session

PR #13 (merged) -- revision `mlb-betting-00233-xjg` live:
1. `runners/run_k.py` -- PITCHER_ER: pre-edge `_cal_apply("PITCHER_ER")` + EDGE_CAP + model-health gate.
2. `runners/run_f5.py` -- F1H: pre-edge `_cal_apply(sys_key)` + EDGE_CAP.
3. `runners/run_hr.py` -- propagate `bookmaker` into the scored row (fixes book=NULL).
4. `docs/solutions/conventions/retrain-calibrate-sequence.md` -- documents the
   SECOND (prediction) calibrator layer + that a retrain invalidates it + 10-system
   coverage list.

Ops done in Cloud Shell:
- Re-fit all 10 prediction calibrators (`mlb-fit-calibrators`) -- now dated 06-24.
- Created `mlb-fit-calibrators-weekly` scheduler (Mondays 10:00 UTC).

Second commit (this session, separate branch) -- retire the no-edge set:
- `mlb_core/registry.py` -- `force_gate="on"` for **1IOU** and **F5**.
- `runners/run_f5.py` -- `LOG_ONLY_SYSTEMS = {"F1H"}`.
  These are reversible pauses (data still logs; kelly_triggered=False). They stop
  the bleed until a retrain restores discrimination.

## Open for next session

1. **Validate the calibration fix** after a betting run:
   `GET https://mlb-betting-xv3m5heozq-uc.a.run.app/edge-analysis` -- PITCHER_ER and
   F1H `>=20%` buckets should stop accumulating placed bets.
2. **Retrain the no-edge set, then clear the pauses:**
   - NRFI: `mlb-retrain-nrfi-v18` -> `mlb-calibrate-nrfi` -> `mlb-fit-calibrators`,
     then registry `1IOU.force_gate = None`. NOTE: NRFI was already retrained 06-22
     and STILL goes live-inverted (OOS AUC 0.589 vs live 0.498) -- this is feature
     drift / live-vs-train mismatch, NOT undertraining. Sub-model AUCs: lineup 0.589
     (only signal), pitcher 0.526 (stump, best_iteration=7), context 0.504 (dead).
     Investigate the Monday PSI drift monitor first; consider a lineup-only model.
   - F5: `mlb-retrain-f5-v5` -> calibrate -> fit-calibrators, then `F5.force_gate = None`
     and remove "F1H" from LOG_ONLY_SYSTEMS (F1H is an F5 scalar proxy).
3. **OUTS: watch one more week.** Do NOT retrain on the 10-bet -46% week; the gate
   is protecting it. Reassess after ~30 fresh settled bets.
4. **HR: it is a variance/bankroll problem, not a model bug** (well-calibrated now).
   Consider restricting HR to small-edge bets (the <5% edge bucket was +6.4% ROI).

## Reusable diagnostics (Cloud Shell)

- Cloud SQL proxy bet-level queries: parse `mlb-db-url` secret -> `cloud-sql-proxy
  --port 5433 <conn>` -> `psql -h 127.0.0.1 -p 5433`. Wrap any `round(double,int)`
  arg in `::numeric`. `bets` schema: system, game_date(TEXT), bet_type, model_prob,
  edge, odds(INT american), stake, kelly_triggered, result, profit, book, clv_pct.
- `/model-health` (per-system verdict+flags), `/edge-analysis` (edge-bucket
  adverse selection), `Gates/model_gates.json` (live suppression state).

## Key pointers
- PR: https://github.com/lmaynor/mlb-betting/pull/13
- Doc: `docs/solutions/conventions/retrain-calibrate-sequence.md`
- Calibration module: `mlb_core/risk/calibration.py` (apply() + EDGE_CAP, fail-open).
- Gate: `mlb_core/risk/gates.py` (registry.force_gate wins over the gate file).
