#!/usr/bin/env bash
# setup_kalshi_alert_job.sh -- provision the Kalshi-vs-books +EV Discord pager.
#
# Cloud Run Job runs mlb.runners.kalshi_alert: scan odds_history (soft book
# quotes vs the Kalshi no-vig mid, mlb.analysis.kalshi_vs_books) -> keep only
# credible verdict=check divergences -> post NEW ones to Discord, deduped per
# day so the same lagging quote never pings twice.
#
# Recommended cadence: 6x/day, ~10 min after each SAME-DAY pairing of
# mlb-kalshi-capture (deploy/setup_kalshi_capture.sh) and the soft-book
# snapshot (CONTEXT.md s4's mlb-snapshot-* cadence) -- this job only reads
# odds_history, it does not fetch anything itself, so there's nothing new to
# find until both feeds land. Deliberately skips the two next-day-opener
# capture times (01:25/03:25 UTC): 20+ hours out, nothing to strike yet.
#
# Read-only against odds_history + Alerts/{day}/kalshi_*.parquet state --
# same GCS-only, no-DB, no-external-API profile as mlb-fast-alert.
#
# Prereq: image rebuilt with mlb/runners/kalshi_alert.py (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_kalshi_alert_job.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-kalshi-alert"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== Kalshi +EV alert job setup ==="
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

ENVV="^@^GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@KALSHI_ALERT_MIN_EV=0.03@KALSHI_ALERT_MIN_BOOKS=4@KALSHI_ALERT_SOFT_ONLY=1@KALSHI_ALERT_MAX_POSTS=10"
JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest"
  --set-env-vars="$ENVV"
  --command="python3" --args="-m,mlb.runners.kalshi_alert"
  --memory=1Gi --cpu=1 --task-timeout=600 --max-retries=0 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

# scheduler-invoker must hold run.invoker ON THE JOB or every scheduler firing
# is PERMISSION_DENIED (code 7) -- bit mlb-track-bettingpros/mlb-fast-alert/
# mlb-weekly-survival/mlb-kalshi-capture before (see docs/solutions/runtime-errors/
# scheduler-permission-denied-on-new-jobs.md). Idempotent -- safe on update too.
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
echo "run.invoker granted to $SCHED_SA on $JOB_NAME"

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

_upsert_sched() {  # sched_name  cron  description
  local name="$1" cron="$2" desc="$3" action=create
  gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" \
    >/dev/null 2>&1 && action=update
  echo "${action^} $name ($cron) -> $JOB_NAME"
  gcloud scheduler jobs "$action" http "$name" \
    --location="$REGION" --schedule="$cron" --time-zone="Etc/UTC" \
    --uri="$URI" --http-method=POST \
    --oauth-service-account-email="$SCHED_SA" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --attempt-deadline=320s --project="$PROJECT_ID" --quiet \
    ${desc:+--description="$desc"}
}

# +10 min after each same-day mlb-kalshi-capture / mlb-snapshot-* pairing
# (CONTEXT.md s4/s8) -- gives both feeds a landing buffer before the scan runs.
_upsert_sched "mlb-kalshi-alert-1605" "5 16 * * *"  "post-morning-run window"
_upsert_sched "mlb-kalshi-alert-1905" "5 19 * * *"  "day close / night lineups"
_upsert_sched "mlb-kalshi-alert-2035" "35 20 * * *" "night lineups confirmed"
_upsert_sched "mlb-kalshi-alert-2135" "35 21 * * *" "pre-close steam"
_upsert_sched "mlb-kalshi-alert-2205" "5 22 * * *"  "pre-evening run"
_upsert_sched "mlb-kalshi-alert-2315" "15 23 * * *" "closing snapshot landed"

echo ""
echo "=== Done: 1 job, 6 schedules/day ==="
echo "Run once now:  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo "List:          gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep kalshi-alert"
echo "Pause off-season / to stop pings:"
echo "  for s in 1605 1905 2035 2135 2205 2315; do gcloud scheduler jobs pause mlb-kalshi-alert-\$s --location=$REGION --project=$PROJECT_ID; done"
