#!/usr/bin/env bash
# setup_bestbook_live.sh -- provision the LIVE BestBook prop crawl for NFL / NHL / NBA as a backup filler feed.
#
#   bb-live-nhl   python -m mlb.runners.bestbook_live_props --sport nhl   hourly at :20
#   bb-live-nfl   python -m mlb.runners.bestbook_live_props --sport nfl   hourly at :20 (Sunday pre-kickoff every 30 min)
#   bb-live-nba   python -m mlb.runners.bestbook_live_props --sport nba   hourly at :20
#
# Each run writes Odds/bestbook_live/{sport}/latest.json (ParlayAPI event shape, real two-sided sportsbooks + exchanges;
# DFS apps and Kalshi are dropped) which ev.daily_projections.load_events unions with ParlayAPI and BettingPros lines.
# BestBook is a free cookie-authenticated feed: this spends no ParlayAPI credits.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIVE_LINES=1 bash deploy/setup_bestbook_live.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_bestbook_live.sh     # pause every bb-live-* scheduler
#   Test one run now:  gcloud run jobs execute bb-live-nba --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# job|sport|scheduler tag|cron (America/Chicago)
ROWS=(
  "bb-live-nhl|nhl|hourly|20 * * 10-12,1-6 *"
  "bb-live-nfl|nfl|weekly|20 * * 9-12,1-2 1-6"
  "bb-live-nfl|nfl|sun-am|20,50 6-12 * 9-12,1-2 0"
  "bb-live-nfl|nfl|sun-rest|20 0-5,13-23 * 9-12,1-2 0"
  "bb-live-nba|nba|hourly|20 * * 10-12,1-6 *"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^bb-live-'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "All bb-live-* schedulers paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_LIVE_LINES:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_LIVE_LINES=1 once $IMAGE contains ev/bestbook_events.py."
  echo "(An older image has no mlb.runners.bestbook_live_props: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

JOBS_DONE=" "
for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB SPORT TAG CRON <<<"$row"
  if [[ "$JOBS_DONE" != *" $JOB "* ]]; then
    FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
           --set-env-vars="BUCKET_NAME=mlb-betting-data"
           --args="-m,mlb.runners.bestbook_live_props,--sport,$SPORT"
           --task-timeout="900s" --max-retries=1 --memory="1Gi" --cpu="1")
    if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      echo "Updating Cloud Run job $JOB..."
      gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID" --quiet
    else
      echo "Creating Cloud Run job $JOB..."
      gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID" --quiet
    fi
    JOBS_DONE="${JOBS_DONE}${JOB} "
  fi

  SCHED="${JOB}-${TAG}"
  URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB}:run"
  SCHED_FLAGS=(--schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method="POST"
               --oauth-service-account-email="$SCHED_SA" --location="$REGION" --project="$PROJECT_ID"
               --description="Trigger $JOB ($TAG) -> Odds/bestbook_live/$SPORT/latest.json")
  if gcloud scheduler jobs describe "$SCHED" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Updating Cloud Scheduler $SCHED ($CRON)..."
    gcloud scheduler jobs update http "$SCHED" "${SCHED_FLAGS[@]}" --quiet
  else
    echo "Creating Cloud Scheduler $SCHED ($CRON)..."
    gcloud scheduler jobs create http "$SCHED" "${SCHED_FLAGS[@]}" --quiet
  fi
done

echo "BestBook live jobs and schedulers provisioned."
