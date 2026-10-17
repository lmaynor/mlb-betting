#!/usr/bin/env bash
# setup_nba_injury_bank.sh -- provision the ESPN NBA injury-list banker Job + Scheduler.
#
# Runs `python -m nba.data.injury_bank` (one poll of the ESPN injuries endpoint; change-only, append-only store under
# NBA/injuries/espn/ + a heartbeat pointer). No DB, no secrets, no API key, no credits, no new dependency. The banked history is
# what lets availability features be compared to what the public list actually said before each tip.
#
# NOT RUN by the PR that added it -- provisioning is a deploy decision. Idempotent (create-or-update job + scheduler).
#
# IMAGE: feeds image, never mlb-betting:latest (docs/solutions/conventions/cloud-run-job-image-tag-resolution.md); rebuild it from
# merged main first (deploy/build_all_images.sh, SKIP_PROD=1).
#
# NOT covered here: the OFFICIAL NBA injury report (nba/data/nba_injury_report.py) parses PDFs with poppler's `pdftotext`, which is
# NOT in the Docker image -- banking it nightly needs `apt-get install -y poppler-utils` in a feeds-image Dockerfile (a decision
# for the image owner; the production image is deliberately untouched).
#
# Cadence: every 30 minutes through the US afternoon/evening game window (14:00-04:59 UTC). Check afterwards:
#   gs://<bucket>/NBA/injuries/espn/latest.json  (checked_at must keep advancing; fetched_at moves only when the list changes)
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_nba_injury_bank.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="nba-injury-bank"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${NBA_INJURY_BANK_SCHEDULE:-*/30 14-23,0-4 * * *}"   # every 30 min, 14:00-04:59 UTC
JOB_RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

echo "=== NBA injury-list banker setup ==="
echo "Project : $PROJECT_ID"
echo "Job     : $JOB_NAME"
echo "Image   : $IMAGE"
echo "SA      : $SA_EMAIL"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it from merged main first (deploy/build_all_images.sh)."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m"
  --args="nba.data.injury_bank"
  --memory=512Mi
  --cpu=1
  --task-timeout=300
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

# scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED (docs/solutions/runtime-errors/
# scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
  echo "Scheduler exists -- updating..."
  gcloud scheduler jobs update http "$JOB_NAME" "${SCHED_FLAGS[@]}"
else
  echo "Scheduler not found -- creating..."
  gcloud scheduler jobs create http "$JOB_NAME" "${SCHED_FLAGS[@]}"
fi

echo ""
echo "Done. Verify with:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --project=$PROJECT_ID"
