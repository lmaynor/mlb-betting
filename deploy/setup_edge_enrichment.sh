#!/usr/bin/env bash
# setup_edge_enrichment.sh -- provision the Edge enrichment Job + schedulers.
#
# Precomputes per-pick enrichment (spray/ev_la/recent_form/velo/release/zone/weather)
# for the beezy.fyi "The Edge" cockpit, so the public API never reads the 300MB
# Statcast master per request. Writes Enrich/edge/{date}.json. Fail-soft.
#
# Reuses the mlb-betting image + SA. Reads MLB_DB_URL + MLB_GCS_BUCKET from secrets,
# and attaches the Cloud SQL instance (the runner reads today's kelly-triggered picks
# from Postgres -- unlike the parlay accumulator it needs DB access).
#
# TIMING: enrichment reads TODAY's scored picks, which do not exist until the betting
# runs log them at 16:00 UTC (morning) and 22:00 UTC (evening). So we schedule it
# AFTER each run -- 16:20 and 22:20 UTC -- NOT after refresh-data (14:00, too early).
# The evening run also benefits from the 21:00 UTC statcast refresh.
#
# Prereq: image rebuilt with runners/build_edge_enrichment.py (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_edge_enrichment.sh
#   # override crons if desired:
#   SCHEDULE_AM="20 16 * * *" SCHEDULE_PM="20 22 * * *" bash ./deploy/setup_edge_enrichment.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-build-edge-enrichment"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"

# enrichment must run AFTER picks are scored (16:00 / 22:00 UTC betting runs)
SCHEDULE_AM="${SCHEDULE_AM:-20 16 * * *}"
SCHEDULE_PM="${SCHEDULE_PM:-20 22 * * *}"

echo "=== Edge enrichment setup ==="
echo "Job : $JOB_NAME"
echo "Crons (UTC): AM='$SCHEDULE_AM'  PM='$SCHEDULE_PM'"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-cloudsql-instances="$INSTANCE"
  --set-secrets="MLB_DB_URL=mlb-db-url:latest,MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,runners.build_edge_enrichment"
  --memory=2Gi --cpu=1 --task-timeout=900 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
make_sched() {
  local sjob="$1" cron="$2"
  local flags=(
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
}

make_sched "${JOB_NAME}-am" "$SCHEDULE_AM"
make_sched "${JOB_NAME}-pm" "$SCHEDULE_PM"

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo "Validate:"
echo "  gcloud storage cat gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/Enrich/edge/\$(TZ=America/Chicago date +%F).json | head -c 1200"
