#!/usr/bin/env bash
# setup_kalshi_rfq_recorder.sh -- provision the Kalshi RFQ tape recorder as a Cloud Run Job.
#
# READ-ONLY by construction: --env production-readonly uses RFQReadOnlyClient (no quote methods; KalshiClient refuses any
# non-GET before the network) and a receive-only WebSocket. It records every RFQ the exchange broadcasts plus our would-be
# quote, and 15 s later the public trade tape for that market, as chunk objects under
#   gs://<bucket>/Kalshi/rfq_tape/production/{UTC day}/{HHMMSS}_{n}.jsonl
# Nothing here can send, confirm or cancel a quote, and no env var in this script enables quoting.
#
# Shape: ONE long-running execution per day (task-timeout 24h, the daemon exits cleanly at --max-runtime-sec and the daily
# scheduler starts the next). A Job rather than a Service: the daemon serves no HTTP port, and Services need one.
# max-retries=0 on purpose: a failure (stream never connected in 120 s = blocked egress or bad key) must be LOUD, not
# silently retried into a crash loop.
#
# Prereqs (this script never handles a credential value):
#   1. image built with the daemon:  ./deploy/deploy_service.sh
#   2. Secret Manager secrets holding the PRODUCTION READ-ONLY API key (create it read-only in Kalshi if the UI offers):
#        printf '%s' "<key id>"  | gcloud secrets create kalshi-prod-api-key-id  --data-file=- --project=$PROJECT_ID
#        gcloud secrets create kalshi-prod-private-key --data-file=/path/to/key.pem --project=$PROJECT_ID
#
# Demo variant (plumbing test: does the job run, does the tape reach GCS, does Kalshi accept this egress; the demo exchange
# has little RFQ flow, so it says nothing about edge). Needs secrets kalshi-demo-api-key-id / kalshi-demo-private-key. No
# scheduler: execute it by hand; it exits after MAX_RUNTIME_SEC (default 30 min) and writes to Kalshi/rfq_tape/demo/.
#   RFQ_ENV=demo PROJECT_ID=... ./deploy/setup_kalshi_rfq_recorder.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_rfq_recorder.sh
#   PAUSE=1 PROJECT_ID=... ./deploy/setup_kalshi_rfq_recorder.sh     # pause the daily scheduler
#   Run once now:  gcloud run jobs execute mlb-kalshi-rfq-recorder --region=us-central1 --project=$PROJECT_ID
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
RFQ_ENV="${RFQ_ENV:-production-readonly}"
case "$RFQ_ENV" in
  production-readonly)
    JOB="mlb-kalshi-rfq-recorder"; SCHED="mlb-kalshi-rfq-recorder-daily"
    MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-85200}"      # 23h40m: exits before the 24h task timeout, leaves a ~20 min gap
    TASK_TIMEOUT=86400; TAPE_PREFIX="Kalshi/rfq_tape/production"
    SECRETS=(kalshi-prod-api-key-id kalshi-prod-private-key)
    SECRET_MAP="KALSHI_PROD_API_KEY_ID=kalshi-prod-api-key-id:latest,KALSHI_PROD_PRIVATE_KEY_PEM=kalshi-prod-private-key:latest"
    MAX_UNPRICED_LEGS="${MAX_UNPRICED_LEGS:-0}" ;;
  demo)
    JOB="mlb-kalshi-rfq-recorder-demo"; SCHED=""
    MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-1800}"
    TASK_TIMEOUT=3600; TAPE_PREFIX="Kalshi/rfq_tape/demo"
    SECRETS=(kalshi-demo-api-key-id kalshi-demo-private-key)
    SECRET_MAP="KALSHI_API_KEY_ID=kalshi-demo-api-key-id:latest,KALSHI_PRIVATE_KEY_PEM=kalshi-demo-private-key:latest"
    MAX_UNPRICED_LEGS="${MAX_UNPRICED_LEGS:-2}" ;;      # exploration: price combos with up to 2 unpriceable legs (upper bound)
  *) echo "ERROR: RFQ_ENV must be production-readonly or demo"; exit 1 ;;
esac

echo "=== Kalshi RFQ recorder (production-readonly) ==="
if [[ "${PAUSE:-0}" == "1" && -n "$SCHED" ]]; then
  gcloud scheduler jobs pause "$SCHED" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused $SCHED (a running execution finishes on its own; cancel it with: gcloud run jobs executions cancel ...)"
  exit 0
fi

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }
for s in "${SECRETS[@]}"; do
  gcloud secrets describe "$s" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || { echo "ERROR: secret $s missing -- see the prereqs at the top of this script."; exit 1; }
  # idempotent: the job's runtime SA must be able to read exactly these two secrets
  gcloud secrets add-iam-policy-binding "$s" --project="$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
done

flags=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,PARLAY_API_KEY=parlay-api-key:latest,${SECRET_MAP}"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},KALSHI_RFQ_ENV=${RFQ_ENV},KALSHI_RFQ_MODE=record,KALSHI_RFQ_TAPE_PREFIX=${TAPE_PREFIX},KALSHI_RFQ_MAX_UNPRICED_LEGS=${MAX_UNPRICED_LEGS},KALSHI_RFQ_GAME_SPORTS=nfl:nba:nhl,KALSHI_RFQ_GAME_MARKETS=h2h:spreads:totals,KALSHI_RFQ_PROP_SPORTS=nfl:nba:nhl,KALSHI_RFQ_GAME_BUDGET=${GAME_BUDGET:-120}"
  --command="python"
  --args="scripts/kalshi_rfq_daemon.py,--mode,record,--env,${RFQ_ENV},--max-runtime-sec,${MAX_RUNTIME_SEC}"
  --memory=512Mi --cpu=1 --task-timeout=${TASK_TIMEOUT} --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${flags[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${flags[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7); see
# docs/solutions/runtime-errors/scheduler-permission-denied-on-new-jobs.md.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

if [[ -z "$SCHED" ]]; then
  echo ""
  echo "=== Done: demo job $JOB (no scheduler) ==="
  echo "Run:   gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
  echo "Tape:  gsutil ls gs://<bucket>/${TAPE_PREFIX}/"
  exit 0
fi

uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
action=create
gcloud scheduler jobs describe "$SCHED" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1 && action=update
gcloud scheduler jobs "$action" http "$SCHED" \
  --location="$REGION" --schedule="0 10 * * *" --time-zone="Etc/UTC" \
  --uri="$uri" --http-method=POST \
  --oauth-service-account-email="$SCHED_SA" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline=320s --project="$PROJECT_ID" --quiet \
  --description="Kalshi RFQ recorder (read-only production): one ~23h40m execution per day"
gcloud scheduler jobs resume "$SCHED" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 || true

echo ""
echo "=== Done: job $JOB + daily scheduler $SCHED (10:00 UTC) ==="
echo "Run now:   gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID"
echo "Logs:      gcloud logging read 'resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=30 --freshness=1h"
echo "Tape:      gsutil ls gs://<bucket>/${TAPE_PREFIX}/"
echo "Watch for: 'RFQ stream never connected in 120 s' (blocked egress / rejected key) and the 5-minute 'heartbeat:' lines."
