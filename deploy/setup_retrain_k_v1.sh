#!/usr/bin/env bash
# setup_retrain_k_v1.sh — provision the Cloud Run Job for K Pro v1 full retrain.
#
# Mirrors deploy/setup_retrain_job.sh (F5 meta patcher). Unlike F5 which only
# patches feature_means into existing meta, this job runs the full notebook
# Section 8 + 8b training pipeline: OOS eval + full retrain on 100% of data.
# That's why this job gets 30min timeout vs F5's 10min.
#
# Reuses the same image, service account, and secrets as the main mlb-betting
# Cloud Run service. Idempotent: running it twice is safe.
#
# Prerequisites:
#   - Main service has been deployed (image exists in Artifact Registry)
#   - mlb-betting-sa exists with appropriate IAM
#   - K_Pro_System/data/model_features.csv exists in GCS
#   - You've authenticated to gcloud
#
# Usage:
#   chmod +x deploy/setup_retrain_k_v1.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_retrain_k_v1.sh
#
# Manual run after setup:
#   gcloud run jobs execute mlb-retrain-k-v1 \
#     --region=us-central1 --project=concrete-crow-445205-m4 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-retrain-k-v1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== K Pro v1 Retrain Job Setup ==="
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

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists — updating..."
  gcloud run jobs update "$JOB_NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
    --command="python" \
    --args="-m,training.retrain_k_v1" \
    --memory=2Gi \
    --cpu=2 \
    --task-timeout=1800 \
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
    --args="-m,training.retrain_k_v1" \
    --memory=2Gi \
    --cpu=2 \
    --task-timeout=1800 \
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
echo "Expected runtime: ~2-5 minutes (OOS eval + full retrain on ~31K rows)"
echo ""
echo "After successful run, verify GCS:"
echo "  gsutil ls gs://\${MLB_GCS_BUCKET}/K_Pro_System/models/"
echo ""
echo "  Should show:"
echo "    xgb_k_v1.json"
echo "    model_meta_v1.json"
echo "    archive/xgb_k_v1.{ts}.json"
echo "    archive/model_meta_v1.{ts}.json"
echo ""
echo "View execution logs:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION"
echo "  gcloud run jobs executions logs read <EXECUTION_NAME> --region=$REGION"
