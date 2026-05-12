#!/usr/bin/env bash
# setup_retrain_job.sh — provision the Cloud Run Job for F5 meta patching.
#
# Reuses the same image, service account, and secrets as the main mlb-betting
# Cloud Run service. Idempotent: creating it twice is safe.
#
# Prerequisites:
#   - Main service has been deployed (image exists in Artifact Registry)
#   - mlb-betting-sa exists with appropriate IAM
#   - You've authenticated to gcloud
#
# Usage:
#   chmod +x deploy/setup_retrain_job.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_retrain_job.sh
#
# Manual run after setup:
#   gcloud run jobs execute mlb-retrain-f5-meta \
#     --region=us-central1 --project=concrete-crow-445205-m4 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-retrain-f5-meta"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== F5 Meta Retrain Job Setup ==="
echo "Project : $PROJECT_ID"
echo "Region  : $REGION"
echo "Job     : $JOB_NAME"
echo "Image   : $IMAGE"
echo "SA      : $SA_EMAIL"
echo ""

# ── Verify prerequisites ────────────────────────────────────────────────────
echo "Verifying mlb-betting-sa exists..."
gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found. Run deploy.sh first."; exit 1; }

echo "Verifying image exists..."
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Deploy the main service first."; exit 1; }

# ── Create or update the job ────────────────────────────────────────────────
# Cloud Run Jobs syntax: `create` fails if it exists, `update` fails if it doesn't.
# Try create; if it fails, fall back to update.
COMMAND="python,-m,training.retrain_f5_meta"

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists — updating..."
  gcloud run jobs update "$JOB_NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
    --command="python" \
    --args="-m,training.retrain_f5_meta" \
    --memory=2Gi \
    --cpu=2 \
    --task-timeout=600 \
    --max-retries=1 \
    --quiet
else
  echo "Job not found — creating..."
  gcloud run jobs create "$JOB_NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
    --command="python" \
    --args="-m,training.retrain_f5_meta" \
    --memory=2Gi \
    --cpu=2 \
    --task-timeout=600 \
    --max-retries=1 \
    --quiet
fi

echo ""
echo "=== Job ready ==="
echo ""
echo "Trigger manually:"
echo "  gcloud run jobs execute $JOB_NAME \\"
echo "    --region=$REGION --project=$PROJECT_ID --wait"
echo ""
echo "View execution logs:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION"
echo "  gcloud run jobs executions logs read <EXECUTION_NAME> --region=$REGION"
