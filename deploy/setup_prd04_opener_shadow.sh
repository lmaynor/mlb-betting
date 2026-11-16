#!/usr/bin/env bash
# setup_prd04_opener_shadow.sh -- provision the PRD-04 opener-cohort SHADOW runner (ev/prd04_openers.py).
#
#   prd04-opener-shadow   python -m ev.prd04_openers --sport all   every 10 min (America/Chicago)
#
# SHADOW ONLY. This job reads the stored board and writes PRD04Openers/{day}/{sport}.json cohort tapes --
# nothing else. It is deliberately provisioned WITHOUT MLB_DB_URL and WITHOUT any Discord webhook secret, so
# it cannot log a pick, post a card, or touch the live Daily Card even if the code regressed. The live
# props-model-picks job and its schedule are untouched by this script. The 10-minute scheduler feeds the
# in-code window gate (ev.prd04_openers.poll_due): ticks inside the league opener windows (NBA 22:30-01:30 CT
# and 09:00-13:00 CT with NHL mornings, NFL Wed 12:00-16:00 / Sun 07:30-10:00 CT) do the cohort pass; outside
# them only the :40 CT baseline tick works and the rest exit immediately. The runner is also inert unless
# PRD04_SHADOW=1 (set here as a plain env var; the same flag gates the shadow block in props-model-picks).
#
# This script is NOT run by the PR that introduces it -- running it is Luc's ops step, like any env change.
# Idempotent.
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_prd04_opener_shadow.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_prd04_opener_shadow.sh        # pause the scheduler
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"     # feeds image, NOT the production mlb-betting image
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
JOB="prd04-opener-shadow"
SJOB="${JOB}-10m"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused."; exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

# Storage only: the bucket secret is the runner's sole credential need (board reads + shadow tape writes).
SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" --set-secrets="$SECRETS"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},PRD04_SHADOW=1" --command="python"
       --args="-m,ev.prd04_openers,--sport,all"
       --memory=1Gi --cpu=1 --task-timeout=300 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="*/10 * * * *" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=320s --project="$PROJECT_ID")
# scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
fi
echo "scheduler ${SJOB} @ '*/10 * * * *' America/Chicago -> ${JOB}"
echo "Done. Verify: gcloud scheduler jobs list --location=$REGION | grep prd04-opener-shadow"
