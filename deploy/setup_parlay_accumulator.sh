#!/usr/bin/env bash
# setup_parlay_accumulator.sh -- provision a ParlayAPI odds-accumulator Job (+ optional scheduler).
#
# Banks live odds snapshots forward into GCS (OddsAccum/{sport}/...). There is no
# historical-props API, so this is how prop history gets built. Sport-parameterized.
#
# Reuses the mlb-betting image + SA. Reads PARLAY_API_KEY + MLB_GCS_BUCKET from secrets.
#
# Prereqs:
#   1. Image rebuilt with nba/ (./deploy/deploy_service.sh after merge).
#   2. ParlayAPI key stored:
#        echo -n "YOUR_PARLAY_KEY" | gcloud secrets create parlay-api-key \
#          --data-file=- --project=concrete-crow-445205-m4
#        gcloud secrets add-iam-policy-binding parlay-api-key \
#          --member="serviceAccount:mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com" \
#          --role="roles/secretmanager.secretAccessor" --project=concrete-crow-445205-m4
#
# Usage (env-parameterized):
#   PROJECT_ID=concrete-crow-445205-m4 SPORT=baseball_mlb  KIND=props bash ./deploy/setup_parlay_accumulator.sh
#   PROJECT_ID=concrete-crow-445205-m4 SPORT=basketball_nba KIND=props bash ./deploy/setup_parlay_accumulator.sh
#   # then create a scheduler at your chosen cadence (see CADENCE block at the end).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
SPORT="${SPORT:-baseball_mlb}"
KIND="${KIND:-props}"
MAX_EVENTS="${MAX_EVENTS:-}"            # optional credit guard (free-tier POC)
SCHEDULE="${SCHEDULE:-}"               # optional cron, e.g. "0 */6 * * *"; empty = no scheduler
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# short sport tag for the job name (basketball_nba -> nba, baseball_mlb -> mlb)
TAG="${SPORT##*_}"
JOB_NAME="parlay-accum-${TAG}-${KIND}"

ARGS="-m,nba.odds.accumulator,--sport,${SPORT},--kind,${KIND}"
[ -n "$MAX_EVENTS" ] && ARGS="${ARGS},--max-events,${MAX_EVENTS}"

echo "=== ParlayAPI accumulator setup ==="
echo "Job : $JOB_NAME  (sport=$SPORT kind=$KIND max_events=${MAX_EVENTS:-all})"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,PARLAY_API_KEY=parlay-api-key:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="$ARGS"
  --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

# Optional scheduler (only if SCHEDULE provided). Cadence guidance:
#   Free 1000 cr/mo  -> ~1 props snapshot/day  (e.g. "0 23 * * *" near tip/first pitch)
#   $5  20000 cr/mo  -> hourly is fine         (e.g. "0 * * * *")
if [ -n "$SCHEDULE" ]; then
  SJOB="${JOB_NAME}"
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
  SFLAGS=(
    --location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC"
    --uri="$URI" --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=320s --project="$PROJECT_ID"
  )
  if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
  else
    gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
  fi
  echo "Scheduler set: $SJOB @ '$SCHEDULE' UTC"
fi

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "Output -> gs://<bucket>/OddsAccum/${SPORT}/  (latest.json + dated raw/csv)"
