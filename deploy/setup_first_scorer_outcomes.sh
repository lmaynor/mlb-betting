#!/usr/bin/env bash
# setup_first_scorer_outcomes.sh -- provision the first-scorer outcomes Jobs + Schedulers.
#
# Nightly derivation from mirrored play-by-play (no API calls, no credits):
#   first-scorer-outcomes-nba: NBA first basket, both settlement variants (needs the hoopR pbp
#                              mirror flip merged + its one-time backfill run first)
#   first-scorer-outcomes-nfl: NFL first TD scorer (nflverse pbp is already mirrored)
#
# NOT RUN by the PR that added it -- provisioning follows merge and is Luc's separate explicit
# call. Idempotent, following deploy/setup_nba_hoopr_refresh.sh.
#
# IMAGE: Cloud Run JOBs resolve their image tag at EVERY EXECUTION, so use the feeds image,
# never mlb-betting:latest. Rebuild from merged main with deploy/build_all_images.sh
# (SKIP_PROD=1) first, then run this script.
#
# ORDER OF OPERATIONS at provisioning time:
#   1. Merge the pbp-flip PR, then run its one-time backfill ONCE:
#        gcloud run jobs execute nba-hoopr-refresh --region=us-central1 --wait \
#          --args="-m" --args="nba.data.hoopr" --args="--mode" --args="backfill"
#   2. Run this script.
#   3. One-time historical outcome derivation (both sports, full windows in the runner defaults):
#        gcloud run jobs execute first-scorer-outcomes-nba --region=us-central1 --wait
#        gcloud run jobs execute first-scorer-outcomes-nfl --region=us-central1 --wait
#   Nightly schedulers (14:30 UTC, after that day's 13:45 UTC hoopR refresh) then keep the
#   current season current; the runner is idempotent per season file.
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_first_scorer_outcomes.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${FIRST_SCORER_OUTCOMES_SCHEDULE:-30 14 * * *}"   # 14:30 UTC daily, after nba-hoopr-refresh 13:45 UTC

echo "=== First-scorer outcomes setup ==="
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it from merged main first (deploy/build_all_images.sh)."; exit 1; }

provision_job() {
  local job_name="$1" sport="$2"
  local job_flags=(
    --image="$IMAGE"
    --region="$REGION"
    --service-account="$SA_EMAIL"
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},FIRST_SCORER_OUTCOMES_ENABLED=1"
    --command="python"
    --args="scripts/derive_first_scorer_outcomes.py"
    --args="--execute"
    --args="--sport"
    --args="${sport}"
    --memory=2Gi
    --cpu=1
    --task-timeout=1800
    --max-retries=1
    --quiet
  )
  if gcloud run jobs describe "$job_name" --region="$REGION" --quiet >/dev/null 2>&1; then
    echo "Job exists -- updating: $job_name"
    gcloud run jobs update "$job_name" "${job_flags[@]}"
  else
    echo "Job not found -- creating: $job_name"
    gcloud run jobs create "$job_name" "${job_flags[@]}"
  fi
  gcloud run jobs add-iam-policy-binding "$job_name" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job_name}:run"
  local sched_flags=(
    --location="$REGION"
    --schedule="$SCHEDULE"
    --time-zone="Etc/UTC"
    --uri="$uri"
    --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=320s
    --project="$PROJECT_ID"
  )
  if gcloud scheduler jobs describe "$job_name" --location="$REGION" --quiet >/dev/null 2>&1; then
    echo "Scheduler exists -- updating: $job_name"
    gcloud scheduler jobs update http "$job_name" "${sched_flags[@]}"
  else
    echo "Scheduler not found -- creating: $job_name"
    gcloud scheduler jobs create http "$job_name" "${sched_flags[@]}"
  fi
}

provision_job "first-scorer-outcomes-nba" "basketball_nba"
provision_job "first-scorer-outcomes-nfl" "americanfootball_nfl"

echo ""
echo "Done. Verify with:"
echo "  gsutil ls gs://<bucket>/FirstScorer/outcomes/basketball_nba/"
echo "  gsutil ls gs://<bucket>/FirstScorer/outcomes/americanfootball_nfl/"
