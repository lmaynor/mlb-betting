#!/usr/bin/env bash
# setup_novig_shadow.sh -- provision the Novig SHADOW loop as a Cloud Run Job + Cloud Scheduler on NFL game days.
#
#   job  novig-shadow-nfl   python -m ev.novig.shadow_job
#
# WHAT IT DOES (ev/novig/shadow_job.py): one firing restores the banked shadow ledger from GCS, reads the NFL props sweep's
# published consensus (EVSweep/nfl_props/consensus.json), reads the PUBLIC Novig catalog/books (no key), runs the same
# paper-fill shadow pass as `python -m ev.props_exec run --novig`, banks every new order / decision / fill to
#   gs://<bucket>/Novig/shadow_ledger/{ET day}/{run}_{hash}.jsonl.gz      (append-only, create-only, idempotent)
# and prints a NOVIG_CLV_GATE status line. It is SHADOW/PAPER ONLY: it holds no Novig key, no Kalshi key, no order client
# and never mirrors orders to the Novig paper sandbox. Nothing here enables, configures or approaches live trading.
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket, already used by every feeds job) -- the bucket name.
# GCS access is the job's service account (mlb-betting-sa), the same one every other job uses. NOT mounted on purpose:
#   * Novig paper trading key (~/.novig/paper.env; the --novig-paper-orders mirror) -- not in Secret Manager, not needed;
#   * PARLAY_API_KEY (the --refresh-consensus credit spend) -- off; the job works only on the sweep's own snapshots;
#   * MLB_DB_URL / Cloud SQL, Kalshi anything.
# The script fails loudly if mlb-gcs-bucket is missing instead of creating or guessing a secret.
#
# TIMING: gates reject consensus older than 25 min, and the sweep (deploy/setup_nfl_sweep_schedule.sh) republishes every 30 min
# on Sunday mornings, hourly on Sunday afternoon/evening and every 2 h Mon-Sat. Each firing below starts 6 min AFTER a sweep
# cycle and works while that snapshot is fresh (max-runtime 1500 s < the 30 min gap, so executions do not overlap; the job also
# exits at once when no NFL game is inside its window, so an offseason firing costs one object read).
#   Sun  6,36 6-11 * * 0    and   6 12-20 * * 0
#   Thu  6 12-18/2 * * 4        (Thursday night + Thanksgiving)
#   Mon  6 12-18/2 * * 1        (Monday night)
#   Fri/Sat 6 10-20/2 * * 5,6   (Black Friday, late-season Saturdays)
#
# IMAGE: defaults to the feeds image per docs/solutions/conventions/cloud-run-job-image-tag-resolution.md. To run a branch build
# without moving feeds:latest (which odds banking / NHL ingest / props alerts resolve at every execution), build a TAG:
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=novig-shadow --project=$PROJECT_ID .
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:novig-shadow PROJECT_ID=... bash deploy/setup_novig_shadow.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_novig_shadow.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_novig_shadow.sh          # pause all schedulers (offseason / emergency)
#   Run once now:  gcloud run jobs execute novig-shadow-nfl --region=us-central1 --project=$PROJECT_ID --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="novig-shadow-nfl"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-1500}"
TASK_TIMEOUT="${TASK_TIMEOUT:-1800}"

SCHEDULES=(
  "sun-am|6,36 6-11 * * 0"
  "sun-rest|6 12-20 * * 0"
  "thu|6 12-18/2 * * 4"
  "mon|6 12-18/2 * * 1"
  "fri-sat|6 10-20/2 * * 5,6"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for row in "${SCHEDULES[@]}"; do
    gcloud scheduler jobs pause "${JOB}-${row%%|*}" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "Novig shadow schedulers paused."; exit 0
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
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python" --args="-m,ev.novig.shadow_job,--max-runtime-sec,${MAX_RUNTIME_SEC}"
  --memory=1Gi --cpu=1 --task-timeout="${TASK_TIMEOUT}" --max-retries=0 --parallelism=1 --tasks=1 --quiet
)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "Job $JOB exists -- updating..."; gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  echo "Job $JOB not found -- creating..."; gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7); see
# docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
for row in "${SCHEDULES[@]}"; do
  IFS='|' read -r TAG CRON <<<"$row"
  SJOB="${JOB}-${TAG}"
  SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
          --attempt-deadline=320s --project="$PROJECT_ID")
  if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
  fi
  echo "scheduler ${SJOB} @ '${CRON}' America/Chicago -> ${JOB}"
done

echo ""
echo "=== Done: job $JOB (${IMAGE}) + ${#SCHEDULES[@]} schedulers ==="
echo "Run once now: gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "Logs:         gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Bank:         gcloud storage ls -r gs://<bucket>/Novig/shadow_ledger/"
echo "Gate:         python -m ev.novig.clv_gate compute --bank --gcs-raw   (reads the banked ledger; status line is in every job's log)"
echo "Not enabled:  Novig paper-sandbox mirror (needs the paper trading key), ParlayAPI consensus refresh, any live path."
