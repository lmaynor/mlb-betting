#!/usr/bin/env bash
# setup_nba_hoopr_refresh.sh -- provision the nightly hoopR/ESPN NBA mirror Job + Scheduler.
#
# Runs `python -m nba.data.hoopr --mode refresh` (current hoopR season, plus last season during the Aug-Oct turnover, for
# player/team box scores + schedules -> NBA/hoopr/ in the shared bucket). Byte-identical files are not rewritten (sha256 vs
# NBA/hoopr/_manifest.json). No DB, no secrets, no API key. Gives nba/props an ESPN-keyed history that already has the new
# season (SportsBlaze does not): `python -m nba.props.score --history-source espn`.
#
# NOT RUN by the PR that added it -- provisioning is a deploy decision. Idempotent (create-or-update job + scheduler).
#
# IMAGE: a Cloud Run JOB resolves its image tag at EVERY EXECUTION, so new feature jobs run from the separate feeds image,
# never mlb-betting:latest (docs/solutions/conventions/cloud-run-job-image-tag-resolution.md). Rebuild it from a clean
# checkout of origin/main with deploy/build_all_images.sh (SKIP_PROD=1) AFTER this code is merged, then run this script.
#
# FIRST RUN is a backfill, not the nightly refresh (override the args for ONE execution; repeated --args, CONTEXT s15.9):
#   gcloud run jobs execute nba-hoopr-refresh --region=us-central1 --wait \
#     --args="-m" --args="nba.data.hoopr" --args="--mode" --args="backfill"
# Size it first from any machine:  python3 -m nba.data.hoopr --mode backfill --probe   (core group ~ 50 MB for 2019-2027)
#
# Check afterwards: gs://<bucket>/NBA/hoopr/last_refresh.json (status ok|partial|error + per-file failures).
# Not wired into monitor_ops (same as nba-refresh-data / nfl-refresh-data): a failed execution shows in Cloud Run only.
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_nba_hoopr_refresh.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="nba-hoopr-refresh"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${NBA_HOOPR_SCHEDULE:-45 13 * * *}"        # 13:45 UTC daily (after nba-refresh-data 13:00, nfl-refresh-data 13:30)
JOB_RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"

echo "=== NBA nightly hoopR/ESPN mirror setup ==="
echo "Project : $PROJECT_ID"
echo "Job     : $JOB_NAME"
echo "Image   : $IMAGE"
echo "SA      : $SA_EMAIL"
echo ""

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it from merged main first (deploy/build_all_images.sh)."; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --args="-m"
  --args="nba.data.hoopr"
  --args="--mode"
  --args="refresh"
  --memory=2Gi
  --cpu=1
  --task-timeout=1800
  --max-retries=1
  --quiet
)

if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."
  gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."
  gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

SCHED_FLAGS=(
  --location="$REGION"
  --schedule="$SCHEDULE"
  --time-zone="Etc/UTC"
  --uri="$JOB_RUN_URI"
  --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=320s
  --project="$PROJECT_ID"
)

# scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED (docs/solutions/runtime-errors/
# scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
  echo "Scheduler exists -- updating..."
  gcloud scheduler jobs update http "$JOB_NAME" "${SCHED_FLAGS[@]}"
else
  echo "Scheduler not found -- creating..."
  gcloud scheduler jobs create http "$JOB_NAME" "${SCHED_FLAGS[@]}"
fi

echo ""
echo "Done. Run the one-time backfill (see header), then verify with:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --project=$PROJECT_ID"
