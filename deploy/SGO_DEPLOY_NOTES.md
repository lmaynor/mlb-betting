# SGO Integration — Deploy Notes

## Files changed / added in this PR

```
mlb_core/odds/sgo.py                       NEW    SGO client + 4 extractors
runners/snapshot_odds.py                   NEW    Fetches slate, writes to GCS
runners/run_hr.py                          EDIT   Reads SGO snapshot instead of Odds API
main.py                                    EDIT   Adds /snapshot-odds endpoint
tests/test_sgo_extractors.py               NEW    Unit tests (8 tests, all green locally)
deploy/add_snapshot_schedulers.sh          NEW    Idempotent scheduler registration
deploy/SGO_DEPLOY_NOTES.md                 NEW    This file
```

`mlb_core/odds/the_odds_api.py` can be deleted in a follow-up PR once we've
verified SGO is reliable for 1-2 weeks. Leaving it in tree for now — nothing
imports it after this change, but easy to revert if needed.

## What this does and doesn't change

**Changes:**
- HR Pro v6 now reads DK odds from `gs://.../Odds/sgo/latest.json` instead of
  The Odds API. Function name `_fetch_hr_odds` is preserved; only its body
  changed.
- Removed `ODDS_API_KEY` / `ODDS_API_BASE` constants from `run_hr.py`.
- Removed one duplicate `batter_launch_speed_L20` entry in `HR_FEATURES`
  (was listed twice; now 34 unique features).
- New Cloud Run endpoint `POST /snapshot-odds` triggers one SGO fetch.
- Two new Cloud Scheduler jobs trigger snapshots before each HR run.

**Untouched:**
- HR Pro feature builder (`runners/build_hr_features.py`)
- NRFI / F5 / K runners (still stubs; extractors are ready but unused)
- The NRFI `pitcher_is_home` inversion bug (separate PR)
- The 35 → 34 feature deduplication does not require model retraining;
  XGBoost was already silently deduping at predict time via `feature_names`.

## Deploy steps (in order)

### 1. Push to `main`

After review, push the commit. CI will build a new Docker image.

### 2. Add the SGO_API_KEY secret

The new code reads `SGO_API_KEY` from the environment. From Cloud Shell:

```bash
# Store the key
echo -n "beceb4f0a660a1204cbc735b9a3082f2" | \
  gcloud secrets create sgo-api-key --data-file=- --project=concrete-crow-445205-m4 \
  || echo -n "beceb4f0a660a1204cbc735b9a3082f2" | \
  gcloud secrets versions add sgo-api-key --data-file=- --project=concrete-crow-445205-m4

# Grant the runtime SA access
gcloud secrets add-iam-policy-binding sgo-api-key \
  --member="serviceAccount:mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor \
  --project=concrete-crow-445205-m4
```

### 3. Redeploy Cloud Run with the new secret wired in

The existing `--set-secrets` line in `deploy.sh` needs `SGO_API_KEY=sgo-api-key:latest`
added. Easiest way without re-running the full bootstrap:

```bash
gcloud run services update mlb-betting \
  --region=us-central1 \
  --update-secrets="SGO_API_KEY=sgo-api-key:latest" \
  --project=concrete-crow-445205-m4
```

Then redeploy the image (your normal CI path), or:

```bash
gcloud run deploy mlb-betting --region=us-central1 \
  --image=gcr.io/concrete-crow-445205-m4/mlb-betting:latest
```

### 4. Trigger one snapshot manually to verify

```bash
SERVICE_URL=$(gcloud run services describe mlb-betting \
  --region=us-central1 --format='value(status.url)')

curl -X POST "$SERVICE_URL/snapshot-odds" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected response (JSON):
```json
{
  "status": "ok",
  "events": 15,
  "archive_key": "Odds/sgo/2026-05-12/snapshot_2200.json",
  "latest_key":  "Odds/sgo/latest.json",
  "objects_consumed": 15,
  "size_kb": 18345.2
}
```

Verify in GCS:
```bash
gcloud storage ls gs://concrete-crow-445205-m4-mlb-data/Odds/sgo/
gcloud storage ls gs://concrete-crow-445205-m4-mlb-data/Odds/sgo/$(date +%F)/
```

### 5. Register the scheduler jobs

```bash
chmod +x deploy/add_snapshot_schedulers.sh
PROJECT_ID=concrete-crow-445205-m4 ./deploy/add_snapshot_schedulers.sh
```

This creates two jobs:
- `mlb-snapshot-morning` at 14:55 UTC (10:55 AM ET, ~5 min before HR morning)
- `mlb-snapshot-evening` at 20:55 UTC (4:55 PM ET, ~5 min before HR evening)

Both POST to `/snapshot-odds`.

### 6. Validate end-to-end by triggering one of the schedulers

```bash
gcloud scheduler jobs run mlb-snapshot-morning \
  --location=us-central1 --project=concrete-crow-445205-m4

# Watch Cloud Run logs
gcloud logging tail "resource.type=cloud_run_revision AND \
  resource.labels.service_name=mlb-betting" \
  --project=concrete-crow-445205-m4
```

## Budget guardrails

- SGO Amateur tier: **2,500 objects/month** (confirmed via /account/usage on 2026-05-12)
- 2× daily snapshots × 15 games × 30 days = **900 objects/month**
- Headroom: 1,600 objects for doubleheaders, debugging, manual probes
- Each snapshot reports `objects_consumed` in its response — monitor for drift

If usage trends higher than expected (e.g. > 1,200 in a month) inspect:
```bash
curl -sS https://api.sportsgameodds.com/v2/account/usage \
  -H "x-api-key: $SGO_API_KEY" | jq .data.rateLimits[\"per-month\"]
```

## Rollback

If anything goes wrong, revert the Cloud Run revision:
```bash
gcloud run revisions list --service=mlb-betting --region=us-central1
gcloud run services update-traffic mlb-betting \
  --to-revisions=mlb-betting-00021-q2n=100 \
  --region=us-central1
```

The Odds API code is gone from `run_hr.py`, so reverting the *file* (not the
deployment) requires `git revert` of the commit. The previous revision
(`mlb-betting-00021-q2n` per the handoff) still has the Odds API code baked in
and will work as before until you redeploy.

## What's not done — next sessions

- Wire NRFI runner to SGO snapshot (uses `sgo.extract_nrfi_odds`)
- Port F5 runner (uses `sgo.extract_f5_odds`)
- Port K runner (uses `sgo.extract_k_odds`)
- Delete `mlb_core/odds/the_odds_api.py` after 1-2 weeks of clean SGO data
- NRFI feature builder `pitcher_is_home` inversion fix (separate PR)
- Upload v17 model artifacts to `gs://.../NRFI_Pro_System/models/`
