#!/usr/bin/env bash
# setup_kalshi_backfill.sh -- provision the one-shot Kalshi closing-line backfill job.
#
# Crawls settled Kalshi MLB markets over a date range, pulls each market's
# candlesticks, and writes the closing quote (source=kalshi, is_closing=True)
# into odds_history -- a season-long sharp CLOSING reference for CLV backtests.
# Public candlestick endpoint (no key). SLOW BY DESIGN (--sleep between calls) +
# RESUMABLE (per-(series,game_date) sentinel), so it crawls for hours without
# tripping rate limits and a re-run skips finished dates.
#
# NO scheduler -- this is a manual, long-lived job (like mlb-backfill-bettingpros).
# Range/series/throttle are baked at setup time via env, then you EXECUTE it.
#
# Prereq: image rebuilt with mlb.analysis.kalshi_history (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_backfill.sh
#   KALSHI_SINCE=2024-04-01 KALSHI_UNTIL=2026-07-22 \
#     KALSHI_SERIES=KXMLBGAME,KXMLBRFI,KXMLBTOTAL,KXMLBSPREAD,KXMLBF5 \
#     KALSHI_SLEEP=0.5 PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_backfill.sh
#   gcloud run jobs execute mlb-kalshi-backfill --region=us-central1 --project=$PROJECT_ID
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="mlb-kalshi-backfill"

# Defaults: LIQUID markets (deep Kalshi mids), both seasons, gentle throttle.
KALSHI_SERIES="${KALSHI_SERIES:-KXMLBGAME,KXMLBRFI,KXMLBTOTAL,KXMLBSPREAD,KXMLBF5}"
KALSHI_SINCE="${KALSHI_SINCE:-2024-04-01}"
KALSHI_UNTIL="${KALSHI_UNTIL:-2026-07-22}"
KALSHI_SLEEP="${KALSHI_SLEEP:-0.5}"

echo "=== Kalshi closing-line backfill setup ($JOB_NAME) ==="
echo "  series=$KALSHI_SERIES  range=[$KALSHI_SINCE,$KALSHI_UNTIL]  sleep=${KALSHI_SLEEP}s"
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# ^@^ arg delimiter so the comma-separated --series value stays ONE argument.
ARGS="^@^-m@mlb.analysis.kalshi_history@--since@${KALSHI_SINCE}@--until@${KALSHI_UNTIL}"
ARGS="${ARGS}@--series@${KALSHI_SERIES}@--sleep@${KALSHI_SLEEP}"

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="$ARGS"
  --memory=1Gi --cpu=1 --task-timeout=86400 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${FLAGS[@]}"
fi

echo ""
echo "=== Done. Execute the crawl (resumable; re-run to resume) with: ==="
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "Progress: gcloud run jobs executions logs (or watch Odds/kalshi/_backfill_done/)"
echo "Output -> gs://<bucket>/Odds/history/ (source=kalshi, is_closing=True)"
