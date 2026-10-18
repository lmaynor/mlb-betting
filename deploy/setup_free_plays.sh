#!/usr/bin/env bash
# setup_free_plays.sh -- provision the daily FREE PLAYS Cloud Run Job + schedulers (mlb.runners.free_plays).
#
# Posts at most 2 STRONG (Beezy Score >= 70) plays per ET day to #daily-free-plays. The job tops up to the cap: it never
# re-posts or replaces a play, so extra schedules are safe. Credit cost: ZERO (reads bets + banked sweep snapshots).
#
# Prereq: image has mlb.runners.free_plays (./deploy/deploy_service.sh). Secrets are mounted only if they exist:
#   discord-webhook-free-plays (created from /setup-server output), beezy-bot-secret (bot-authored posts), mlb-db-url.
# Usage: PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_free_plays.sh      (idempotent)
# Preview without posting: PYTHONPATH=. python3 -m mlb.runners.free_plays --dry-run
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-free-plays"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# UTC. After the 16:00 / 19:05 / 22:00 /run jobs and the 15-min EV pager window has populated `bets`.
SCHEDULES=("30 17 * * *" "20 19 * * *" "15 22 * * *")

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,MLB_DB_URL=mlb-db-url:latest,DISCORD_WEBHOOK_OPS=discord-ops-webhook-url:latest"
ENVV="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
for pair in "DISCORD_WEBHOOK_FREE_PLAYS=discord-webhook-free-plays" "BEEZY_BOT_SECRET=beezy-bot-secret"; do
  if gcloud secrets describe "${pair#*=}" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    SECRETS="${SECRETS},${pair}:latest"
    [[ "$pair" == BEEZY_BOT_SECRET=* ]] && ENVV="${ENVV},BEEZY_BOT_URL=https://beezy-discord-bot-628109313129.us-central1.run.app"
  else
    echo "note: secret ${pair#*=} not found -- not mounted"
  fi
done

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" --set-secrets="$SECRETS"
       --set-env-vars="$ENVV" --command="python3" --args="-m,mlb.runners.free_plays"
       --memory=1Gi --cpu=1 --task-timeout=300 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB_NAME" "${FLAGS[@]}"
else
  gcloud run jobs create "$JOB_NAME" "${FLAGS[@]}"
fi

# scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7).
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
i=0
for CRON in "${SCHEDULES[@]}"; do
  i=$((i+1)); SJOB="${JOB_NAME}-${i}"
  SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform")
  if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
  else
    gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
  fi
  echo "scheduler ${SJOB} @ '${CRON}' UTC -> ${JOB_NAME}"
done
echo "Done. Trigger once and check status.code: gcloud scheduler jobs run ${JOB_NAME}-1 --location=$REGION"
