#!/usr/bin/env bash
# setup_backfill_bettingpros.sh -- idempotently create/update the
# mlb-backfill-bettingpros Cloud Run Job.
#
# One-shot, long-running backfill of BettingPros odds across all markets
# (player props + game lines + inning markets) to GCS, partitioned as
# Odds/bettingpros/{market}/{YYYY-MM-DD}.csv. Runs server-side so there is no
# Cloud Shell session to babysit; re-execute to resume idempotently.
#
# Run from Cloud Shell after deploy/deploy_service.sh (so :latest has the
# mlb.runners.backfill_bettingpros module + mlb_core.odds.bettingpros):
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_backfill_bettingpros.sh
#   gcloud run jobs execute mlb-backfill-bettingpros --region=us-central1 --wait
#
# Tune the range/markets via env at setup time (baked into the job):
#   BP_START=2024-04-01 BP_END=2026-06-29 BP_MARKETS=all ./deploy/setup_backfill_bettingpros.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-mlb-betting}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/${SERVICE}:latest}"
SA_EMAIL="${SA_EMAIL:-${SERVICE}-sa@${PROJECT_ID}.iam.gserviceaccount.com}"

# Backfill config -- baked as env vars on the job (the runner reads BP_*).
BP_START="${BP_START:-2024-04-01}"
BP_END="${BP_END:-$(date -u +%F)}"
BP_MARKETS="${BP_MARKETS:-all}"
BP_PREFIX="${BP_PREFIX:-Odds/bettingpros}"
BP_DELAY="${BP_DELAY:-0.4}"

JOB="mlb-backfill-bettingpros"

# BP_MARKETS may contain commas (e.g. "total_bases,hits") -> use gcloud's ^@^
# alternate-delimiter so the comma is not parsed as a key=value separator.
ENV_VARS="GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}"
ENV_VARS="${ENV_VARS}@BP_START=${BP_START}@BP_END=${BP_END}@BP_MARKETS=${BP_MARKETS}"
ENV_VARS="${ENV_VARS}@BP_PREFIX=${BP_PREFIX}@BP_DELAY=${BP_DELAY}"

action="create"
if gcloud run jobs describe "$JOB" \
    --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  action="update"
fi

echo "${action^} job: $JOB (image: $IMAGE)"
echo "  range:   $BP_START -> $BP_END"
echo "  markets: $BP_MARKETS  prefix: $BP_PREFIX  delay: $BP_DELAY"

# No Cloud SQL / DB: this job only writes odds CSVs to GCS.
gcloud run jobs "$action" "$JOB" \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$SA_EMAIL" \
  --command="python3" \
  --args="-m,mlb.runners.backfill_bettingpros" \
  --memory="1Gi" \
  --cpu="1" \
  --task-timeout="43200s" \
  --max-retries=3 \
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
  --set-env-vars="^@^$ENV_VARS"

echo ""
echo "=== Done ==="
echo "Execute:  gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Logs:     gcloud run jobs executions list --job=$JOB --region=$REGION --project=$PROJECT_ID"
echo "Output:   gsutil ls gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/${BP_PREFIX}/"
