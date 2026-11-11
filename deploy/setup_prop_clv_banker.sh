#!/usr/bin/env bash
# setup_prop_clv_banker.sh -- provision the prop CLV banker jobs (Argon Data Direction 1, 2026-11-10).
#
#   prop-clv-close-nba   python -m ev.prop_clv_banker capture --sport basketball_nba
#   prop-clv-close-nhl   python -m ev.prop_clv_banker capture --sport icehockey_nhl
#
# WHAT THE JOB DOES: a T-15 closing-snapshot capture for props. Each firing asks ParlayAPI for the slate
# (1 credit) and buys core prop markets ONLY for events starting within the next 20 minutes, banking the
# usual OddsAccum raw + best-book CSV shapes. The last pre-start snapshot of a game is its close; the CLV
# attach + coverage report (ev.prop_clv_banker attach|report) pair those closes with settled rows offline.
#
# CREDIT SAFETY (read before changing cadence): spend goes through nba/odds/credit_ledger.py against the
# UNCHANGED per-sport accumulator caps in nba/config.py (34,400/mo per sport), plus the closing carve-out
# sub-cap (CLV_CLOSING_MONTHLY_BUDGET: 5,000/mo per sport, tracked on OddsAccum/{sport}/_credits_closing/).
# Carve-out math: game-window firings are ~8.6 h/day (22:00-03:35 UTC plus weekend afternoons) at 12/h, so
# slate discovery costs ~3.1k/mo per sport; imminent-event props add ~50 credits/game-day. The 5k sub-cap
# binds BEFORE the accumulator cap is ever at risk, and --max-retries=0 means a failed firing never
# re-spends. Budgets below are copies of the nba/config.py constants.
#
# CADENCE (UTC): every 5 minutes inside the windows when NBA/NHL games actually start (tips/drops run
# ~19:00-22:30 ET on weekdays, ~12:00-17:00 ET on weekend afternoons). A 20-minute capture window against a
# 5-minute cadence gives every game ~4 chances to bank its T-15 snapshot; the LAST pre-start bank is the
# close regardless of which firing caught it.
#
# NOT RUN by the PR that introduces it: provisioning is a separate, later step (image build + this script),
# like deploy/setup_ncaaf_banking.sh was. Nothing here has been executed against GCP.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_CLV_BANKER=1 bash deploy/setup_prop_clv_banker.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_prop_clv_banker.sh     # pause every prop-clv-* scheduler
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
# Same feeds image as the odds banking jobs: a job runs whatever is in its image, and the production
# mlb-betting:latest tag must never be pointed at by a banking job (see deploy/setup_odds_banking.sh).
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# Budgets / floor: env override, else the nba/config.py values (the accumulator caps are NOT raised here).
BUDGET_NBA="${ODDS_BANK_BUDGET_BASKETBALL_NBA:-34400}"
BUDGET_NHL="${ODDS_BANK_BUDGET_ICEHOCKEY_NHL:-34400}"
RESERVE_FLOOR="${PARLAY_RESERVE_FLOOR:-500}"

# job|sport_key
JOBS=(
  "prop-clv-close-nba|basketball_nba"
  "prop-clv-close-nhl|icehockey_nhl"
)
# scheduler suffix|cron (UTC) -- game-start windows only, every 5 minutes
WINDOWS=(
  "eve|*/5 22-23 * * *"
  "late|*/5 0-3 * * *"
  "wknd|*/5 17-21 * * 0,6"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^prop-clv-'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "All prop-clv-* schedulers paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_CLV_BANKER:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_CLV_BANKER=1 once $IMAGE contains ev/prop_clv_banker.py."
  echo "(An older image has no CLV banker: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

for row in "${JOBS[@]}"; do
  IFS='|' read -r JOB SPORT <<<"$row"
  ARGS="-m,ev.prop_clv_banker,capture,--sport,${SPORT},--max-credits,400"
  FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
         --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,PARLAY_API_KEY=parlay-api-key:latest"
         --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},ODDS_BANK_BUDGET_BASKETBALL_NBA=${BUDGET_NBA},ODDS_BANK_BUDGET_ICEHOCKEY_NHL=${BUDGET_NHL},PARLAY_RESERVE_FLOOR=${RESERVE_FLOOR}"
         --command="python" --args="$ARGS"
         --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=0 --quiet)
  # max-retries=0: a retried capture would re-spend credits; the ledgers record the first attempt either way.
  if gcloud run jobs describe "$JOB" --region="$REGION" --quiet >/dev/null 2>&1; then
    gcloud run jobs update "$JOB" "${FLAGS[@]}"
  else
    gcloud run jobs create "$JOB" "${FLAGS[@]}"
  fi
  # scheduler-invoker must hold run.invoker ON THE JOB, or every scheduler firing is PERMISSION_DENIED
  # (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
  gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  for wrow in "${WINDOWS[@]}"; do
    IFS='|' read -r SUFFIX CRON <<<"$wrow"
    SJOB="${JOB}-${SUFFIX}"
    SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
            --oauth-service-account-email="$SCHED_SA"
            --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
            --attempt-deadline=320s --project="$PROJECT_ID")
    if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
    else
      gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
    fi
    echo "scheduler ${SJOB} @ '${CRON}' UTC -> ${JOB}"
  done
done

echo
echo "Done. Verify:  gcloud scheduler jobs list --location=$REGION | grep prop-clv"
echo "Ledgers:       gsutil cat gs://<bucket>/OddsAccum/basketball_nba/_credits/\$(date +%Y-%m).json"
echo "Closing sub:   gsutil cat gs://<bucket>/OddsAccum/basketball_nba/_credits_closing/\$(date +%Y-%m).json"
