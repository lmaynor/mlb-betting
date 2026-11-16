#!/usr/bin/env bash
# setup_res11_shadow.sh -- provision the RES-11 Stage 2 forward shadow (ev/res11_shadow.py).
#
#   job  res11-shadow   python -m ev.res11_shadow
#
# WHAT IT DOES (ev/res11_shadow.py): one firing rebuilds the Stage 1 replay frame for the
# current window from the live banks (OddsAccum raw snapshots, EVSweep board archives,
# official NBA injury reports, NBA box-score master), runs the Stage 1 pop engine at the
# primary delta 0.045 with variant 2 (2-snapshot persistence) as the firing rule and G6
# (stale opener) as a HARD VETO, banks new triggers create-only to
#   gs://<bucket>/RES11/shadow/chunks/ (+ runs/ summaries, finalized/ day files with
#   closing consensus + next-snapshot fill linkage, state/ mpg cache).
# SHADOW ONLY: read-only against every bank, no order path, no venue calls, no contact
# with AUT-01 or PRD-04 jobs/windows/tapes. Judgment read Dec 13, 2026 against the
# ruling's Stage 2 bars (RUL-20261116-intraday-pop-approach).
#
# ARMING: RES11_SHADOW_ARMED=1 is set ONLY in this job's environment -- that env var is
# the kill switch (ev/res11_shadow.py exits 3 without it). It is set nowhere else.
# Removing it (or pausing the scheduler) disarms the shadow.
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket, used by every
# feeds job). GCS access is the job's service account (mlb-betting-sa). The script fails
# loudly if mlb-gcs-bucket is missing instead of creating or guessing a secret.
#
# TIMING: every 30 minutes at :07/:37 America/Chicago (offset from the :25/:55 board
# republishes and the :05/:35 executor passes). WINDOW: collection starts at arming
# (Nov 16, 2026, Luc's decision) and is judged Dec 13, 2026. REVIEW/DISABLE: Dec 14, 2026
# (pause or delete the scheduler once the window is scored).
#
# IMAGE: build a TAG from this branch first (the runner code is not in :latest):
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=res11-shadow-20261116 --project=$PROJECT_ID .
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:res11-shadow-20261116 PROJECT_ID=... bash deploy/setup_res11_shadow.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_res11_shadow.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_res11_shadow.sh          # pause the scheduler (disarm)
#   Run once now:  gcloud run jobs execute res11-shadow --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="res11-shadow"
SJOB="${JOB}-30m"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${SCHEDULE:-7,37 * * * *}"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "RES-11 shadow scheduler paused (shadow disarmed at the scheduler; the job env still carries the arm flag)."
  exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see the IMAGE note at the top of this script)."; exit 1; }
gcloud secrets describe mlb-gcs-bucket --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: secret mlb-gcs-bucket is missing. This script never creates secrets; provision it (the bucket name) and re-run."; exit 1; }

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},RES11_SHADOW_ARMED=1"
  --command="python" --args="-m,ev.res11_shadow"
  --memory=4Gi --cpu=1 --task-timeout=1800 --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="$SCHEDULE" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=180s
        --description="RES-11 Stage 2 forward shadow (variant 2, G6 veto), read-only logger. Collection from Nov 16, 2026; judgment Dec 13, 2026. Review/disable Dec 14, 2026."
        --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${SCHEDULE}' America/Chicago -> ${JOB}"

echo ""
echo "=== Done: job $JOB (${IMAGE}) + scheduler $SJOB ==="
echo "Run once now: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Logs:         gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Bank:         gcloud storage ls gs://<bucket>/RES11/shadow/"
echo "Not enabled:  any order path (none exists in the module), AUT-01 / PRD-04 contact (none)."
