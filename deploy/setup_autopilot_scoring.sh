#!/usr/bin/env bash
# setup_autopilot_scoring.sh -- provision the AUT-01 Phase 0 DEC 1 SCORING runner (ev/autopilot/scoring_job.py).
#
#   job  autopilot-scoring   python -m ev.autopilot.scoring_job --from-day 2026-11-16 --stem verdict-2026-12-01
#
# WHAT IT DOES (ev/autopilot/scoring_job.py): reads the banked Phase 0 shadow ledger
# (Autopilot/shadow_ledger/latest.jsonl.gz) plus the banked OddsAccum snapshot series, attaches closing
# consensus to ALL gate-pass rows (closes.attach_consensus_closes), runs the amended evaluator
# (evaluate.evaluate_phase0, bars 1-6 per RUL-20261116-autopilot-clv-drift with RUL-20261118 drag-free
# net CLV), renders the verdict (evaluate.render_phase0), and banks the reports to
#   gs://<bucket>/Autopilot/scoring/{stem}.md
#   gs://<bucket>/Autopilot/scoring/{stem}.json
#   gs://<bucket>/Autopilot/scoring/runs/{run_id}.json (create-only audit summary)
#
# OFFLINE READER ONLY: reads banked artifacts only and contains no order path of any kind.
# Nothing here places, mirrors, or routes an order, and no venue credential is mounted.
#
# ARMING: AUTOPILOT_SHADOW_ARMED is deliberately NOT set -- the scorer is an offline reader of
# banked artifacts and must not carry the arm flag. AUTOPILOT_ENABLE_SAVES is also unset.
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket, already used by every
# feeds job) -- the bucket name. GCS access is the job's service account (mlb-betting-sa). The
# script fails loudly if mlb-gcs-bucket is missing instead of creating or guessing a secret.
#
# TIMING & ONE-SHOT TRIGGER: NO Cloud Scheduler is created. This job is fired exactly once,
# by a Cloud Tasks one-shot task (dec1-2026-0800ct) scheduled for 2026-12-01T14:00:00Z
# (08:00 America/Chicago on Dec 1, 2026 = 14:00 UTC). Unlike a recurring cron scheduler,
# Cloud Tasks executes the HTTP target exactly once and automatically retires when done.
#
# IMAGE: defaults to the feeds image per docs/solutions/conventions/cloud-run-job-image-tag-resolution.md.
# The scorer code merged after the last feeds build, so build a TAG from the branch/main containing
# scoring_job.py:
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=autopilot-scoring-20261119 --project=$PROJECT_ID .
# and deploy with:
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:autopilot-scoring-20261119 PROJECT_ID=... bash deploy/setup_autopilot_scoring.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_autopilot_scoring.sh
#   STEM=dry-run-2026-11-19 PROJECT_ID=... bash deploy/setup_autopilot_scoring.sh
#   CREATE_ONESHOT=0 PROJECT_ID=... bash deploy/setup_autopilot_scoring.sh    # update job only, skip task
#   Run once now:  gcloud run jobs execute autopilot-scoring --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="autopilot-scoring"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
FROM_DAY="${FROM_DAY:-2026-11-16}"
STEM="${STEM:-verdict-2026-12-01}"
CREATE_ONESHOT="${CREATE_ONESHOT:-1}"
QUEUE="autopilot-scoring-dec1"

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see the IMAGE note at the top of this script)."; exit 1; }
gcloud secrets describe mlb-gcs-bucket --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: secret mlb-gcs-bucket is missing. This script never creates secrets; provision it (the bucket name) and re-run."; exit 1; }

FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,ev.autopilot.scoring_job,--from-day,${FROM_DAY},--stem,${STEM}"
  --memory=4Gi --cpu=1 --task-timeout=1800 --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or firing is PERMISSION_DENIED (code 7). Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

if [ "${CREATE_ONESHOT}" = "1" ]; then
  if ! gcloud tasks queues describe "$QUEUE" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Creating Cloud Tasks queue $QUEUE in $REGION..."
    gcloud tasks queues create "$QUEUE" --location="$REGION" --project="$PROJECT_ID" --quiet
  fi

  TASK_ID="dec1-2026-0800ct"
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  SCHEDULE_TIME="2026-12-01T14:00:00Z"

  echo "Scheduling one-shot task $TASK_ID for $SCHEDULE_TIME..."
  if ! gcloud tasks create-http-task "$TASK_ID" \
    --queue="$QUEUE" \
    --location="$REGION" \
    --url="$URI" \
    --method=POST \
    --oidc-service-account-email="$SCHED_SA" \
    --schedule-time="$SCHEDULE_TIME" \
    --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Note: task $TASK_ID in queue $QUEUE already exists or could not be created; continuing."
  else
    echo "Scheduled one-shot task $TASK_ID in queue $QUEUE for $SCHEDULE_TIME -> $JOB:run"
  fi
fi

echo ""
echo "=== Done: job $JOB (${IMAGE}) ==="
echo "Run once now: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Logs:         gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Tasks list:   gcloud tasks list --queue=$QUEUE --location=$REGION --project=$PROJECT_ID"
echo "Outputs:      gs://<bucket>/Autopilot/scoring/${STEM}.md and gs://<bucket>/Autopilot/scoring/${STEM}.json"
echo "Note:         autopilot-shadow-daily is NOT touched by this script and its disable is a separate Dec 1 review action."
