#!/usr/bin/env bash
# setup_action_network_daily.sh -- provision FORWARD DAILY Action Network
# banking (BNK-11 follow-on; Luc's decision Nov 16, 2026: "bank every day
# on action network"). One Cloud Run job + one Cloud Scheduler per league.
#
# Each job runs scripts/bank_action_network.py in daily mode
# (AN_SHARD=daily, AN_LOOKBACK_DAYS=7, no AN_START/AN_END): the window
# resolves at run time to the 7 ET dates ending yesterday, banked
# partitions are skipped, so a run banks the new date plus any date in
# the window a previous run missed or failed -- a failed day self-heals
# on the next run. The market set is the league's verified-markets
# manifest in the bucket (written during the BNK-11 backfill), i.e.
# exactly the matrix the backfill proved. Offseason leagues return empty
# boards (one gamelines probe call per date) and record empty dates --
# expected, not an error.
#
# Cadence: daily from 06:00 America/Chicago (07:00 ET -- the prior ET
# day's boards are final), staggered 4 minutes per league so the 14
# streams do not start at the same instant (each stream still paces
# itself at ~1 req/s with the banker's adaptive backoff). Scheduler
# pattern matches the house: POST to the Cloud Run v2 jobs :run endpoint
# with the scheduler-invoker service account's OAuth token.
#
# IMAGE: use a tag built from the branch carrying the daily-mode banker,
# never a moving tag:
#   gcloud builds submit --config=cloudbuild.yaml \
#     --substitutions="_IMAGE=gcr.io/${PROJECT_ID}/mlb-betting-feeds,_TAG=bnk11-an-daily-20261116" .
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_action_network_daily.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds:bnk11-an-daily-20261116}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="concrete-crow-445205-m4-mlb-data"
TIME_BUDGET_S="${AN_TIME_BUDGET_S:-3300}"
LOOKBACK_DAYS="${AN_LOOKBACK_DAYS:-7}"

echo "=== Action Network DAILY banking setup (BNK-11) ==="
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE"
echo "Window  : last ${LOOKBACK_DAYS} ET dates ending yesterday, resolved at run time"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud iam service-accounts describe "$SCHEDULER_SA" --quiet >/dev/null \
  || { echo "ERROR: $SCHEDULER_SA not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it first (see header)."; exit 1; }

provision_job() {
  local league="$1"
  local job_name="an-daily-${league}"
  local job_flags=(
    --image="$IMAGE"
    --region="$REGION"
    --service-account="$SA_EMAIL"
    --set-env-vars="MLB_GCS_BUCKET=${BUCKET},GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},ACTION_NETWORK_BANK_ENABLED=1,AN_LEAGUE=${league},AN_SHARD=daily,AN_LOOKBACK_DAYS=${LOOKBACK_DAYS},AN_TIME_BUDGET_S=${TIME_BUDGET_S},AN_SITE=gcp"
    --command="python"
    --args="scripts/bank_action_network.py"
    --args="--execute"
    --memory=1Gi
    --cpu=1
    --task-timeout=3600
    --max-retries=0
    --quiet
  )
  if gcloud run jobs describe "$job_name" --region="$REGION" --quiet >/dev/null 2>&1; then
    echo "Job exists -- updating: $job_name"
    gcloud run jobs update "$job_name" "${job_flags[@]}"
  else
    echo "Job not found -- creating: $job_name"
    gcloud run jobs create "$job_name" "${job_flags[@]}"
  fi
}

provision_iam() {
  # The scheduler-invoker SA's run.jobs.run grant is PER-JOB in this
  # project (see autopilot-shadow's policy): without this binding the
  # scheduler's POST fails PERMISSION_DENIED and no execution is created.
  local job_name="an-daily-$1"
  echo "Granting roles/run.invoker to $SCHEDULER_SA on $job_name"
  gcloud run jobs add-iam-policy-binding "$job_name" \
    --region="$REGION" \
    --member="serviceAccount:${SCHEDULER_SA}" \
    --role="roles/run.invoker" \
    --quiet >/dev/null
}

provision_scheduler() {
  local league="$1" minute="$2"
  local job_name="an-daily-${league}"
  local sched_name="an-daily-${league}"
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job_name}:run"
  local sched_flags=(
    --location="$REGION"
    --schedule="${minute} 6 * * *"
    --time-zone="America/Chicago"
    --uri="$uri"
    --http-method=POST
    --oauth-service-account-email="$SCHEDULER_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --description="BNK-11 daily Action Network banking (${league}): banks the prior ET date plus any missed dates in the 7-day lookback. Provisioned Nov 16, 2026."
    --quiet
  )
  if gcloud scheduler jobs describe "$sched_name" --location="$REGION" --quiet >/dev/null 2>&1; then
    echo "Scheduler exists -- updating: $sched_name"
    gcloud scheduler jobs update http "$sched_name" "${sched_flags[@]}"
  else
    echo "Scheduler not found -- creating: $sched_name"
    gcloud scheduler jobs create http "$sched_name" "${sched_flags[@]}"
  fi
}

# League order sets the stagger: minute = 4 * index, from 06:00 CT.
LEAGUES=(nba nhl nfl ncaab ncaaf mlb wnba epl mls laliga bundesliga seriea ligue1 ncaaw)
for i in "${!LEAGUES[@]}"; do
  league="${LEAGUES[$i]}"
  provision_job "$league"
  provision_iam "$league"
  provision_scheduler "$league" "$((4 * i))"
done

echo ""
echo "Done. ${#LEAGUES[@]} daily jobs + schedulers provisioned (06:00-06:52 America/Chicago)."
echo "Verify with:"
echo "  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep an-daily"
echo "  gcloud run jobs executions list --job=an-daily-nba --region=$REGION --project=$PROJECT_ID"
echo "  gcloud storage ls gs://${BUCKET}/Odds/actionnetwork_nba/checkpoints/"
