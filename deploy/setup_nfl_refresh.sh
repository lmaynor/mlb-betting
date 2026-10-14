#!/usr/bin/env bash
# setup_nfl_refresh.sh -- provision the nightly nflverse mirror Job + Scheduler.
#
# Reuses the mlb-betting image, service account, and MLB_GCS_BUCKET secret. The job runs
# `python -m nfl.data.ingest --mode refresh` (current season + every single-file dataset ->
# NFL/nflverse/ in the shared bucket). Byte-identical files are not rewritten (sha256 vs
# NFL/_manifest.json), so the offseason is a no-op. No DB, no secrets, no API key.
#
# Idempotent: safe to re-run (create-or-update for both job and scheduler).
#
# Prerequisites:
#   - The image has been (re)built WITH nfl/ in it (Dockerfile `COPY nfl/ ./nfl/`):
#     run ./deploy/deploy_service.sh after merging, so gcr.io/$PROJECT/mlb-betting:latest has nfl/.
#   - mlb-betting-sa and scheduler-invoker SAs exist.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_nfl_refresh.sh
#
# FIRST RUN is a backfill, not the nightly refresh (override the args for one execution):
#   gcloud run jobs execute nfl-refresh-data --region=us-central1 --wait \
#     --args="-m" --args="nfl.data.ingest" --args="--mode" --args="backfill"
# (repeated --args, not a comma list -- CONTEXT s15.9. Verify the override took by reading the execution's args.)
# Size it first from any machine:  python3 -m nfl.data.ingest --mode backfill --probe
#
# Check afterwards:  gs://<bucket>/NFL/last_refresh.json  (status ok|partial|error + per-file failures).
# NOTE: not wired into monitor_ops (same as NBA) -- a failed execution shows in Cloud Run only.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="nfl-refresh-data"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${NFL_SCHEDULE:-30 13 * * *}"            # 13:30 UTC daily (offset from nba-refresh-data at 13:00)
JOB_RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

echo "=== NFL nightly nflverse mirror setup ==="
echo "Project : $PROJECT_ID"
echo "Job     : $JOB_NAME"
echo "Image   : $IMAGE"
echo "SA      : $SA_EMAIL"
echo ""

echo "Verifying $SA_EMAIL exists..."
gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }

echo "Verifying image exists..."
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# -- Cloud Run Job (no Cloud SQL -- the mirror never touches the DB) ------------
JOB_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m"
  --args="nfl.data.ingest"
  --args="--mode"
  --args="refresh"
  --memory=4Gi
  --cpu=2
  --task-timeout=3600
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

# -- Cloud Scheduler (OAuth + Run API v2 trigger; NOT OIDC -- see CONTEXT s9) --
SCHED_FLAGS=(
  --location="$REGION"
  --schedule="$SCHEDULE"
  --time-zone="Etc/UTC"
  --uri="$JOB_RUN_URI"
  --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=320s
  --project="$PROJECT_ID"
)

if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
  echo "Scheduler exists -- updating..."
  gcloud scheduler jobs update http "$JOB_NAME" "${SCHED_FLAGS[@]}"
else
  echo "Scheduler not found -- creating..."
  gcloud scheduler jobs create http "$JOB_NAME" "${SCHED_FLAGS[@]}"
fi

echo ""
echo "Done. Run the one-time backfill (see header), then verify with:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --project=$PROJECT_ID"
