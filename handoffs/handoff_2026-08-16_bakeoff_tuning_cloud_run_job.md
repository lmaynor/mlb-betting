# Handoff -- 2026-08-16 -- model bake-off tuning: merged to main, Cloud Run Job in flight

Built real per-system hyperparameter tuning + durable GCS persistence + a codified
profitability verdict on top of the existing HR/all-system model bake-off scripts, then
merged the tooling to `main` (curated -- see "Merge decision" below). **The actual
exercise (find out which of the 6 systems are profitable after tuning) is still running
as a Cloud Run Job as of this writing** -- this handoff exists specifically so whoever
picks this up next (could be a future me) doesn't have to reconstruct where it's at.

## TL;DR

- **Tooling merged to `main`**: `model_bakeoff.py` / `hr_model_bakeoff.py` (train several
  model families per system OOS, backtest each), `backtest_market.verdict()` (codifies
  the go/no-go), `bakeoff_tuning.py` (real walk-forward-safe Optuna search),
  `bakeoff_persist.py` (GCS persistence + `--resume` + `--notify`), `bakeoff_report.py`
  (renders a persisted run as a handoff doc), `deploy/setup_bakeoff_job.sh`.
- **The exercise itself is NOT finished.** It's running as the `mlb-bakeoff` Cloud Run
  Job (built because Cloud Shell's own VM got reclaimed mid-run three times -- `tmux`
  only survives a client disconnect, not the VM disappearing). See "Where to pick up"
  below for exactly what to check and run next.
- **Interim result (3 of 6 systems, from before the Job existed):** K, OUTS, BATTER_HITS
  all landed `NO_EDGE` on every model family, including the newly-tuned `xgb_optuna` --
  `lo_clv` clustered tight around 0% (roughly -0.3% to +0.2%), nowhere near the +2%/t>2
  bar. Consistent with `handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md`'s
  finding of no capturable model-vs-line edge. Real tuning has not flipped this so far.
- **Two loose threads, outside the Job's scope**, not to lose track of: `hr_softline.py
  --validate` was requested but never actually run/reported this session; the held-back
  HR feature-engineering work is still sitting unvalidated on `analysis/hr-model-bakeoff`.

## Merge decision: what's on main vs what's held back

`analysis/hr-model-bakeoff` had 29 commits ahead of main, mixing two different things:
the bake-off tooling (safe, tested) and HR feature-engineering experiments (Lever B/C
wind/PA/arsenal-matchup features, bat-tracking/xISO, humidity/pressure -- all in
`build_hr_features.py` + `weather.py` + `retrain_hr_v6.py`) whose own commit messages
explicitly call for bake-off validation before adoption, which hasn't specifically
happened. Only the tooling went to `main` (one curated commit, `a71c764`); the feature
work stays on `analysis/hr-model-bakeoff` (branch untouched, still pushed) until it
clears that bar. Full reasoning in `a71c764`'s commit message.

One cross-file dependency from this split: `hr_model_bakeoff.py`'s `xhr_poisson`
candidate needs a `game_xhr` column that only exists via the held-back
`build_hr_features.py` changes. On `main` it degrades gracefully (the per-model loop's
existing try/except catches it, logs `FAILED: RuntimeError: game_xhr column absent`,
the other 6 candidates score normally) -- but this means **the Cloud Run Job, whose
image was built from `analysis/hr-model-bakeoff`, has `game_xhr` and gets a real
`xhr_poisson` score; a future run from a `main` checkout won't.** Not a bug, just don't
mix the two when comparing results.

## Where to pick up once `mlb-bakeoff` finishes

```bash
export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data

# 1. Check status (or just wait for the Discord #ops-alerts ping -- --notify is wired in)
gcloud run jobs executions list --job=mlb-bakeoff --region=us-central1 --limit=5

# 2. Get the actual persisted run prefixes from the job's logs -- model_bakeoff's is
#    known (below), hr_model_bakeoff's is NOT (it generates a fresh run_id; find it here)
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=mlb-bakeoff' \
    --limit=200 --order=asc --format='value(textPayload)' --project=concrete-crow-445205-m4 \
    | grep -E "persisted ->|PROMOTE_CANDIDATE|verdict="
```

If `systems_completed` in `model_bakeoff`'s `run_meta.json` isn't all 6 yet (job failed
even after its 1 retry), just resume again -- `--resume` is idempotent, safe to rerun as
many times as needed:
```bash
gcloud run jobs execute mlb-bakeoff --region=us-central1 --async
```

Once BOTH scripts show `status: "complete"` in their `run_meta.json`, **from a Cloud
Shell checkout of `analysis/hr-model-bakeoff`** (not `main` -- see the `xhr_poisson`/
`game_xhr` note above, this keeps the ungated run's `xhr_poisson` behavior consistent
with the gated run's):

```bash
cd ~/mlb-betting && git checkout analysis/hr-model-bakeoff && git pull
export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
pip install optuna --break-system-packages -q

MODEL_RUN=2026-06-01_4abef3d_221339      # model_bakeoff's run (known)
HR_RUN=<from step 2 above>               # hr_model_bakeoff's run (look it up)

# 3. Ungated comparison, reusing the SAME tuned params (isolates the gate as the only
#    variable -- the soft-line-artifact check from
#    docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md)
PYTHONPATH=. python3 -m mlb.analysis.model_bakeoff --cutoff 2026-06-01 \
    --tune --load-tuned-from "Analysis/bakeoff/runs/$MODEL_RUN" \
    --min-books 1 --max-spread 1.0 --persist
PYTHONPATH=. python3 -m mlb.analysis.hr_model_bakeoff --cutoff 2026-06-01 \
    --tune --load-tuned-from "Analysis/bakeoff/runs/$HR_RUN" \
    --min-books 1 --max-spread 1.0 --persist

# 4. Render the gated run's scorecard into a handoff doc, commit it (to whichever
#    branch you're on -- main is fine too, this file has no code dependency)
PYTHONPATH=. python3 -m mlb.analysis.bakeoff_report --prefix "Analysis/bakeoff/runs/$MODEL_RUN" \
    > handoffs/handoff_$(date -u +%F)_bakeoff_tuning_verdict.md
git add handoffs/ && git commit -m "docs: bake-off tuning verdict" && git push
```

That last handoff doc is the actual conclusion of this exercise -- read it (or write it)
before doing anything else with these numbers. A clean `NO_EDGE` everywhere is a fully
legitimate, well-precedented result (see the June 30 handoff), not a failed exercise.

## Loose threads (not part of the Job, don't lose track of these)

- **`hr_softline.py --validate`**: asked whether the soft-book +EV-vs-sharp-anchor
  strategy (a market-structure play, independent of the model) has actually been
  profitable. Never got run/reported this session --
  `PYTHONPATH=. python3 -m mlb.analysis.hr_softline --validate` (run in a separate
  Cloud Shell tab from anything else, it's lightweight -- no training, just reads
  `odds_history`).
- **Held-back HR feature engineering** (`analysis/hr-model-bakeoff`, not on main): Lever
  B (expected PAs, directional wind, arsenal matchup), Lever C (`xhr_poisson`/`game_xhr`),
  bat-tracking/xISO, humidity/pressure. Each one's own commit message says what would
  need to be true to adopt it (e.g. "adopt only if xhr_poisson improves the YES-side
  roi/clv vs xgb_prod"). None of that validation has specifically happened -- the
  all-`NO_EDGE` bake-off result so far doesn't validate or invalidate any ONE of these
  features individually, it's a statement about the model family as a whole.
