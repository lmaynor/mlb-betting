#!/usr/bin/env bash
# setup_nba_kaggle_ingest.sh -- provision the overnight Kaggle -> GCS ingest Job.
#
# Mirrors the eoinamoore historical NBA dataset (stats.nba.com) into GCS under
# NBA/stats_nba/raw/. Reuses the mlb-betting image + service account. Reads
# Kaggle credentials from Secret Manager (kaggle-username, kaggle-key).
#
# Idempotent: safe to re-run.
#
# Prerequisites:
#   1. Image rebuilt WITH nba/ + kagglehub -- run ./deploy/deploy_service.sh after merge.
#   2. Kaggle API token created at kaggle.com/settings -> "Create New Token"
#      (downloads kaggle.json containing {"username","key"}), then stored as secrets:
#
#        echo -n "YOUR_KAGGLE_USERNAME" | gcloud secrets create kaggle-username \
#          --data-file=- --project=concrete-crow-445205-m4
#        echo -n "YOUR_KAGGLE_KEY" | gcloud secrets create kaggle-key \
#          --data-file=- --project=concrete-crow-445205-m4
#        # grant the runtime SA access:
#        for S in kaggle-username kaggle-key; do
#          gcloud secrets add-iam-policy-binding $S \
#            --member="serviceAccount:mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com" \
#            --role="roles/secretmanager.secretAccessor" --project=concrete-crow-445205-m4
#        done
#
# Usage:
#   chmod +x deploy/setup_nba_kaggle_ingest.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_nba_kaggle_ingest.sh
#
# Run it (overnight, one-shot):
#   gcloud run jobs execute nba-kaggle-ingest --region=us-central1 --project=concrete-crow-445205-m4
#   # (omit --wait so it runs detached; check logs/the last_ingest.json sentinel later)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="nba-kaggle-ingest"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== NBA Kaggle ingest setup ==="
echo "Job   : $JOB_NAME"
echo "Image : $IMAGE"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# Generous memory: kagglehub caches to a memory-backed FS on Cloud Run, so memory
# must exceed the dataset size. 16Gi/4cpu covers the current dataset with headroom.
# task-timeout 2h -- "as slow as possible" overnight batch.
JOB_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,KAGGLE_USERNAME=kaggle-username:latest,KAGGLE_KEY=kaggle-key:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m,nba.data.kaggle_ingest"
  --memory=16Gi
  --cpu=4
  --task-timeout=7200
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

echo ""
echo "Done. Kick off the overnight ingest with:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "Then check gs://<bucket>/NBA/stats_nba/last_ingest.json when it finishes."
