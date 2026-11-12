#!/usr/bin/env bash
# setup_first_scorer_banking.sh -- provision the first-scorer price-banking Jobs + Schedulers.
#
# Banks ParlayAPI micro markets forward (raw-first, snapshots T-120 / T-30 / T-5, completeness
# gate >=8 listed outcomes): NBA player_first_basket + player_first_team_basket, and NFL
# player_1st_td. See nba/odds/first_scorer.py and scripts/bank_first_scorer.py.
#
# NOT RUN by the PR that added it -- provisioning follows merge and is Luc's separate explicit
# call. Idempotent (create-or-update jobs + schedulers), following deploy/setup_nba_hoopr_refresh.sh.
#
# IMAGE: Cloud Run JOBs resolve their image tag at EVERY EXECUTION, so use the feeds image,
# never mlb-betting:latest. Rebuild from a clean checkout of origin/main with
# deploy/build_all_images.sh (SKIP_PROD=1) AFTER the PR is merged, then run this script.
#
# CREDIT BUDGETS (one shared 100k/mo pool; no new money):
#   NBA job sets ODDS_BANK_BUDGET_BASKETBALL_NBA only if the operator also lowers the main NBA
#   accumulator job's env by the same carve (default carve 2500/mo: worst case ~2000 + slate
#   discovery). The ledger is shared per sport, so the carve is an accounting split, not new spend.
#   NFL job MUST set ODDS_BANK_BUDGET_AMERICANFOOTBALL_NFL (default 500/mo here): that sport's
#   accumulator-ledger default is 0 (manual probes only), so without it every run is refused.
#
# CADENCE: every 15 minutes; the runner buys only events inside a snapshot window, so most
# firings spend slate-discovery credits only. All times UTC in the scheduler; windows are
# computed from each event's commence_time, not from wall-clock tip assumptions.
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_first_scorer_banking.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${FIRST_SCORER_SCHEDULE:-*/15 * * * *}"
NFL_BUDGET="${FIRST_SCORER_NFL_BUDGET:-500}"

echo "=== First-scorer price banking setup ==="
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it from merged main first (deploy/build_all_images.sh)."; exit 1; }

provision_job() {
  local job_name="$1" sport="$2" extra_env="$3"
  local job_flags=(
    --image="$IMAGE"
    --region="$REGION"
    --service-account="$SA_EMAIL"
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,PARLAY_API_KEY=parlay-api-key:latest"
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},FIRST_SCORER_BANK_ENABLED=1${extra_env}"
    --command="python"
    --args="scripts/bank_first_scorer.py"
    --args="--execute"
    --args="--sport"
    --args="${sport}"
    --memory=1Gi
    --cpu=1
    --task-timeout=900
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
  # scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED. Idempotent.
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

provision_job "first-scorer-nba" "basketball_nba" ""
provision_job "first-scorer-nfl" "americanfootball_nfl" ",ODDS_BANK_BUDGET_AMERICANFOOTBALL_NFL=${NFL_BUDGET}"

echo ""
echo "Done. Verify with:"
echo "  gcloud run jobs executions list --job=first-scorer-nba --region=$REGION --project=$PROJECT_ID"
echo "  gsutil ls gs://<bucket>/FirstScorer/basketball_nba/   # raw/ + summary_*.json + latest.json"
echo "  gsutil ls gs://<bucket>/FirstScorer/americanfootball_nfl/"
