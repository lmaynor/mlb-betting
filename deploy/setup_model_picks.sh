#!/usr/bin/env bash
# setup_model_picks.sh -- provision the NFL/NBA/NHL MODEL-pick tracker (ev/model_picks.py).
#
#   props-model-picks   python -m ev.model_picks --sport all   hourly at :40 UTC (the daily-projections board refreshes at :25/:55)
#
# One pass per sport: select the board's confluence rows -> log them to the bets table (system='PROPS', flat 1u, paper) -> post the
# OFFICIAL-tier ones to the sport's alerts channel (paper / unvalidated) -> capture closing lines for started games from the board archive.
# COST: zero ParlayAPI credits (reads the stored board). It refuses a board older than 45 min, so a stalled projections job means
# "no picks", never picks on stale prices.
#
# Needs (like deploy/setup_props_alerts.sh): the feeds image rebuilt with this code, MLB_DB_URL + the Cloud SQL socket (without them the
# code refuses to write rather than using an ephemeral sqlite file), BEEZY_BOT_URL/SECRET and the optional card-channel webhooks for Discord.
# Idempotent.
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_model_picks.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_model_picks.sh        # pause the scheduler
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"     # feeds image, NOT the production mlb-betting image (see setup_props_alerts.sh)
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"
JOB="props-model-picks"
SJOB="${JOB}-1"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused."; exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

BOT_URL="${BEEZY_BOT_URL:-https://beezy-discord-bot-628109313129.us-central1.run.app}"
SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_OPS=discord-ops-webhook-url:latest,BEEZY_BOT_SECRET=beezy-bot-secret:latest,MLB_DB_URL=mlb-db-url:latest"
if gcloud secrets describe discord-webhook-alerts --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  SECRETS="${SECRETS},DISCORD_WEBHOOK_ALERTS=discord-webhook-alerts:latest"
fi
for pair in "DISCORD_WEBHOOK_CARD_NFL=discord-webhook-card-nfl" "DISCORD_WEBHOOK_CARD_NHL=discord-webhook-card-nhl" "DISCORD_WEBHOOK_CARD_NBA=discord-webhook-card-nba"; do
  if gcloud secrets describe "${pair#*=}" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    SECRETS="${SECRETS},${pair}:latest"
  fi
done

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" --set-secrets="$SECRETS"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},BEEZY_BOT_URL=${BOT_URL}" --command="python"
       --args="-m,ev.model_picks,--sport,all" --set-cloudsql-instances="$INSTANCE"
       --memory=1Gi --cpu=1 --task-timeout=300 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}"
fi

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="40 * * * *" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=320s --project="$PROJECT_ID")
# scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED (docs/solutions/runtime-errors/
# scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
fi
echo "scheduler ${SJOB} @ '40 * * * *' UTC -> ${JOB}"
echo "Done. Verify: gcloud scheduler jobs list --location=$REGION | grep props-model-picks"
