#!/usr/bin/env bash
# setup_contest_scheduler.sh -- provision the two Cloud Scheduler jobs that drive the Bankroll Challenge (play money):
#
#   contest-refresh   every 20 minutes   POST /contest-refresh {slug, include}              fetch ESPN, refresh the board, grade finals, announce
#   contest-halftime  every minute       POST /contest-refresh {slug, include, halftime}    only does anything while a game is at halftime AND the
#                                                                                           halftime rule + CONTEST_HALFTIME_ENABLED are on
#
# Both call the main Cloud Run service (the contest routes live in main.py). Jobs are created PAUSED unless START=1: nothing runs until
# the owner says so, and the whole surface answers 404 unless CONTEST_ENABLED is on in the service.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONTEST_SLUG=bowls-2026 bash deploy/setup_contest_scheduler.sh            # create / update, paused
#   START=1 PROJECT_ID=... CONTEST_SLUG=... bash deploy/setup_contest_scheduler.sh                               # create / update and resume
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_contest_scheduler.sh                                                # pause both
#
# Needs (set on the service by deploy/deploy_service.sh, not here): CONTEST_ENABLED=1 and SITE_API_KEY (already set). Optional:
# CONTEST_NOTIFY=1 + CONTEST_DISCORD_WEBHOOK for announcements, CONTEST_HALFTIME_ENABLED=1 for the halftime window.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
REFRESH_JOB="contest-refresh"
HALFTIME_JOB="contest-halftime"

if [ "${PAUSE:-0}" = "1" ]; then
  for j in "$REFRESH_JOB" "$HALFTIME_JOB"; do
    gcloud scheduler jobs pause "$j" --location="$REGION" --project="$PROJECT_ID" --quiet || true
  done
  echo "contest schedulers paused."; exit 0
fi

SLUG="${CONTEST_SLUG:?Set CONTEST_SLUG (the contest to drive)}"
INCLUDE="${CONTEST_INCLUDE:-Bowl|Playoff|Championship}"      # round-label regex that keeps the contest to its own games; "" = every game ESPN lists
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

# The scheduler's service account must be allowed to invoke the service or every firing is PERMISSION_DENIED
# (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
gcloud run services add-iam-policy-binding "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHEDULER_SA}" --role="roles/run.invoker" --quiet >/dev/null

upsert() {
  local name="$1" cron="$2" body="$3" description="$4" deadline="$5"
  if gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron)..."
    gcloud scheduler jobs update http "$name" --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-refresh" --http-method=POST --message-body="$body" \
      --update-headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="$deadline" --project="$PROJECT_ID" --quiet
  else
    echo "Creating $name ($cron)..."
    gcloud scheduler jobs create http "$name" --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-refresh" --http-method=POST --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="$deadline" --description="$description" --project="$PROJECT_ID" --quiet
  fi
  if [ "${START:-0}" = "1" ]; then
    gcloud scheduler jobs resume "$name" --location="$REGION" --project="$PROJECT_ID" --quiet
  else
    gcloud scheduler jobs pause "$name" --location="$REGION" --project="$PROJECT_ID" --quiet
  fi
}

upsert "$REFRESH_JOB" "*/20 * * * *" "{\"slug\":\"${SLUG}\",\"include\":\"${INCLUDE}\"}" \
  "Bankroll Challenge: refresh the board from ESPN, grade finals, announce" "120s"
upsert "$HALFTIME_JOB" "* * * * *" "{\"slug\":\"${SLUG}\",\"include\":\"${INCLUDE}\",\"halftime\":true}" \
  "Bankroll Challenge: halftime window poll (no-op unless the halftime rule is on)" "60s"

echo ""
echo "=== Done ==="
if [ "${START:-0}" = "1" ]; then echo "Both jobs are RUNNING."; else echo "Both jobs are PAUSED. Resume with START=1, or: gcloud scheduler jobs resume <name> --location=$REGION"; fi
echo "Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep contest"
