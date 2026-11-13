#!/usr/bin/env bash
# setup_nhl_lines_jobs.sh -- provision the NHL shift-chart banking and even-strength lines Cloud Run Jobs (+ schedulers).
#
#   nhl-shiftcharts-bank   python -m nhl.data.shiftcharts_bank   banks raw NHL shift charts for the trailing-10-games-per-team
#                                                                window to NHL/raw/shiftcharts/ (idempotent: already-banked
#                                                                games are skipped; games the NHL API has no chart for are
#                                                                retried nightly)
#   nhl-lines-build        python -m nhl.data.lines              derives even-strength lines from the banked charts and
#                                                                publishes NHL/lines/lines_latest.json (+ dated copy)
#
# Reuses the mlb-betting service account and MLB_GCS_BUCKET secret (no DB, no API keys: the NHL web API
# is unauthenticated). Idempotent: create-or-update for every resource.
#
# ORDERING CONSTRAINT:
#   The pipeline has a strict 3-stage dependency chain scheduled in Etc/UTC:
#   1. nhl-refresh-data at 12:30 UTC: fetches trailing box scores and updates the games master (~12:30 UTC).
#   2. nhl-shiftcharts-bank at 13:00 UTC ("0 13 * * *"): runs 30 minutes after the refresh so yesterday's
#      completed games are already present in the games master when determining trailing-10 games per team.
#      It pulls raw shift charts from api.nhle.com and saves them to NHL/raw/shiftcharts/<game_id>.json.gz.
#   3. nhl-lines-build at 13:30 UTC ("30 13 * * *"): runs 30 minutes after the bank job so all raw shift
#      charts for the trailing 10-game window are banked on GCS before 5v5 pairwise TOI derivation begins.
#      It outputs NHL/lines/lines_latest.json and the dated copy.
#
# MODES:
#   MODE=jobs       create/update both Jobs only (default)
#   MODE=scheduler  create/update both Schedulers only
#   MODE=pause      pause BOTH Schedulers (e.g. before manual runs or backfills)
#   MODE=all        jobs + schedulers
#
# IMAGE:
#   IMAGE defaults to gcr.io/${PROJECT_ID}/mlb-betting-feeds.
#   Like deploy/setup_alt_ladder_sink.sh, a pre-merge provisioning pins the branch-tagged image
#   (for example IMAGE=gcr.io/<project>/mlb-betting-feeds:sgp-lines-20261112).
#   A Cloud Run Job stores the tag and resolves it to a digest at each execution, so the job must NOT
#   be repointed at the untagged image until the code has merged to main and a main roll has run.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 MODE=jobs ./deploy/setup_nhl_lines_jobs.sh
#   PROJECT_ID=concrete-crow-445205-m4 MODE=scheduler ./deploy/setup_nhl_lines_jobs.sh
#   PROJECT_ID=concrete-crow-445205-m4 MODE=pause ./deploy/setup_nhl_lines_jobs.sh
#   PROJECT_ID=concrete-crow-445205-m4 IMAGE=gcr.io/concrete-crow-445205-m4/mlb-betting-feeds:sgp-lines-20261112 MODE=all ./deploy/setup_nhl_lines_jobs.sh
#
# Manual test runs:
#   gcloud run jobs execute nhl-shiftcharts-bank --region=us-central1 --project=concrete-crow-445205-m4 --wait
#   gcloud run jobs execute nhl-lines-build --region=us-central1 --project=concrete-crow-445205-m4 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
MODE="${MODE:-jobs}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
BANK_JOB="nhl-shiftcharts-bank"
LINES_JOB="nhl-lines-build"
# Pre-merge provisioning pins the branch-tagged image (e.g. IMAGE=gcr.io/<project>/mlb-betting-feeds:sgp-lines-20261112).
# Cloud Run Jobs resolve the tag to a digest at each execution, so do NOT repoint at the untagged image until
# merged to main and a main roll has run.
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
BANK_SCHEDULE="0 13 * * *"
LINES_SCHEDULE="30 13 * * *"
RUN_URI() { echo "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/$1:run"; }

case "$MODE" in jobs|scheduler|pause|all) ;; *) echo "MODE must be jobs|scheduler|pause|all"; exit 1;; esac

if [ "$MODE" = "pause" ]; then
  echo "Pausing schedulers for $BANK_JOB and $LINES_JOB..."
  gcloud scheduler jobs pause "$BANK_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  gcloud scheduler jobs pause "$LINES_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Both schedulers ($BANK_JOB, $LINES_JOB) paused."; exit 0
fi

COMMON_FLAGS=(
  --image="$IMAGE"
  --region="$REGION"
  --project="$PROJECT_ID"
  --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="python"
  --quiet
)

upsert_job() {   # name, args, memory, cpu, task-timeout, max-retries
  local name="$1" args="$2" mem="$3" cpu="$4" timeout="$5" retries="$6"
  local flags=("${COMMON_FLAGS[@]}" --args="$args" --memory="$mem" --cpu="$cpu"
               --task-timeout="$timeout" --max-retries="$retries")
  if gcloud run jobs describe "$name" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Job $name exists -- updating..."; gcloud run jobs update "$name" "${flags[@]}"
  else
    echo "Job $name not found -- creating..."; gcloud run jobs create "$name" "${flags[@]}"
  fi
}

upsert_scheduler() {   # name, schedule
  local name="$1" sched="$2"
  # scheduler-invoker must hold run.invoker ON THE JOB, or every scheduler firing is PERMISSION_DENIED (status code 7)
  # and the job silently never runs. Missed on 11 jobs provisioned 2026-10-14 (docs/solutions/runtime-errors/
  # scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
  gcloud run jobs add-iam-policy-binding "$name" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

  local sched_flags=(
    --location="$REGION" --project="$PROJECT_ID"
    --schedule="$sched" --time-zone="Etc/UTC"
    --uri="$(RUN_URI "$name")" --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=320s
  )
  if gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Scheduler $name exists -- updating..."
    gcloud scheduler jobs update http "$name" "${sched_flags[@]}"
    gcloud scheduler jobs resume "$name" --location="$REGION" --project="$PROJECT_ID" --quiet || true
  else
    echo "Scheduler $name not found -- creating..."
    gcloud scheduler jobs create http "$name" "${sched_flags[@]}"
  fi
}

echo "=== NHL lines jobs setup (MODE=$MODE) ==="
echo "Project: $PROJECT_ID   Image: $IMAGE   SA: $SA_EMAIL"

if [ "$MODE" = "jobs" ] || [ "$MODE" = "all" ]; then
  gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
    || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
  gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
    || { echo "ERROR: $IMAGE not found."; exit 1; }

  # nhl-shiftcharts-bank: banks raw NHL shift charts for the trailing-10-games-per-team window to NHL/raw/shiftcharts/
  # (idempotent: already-banked games are skipped; games the NHL API has no chart for are retried nightly).
  # 1 CPU, 1Gi, task timeout 2700, max-retries 0.
  upsert_job "$BANK_JOB" "-m,nhl.data.shiftcharts_bank" 1Gi 1 2700 0

  # nhl-lines-build: derives even-strength lines from the banked charts and publishes NHL/lines/lines_latest.json (+ dated copy).
  # 2 CPU, 2Gi, task timeout 1800, max-retries 0.
  upsert_job "$LINES_JOB" "-m,nhl.data.lines" 2Gi 2 1800 0
fi

if [ "$MODE" = "scheduler" ] || [ "$MODE" = "all" ]; then
  # Schedulers (Etc/UTC, matching the nhl-refresh-data convention - it lands yesterday's games ~12:30 UTC):
  # - nhl-shiftcharts-bank at "0 13 * * *" (after the refresh, so last night's games are in the master)
  # - nhl-lines-build at "30 13 * * *" (after the bank)
  upsert_scheduler "$BANK_JOB" "$BANK_SCHEDULE"
  upsert_scheduler "$LINES_JOB" "$LINES_SCHEDULE"
fi

echo ""
echo "Done. Next:"
echo "  bank charts : gcloud run jobs execute $BANK_JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "  build lines : gcloud run jobs execute $LINES_JOB --region=$REGION --project=$PROJECT_ID --wait"
echo "  schedule    : PROJECT_ID=$PROJECT_ID MODE=scheduler ./deploy/setup_nhl_lines_jobs.sh"
echo "  verify out  : gcloud storage cat gs://\$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)/NHL/lines/lines_latest.json"
