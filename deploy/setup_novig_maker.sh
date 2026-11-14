#!/usr/bin/env bash
# setup_novig_maker.sh -- provision the NOV-03 maker study launch runner as a Cloud Run Job.
#
#   job  novig-maker-study   python -m ev.novig.maker_runner
#
# PAPER ONLY. Study window 2026-11-14..28. Real-money stays refused (the only client the
# runner builds is the paper sandbox + public read-only catalog).
# Scheduler is a separate decision (NO scheduler provisioned here).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 BUCKET=my-bucket bash deploy/setup_novig_maker.sh
#   Run once now:
#     gcloud run jobs execute novig-maker-study --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="novig-maker-study"
BUCKET="${BUCKET:?Set BUCKET env var (or pass MLB_GCS_BUCKET)}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-1500}"
TASK_TIMEOUT="${TASK_TIMEOUT:-1800}"

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see branch tag notes)."; exit 1; }

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-env-vars="MLB_GCS_BUCKET=${BUCKET},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},NOVIG_PAPER_TRADING_KEY_PATH=/secrets/novig/paper_trading.pem"
  --set-secrets="NOVIG_PAPER_TRADING_KEY_ID=novig-paper-trading-key-id:latest"
  --add-volume="name=novig-paper-key,type=secret,secret=novig-paper-trading-private-key,items=latest:paper_trading.pem"
  --add-volume-mount="volume=novig-paper-key,mount-path=/secrets/novig"
  --command="python" --args="-m,ev.novig.maker_runner,--max-runtime-sec,${MAX_RUNTIME_SEC}"
  --memory=2Gi --cpu=1 --task-timeout="${TASK_TIMEOUT}" --max-retries=0 --parallelism=1 --tasks=1 --quiet
)

if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

echo "Job $JOB configured successfully (NO scheduler provisioned)."
echo "To execute manually:"
echo "  gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
