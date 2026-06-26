#!/usr/bin/env bash
# setup_betting_schedulers.sh -- idempotently provision the four scoring (/run)
# Cloud Scheduler jobs, one ~5 min after each odds snapshot.
#
# Scoring fires 4x/day to match the 4 daily odds snapshots, so late-appearing
# markets (evening-game props posted mid-afternoon, lineups confirmed near first
# pitch) get scored. Safe under strict first-wins dedup
# (BetTracker.log_bet on (system, game_date, game_pk, bet_type)): extra runs only
# log brand-new markets, never double-log or re-price.
#
# The request body is the AUTHORITATIVE system list -- it must match main.py
# DEFAULT_RUN_SYSTEMS (VALID_SYSTEMS names: 1IOU/1I, NOT legacy NRFI). Previously
# the live morning/evening bodies omitted BATTER_TB and 1I; this script is the
# single source of truth so that can't drift.
#
# Supersedes deploy/add_betting_schedulers.sh (which cloned the evening job's body
# -- fragile, and inherited the BATTER_TB/1I omission).
#
# Run from Cloud Shell after deploy/deploy_service.sh:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_betting_schedulers.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

# Authoritative system list -- keep in sync with main.py DEFAULT_RUN_SYSTEMS.
SYSTEMS_JSON='["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"]'

_upsert_run_job() {
  local name="$1" cron="$2" run_type="$3" description="$4"
  local body="{\"systems\":${SYSTEMS_JSON},\"run_type\":\"${run_type}\"}"

  if gcloud scheduler jobs describe "$name" \
       --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron, run_type=$run_type)..."
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/run" --http-method=POST \
      --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="180s" \
      --project="$PROJECT_ID" --quiet
  else
    echo "Creating $name ($cron, run_type=$run_type)..."
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/run" --http-method=POST \
      --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="180s" \
      --description="$description" \
      --project="$PROJECT_ID" --quiet
  fi
}

# Each scoring run fires ~5 min after the matching snapshot (15:55 / 19:00 / 21:55 / 23:30).
_upsert_run_job "mlb-betting-morning"   "0 16 * * *"  "morning"   "Scoring /run after the morning SGO snapshot"
_upsert_run_job "mlb-betting-afternoon" "5 19 * * *"  "afternoon" "Scoring /run after the afternoon SGO snapshot (catches evening-game props)"
_upsert_run_job "mlb-betting-evening"   "0 22 * * *"  "evening"   "Scoring /run after the evening SGO snapshot"
_upsert_run_job "mlb-betting-pregame"   "35 23 * * *" "pregame"   "Scoring /run after the pregame SGO snapshot (catches late-confirmed lineups)"

echo ""
echo "=== Done ==="
echo "Four scoring jobs provisioned with body systems=${SYSTEMS_JSON}"
echo "Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
