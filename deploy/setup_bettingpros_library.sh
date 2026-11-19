#!/usr/bin/env bash
# setup_bettingpros_library.sh -- provision the BettingPros full-catalog odds library jobs & schedulers.
#
# COST CASE:
#   ~13,000 requests/month across all sports catalog walks.
#   One daily Cloud Run job execution ~30 min at 1 vCPU / 512Mi RAM.
#   Steady-state compute cost ≈ $1.10/month.
#
# FUTURES CLOCK WARNING:
#   Futures prices CANNOT be backfilled. Historical snapshots do not exist in the API;
#   BettingPros only exposes current quotes for active seasons. Every week undeployed
#   is a week of futures prices lost permanently.
#
# JOBS PROVISIONED:
#   bp-library-daily:
#     python -m mlb.runners.bettingpros_library (BP_MODE=daily)
#     Scheduler: bp-library-daily-daily @ "0 7 * * *" America/Chicago
#     (Runs daily at 07:00 CT, placed after the Action Network daily bankers' 06:00 CT stagger).
#
#   bp-library-futures-weekly:
#     python -m mlb.runners.bettingpros_library (BP_MODE=futures)
#     Scheduler: bp-library-futures-weekly-weekly @ "30 7 * * 1" America/Chicago
#     (Runs weekly on Mondays at 07:30 CT).
#
# IMAGE CONVENTION:
#   IMAGE: gcr.io/<project>/mlb-betting-feeds (NOT mlb-betting:latest)
#   The feeds image must contain mlb/runners/bettingpros_library.py.
#   Guard: CONFIRM_IMAGE_HAS_LIBRARY=1 must be set. Idempotent create-or-update.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIBRARY=1 bash deploy/setup_bettingpros_library.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_bettingpros_library.sh   # pause both schedulers
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# job|mode|scheduler tag|cron (America/Chicago)
ROWS=(
  "bp-library-daily|daily|daily|0 7 * * *"
  "bp-library-futures-weekly|futures|weekly|30 7 * * 1"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^bp-library-'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "All bp-library-* schedulers paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_LIBRARY:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_LIBRARY=1 once $IMAGE contains mlb/runners/bettingpros_library.py."
  echo "(An older image has no mlb.runners.bettingpros_library: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

JOBS_DONE=" "
for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB MODE TAG CRON <<<"$row"
  if [[ "$JOBS_DONE" != *" $JOB "* ]]; then
    FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
           --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
           --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},BP_MODE=${MODE}"
           --command="python" --args="-m,mlb.runners.bettingpros_library"
           --memory=512Mi --cpu=1 --task-timeout=3600 --max-retries=1 --quiet)
    if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
    else
      gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
    fi
    gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
      --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
    JOBS_DONE="${JOBS_DONE}${JOB} "
  fi
  SJOB="${JOB}-${TAG}"
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
          --attempt-deadline=320s --project="$PROJECT_ID")
  if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
  fi
  echo "scheduler ${SJOB} @ '${CRON}' America/Chicago -> ${JOB}"
done

echo
echo "Done. Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep bp-library"
