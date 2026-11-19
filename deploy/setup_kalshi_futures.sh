#!/usr/bin/env bash
# setup_kalshi_futures.sh -- provision the Kalshi futures daily snapshotter (BNK-14).
#
# Part of BNK-14 (RES-15 P5 Kalshi half): captures daily snapshots of public Kalshi
# futures and outright markets across NBA, NFL, MLB, NHL, NCAAF, NCAAB, and WNBA,
# banking raw normalized superset records into:
#   gs://<bucket>/Futures/kalshi/dt=YYYY-MM-DD/snapshot.parquet
#   gs://<bucket>/Futures/kalshi/dt=YYYY-MM-DD/manifest.json
#
# Consumer: ev/futures_sleeve.py (Module 8: Slow Markets / Futures Swing Sleeve).
#
# Public Kalshi market-data only: NO credentials, NO auth headers, NO order path,
# NO credit budget, NO database writes.
#
# Prereq image build:
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting,_TAG=kalshi-futures-20261119
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_futures.sh
#   IMAGE_TAG=kalshi-futures-20261119 PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_futures.sh
#   Run once now: gcloud run jobs execute kalshi-futures-daily --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:${IMAGE_TAG}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
JOB="kalshi-futures-daily"
SJOB="${JOB}-0610ct"
SCHEDULE="10 6 * * *"

echo "=== Kalshi futures daily snapshotter setup (BNK-14) ==="
gcloud container images describe "$IMAGE" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it first (see prereq command above)."; exit 1; }

FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m,ev.kalshi_futures_snapshot"
  --memory=512Mi --cpu=1 --task-timeout=1200 --max-retries=1 --quiet
)

if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."
  gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."
  gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker must hold run.invoker ON THE JOB or every scheduler firing
# is PERMISSION_DENIED (code 7) -- see fleet postmortem in setup_kalshi_capture.sh.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
echo "run.invoker granted to $SCHED_SA on $JOB"

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(
  --location="$REGION"
  --schedule="$SCHEDULE"
  --time-zone="America/Chicago"
  --uri="$URI"
  --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=320s
  --description="Kalshi futures daily snapshotter (BNK-14 / RES-15 P5 Kalshi half)"
  --project="$PROJECT_ID"
)

if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Scheduler $SJOB exists -- updating..."
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  echo "Scheduler $SJOB not found -- creating..."
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${SCHEDULE}' America/Chicago -> ${JOB}"

echo ""
echo "=== Done: job $JOB ($IMAGE) + scheduler $SJOB ==="
echo "Run once now: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "List:         gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep kalshi-futures"
echo "Output ->     gs://<bucket>/Futures/kalshi/dt=YYYY-MM-DD/"
