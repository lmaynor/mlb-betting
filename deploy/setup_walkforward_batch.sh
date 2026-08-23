#!/usr/bin/env bash
# setup_walkforward_batch.sh -- idempotently create/update the
# mlb-walkforward-batch Cloud Run Job.
#
# Runs the rolling walk-forward (train pre-cutoff, score next month cold) for all
# count systems and persists per-system results to GCS. Server-side so no Cloud
# Shell session to babysit (the ~15-retrains-x-4-systems grind outlives a VM recycle).
#
# Run from Cloud Shell after deploy/deploy_service.sh (so :latest has
# mlb.runners.walkforward_batch + mlb.analysis.walkforward + the training modules):
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_walkforward_batch.sh
#   gcloud run jobs execute mlb-walkforward-batch --region=us-central1 --wait
#
# Tune via env at setup time (baked into the job):
#   WF_SYSTEMS=BATTER_TB WF_SELECT=consensus WF_START=2024-05-01 WF_END=2026-06-01 \
#     ./deploy/setup_walkforward_batch.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-mlb-betting}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/${SERVICE}:latest}"
SA_EMAIL="${SA_EMAIL:-${SERVICE}-sa@${PROJECT_ID}.iam.gserviceaccount.com}"

WF_SYSTEMS="${WF_SYSTEMS:-all}"
WF_START="${WF_START:-2024-05-01}"
WF_END="${WF_END:-2026-06-01}"
WF_STEP="${WF_STEP:-1}"
WF_EDGE="${WF_EDGE:-0.10}"
WF_SELECT="${WF_SELECT:-consensus}"
WF_CONFIGS="${WF_CONFIGS:-both}"

JOB="mlb-walkforward-batch"

ENV_VARS="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
ENV_VARS="${ENV_VARS},WF_SYSTEMS=${WF_SYSTEMS},WF_START=${WF_START},WF_END=${WF_END}"
ENV_VARS="${ENV_VARS},WF_STEP=${WF_STEP},WF_EDGE=${WF_EDGE},WF_SELECT=${WF_SELECT}"
ENV_VARS="${ENV_VARS},WF_CONFIGS=${WF_CONFIGS}"

action="create"
if gcloud run jobs describe "$JOB" \
    --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  action="update"
fi

echo "${action} job: $JOB (image: $IMAGE)"
echo "  systems: $WF_SYSTEMS  range: $WF_START -> $WF_END  step: ${WF_STEP}mo"
echo "  edge>=$WF_EDGE  select=$WF_SELECT  configs=$WF_CONFIGS"

# XGBoost retrains on up to 268k rows are memory+CPU heavy -> 4Gi / 2 CPU.
# task-timeout 5h: ~4 systems x 2 configs x ~15 monthly retrains, headroom for the
# 268k-row batter systems. --max-retries 0 so a partial run isn't silently restarted.
gcloud run jobs "$action" "$JOB" \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$SA_EMAIL" \
  --command="python3" \
  --args="-m,mlb.runners.walkforward_batch" \
  --memory="4Gi" \
  --cpu="2" \
  --task-timeout="18000s" \
  --max-retries=0 \
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
  --set-env-vars="$ENV_VARS"

echo ""
echo "=== Done ==="
echo "Execute:  gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Results:  gsutil ls gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/Analysis/walkforward/"
