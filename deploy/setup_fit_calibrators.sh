#!/usr/bin/env bash
# setup_fit_calibrators.sh -- provision the Cloud Run Job that fits per-system
# prediction calibrators (Task #3) from settled bets.
#
# Unlike the retrain jobs (GCS-only), this job READS THE BETS TABLE, so it needs
# Cloud SQL access (--set-cloudsql-instances + MLB_DB_URL secret) in addition to
# the GCS bucket secret.
#
# Reuses the production image, SA, and secrets. Idempotent.
#
# Usage:
#   chmod +x deploy/setup_fit_calibrators.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_fit_calibrators.sh
#
# Run after the service is deployed with the calibration code, before the next
# betting run so calibrators exist when the runners load them:
#   gcloud run jobs execute mlb-fit-calibrators --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-fit-calibrators"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"

echo "=== Fit-calibrators Job Setup ==="
echo "Project  : $PROJECT_ID"
echo "Job      : $JOB_NAME"
echo "Image    : $IMAGE"
echo "CloudSQL : $INSTANCE"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found. Deploy the service first."; exit 1; }

COMMON_ARGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-cloudsql-instances="$INSTANCE"
  --set-secrets="MLB_DB_URL=mlb-db-url:latest,MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m,mlb.training.fit_prediction_calibrators"
  --memory=2Gi
  --cpu=2
  --task-timeout=900
  --max-retries=1
)

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."
  gcloud run jobs update "$JOB_NAME" "${COMMON_ARGS[@]}" --quiet
else
  echo "Job not found -- creating..."
  gcloud run jobs create "$JOB_NAME" "${COMMON_ARGS[@]}" --quiet
fi

echo ""
echo "=== Job ready ==="
echo "Run it:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo ""
echo "Then verify calibrators landed in GCS:"
echo "  gsutil ls gs://\${MLB_GCS_BUCKET}/Calibration/"
echo ""
echo "Re-run weekly (or after notable bet volume) to refresh calibrators."
