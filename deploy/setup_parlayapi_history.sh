#!/usr/bin/env bash
# setup_parlayapi_history.sh -- provision the daily ParlayAPI -> odds_history job.
#
# Normalizes the ParlayAPI snapshots banked in OddsAccum/baseball_mlb/ into the
# odds_history Parquet store (source="parlayapi") -- the FORWARD odds feed.
# Reads only from GCS (no ParlayAPI key, no DB). Runs once/day on the recent
# window (--days-back 3, idempotent re-dedup) after the day's snapshots land.
#
# Historical odds_history comes from BettingPros (mlb-backfill-bettingpros);
# this job keeps the store current going forward.
#
# Prereq: image rebuilt with pyarrow + mlb/analysis (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_parlayapi_history.sh
#   # default daily schedule 04:10 UTC (after the 03:25 snapshot); override with SCHEDULE=
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
SCHEDULE="${SCHEDULE:-10 4 * * *}"     # daily 04:10 UTC; "" = job only, no scheduler
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="mlb-parlayapi-history"

echo "=== ParlayAPI -> odds_history setup ($JOB_NAME) ==="

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,mlb.analysis.parlayapi_to_history,--days-back,3"
  --memory=1Gi --cpu=1 --task-timeout=1800 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

if [ -n "$SCHEDULE" ]; then
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
  SFLAGS=(
    --location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC"
    --uri="$URI" --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=320s --project="$PROJECT_ID"
  )
  if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$JOB_NAME" "${SFLAGS[@]}"
  else
    gcloud scheduler jobs create http "$JOB_NAME" "${SFLAGS[@]}"
  fi
  echo "Scheduler set: $JOB_NAME @ '$SCHEDULE' UTC"
fi

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "Output -> gs://<bucket>/Odds/history/  (source=parlayapi partitions)"
