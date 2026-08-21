#!/usr/bin/env bash
# setup_build_all_features.sh -- idempotently create/update the mlb-build-all-features
# Cloud Run Job.
#
# WHY THIS EXISTS: this job's container command was previously created by hand
# (gcloud run jobs), so it was NOT covered by any setup script and got missed in
# the 2026-06-24 pillarize re-provisioning. It kept running the old
# `runners.build_hr_features` module path -> ModuleNotFoundError -> the whole &&
# chain aborted -> no last_build.json sentinels -> every runner aborted on the
# stale-build guard (and HR crashed on its abort path). See
# handoffs/handoff_2026-06-25_stale_build_and_hr_crash.md.
#
# Committing this means the job's command + image are version-controlled and
# re-runnable after any module move. Re-provision after every restructure.
#
# Run from Cloud Shell after deploy/deploy_service.sh:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_build_all_features.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-mlb-betting}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/${SERVICE}:latest}"
SA_EMAIL="${SA_EMAIL:-${SERVICE}-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
CLOUDSQL_INSTANCE="${CLOUDSQL_INSTANCE:-${PROJECT_ID}:${REGION}:mlb-betting-db}"

COMMON_SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,MLB_DB_URL=mlb-db-url:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest"
COMMON_ENV="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"

JOB="mlb-build-all-features"

# Builders run in dependency order (F5 reads NRFI output); any failure aborts the
# rest via &&. Keep in sync with main.py builders + CONTEXT.md section 4.
CHAIN="python3 -m mlb.runners.build_hr_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_nrfi_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_k_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_f5_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_batter_hits_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_batter_tb_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_sb_features"
CHAIN="$CHAIN && python3 -m mlb.runners.build_game_features"

action="create"
if gcloud run jobs describe "$JOB" \
    --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  action="update"
fi

echo "${action^} job: $JOB (image: $IMAGE)"
echo "  chain: $CHAIN"

# The chain contains no commas, so gcloud's comma-split yields exactly ["-c", "<chain>"].
gcloud run jobs "$action" "$JOB" \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$SA_EMAIL" \
  --command="/bin/sh" \
  --args="-c,${CHAIN}" \
  --memory="4Gi" \
  --cpu="2" \
  --task-timeout="3600s" \
  --max-retries=1 \
  --set-secrets="$COMMON_SECRETS" \
  --set-env-vars="$COMMON_ENV" \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE"

echo ""
echo "=== Done ==="
echo "Verify:   gcloud run jobs describe $JOB --region=$REGION --project=$PROJECT_ID \\"
echo "            --format='value(spec.template.spec.template.spec.containers[0].args)'"
echo "Test-run: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
