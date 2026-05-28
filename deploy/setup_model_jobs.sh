#!/usr/bin/env bash
# setup_model_jobs.sh -- idempotently create/update retrain and calibrate Cloud Run Jobs.
#
# Run from Cloud Shell after deploy/deploy_service.sh:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_model_jobs.sh
#
# BATTER_TB is currently an active HR-proxy market, so it is covered by
# mlb-retrain-hr-v6 and mlb-calibrate-hr. Add a dedicated TB job here only
# after a true BATTER_TB model artifact exists.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-mlb-betting}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/${SERVICE}:latest}"
SA_EMAIL="${SA_EMAIL:-${SERVICE}-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
CLOUDSQL_INSTANCE="${CLOUDSQL_INSTANCE:-${PROJECT_ID}:${REGION}:mlb-betting-db}"

COMMON_SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,MLB_DB_URL=mlb-db-url:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest"
COMMON_ENV="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"

upsert_job() {
  local job_name="$1"
  local module="$2"
  local memory="${3:-4Gi}"
  local cpu="${4:-2}"
  local timeout="${5:-7200s}"

  local action="create"
  if gcloud run jobs describe "$job_name" \
      --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    action="update"
  fi

  echo "${action^} job: $job_name -> python -m $module"
  gcloud run jobs "$action" "$job_name" \
    --image="$IMAGE" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" \
    --command="python" \
    --args="-m,${module}" \
    --memory="$memory" \
    --cpu="$cpu" \
    --task-timeout="$timeout" \
    --max-retries=1 \
    --set-secrets="$COMMON_SECRETS" \
    --set-env-vars="$COMMON_ENV" \
    --set-cloudsql-instances="$CLOUDSQL_INSTANCE"
}

echo "Configuring model retrain/calibration jobs from image: $IMAGE"

upsert_job "mlb-retrain-nrfi-v18"       "training.retrain_nrfi_v18"        "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-nrfi"         "training.calibrate_nrfi_v18"      "2Gi" "1" "1800s"

upsert_job "mlb-retrain-hr-v6"          "training.retrain_hr_v6"           "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-hr"           "training.calibrate_hr_v6"         "2Gi" "1" "1800s"

upsert_job "mlb-retrain-f5-v5"          "training.retrain_f5_v5"           "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-f5"           "training.calibrate_f5_v5"         "2Gi" "1" "1800s"

upsert_job "mlb-retrain-k-v1"           "training.retrain_k_v1"            "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-k"            "training.calibrate_k_v1"          "2Gi" "1" "1800s"
upsert_job "mlb-retrain-outs-v1"        "training.retrain_outs_v1"         "4Gi" "2" "7200s"

upsert_job "mlb-retrain-game-v1"        "training.retrain_game_v1"         "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-game"         "training.calibrate_game_v1"       "2Gi" "1" "1800s"

upsert_job "mlb-retrain-batter-hits"    "training.retrain_batter_hits_v1"  "4Gi" "2" "7200s"
upsert_job "mlb-calibrate-batter-hits"  "training.calibrate_batter_hits_v1" "2Gi" "1" "1800s"

echo "Model jobs ready."
echo "BATTER_TB: active via HR proxy; retrain/calibration covered by HR jobs."
