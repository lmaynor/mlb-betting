#!/usr/bin/env bash
# setup_ncaab_banking.sh -- provision the NCAAB game-line banking jobs (BettingPros + Kalshi). BestBook's cbb crawl is
# deploy/setup_bestbook_live.sh (bb-live-cbb).
#
#   ncaab-bank-bp       python -m ev.ncaab_banker --source bettingpros   hourly at :35, Nov-Apr
#   ncaab-bank-kalshi   python -m ev.ncaab_banker --source kalshi        hourly at :40, Nov-Apr
#   ncaab-board         python -m ev.ncaab_board                         hourly at :55, Nov-Apr (joins the three banked feeds + BestBook's
#                                                                        cbb file into EVSweep/ncaab_games/odds_board_latest.json, the Game Lines board)
#
# Each writes Odds/{source}_live/ncaab/game_lines_latest.json (BestBook's ladder shape; the previous good file is kept when a
# crawl finds no priced games). Both feeds are free and unauthenticated: no ParlayAPI credits. Sources keep their own team codes
# (no cross-source D1 code map exists yet). Nothing reads these files yet: this is banking only.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_NCAAB_BANKER=1 bash deploy/setup_ncaab_banking.sh
#   PAUSE=1 / RESUME=1 PROJECT_ID=... bash deploy/setup_ncaab_banking.sh      # pause / resume every ncaab-bank-* scheduler
#   Test one run now:  gcloud run jobs execute ncaab-bank-kalshi --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# job|module args|cron (America/Chicago); the board runs after the :35 / :40 banks and BestBook's :50 crawl
ROWS=(
  "ncaab-bank-bp|ev.ncaab_banker,--source,bettingpros|35 * * 11-12,1-4 *"
  "ncaab-bank-kalshi|ev.ncaab_banker,--source,kalshi|40 * * 11-12,1-4 *"
  "ncaab-board|ev.ncaab_board|55 * * 11-12,1-4 *"
)

for MODE in PAUSE RESUME; do
  if [ "${!MODE:-0}" = "1" ]; then
    verb=$(echo "$MODE" | tr 'A-Z' 'a-z')
    for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^ncaab-bank-'); do
      gcloud scheduler jobs "$verb" "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
    done
    echo "All ncaab-bank-* schedulers: $verb done."; exit 0
  fi
done

[ "${CONFIRM_IMAGE_HAS_NCAAB_BANKER:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_NCAAB_BANKER=1 once $IMAGE contains ev/ncaab_banker.py."
  echo "(An older image has no ev.ncaab_banker: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB MODARGS CRON <<<"$row"
  FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
         --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
         --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
         --command="python" --args="-m,$MODARGS"
         --task-timeout="600s" --max-retries=1 --memory="512Mi" --cpu="1")
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
               --description="Trigger $JOB (ncaab banking / board)")
  if gcloud scheduler jobs describe "$JOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$JOB" "${SCHED_FLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$JOB" "${SCHED_FLAGS[@]}" --quiet
  fi
done
echo "NCAAB banking jobs and schedulers provisioned (enabled)."
