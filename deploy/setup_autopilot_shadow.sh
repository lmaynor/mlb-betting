#!/usr/bin/env bash
# setup_autopilot_shadow.sh -- provision the AUT-01 Phase 0 LIVE shadow runner (ev/autopilot/shadow_job.py).
#
#   job  autopilot-shadow   python -m ev.autopilot.shadow_job --from-day 2026-11-16
#
# WHAT IT DOES (ev/autopilot/shadow_job.py): one firing re-derives the Phase 0 shadow ledger for the
# 14-day live window from the DEC-08 accumulator bank (OddsAccum/{feed}/raw/{ET day}/), attaches
# closes from the same snapshot series, and banks the derived ledger to
#   gs://<bucket>/Autopilot/shadow_ledger/latest.jsonl.gz (+ thin-anchor annex, + a create-only
#   per-firing summary under Autopilot/shadow_ledger/runs/).
# SHADOW ONLY: the package has no order path (asserted in tests/test_autopilot_shadow.py); a "fill"
# is a simulated row in the private shadow ledger at the banked ask. Nothing here places, mirrors,
# or routes an order, and no venue credential is mounted.
#
# ARMING: AUTOPILOT_SHADOW_ARMED=1 is set ONLY in this job's environment -- that env var is the
# kill switch (ev/autopilot/config.py). It is set nowhere else: not on any other job, not in the
# repo, not locally. Removing it (or pausing the scheduler) disarms the shadow. AUTOPILOT_ENABLE_SAVES
# is deliberately NOT set: saves stay off behind the Dec 21 gate.
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket, already used by every
# feeds job) -- the bucket name. GCS access is the job's service account (mlb-betting-sa). The
# script fails loudly if mlb-gcs-bucket is missing instead of creating or guessing a secret.
#
# TIMING: daily 06:30 America/Chicago, after closing-panel-daily (06:00 CT), so each firing scores
# the prior ET day's completed snapshot series with closes attached. WINDOW: the 14-day live shadow
# runs 2026-11-16 to 2026-11-30 (ET snapshot days, pinned by --from-day). REVIEW/DISABLE: Dec 1, 2026
# (pause or delete the scheduler once the window is scored).
#
# IMAGE: defaults to the feeds image per docs/solutions/conventions/cloud-run-job-image-tag-resolution.md.
# The runner code merged after the last feeds build, so run the job on a TAG built from current main:
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=autopilot-shadow-20261116 --project=$PROJECT_ID .
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:autopilot-shadow-20261116 PROJECT_ID=... bash deploy/setup_autopilot_shadow.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_autopilot_shadow.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_autopilot_shadow.sh          # pause the scheduler (disarm)
#   Run once now:  gcloud run jobs execute autopilot-shadow --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="autopilot-shadow"
SJOB="${JOB}-daily"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
FROM_DAY="${FROM_DAY:-2026-11-16}"
SCHEDULE="${SCHEDULE:-30 6 * * *}"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Autopilot shadow scheduler paused (shadow disarmed at the scheduler; the job env still carries the arm flag)."
  exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see the IMAGE note at the top of this script)."; exit 1; }
gcloud secrets describe mlb-gcs-bucket --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: secret mlb-gcs-bucket is missing. This script never creates secrets; provision it (the bucket name) and re-run."; exit 1; }

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},AUTOPILOT_SHADOW_ARMED=1"
  --command="python" --args="-m,ev.autopilot.shadow_job,--from-day,${FROM_DAY}"
  --memory=4Gi --cpu=1 --task-timeout=1800 --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="$SCHEDULE" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=180s
        --description="AUT-01 Phase 0 live shadow, 14-day window 2026-11-16 to 2026-11-30 (ET). Review/disable Dec 1, 2026."
        --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${SCHEDULE}' America/Chicago -> ${JOB}"

echo ""
echo "=== Done: job $JOB (${IMAGE}) + scheduler $SJOB ==="
echo "Run once now: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Logs:         gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Ledger:       gcloud storage ls gs://<bucket>/Autopilot/shadow_ledger/"
echo "Not enabled:  saves (AUTOPILOT_ENABLE_SAVES unset), any order path (none exists in the package)."
