#!/usr/bin/env bash
# setup_weekly_retrain_calibrate.sh -- idempotently provision the weekly
# retrain + calibrate Cloud Scheduler jobs.
#
# mlb-retrain-weekly already existed (created by hand, no setup script --
# this closes that gap) and hits /retrain-weekly at 06:00 UTC every Monday.
#
# mlb-calibrate-weekly is NEW (2026-08-19): /retrain-weekly used to also fire
# the 7 calibrate jobs itself, 30 minutes later, from a `time.sleep(1800)`
# daemon thread that kept running after the HTTP response had already
# returned. This service has no always-allocated-CPU annotation, so it runs
# under Cloud Run's default CPU throttling -- a thread that needs to keep
# running past its own request's response has no guaranteed CPU. Confirmed
# via execution history: the calibrate jobs missed 4 straight scheduled
# Mondays (2026-07-27 through 2026-08-17) while their paired retrain jobs
# never missed one. See docs/audits/2026-08-19_feature_data_pipeline_review.md
# finding 2.4. /retrain-weekly and /calibrate-weekly are now two independent
# routes, each firing its own jobs synchronously within its own request --
# same pattern this codebase already uses correctly for /run relative to
# /snapshot-odds.
#
# Run from Cloud Shell after deploy/deploy_service.sh:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_weekly_retrain_calibrate.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

_upsert_job() {
  local name="$1" cron="$2" path="$3" description="$4"

  if gcloud scheduler jobs describe "$name" \
       --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron -> $path)..."
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}${path}" --http-method=POST \
      --message-body="{}" \
      --update-headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="300s" \
      --project="$PROJECT_ID" --quiet
  else
    echo "Creating $name ($cron -> $path)..."
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}${path}" --http-method=POST \
      --message-body="{}" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="300s" \
      --description="$description" \
      --project="$PROJECT_ID" --quiet
  fi
}

_upsert_job "mlb-retrain-weekly"   "0 6 * * 1"  "/retrain-weekly"   "Weekly retrain trigger (8 systems)"
_upsert_job "mlb-calibrate-weekly" "35 6 * * 1" "/calibrate-weekly" "Weekly calibrate trigger, 35 min after mlb-retrain-weekly (7 systems, OUTS self-calibrates inline)"

echo ""
echo "=== Done ==="
echo "Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
echo "Verify:  gcloud scheduler jobs describe mlb-calibrate-weekly --location=$REGION --project=$PROJECT_ID"
