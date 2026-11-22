#!/usr/bin/env bash
# setup_kalshi_probe.sh -- provision the Kalshi pre-game liquidity probe
# (RUL-20261122 Step 1; ev/kalshi_probe/job.py).
#
#   job       kalshi-liquidity-probe        python -m ev.kalshi_probe.job
#   scheduler kalshi-liquidity-probe-15m    every 15 minutes (UTC)
#
# WHAT IT DOES: each firing samples Kalshi's PUBLIC orderbooks for NBA
# points / rebounds / threes and NBA game moneyline / spread / total
# markets whose game starts in the T-4h..T-15m pre-game window, and banks
# append-only JSONL chunks to gs://<bucket>/Kalshi/probe/dt=<ET date>/.
# READ-ONLY: no Kalshi credentials exist in this job; the probe code makes
# unauthenticated GETs only (no orders, no RFQs).
#
# HARD STOP: the probe code refuses to sample at/after
# 2026-11-24T08:00:00Z (end of the Nov 23 slate's windows) and exits 0.
# The scheduler is left in place after the stop; every later firing is a
# no-op clock check. REVIEW/DISABLE: Nov 24, 2026 (probe window over).
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket,
# used by every feeds job). The script fails loudly if it is missing.
#
# IMAGE: build a TAG from this branch first (the probe code is not in
# :latest):
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=kalshi-probe-20261122 --project=$PROJECT_ID .
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:kalshi-probe-20261122 PROJECT_ID=... bash deploy/setup_kalshi_probe.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_kalshi_probe.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_kalshi_probe.sh   # pause scheduler
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="kalshi-liquidity-probe"
SJOB="${JOB}-15m"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${SCHEDULE:-*/15 * * * *}"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Kalshi probe scheduler paused."
  exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see the IMAGE note at the top of this script)."; exit 1; }
gcloud secrets describe mlb-gcs-bucket --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: secret mlb-gcs-bucket is missing. This script never creates secrets; provision it (the bucket name) and re-run."; exit 1; }

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,ev.kalshi_probe.job"
  --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=180s
        --description="Kalshi pre-game liquidity probe (RUL-20261122 Step 1): read-only public orderbook sampling, T-4h..T-15m, NBA props + game lines, Nov 22-23 2026 slates. Code hard-stops 2026-11-24T08:00:00Z. Review/disable Nov 24, 2026."
        --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${SCHEDULE}' Etc/UTC -> ${JOB}"

echo ""
echo "=== Done: job $JOB (${IMAGE}) + scheduler $SJOB ==="
echo "Fire via scheduler: gcloud scheduler jobs run $SJOB --location=$REGION --project=$PROJECT_ID"
echo "Logs:               gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Bank:               gcloud storage ls gs://<bucket>/Kalshi/probe/"
echo "Not enabled:        any Kalshi credential (none exist in this job); probe is public-GET only."
