#!/usr/bin/env bash
# setup_bettingpros_history.sh -- provision the one-shot BettingPros -> odds_history job.
#
# Normalizes the BettingPros historical backfill (Odds/bettingpros/{market}/*.csv,
# written by mlb-backfill-bettingpros) into the odds_history Parquet store
# (source="bettingpros"). This is the DEEP HISTORICAL load (2024->present, all 29
# markets), distinct from the daily ParlayAPI forward feed (mlb-parlayapi-history).
#
# Long-running: resolves game_pk per date via the MLB Stats API across hundreds of
# dates, so it must run as a Cloud Run Job (longer than a Cloud Shell session).
# One-shot -- NO scheduler; execute on demand after the backfill completes.
#
# GCS-only (no ParlayAPI key, no DB). Needs the image rebuilt with pyarrow +
# mlb/analysis (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_bettingpros_history.sh
#   gcloud run jobs execute mlb-bettingpros-history --region=us-central1
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="mlb-bettingpros-history"

echo "=== BettingPros -> odds_history setup ($JOB_NAME) ==="

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,mlb.analysis.bettingpros_to_parquet,--markets,all"
  --memory=4Gi --cpu=2 --task-timeout=7200 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

echo ""
echo "Run it (after the mlb-backfill-bettingpros backfill completes):"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo "Re-runnable: write_partition merges+dedups on DEDUP_KEYS (finding C4.4 --"
echo "append=True as of 2026-08-17, was overwrite-by-default), so re-executing is safe."
echo "For an INCREMENTAL re-run (new dates only, not the full multi-year history"
echo "every time -- finding C4.4's cloud-cost half), override the job's default args:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait \\"
echo "    --args=\"-m,mlb.analysis.bettingpros_to_parquet,--markets,all,--since,YYYY-MM-DD\""
echo "Output -> gs://<bucket>/Odds/history/  (source=bettingpros partitions)"
