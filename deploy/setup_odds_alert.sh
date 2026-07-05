#!/usr/bin/env bash
# setup_odds_alert.sh -- provision the +EV outlier alert-and-resolve Job + schedulers.
#
# Runs AFTER the snapshot window: freshness-checks the feed, scans odds_history for
# books lagging the consensus (+EV), logs alerts to Alerts/{date}/log.parquet, and
# re-scores every alert against the latest consensus (Alerts/{date}/resolved.parquet)
# -> the per-market lag-vs-informed scorecard. GCS only (no DB). Reuses the image + SA.
#
# Schedules (UTC) trail the tracker/ParlayAPI snapshots so each run sees fresh lines:
#   21:15, 23:00, 23:45  (the last does end-of-day resolution).
#
# Prereq: image has mlb.runners.odds_alert (./deploy/deploy_service.sh or a :latest build).
#
# Usage: PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_odds_alert.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-odds-alert"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

OA_MARKETS="${OA_MARKETS:-hr_yn,outs_ou,btb_ou,bhits_ou,k_ou}"
OA_MIN_EV="${OA_MIN_EV:-0.03}"
OA_MIN_BOOKS="${OA_MIN_BOOKS:-4}"
SCHEDULES=("15 21 * * *" "00 23 * * *" "45 23 * * *")

echo "=== +EV alert job setup ==="
echo "Job: $JOB_NAME  markets=$OA_MARKETS  min_ev=$OA_MIN_EV  min_books=$OA_MIN_BOOKS"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# OA_MARKETS contains commas -> ^@^ alternate delimiter
ENVV="^@^GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@OA_MARKETS=${OA_MARKETS}@OA_MIN_EV=${OA_MIN_EV}@OA_MIN_BOOKS=${OA_MIN_BOOKS}"
JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="$ENVV"
  --command="python3" --args="-m,mlb.runners.odds_alert"
  --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
i=0
for cron in "${SCHEDULES[@]}"; do
  i=$((i+1)); sjob="${JOB_NAME}-${i}"
  flags=(
    --location="$REGION" --schedule="$cron" --time-zone="Etc/UTC"
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
  echo "Scheduler set: $sjob @ '$cron' UTC"
done

echo ""
echo "Run once now: gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo "Alerts:       gsutil ls gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/Alerts/"
