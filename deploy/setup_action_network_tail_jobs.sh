#!/usr/bin/env bash
# setup_action_network_tail_jobs.sh -- BNK-11 expansion-3 tail leagues.
# Same contract as setup_action_network_backfill.sh: Cloud Run JOBS ONLY,
# no schedulers, env-parameterized, bucket checkpoints, one-off executions.
#
#   ncaaf-26a: 2026 season to date    ncaaf-25a: 2025 season (Aug-Jan)
#   mlb-26:    2026 season            mlb-25:    2025 season
#   wnba-26:   2026 season            wnba-25:   2025 season
#   epl/mls/laliga/bundesliga/seriea/ligue1 -26: current season, gamelines
#   ncaaw-25:  2025-26 season, gamelines
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_action_network_tail_jobs.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds:bnk11-an-20261116}"
SA_EMAIL="mlb-betting-sa@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="concrete-crow-445205-m4-mlb-data"
TIME_BUDGET_S="${AN_TIME_BUDGET_S:-3300}"

provision_job() {
  local job_name="$1" league="$2" shard="$3" start="$4" end="$5"
  local job_flags=(
    --image="$IMAGE"
    --region="$REGION"
    --service-account="$SA_EMAIL"
    --set-env-vars="MLB_GCS_BUCKET=${BUCKET},GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},ACTION_NETWORK_BANK_ENABLED=1,AN_LEAGUE=${league},AN_SHARD=${shard},AN_START=${start},AN_END=${end},AN_TIME_BUDGET_S=${TIME_BUDGET_S},AN_SITE=gcp"
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

provision_job "an-backfill-ncaaf-26a" "ncaaf" "ncaaf-26a" "20260801" "20261116"
provision_job "an-backfill-ncaaf-25a" "ncaaf" "ncaaf-25a" "20250801" "20260131"
provision_job "an-backfill-mlb-26" "mlb" "mlb-26" "20260301" "20261116"
provision_job "an-backfill-mlb-25" "mlb" "mlb-25" "20250301" "20251130"
provision_job "an-backfill-mlb-24" "mlb" "mlb-24" "20240301" "20241130"
provision_job "an-backfill-wnba-26" "wnba" "wnba-26" "20260501" "20261116"
provision_job "an-backfill-wnba-25" "wnba" "wnba-25" "20250501" "20251031"
provision_job "an-backfill-epl-26" "epl" "epl-26" "20260801" "20261116"
provision_job "an-backfill-mls-26" "mls" "mls-26" "20260201" "20261116"
provision_job "an-backfill-laliga-26" "laliga" "laliga-26" "20260801" "20261116"
provision_job "an-backfill-bundesliga-26" "bundesliga" "bundesliga-26" "20260801" "20261116"
provision_job "an-backfill-seriea-26" "seriea" "seriea-26" "20260801" "20261116"
provision_job "an-backfill-ligue1-26" "ligue1" "ligue1-26" "20260801" "20261116"
provision_job "an-backfill-ncaaw-25" "ncaaw" "ncaaw-25" "20251101" "20260430"

echo "Done. Tail jobs created; NO schedulers."
