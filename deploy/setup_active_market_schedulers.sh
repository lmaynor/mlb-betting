#!/usr/bin/env bash
# setup_active_market_schedulers.sh -- update daily run schedulers for active SGO markets.
#
# Run from Cloud Shell after deploy/deploy_service.sh:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_active_market_schedulers.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-mlb-betting}"
SERVICE_URL="${SERVICE_URL:-https://mlb-betting-628109313129.us-central1.run.app}"
SCHEDULER_SA="${SCHEDULER_SA:-scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
AUDIENCE="${AUDIENCE:-$SERVICE_URL}"

SYSTEMS='["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"]'

upsert_job() {
  local name="$1"
  local schedule="$2"
  local run_type="$3"
  local body
  body="{\"systems\":${SYSTEMS},\"run_type\":\"${run_type}\"}"

  if gcloud scheduler jobs describe "$name" \
      --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating scheduler: $name"
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --schedule="$schedule" \
      --uri="${SERVICE_URL}/run" \
      --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$AUDIENCE" \
      --time-zone="UTC" \
      --attempt-deadline=1800s
  else
    echo "Creating scheduler: $name"
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --schedule="$schedule" \
      --uri="${SERVICE_URL}/run" \
      --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$AUDIENCE" \
      --time-zone="UTC" \
      --attempt-deadline=1800s
  fi
}

echo "Configuring active market run schedulers for: $SYSTEMS"
upsert_job "${SERVICE}-morning" "0 13 * * *" "morning"
upsert_job "${SERVICE}-evening" "0 21 * * *" "evening"

echo "Schedulers ready."
