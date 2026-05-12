# HR Meta Retrain Runbook

Companion to `deploy/RETRAIN_NOTES.md` (F5 meta patcher). This documents the
HR meta patching pipeline, which is the HR-side mirror of F5.

## What it does

`training/retrain_hr_meta.py` is a **meta-only patch** — it does NOT retrain
the model. It computes per-feature means from the production HR feature
table and writes them into the existing v6 meta so the runner can fill NaN
at predict time.

1. **Load** `HR_Pro/models/model_meta_hr_v6.json` from GCS.
2. **Load** `HR_Pro/data/model_features.csv` from GCS.
3. **Compute** `feature_means` per-feature (skipna), only for features the
   meta declares in `meta["features"]`.
4. **Patch** `feature_means` + `feature_means_computed_at` +
   `feature_means_source_rows` into the meta. Everything else is preserved.
5. **Write to GCS** with archive + latest pointer pattern:
   - `HR_Pro/models/model_meta_hr_v6.json`               (latest, overwritten)
   - `HR_Pro/models/archive/model_meta_hr_v6.{ts}.json`  (archive, immutable)

The booster (`xgb_hr_v6.json`) is not touched.

## Why this exists

The v6 model artifact arrived without a `feature_means` field. The HR
runner uses `feature_means` to fill NaN at predict time so missing
features (live weather, missing pitcher rows, unposted lineups) inherit
the training-set mean rather than NaN. Without it, predictions degrade
silently on partial inputs — a problem the recent SGO odds rollout
exposed once predictions actually started running end-to-end.

The HR runner has a graceful fallback (lets XGBoost handle NaN natively
when `feature_means` is empty), so this patch is an improvement rather
than a fix-for-broken — but it tightens predict-time behavior.

## Difference from F5 retrain

None, by design. This is a copy-paste of `retrain_f5_meta.py` pointed
at HR paths. Same archive-then-latest pattern, same compute logic, same
structured `run()` return shape, same `main()` exit-code contract.

## Difference from NRFI v17 retrain

- **NRFI v17 retrain** produces both the booster and the meta from
  scratch (full Section 8 + 8b training run, ~2-5 minutes).
- **HR meta retrain** only patches the meta (~30-60 seconds, no
  training).

If/when HR v7 is built and a full retrain is needed, this script is the
wrong tool — write a `retrain_hr_v7.py` modeled on `retrain_nrfi_v17.py`.

## Prerequisite: meta must already exist with `features` key

This script **requires** `HR_Pro/models/model_meta_hr_v6.json` to exist
in GCS and contain a `features` key. If the meta is missing or has no
features key, the job returns `{"status": "error", ...}` and exits 1.

The runner (`runners/run_hr.py::_load_model`) has a fallback that uses
`booster.feature_names` when meta is missing — but this script does not
bootstrap a meta that way, because:

1. We'd need to download the booster (out of scope for a meta patch).
2. If meta is truly missing, that's a deployment issue, not a feature_means
   issue — surface it loudly rather than silently bootstrap.

**If the meta is missing entirely**, bootstrap it manually first:

```bash
# Quick bootstrap from booster.feature_names (one-time)
python -c "
import json, xgboost as xgb
from mlb_core.storage import download_model, write_bytes
from pathlib import Path
b = xgb.Booster()
local = download_model('HR_Pro/models/xgb_hr_v6.json', Path('/tmp/xgb_hr_v6.json'))
b.load_model(str(local))
meta = {
    'version': 'v6',
    'model_type': 'hr',
    'features': list(b.feature_names),
}
write_bytes(json.dumps(meta, indent=2).encode(), 'HR_Pro/models/model_meta_hr_v6.json')
print(f'bootstrapped meta with {len(b.feature_names)} features')
"
# Then run the retrain job to add feature_means
gcloud run jobs execute mlb-retrain-hr-meta --region=us-central1 --wait
```

## Provisioning the Cloud Run Job

One-time setup:

```bash
chmod +x deploy/setup_retrain_hr_meta.sh
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_retrain_hr_meta.sh
```

Idempotent — re-running updates the job in place.

## Triggering

```bash
gcloud run jobs execute mlb-retrain-hr-meta \
  --region=us-central1 --project=concrete-crow-445205-m4 --wait
```

Expected runtime: **30-60 seconds** (meta load + CSV read + means compute
+ 2 writes). Job timeout is 600s.

## Verifying the output

```bash
# Confirm latest pointer updated
gsutil ls -l gs://${MLB_GCS_BUCKET}/HR_Pro/models/model_meta_hr_v6.json

# Confirm an archive was created
gsutil ls gs://${MLB_GCS_BUCKET}/HR_Pro/models/archive/

# Confirm feature_means populated
gsutil cat gs://${MLB_GCS_BUCKET}/HR_Pro/models/model_meta_hr_v6.json \
  | jq '{features_count: (.features | length), means_count: (.feature_means | length)}'
```

`features_count` and `means_count` should be close. The runner logs a
warning at startup if `feature_means` is short — check the next HR run
log to confirm:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND textPayload:"HR model loaded"' \
  --limit=5 --format='value(textPayload)' \
  --project=concrete-crow-445205-m4
```

You should see `feature_means=<N>` where N > 0 (was 0 before the patch).

## Rollback

If a patch turns out wrong, restore the previous archive over the latest
pointer:

```bash
# List archives
gsutil ls gs://${MLB_GCS_BUCKET}/HR_Pro/models/archive/

# Pick the previous one and copy it over the latest
gsutil cp \
  gs://${MLB_GCS_BUCKET}/HR_Pro/models/archive/model_meta_hr_v6.{PREVIOUS_TS}.json \
  gs://${MLB_GCS_BUCKET}/HR_Pro/models/model_meta_hr_v6.json
```

The booster is never touched by this job, so there's no booster rollback
to worry about.

## When to re-run

- After regenerating `HR_Pro/data/model_features.csv` with a different
  date range or feature engineering change.
- After updating the meta's `features` list (which would invalidate
  existing `feature_means` keys anyway).
- Generally: not on a schedule. Training-set means don't drift fast
  enough to warrant nightly recomputation. Quarterly is plenty.

## Gotchas

- **Meta `features` key is the source of truth, not the booster.** This
  script trusts `meta["features"]`. If those drift apart from
  `booster.feature_names`, you'll get a runner-side `feature_names
  mismatch` error at predict time — fix that by syncing the meta, not by
  re-running this script. (See `runners/run_hr.py` for the recent fix
  that removed a hardcoded HR_FEATURES list that drifted.)
- **`MLB_GCS_BUCKET` must be set.** The script refuses to run in local
  mode because the retrain Job runs in GCS-only land.
- **No Cloud SQL binding.** This Job writes only to GCS, never to
  Postgres. Don't add `--add-cloudsql-instances` to the setup script.
- **Discord not wired.** The Job logs its result to stderr as JSON; it
  does not post to Discord. If you want a Discord ping, wrap the
  execution in a Cloud Scheduler + Pub/Sub + Cloud Function, or just
  read logs after manual triggers.
