#!/usr/bin/env bash
# setup_fast_alert.sh -- provision the fast +EV alert loop (the pager).
#
# Cloud Run Job runs mlb.runners.fast_alert_loop every 15 minutes inside the
# 19:00-23:45 UTC strike window: lineup-event detection -> free BettingPros
# snapshot -> Pinnacle-anchored outlier scan -> Discord alert on NEW +EV
# quotes only (per-day dedup, capped per run). Off-window nothing runs.
#
# GCS only (no DB). Free data source, so 20 runs/day costs only the job compute
# (~1 min each on 1Gi).
#
# Prereq: image rebuilt with mlb/runners/fast_alert_loop.py
# (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_fast_alert.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-fast-alert"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# every 15 min, 19:00-23:45 UTC (2-7pm ET: lineups post -> closing window)
SCHEDULE="*/15 19-23 * * *"

echo "=== Fast alert loop setup ==="
echo "Job: $JOB_NAME  schedule='$SCHEDULE' UTC (20 runs/day in strike window)"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# BP_MARKETS commas -> ^@^ alternate delimiter
ENVV="^@^GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@BP_MARKETS=player@BP_DAYS=1@FAL_MIN_EV=0.03@FAL_MIN_BOOKS=4@FAL_MAX_POSTS=10"
JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest"
  --set-env-vars="$ENVV"
  --command="python3" --args="-m,mlb.runners.fast_alert_loop"
  --memory=1Gi --cpu=1 --task-timeout=840 --max-retries=0 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
sjob="${JOB_NAME}-loop"
flags=(
  --location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC"
  --uri="$URI" --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=900s --project="$PROJECT_ID"
)
if gcloud scheduler jobs describe "$sjob" --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$sjob" "${flags[@]}"
else
  gcloud scheduler jobs create http "$sjob" "${flags[@]}"
fi
echo "Scheduler set: $sjob @ '$SCHEDULE' UTC"

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --wait"
echo "Pause off-season / to stop pings:"
echo "  gcloud scheduler jobs pause $sjob --location=$REGION"
