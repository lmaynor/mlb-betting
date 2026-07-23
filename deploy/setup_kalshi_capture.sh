#!/usr/bin/env bash
# setup_kalshi_capture.sh -- provision the Kalshi -> odds_history capture jobs.
#
# Kalshi is a no-vig EXCHANGE; we capture its mid as a SHARP REFERENCE feed into
# the odds_history store (book="kalshi", source="kalshi", fair_prob=mid). Two
# Cloud Run Jobs share the image:
#   mlb-kalshi-capture          -m mlb.analysis.kalshi_to_history
#   mlb-kalshi-capture-closing  -m mlb.analysis.kalshi_to_history --closing
# Multiple scheduler entries point at each job's :run URI, timed to the existing
# mlb-snapshot-* cadence so Kalshi mids and the soft-book snapshots are
# contemporaneous (the comparison layer wants them aligned).
#
# Public Kalshi market-data only: NO Kalshi API key, NO credit budget, NO DB.
# Each run captures ALL active MLB markets (today + already-listed tomorrow), so
# there is no day_offset -- a late run naturally banks the next day's openers.
#
# Prereq: image rebuilt with the kalshi adapter (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_capture.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== Kalshi -> odds_history capture setup ==="
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

_upsert_job() {  # job_name  extra_args
  local job_name="$1" extra="$2"
  local flags=(
    --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
    --command="python"
    --args="-m,mlb.analysis.kalshi_to_history${extra}"
    --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=1 --quiet
  )
  if gcloud run jobs describe "$job_name" --region="$REGION" --quiet >/dev/null 2>&1; then
    echo "Job $job_name exists -- updating..."; gcloud run jobs update "$job_name" "${flags[@]}"
  else
    echo "Job $job_name not found -- creating..."; gcloud run jobs create "$job_name" "${flags[@]}"
  fi
}

_upsert_sched() {  # sched_name  cron  job_name  description
  local name="$1" cron="$2" job_name="$3" desc="$4" action=create
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job_name}:run"
  gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" \
    >/dev/null 2>&1 && action=update
  echo "${action^} $name ($cron) -> $job_name"
  gcloud scheduler jobs "$action" http "$name" \
    --location="$REGION" --schedule="$cron" --time-zone="Etc/UTC" \
    --uri="$uri" --http-method=POST \
    --oauth-service-account-email="$SCHED_SA" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --attempt-deadline=320s --project="$PROJECT_ID" --quiet \
    ${desc:+--description="$desc"}
}

_upsert_job "mlb-kalshi-capture"         ""
_upsert_job "mlb-kalshi-capture-closing" ",--closing"

_upsert_sched "mlb-kalshi-1555" "55 15 * * *" "mlb-kalshi-capture"         "Kalshi capture (pre-morning run)"
_upsert_sched "mlb-kalshi-1855" "55 18 * * *" "mlb-kalshi-capture"         "Kalshi capture (day close / night lineups)"
_upsert_sched "mlb-kalshi-2025" "25 20 * * *" "mlb-kalshi-capture"         "Kalshi capture (night lineups confirmed)"
_upsert_sched "mlb-kalshi-2125" "25 21 * * *" "mlb-kalshi-capture"         "Kalshi capture (pre-close steam)"
_upsert_sched "mlb-kalshi-2155" "55 21 * * *" "mlb-kalshi-capture"         "Kalshi capture (pre-evening run)"
_upsert_sched "mlb-kalshi-2305" "05 23 * * *" "mlb-kalshi-capture-closing" "Kalshi CLOSING snapshot (is_closing)"
_upsert_sched "mlb-kalshi-0125" "25  1 * * *" "mlb-kalshi-capture"         "Kalshi capture (next-day openers)"
_upsert_sched "mlb-kalshi-0325" "25  3 * * *" "mlb-kalshi-capture"         "Kalshi capture (next-day)"

echo ""
echo "=== Done: 2 jobs, 8 schedules/day (7 intraday + 1 closing) ==="
echo "Run once now:  gcloud run jobs execute mlb-kalshi-capture --region=$REGION --project=$PROJECT_ID"
echo "List:          gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep kalshi"
echo "Output -> gs://<bucket>/Odds/history/ (source=kalshi) + Odds/kalshi/raw/"
