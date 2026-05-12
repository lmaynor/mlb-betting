#!/usr/bin/env bash
# deploy/add_snapshot_schedulers.sh — register the two SGO snapshot jobs.
#
# Idempotent: if a job already exists it gets updated rather than failing.
# Run from Cloud Shell after deploying the new Cloud Run revision that
# includes the /snapshot-odds endpoint.
#
# Usage:
#   chmod +x deploy/add_snapshot_schedulers.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/add_snapshot_schedulers.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

# Snapshot jobs: 15:00 and 21:00 UTC (11 AM and 5 PM ET).
# Each fires ~minutes before HR's morning/evening runs so the runners
# read the freshest snapshot.

_upsert_job() {
  local name="$1"
  local cron="$2"
  local description="$3"

  if gcloud scheduler jobs describe "$name" \
       --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron)..."
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" \
      --schedule="$cron" \
      --uri="${SERVICE_URL}/snapshot-odds" \
      --message-body='{}' \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --time-zone="UTC" \
      --project="$PROJECT_ID" \
      --quiet
  else
    echo "Creating $name ($cron)..."
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" \
      --schedule="$cron" \
      --uri="${SERVICE_URL}/snapshot-odds" \
      --message-body='{}' \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --time-zone="UTC" \
      --description="$description" \
      --project="$PROJECT_ID" \
      --quiet
  fi
}

# Morning snapshot: 14:55 UTC (10:55 AM ET). Fires ~5 min before the
# mlb-betting-morning job at 15:00 UTC so HR reads the fresh snapshot.
_upsert_job "mlb-snapshot-morning" "55 14 * * *" \
  "SGO MLB slate snapshot — fires before the 15:00 UTC HR/NRFI run"

# Evening snapshot: 20:55 UTC (4:55 PM ET). Fires ~5 min before the
# mlb-betting-evening job at 21:00 UTC.
_upsert_job "mlb-snapshot-evening" "55 20 * * *" \
  "SGO MLB slate snapshot — fires before the 21:00 UTC HR/NRFI run"

echo ""
echo "=== Done ==="
echo "Two scheduler jobs registered (~900 SGO objects/month budgeted)"
echo ""
echo "To trigger one manually right now:"
echo "  gcloud scheduler jobs run mlb-snapshot-morning --location=$REGION --project=$PROJECT_ID"
echo ""
echo "To inspect:"
echo "  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
