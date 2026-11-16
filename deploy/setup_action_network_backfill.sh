#!/usr/bin/env bash
# setup_action_network_backfill.sh -- provision the Action Network harvest
# Cloud Run Jobs (BNK-11). ONE-OFF backfill executions: JOBS ONLY, no
# schedulers (forward daily banking is a separate provisioning decision).
#
# Jobs are parameterized by env (AN_LEAGUE / AN_SHARD / AN_START / AN_END /
# AN_TIME_BUDGET_S), which scripts/bank_action_network.py reads directly.
# Checkpoints live in the bucket (Odds/actionnetwork_{league}/checkpoints/),
# so an execution that hits its task timeout is simply executed again --
# banked partitions are skipped. No secrets: the endpoint is unauthenticated
# and the bucket name is config, passed as a plain env var.
#
# Shard map (disjoint ranges; the local VM runs NBA proving + 2026-27 and
# the NHL current season):
#   NBA  b1-b4: 2025-26 in quarters   c1-c4: 2024-25 in quarters
#   NHL  nhl-b1/b2: 2025-26 halves    nhl-c1/c2: 2024-25 halves
#   NFL  nfl-26: 2026 season to date  nfl-25a/b, nfl-24a/b: halves
#   NCAAB ncaab-26: 2026-27 to date   ncaab-25a/b, ncaab-24a/b: halves
# Execute selectively, in priority waves (NBA first):
#   gcloud run jobs execute an-backfill-b1 --region=us-central1 --project=$PROJECT_ID --wait
# and repeat until the shard's checkpoint reports status "complete".
#
# IMAGE: use a tag built from the branch/commit carrying BNK-11, never
# mlb-betting:latest and never the moving feeds:latest:
#   gcloud builds submit --config=cloudbuild.yaml \
#     --substitutions="_IMAGE=gcr.io/${PROJECT_ID}/mlb-betting-feeds,_TAG=bnk11-an-20261116" .
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_action_network_backfill.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds:bnk11-an-20261116}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="concrete-crow-445205-m4-mlb-data"
TIME_BUDGET_S="${AN_TIME_BUDGET_S:-3300}"

echo "=== Action Network backfill setup (BNK-11, jobs only, no schedulers) ==="
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it first (see header)."; exit 1; }

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

# NBA (priority 1)
provision_job "an-backfill-b1" "nba" "b1" "20251020" "20251215"
provision_job "an-backfill-b2" "nba" "b2" "20251216" "20260215"
provision_job "an-backfill-b3" "nba" "b3" "20260216" "20260415"
provision_job "an-backfill-b4" "nba" "b4" "20260416" "20260630"
provision_job "an-backfill-c1" "nba" "c1" "20241021" "20241215"
provision_job "an-backfill-c2" "nba" "c2" "20241216" "20250215"
provision_job "an-backfill-c3" "nba" "c3" "20250216" "20250415"
provision_job "an-backfill-c4" "nba" "c4" "20250416" "20250630"
# NHL (priority 2)
provision_job "an-backfill-nhl-b1" "nhl" "nhl-b1" "20251001" "20251231"
provision_job "an-backfill-nhl-b2" "nhl" "nhl-b2" "20260101" "20260630"
provision_job "an-backfill-nhl-c1" "nhl" "nhl-c1" "20241001" "20241231"
provision_job "an-backfill-nhl-c2" "nhl" "nhl-c2" "20250101" "20250630"
# NFL (priority 3)
provision_job "an-backfill-nfl-26" "nfl" "nfl-26" "20260901" "20261116"
provision_job "an-backfill-nfl-25a" "nfl" "nfl-25a" "20250901" "20251130"
provision_job "an-backfill-nfl-25b" "nfl" "nfl-25b" "20251201" "20260228"
provision_job "an-backfill-nfl-24a" "nfl" "nfl-24a" "20240901" "20241130"
provision_job "an-backfill-nfl-24b" "nfl" "nfl-24b" "20241201" "20250228"
# NCAAB (priority 4)
provision_job "an-backfill-ncaab-26" "ncaab" "ncaab-26" "20261101" "20261116"
provision_job "an-backfill-ncaab-25a" "ncaab" "ncaab-25a" "20251101" "20260131"
provision_job "an-backfill-ncaab-25b" "ncaab" "ncaab-25b" "20260201" "20260430"
provision_job "an-backfill-ncaab-24a" "ncaab" "ncaab-24a" "20241101" "20250131"
provision_job "an-backfill-ncaab-24b" "ncaab" "ncaab-24b" "20250201" "20250430"

echo ""
echo "Done. NO schedulers were created (one-off backfill)."
echo "Verify with:"
echo "  gcloud run jobs executions list --job=an-backfill-b1 --region=$REGION --project=$PROJECT_ID"
echo "  gcloud storage ls gs://${BUCKET}/Odds/actionnetwork_nba/checkpoints/"
