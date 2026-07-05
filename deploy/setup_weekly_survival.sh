#!/usr/bin/env bash
# setup_weekly_survival.sh -- provision the Monday soft-line intelligence report.
#
# Cloud Run Job runs mlb.runners.weekly_survival_report: refits the empirical
# per-(market,book) vig lookup (book_vig --save) and runs the stale-quote
# survival analysis over the trailing 14 days, posting both to Discord
# (#performance). The runner itself no-ops unless it is Monday (UTC), so the
# schedule below is belt-and-braces; SURVIVAL_FORCE=1 for ad-hoc runs.
#
# GCS only (no DB). Reuses the mlb-betting image + SA.
#
# Prereq: image rebuilt with mlb/analysis/{book_vig,quote_survival}.py and
# mlb/runners/weekly_survival_report.py (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_weekly_survival.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-weekly-survival"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# Monday 13:00 UTC -- after the 09:00 settle and before the 14:00 refresh.
SCHEDULE="0 13 * * 1"

echo "=== Weekly survival report setup ==="
echo "Job: $JOB_NAME  schedule='$SCHEDULE' UTC (runner also self-guards to Monday)"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_PERFORMANCE=discord-webhook-performance:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python3" --args="-m,mlb.runners.weekly_survival_report"
  --memory=1Gi --cpu=1 --task-timeout=1800 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

# scheduler-invoker must hold run.invoker ON THE JOB or every scheduler firing
# is PERMISSION_DENIED (code 7) -- this bit us for 3 jobs in a row.
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
echo "run.invoker granted to $SCHED_SA on $JOB_NAME"

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
sjob="${JOB_NAME}-mon"
flags=(
  --location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC"
  --uri="$URI" --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=1800s --project="$PROJECT_ID"
)
if gcloud scheduler jobs describe "$sjob" --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$sjob" "${flags[@]}"
else
  gcloud scheduler jobs create http "$sjob" "${flags[@]}"
fi
echo "Scheduler set: $sjob @ '$SCHEDULE' UTC"

echo ""
echo "Force a report right now (any day):"
echo "  gcloud run jobs update $JOB_NAME --region=$REGION --update-env-vars=SURVIVAL_FORCE=1 --quiet && \\"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --wait && \\"
echo "  gcloud run jobs update $JOB_NAME --region=$REGION --remove-env-vars=SURVIVAL_FORCE --quiet"
