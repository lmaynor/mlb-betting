#!/usr/bin/env bash
# setup_ncaaf_banking.sh -- provision the NCAAF game-line banking jobs (BettingPros + Kalshi). Follows
# deploy/setup_ncaab_banking.sh row-for-row.
#
#   ncaaf-bank-bp       python -m ev.ncaaf_banker --source bettingpros   every 20 min, Aug-Jan
#   ncaaf-bank-kalshi   python -m ev.ncaaf_banker --source kalshi        every 20 min, Aug-Jan
#
# CADENCE (deliberate, differs from the NCAAB hourly rows): the RFQ pricer's leg staleness gate is 1500 s, so an hourly
# bank leaves NCAA rows usable only ~25 min of each hour (the coverage measurement of 2026-11-10). A 20-minute cadence
# inside the season keeps every banked row inside the gate. BettingPros' offers crawl is slow by design (paged at 10,
# ~0.8 s/page): a Saturday-window execution that overruns its slot is bounded by the 900 s task timeout, and the banker
# keeps the previous good file whenever a crawl finds no priced games, so an overrun degrades to a skipped cycle, never
# to an empty file. Both feeds are free and unauthenticated: no ParlayAPI credits, no pool impact.
#
# Each writes Odds/{source}_live/ncaaf/game_lines_latest.json (BestBook's ladder shape). Sources keep their own team
# codes; the BettingPros -> Kalshi crosswalk is applied by the consumer (ev.kalshi_game_banked via
# ev.sports.ncaa_teams), never by the banker.
#
# NOT RUN by the PR that introduces it: provisioning is a deploy step for Luc, like setup_ncaab_banking.sh was.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_NCAAF_BANKER=1 bash deploy/setup_ncaaf_banking.sh
#   PAUSE=1 / RESUME=1 PROJECT_ID=... bash deploy/setup_ncaaf_banking.sh      # pause / resume every ncaaf-bank-* scheduler
#   Test one run now:  gcloud run jobs execute ncaaf-bank-bp --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# job|module args|cron (America/Chicago); */20 keeps banked rows inside the pricer's 1500 s staleness gate
ROWS=(
  "ncaaf-bank-bp|ev.ncaaf_banker,--source,bettingpros|*/20 * * 8-12,1 *"
  "ncaaf-bank-kalshi|ev.ncaaf_banker,--source,kalshi|*/20 * * 8-12,1 *"
)

for MODE in PAUSE RESUME; do
  if [ "${!MODE:-0}" = "1" ]; then
    verb=$(echo "$MODE" | tr 'A-Z' 'a-z')
    for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^ncaaf-bank-'); do
      gcloud scheduler jobs "$verb" "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
    done
    echo "All ncaaf-bank-* schedulers: $verb done."; exit 0
  fi
done

[ "${CONFIRM_IMAGE_HAS_NCAAF_BANKER:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_NCAAF_BANKER=1 once $IMAGE contains ev/ncaaf_banker.py."
  echo "(An older image has no ev.ncaaf_banker: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB MODARGS CRON <<<"$row"
  FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
         --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
         --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
         --command="python" --args="-m,$MODARGS"
         --task-timeout="900s" --max-retries=1 --memory="512Mi" --cpu="1")
  if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Updating Cloud Run job $JOB..."
    gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID" --quiet
  else
    echo "Creating Cloud Run job $JOB..."
    gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID" --quiet
  fi
  # scheduler-invoker needs run.invoker ON THE JOB (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md)
  gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  SCHED_FLAGS=(--schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method="POST"
               --oauth-service-account-email="$SCHED_SA"
               --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
               --location="$REGION" --project="$PROJECT_ID"
               --description="Trigger $JOB (ncaaf banking)")
  if gcloud scheduler jobs describe "$JOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$JOB" "${SCHED_FLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$JOB" "${SCHED_FLAGS[@]}" --quiet
  fi
done
echo "NCAAF banking jobs and schedulers provisioned (enabled)."
