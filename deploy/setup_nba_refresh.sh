#!/usr/bin/env bash
# setup_nba_refresh.sh -- provision the nightly NBA ingest Job + Scheduler.
#
# Reuses the mlb-betting image, service account, and MLB_GCS_BUCKET secret.
# The job runs `python -m nba.data.refresh` (yesterday's NBA boxscores ->
# appends to NBA/ masters). Runs year-round; no-ops on empty days, so the same
# daily schedule auto-starts when the new season's games appear (~late Oct).
#
# Idempotent: safe to re-run (create-or-update for both job and scheduler).
#
# Prerequisites:
#   - The image has been (re)built WITH nba/ in it -- run ./deploy/deploy_service.sh
#     after merging the NBA branch so gcr.io/$PROJECT/mlb-betting:latest contains nba/.
#   - mlb-betting-sa and scheduler-invoker SAs exist.
#
# Usage:
#   chmod +x deploy/setup_nba_refresh.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_nba_refresh.sh
#
# Manual test run after setup:
#   gcloud run jobs execute nba-refresh-data --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="nba-refresh-data"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="0 13 * * *"                              # 13:00 UTC daily (8am ET buffer)
JOB_RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

echo "=== NBA nightly refresh setup ==="
echo "Project : $PROJECT_ID"
echo "Job     : $JOB_NAME"
echo "Image   : $IMAGE"
echo "SA      : $SA_EMAIL"
echo ""

echo "Verifying $SA_EMAIL exists..."
gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }

echo "Verifying image exists..."
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# -- Cloud Run Job (no Cloud SQL -- NBA refresh never touches the DB) ----------
JOB_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m,nba.data.refresh"
  --memory=2Gi
  --cpu=2
  --task-timeout=1800
  --max-retries=1
  --quiet
)

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."
  gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."
  gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

# -- Cloud Scheduler (OAuth + Run API v2 trigger; NOT OIDC -- see CONTEXT s9) --
SCHED_FLAGS=(
  --location="$REGION"
  --schedule="$SCHEDULE"
  --time-zone="Etc/UTC"
  --uri="$JOB_RUN_URI"
  --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=320s
  --project="$PROJECT_ID"
)

if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
  echo "Scheduler exists -- updating..."
  gcloud scheduler jobs update http "$JOB_NAME" "${SCHED_FLAGS[@]}"
else
  echo "Scheduler not found -- creating..."
  gcloud scheduler jobs create http "$JOB_NAME" "${SCHED_FLAGS[@]}"
fi

echo ""
echo "Done. Test now with:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
