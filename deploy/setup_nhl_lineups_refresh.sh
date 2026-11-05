#!/usr/bin/env bash
# setup_nhl_lineups_refresh.sh -- provision the RotoWire confirmed-starting-goalie refresh for the NHL game model.
#
#   job nhl-lineups-refresh    python -m nhl.data.rotowire_lineups --refresh
#   scheduler nhl-lineups-refresh-30m   :15 and :45, 09:00-19:45 America/New_York
#
# WHAT: ONE request to https://www.rotowire.com/hockey/nhl-lineups.php per firing (today's page), parsed for goalies RotoWire
# marks "Confirmed", written to EVSweep/nhl_games/confirmed_goalies.json {fetched_at, day, confirmed:{TEAM: goalie}}. The NHL game
# model (nhl.game.run_daily, run inside daily-projections at :25 / :55) READS THAT FILE and never touches the site itself, so
# RotoWire sees ~22 requests a day, not ~48. :15 / :45 puts the refresh ahead of the :25 / :55 board run. A cache older than
# 120 min, or for another day, is ignored and the model falls back to its inferred starter. An empty / unparseable page fails
# the job (red in Cloud Run) and leaves the previous cache in place.
#
# ACCESS: RotoWire's terms (Article VI) bar automated access without WRITTEN consent. The owner has a verbal yes; get it in
# writing before this runs on a schedule. The script refuses unless CONFIRM_ROTOWIRE_CONSENT=1 is set, as a speed bump.
#
# WINDOW: 09:00-19:45 ET covers morning skates through the evening puck drops (7:00-7:30 PM ET games; weekend matinees confirm
# earlier). Widen CRON if late-West-coast confirmations matter.
#
# IMAGE: gcr.io/<project>/mlb-betting-feeds (same as daily-projections; deploy/build_all_images.sh with SKIP_PROD=1). The image must
# contain nhl/data/rotowire_lineups.py: a job runs whatever is in its image (an older image fails with ModuleNotFoundError).
# Needs only the bucket secret: no DB, no API key, no Discord. Idempotent.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_ROTOWIRE_CONSENT=1 bash deploy/setup_nhl_lineups_refresh.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_nhl_lineups_refresh.sh            # pause the scheduler
#   Test one run now:  gcloud run jobs execute nhl-lineups-refresh --region=us-central1 --wait
#   Turn the model side off without a redeploy:
#     gcloud run jobs update daily-projections --region=us-central1 --update-env-vars NHL_ROTOWIRE_GOALIES=0
#   (GOTCHA: setup_daily_projections_job.sh uses --set-env-vars, a full replace, and will drop that flag on its next run.)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB="nhl-lineups-refresh"
SJOB="nhl-lineups-refresh-30m"
CRON="${CRON:-15,45 9-19 * * *}"
TZ_NAME="America/New_York"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused ${SJOB} (the model falls back to inferred starters once the cache is >120 min old)."; exit 0
fi

[ "${CONFIRM_ROTOWIRE_CONSENT:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_ROTOWIRE_CONSENT=1 once RotoWire's written consent to automated access is on file."; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
       --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
       --command="python" --args="-m,nhl.data.rotowire_lineups,--refresh"
       --memory=512Mi --cpu=1 --task-timeout=120 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker must hold run.invoker ON THE JOB, or every firing is PERMISSION_DENIED (status code 7).
# docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="$TZ_NAME" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=180s --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${CRON}' ${TZ_NAME} -> ${JOB}"

echo
echo "Done. Prove the REAL path (a manual 'jobs execute' uses YOUR credentials; the scheduler uses scheduler-invoker):"
echo "  gcloud scheduler jobs run ${SJOB} --location=${REGION} --project=${PROJECT_ID}"
echo "  gcloud scheduler jobs describe ${SJOB} --location=${REGION} --format='yaml(status,lastAttemptTime)'   # status.code must be empty"
echo "  gcloud run jobs executions list --job=${JOB} --region=${REGION} --limit=3"
echo "  gcloud storage cat gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/EVSweep/nhl_games/confirmed_goalies.json"
