#!/usr/bin/env bash
# setup_track_bettingpros.sh -- provision the FREE BettingPros intraday tracker Job
# + multiple daily schedulers.
#
# BettingPros is a free public API, so we snapshot it several times a day for nothing.
# Each run banks today's (+ tomorrow's) lines into odds_history at a real snapshot_ts
# (source=bettingpros), using the FIXED per-line parser. Accumulating snapshots give
# genuine open->close line movement / CLV -- the data the model-vs-line analysis needs.
#
# GCS only (no DB, no Cloud SQL). Reuses the mlb-betting image + SA.
#
# Schedules (UTC) concentrate around when MLB lines post/move (afternoon->evening ET):
#   16:00, 19:00, 21:00, 22:30, 23:30  (late runs capture near-closing lines).
#
# Prereq: image rebuilt with runners/track_bettingpros.py + the per-line parser fix
# (./deploy/deploy_service.sh, or: gcloud builds submit --tag=$IMAGE).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_track_bettingpros.sh
#   BP_MARKETS="total_bases,hits" bash ./deploy/setup_track_bettingpros.sh   # scope markets
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-track-bettingpros"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

BP_MARKETS="${BP_MARKETS:-player}"   # player props (total_bases, hits, ...) by default
BP_DAYS="${BP_DAYS:-2}"              # today + tomorrow
# five snapshots/day, UTC, concentrated afternoon->close (ET line-move window)
SCHEDULES=("00 16 * * *" "00 19 * * *" "00 21 * * *" "30 22 * * *" "30 23 * * *")

echo "=== BettingPros tracker setup ==="
echo "Job: $JOB_NAME  markets=$BP_MARKETS  days=$BP_DAYS  snapshots/day=${#SCHEDULES[@]}"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# BP_MARKETS may contain commas -> ^@^ alternate delimiter for --set-env-vars.
ENVV="^@^GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@BP_MARKETS=${BP_MARKETS}@BP_DAYS=${BP_DAYS}"
JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="$ENVV"
  --command="python3" --args="-m,mlb.runners.track_bettingpros"
  --memory=1Gi --cpu=1 --task-timeout=1800 --max-retries=2 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
i=0
for cron in "${SCHEDULES[@]}"; do
  i=$((i+1))
  sjob="${JOB_NAME}-${i}"
  flags=(
    --location="$REGION" --schedule="$cron" --time-zone="Etc/UTC"
    --uri="$URI" --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=1800s --project="$PROJECT_ID"
  )
  if gcloud scheduler jobs describe "$sjob" --location="$REGION" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$sjob" "${flags[@]}"
  else
    gcloud scheduler jobs create http "$sjob" "${flags[@]}"
  fi
  echo "Scheduler set: $sjob @ '$cron' UTC"
done

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo "Verify snapshots accumulating:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --project=$PROJECT_ID"
