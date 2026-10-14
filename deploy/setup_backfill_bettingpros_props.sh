#!/usr/bin/env bash
# setup_backfill_bettingpros_props.sh -- create/update the NFL / NHL / NBA BettingPros player-prop SLOW-CRAWL jobs.
#
#   bp-backfill-nfl | bp-backfill-nhl | bp-backfill-nba   python -m mlb.runners.backfill_bettingpros_props
#
# Writes long-format parquet to gs://<bucket>/Odds/bettingpros_{nfl|nhl|nba}/{market}/{YYYY-MM-DD}.parquet, NEWEST
# FIRST, resume-exact (an existing partition -- including a 0-row "nothing there" marker -- is skipped). No DB, no
# API key (BettingPros' public web key is baked into mlb_core.odds.bettingpros; override with BP_API_KEY if it rotates).
# Spends no ParlayAPI credits.
#
# Crawl-time estimate (smoke-measured 2026-10-14: ~7s per market-day for NHL): NFL ~1-2h, NHL ~10-15h, NBA (5 core
# markets) ~25-35h for the full history. A task is capped at 24h; max-retries=3 re-enters and resumes, so a timeout only
# costs the in-flight day. Pace is env-tunable (BP_DELAY / BP_MARKET_SLEEP / BP_DATE_SLEEP); slow it down if BettingPros
# starts returning 429s -- api_get already backs off 2/4/6s and the day is retried on the next attempt.
#
# IMAGE: a dedicated image, NOT mlb-betting:latest (every scheduled MLB job re-resolves that tag at each execution) and not
# the shared feeds image while this is unmerged. Build it from a CLEAN checkout:
#   gcloud builds submit --config=cloudbuild.yaml --project=$PROJECT_ID \
#     --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-bp-crawl .
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_backfill_bettingpros_props.sh
#   gcloud run jobs execute bp-backfill-nhl --region=us-central1 --project=$PROJECT_ID --async
#   # one season / one market:  --update-env-vars=BP_START=2024-10-01,BP_END=2025-06-30,BP_MARKETS=sog
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-bp-crawl}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
BP_DELAY="${BP_DELAY:-0.8}"
BP_MARKET_SLEEP="${BP_MARKET_SLEEP:-1.5}"
BP_DATE_SLEEP="${BP_DATE_SLEEP:-3.0}"

# sport|markets  (NBA: the 5 core markets first -- 11 markets would triple the crawl; widen later with BP_MARKETS)
SPORTS=(
  "NFL|all"
  "NHL|all"
  "NBA|pts,reb,ast,threes,pra"
)

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found -- build it first (see header)."; exit 1; }

for spec in "${SPORTS[@]}"; do
  IFS='|' read -r sport markets <<<"$spec"
  job="bp-backfill-$(echo "$sport" | tr '[:upper:]' '[:lower:]')"
  # ^@^ delimiter: BP_MARKETS contains commas.
  env="GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@BP_SPORT=${sport}@BP_MARKETS=${markets}"
  env="${env}@BP_DELAY=${BP_DELAY}@BP_MARKET_SLEEP=${BP_MARKET_SLEEP}@BP_DATE_SLEEP=${BP_DATE_SLEEP}"
  flags=(
    --image="$IMAGE" --region="$REGION" --project="$PROJECT_ID" --service-account="$SA_EMAIL"
    --command="python" --args="-m" --args="mlb.runners.backfill_bettingpros_props"
    --memory=2Gi --cpu=1 --task-timeout=86400s --max-retries=3
    --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
    --set-env-vars="^@^${env}" --quiet
  )
  if gcloud run jobs describe "$job" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Job $job exists -- updating..."; gcloud run jobs update "$job" "${flags[@]}"
  else
    echo "Job $job not found -- creating..."; gcloud run jobs create "$job" "${flags[@]}"
  fi
done

echo ""
echo "Done. Execute (each resumes where it stopped):"
for spec in "${SPORTS[@]}"; do
  s="${spec%%|*}"
  echo "  gcloud run jobs execute bp-backfill-$(echo "$s" | tr '[:upper:]' '[:lower:]') --region=$REGION --project=$PROJECT_ID --async"
done
echo "Progress: gcloud storage ls gs://<bucket>/Odds/bettingpros_{nfl,nhl,nba}/<market>/ | tail"
