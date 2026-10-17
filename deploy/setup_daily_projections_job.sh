#!/usr/bin/env bash
# setup_daily_projections_job.sh -- provision the scheduled recompute of the NFL / NHL / NBA picks boards.
#
#   job daily-projections   python -m ev.daily_projections --all --write-gcs
#   scheduler daily-projections-30m   :25 and :55 every hour (America/Chicago)
#
# WHY: nothing recomputed EVSweep/{sport}_props/projections_latest.json on a schedule. The web service only started a
# BACKGROUND recompute once the file was 90 min stale, and a Cloud Run request thread gets no CPU after the response, so the
# boards were routinely hours old (ev/daily_projections.py always named this script as "the dependable path"; it did not exist).
# One execution recomputes all three sports in turn (a failing sport never costs the others their refresh; the exit code is
# non-zero if any failed, so Cloud Run shows it) and also: refreshes the active-roster cache Rosters/{sport}.json (at most every
# 6 h, see ev/rosters.py), unions the ParlayAPI and BettingPros live lines, and merges the board into the Central-day archive
# EVSweep/{sport}_props/archive/{YYYY-MM-DD}.json that serves the site's Yesterday toggle.
#
# CADENCE: every 30 min, offset to :25 / :55 so it runs AFTER the BettingPros live crawls (deploy/setup_bettingpros_live.sh:
# NHL :05, NFL :10, NBA :15, each ~2-3 min), so :25 sees lines <= 20 min old and :55 <= 50 min. MEASURED 2026-10-17 against the
# real masters: ~90 s for all three sports (game-log load 7-12 s per sport, ESPN schedule + compute the rest), peak RSS ~0.8 GB.
# The job is sized 2 Gi / 900 s with max-retries=0 (a retry would overlap the next firing; the next one is 30 min away).
#
# IMAGE: gcr.io/<project>/mlb-betting-feeds, NOT mlb-betting:latest. A Cloud Run JOB resolves its image tag at EVERY
# execution (docs/solutions/conventions/cloud-run-job-image-tag-resolution.md), so a job on :latest silently changes whenever
# anyone builds production. deploy/build_all_images.sh builds once from a clean origin/main and tags the same digest onto the
# feeds image (SKIP_PROD=1 leaves production :latest alone). The job reads the game-log masters, the odds banks and the
# BettingPros live files from the bucket and writes only EVSweep/ and Rosters/; it needs no DB, no API key, no Discord secret.
#
# Prereqs: the feeds image rebuilt WITH this code (a job runs whatever is in its image: an older image would recompute with the
# old engine -- no BettingPros union, no roster layer, no archive). The script refuses to continue unless
# CONFIRM_IMAGE_HAS_LIVE_LINES=1 is set, as a speed bump for exactly that mistake. Idempotent.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIVE_LINES=1 bash deploy/setup_daily_projections_job.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_daily_projections_job.sh      # pause the scheduler (the web fallback resumes)
#   Test one run now:  gcloud run jobs execute daily-projections --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB="daily-projections"
SJOB="daily-projections-30m"
CRON="25,55 * * * *"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused ${SJOB}."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_LIVE_LINES:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_LIVE_LINES=1 once $IMAGE contains ev/rosters.py and ev/bettingpros_events.py."
  echo "(An older image would recompute the boards with the OLD engine.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
       --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
       --command="python" --args="-m,ev.daily_projections,--all,--write-gcs"
       --memory=2Gi --cpu=1 --task-timeout=900 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker must hold run.invoker ON THE JOB, or every scheduler firing is PERMISSION_DENIED (status code 7) and the job
# silently never runs. docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=320s --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${CRON}' America/Chicago -> ${JOB}"

echo
echo "Done. Prove the REAL path (a manual 'jobs execute' uses YOUR credentials; the scheduler uses scheduler-invoker):"
echo "  gcloud scheduler jobs run ${SJOB} --location=${REGION} --project=${PROJECT_ID}"
echo "  gcloud scheduler jobs describe ${SJOB} --location=${REGION} --format='yaml(status,lastAttemptTime)'   # status.code must be empty"
echo "  gcloud run jobs executions list --job=${JOB} --region=${REGION} --limit=3"
echo "  gcloud logging read 'resource.labels.job_name=\"${JOB}\" textPayload:\"PROJECTIONS\"' --limit=6 --freshness=2h --format='value(textPayload)'"
