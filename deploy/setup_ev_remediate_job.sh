#!/usr/bin/env bash
# setup_ev_remediate_job.sh -- provision the ONE-OFF Cloud Run Job that cleans up
# system='EV' rows in the bets table after the 2026-10-02 audit
# (mlb.analysis.ev_remediate; docs/solutions/logic-errors/
# ev-bet-type-ignores-side-and-line-misgrades.md).
#
# Needs Cloud SQL (--set-cloudsql-instances + MLB_DB_URL) and the GCS bucket
# (the alert-trail parquet files), same wiring as mlb-fit-calibrators. The job's
# default args are the READ-ONLY --stats mode; the destructive mode is only ever
# selected explicitly at execute time.
#
# Usage (image must already contain mlb/analysis/ev_remediate.py -- deploy first):
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_ev_remediate_job.sh
#   # 1) read-only persistence check + profile:
#   gcloud run jobs execute mlb-ev-remediate --region=us-central1 --wait
#   # 2) backup -> delete -> rebuild -> grade -> report:
#   gcloud run jobs execute mlb-ev-remediate --region=us-central1 --args="-m,mlb.analysis.ev_remediate,--apply" --wait
#   # read the output:
#   gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="mlb-ev-remediate"' --freshness=1h --format='value(textPayload)'
# Delete the job when done: gcloud run jobs delete mlb-ev-remediate --region=us-central1
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-ev-remediate"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"

COMMON_ARGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-cloudsql-instances="$INSTANCE"
  --set-secrets="MLB_DB_URL=mlb-db-url:latest,MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python3"
  --args="-m,mlb.analysis.ev_remediate,--stats"
  --memory=2Gi
  --cpu=1
  --task-timeout=3000
  --max-retries=0
)

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."
  gcloud run jobs update "$JOB_NAME" "${COMMON_ARGS[@]}" --quiet
else
  echo "Job not found -- creating..."
  gcloud run jobs create "$JOB_NAME" "${COMMON_ARGS[@]}" --quiet
fi
echo "=== $JOB_NAME ready (default args: --stats, read-only) ==="
