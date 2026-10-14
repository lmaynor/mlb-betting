#!/usr/bin/env bash
# setup_nhl_jobs.sh -- provision the NHL data-feed Cloud Run Jobs (+ the nightly Scheduler).
#
#   nhl-refresh-data   python -m nhl.data.refresh    nightly: re-fetch the trailing 2 days of box scores +
#                                                    play-by-play, then the roster/team crosswalks
#   nhl-backfill       python -m nhl.data.backfill   one-off multi-season pull (resume-safe via the raw cache)
#
# Reuses the mlb-betting service account and MLB_GCS_BUCKET secret (no DB, no API keys: the NHL web API
# is unauthenticated). Idempotent: create-or-update for every resource.
#
# WHY three modes: the NHL API rate-limits per IP, so a backfill and the nightly refresh (or any roster pull) must
# never run at the same time -- an overlap produced 429s on 4 rosters in testing. Provision the jobs, run the
# backfill to completion, THEN enable the schedule.
#
#   MODE=jobs       create/update both Jobs only (default)
#   MODE=scheduler  create/update the nightly Scheduler only (do this after the backfill finished)
#   MODE=pause      pause the nightly Scheduler (e.g. before a re-backfill)
#   MODE=all        jobs + scheduler
#
# NOT named odds-bank-*: that prefix is reserved for credit-spending ParlayAPI jobs so
# `gcloud scheduler jobs list | grep odds-bank` stays the one place to look. NHL jobs spend no credits.
#
# Prereq: the feeds image (see IMAGE below) must exist and contain nhl/ -- built from a CLEAN checkout of main.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 MODE=jobs ./deploy/setup_nhl_jobs.sh
#   gcloud run jobs execute nhl-backfill --region=us-central1 --project=concrete-crow-445205-m4 --wait
#   PROJECT_ID=concrete-crow-445205-m4 MODE=scheduler ./deploy/setup_nhl_jobs.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
MODE="${MODE:-jobs}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
REFRESH_JOB="nhl-refresh-data"
BACKFILL_JOB="nhl-backfill"
# SEPARATE image repo, not mlb-betting:latest. A Cloud Run Job stores the tag and resolves it to a digest at each
# EXECUTION (verified 2026-10-14: mlb-build-all-features lists :latest but its executions run @sha256:...), so pushing
# a new mlb-betting:latest silently changes what every existing scheduled job runs next. Build this image with
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds .
# from a CLEAN checkout of main (same precedent as the mlb-betting-ev:shadow image).
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
# 12:30 UTC daily: west-coast games end ~05:00 UTC, so 7.5h of buffer; offset from nba-refresh-data (13:00).
SCHEDULE="30 12 * * *"
BACKFILL_SEASONS="${BACKFILL_SEASONS:-2019-2026}"
RUN_URI() { echo "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/$1:run"; }

case "$MODE" in jobs|scheduler|pause|all) ;; *) echo "MODE must be jobs|scheduler|pause|all"; exit 1;; esac

if [ "$MODE" = "pause" ]; then
  gcloud scheduler jobs pause "$REFRESH_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "$REFRESH_JOB paused."; exit 0
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

echo "=== NHL jobs setup (MODE=$MODE) ==="
echo "Project: $PROJECT_ID   Image: $IMAGE   SA: $SA_EMAIL"

if [ "$MODE" = "jobs" ] || [ "$MODE" = "all" ]; then
  gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null \
    || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
  gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
    || { echo "ERROR: $IMAGE not found."; exit 1; }

  # Nightly refresh: ~30 games x 2 calls + 33 roster calls at 0.25s, plus 429 back-off headroom.
  # max-retries=1: the refresh is idempotent (upsert), so a retry is safe and a partial run exits non-zero.
  upsert_job "$REFRESH_JOB" "-m,nhl.data.refresh" 2Gi 2 1800 1

  # Backfill: ~3,000 API calls/season at 0.25s ~ 15-30 min/season; 8 seasons -> hours. 8Gi covers one season's
  # rows in memory (masters are flushed per season). Resume-safe (raw cache), so one retry is a cheap no-op for
  # whatever finished. 6h task timeout.
  upsert_job "$BACKFILL_JOB" "-m,nhl.data.backfill,--seasons,${BACKFILL_SEASONS}" 8Gi 4 21600 1
fi

if [ "$MODE" = "scheduler" ] || [ "$MODE" = "all" ]; then
  # OAuth + Run API v2 trigger -- NOT OIDC (CONTEXT s9).
  SCHED_FLAGS=(
    --location="$REGION" --project="$PROJECT_ID"
    --schedule="$SCHEDULE" --time-zone="Etc/UTC"
    --uri="$(RUN_URI "$REFRESH_JOB")" --http-method=POST
    --oauth-service-account-email="$SCHED_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=320s
  )
  # scheduler-invoker must hold run.invoker ON THE JOB, or every scheduler firing is PERMISSION_DENIED (status code 7)
  # and the job silently never runs. Missed on 11 jobs provisioned 2026-10-14 (docs/solutions/runtime-errors/
  # scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
  gcloud run jobs add-iam-policy-binding "$REFRESH_JOB" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
  if gcloud scheduler jobs describe "$REFRESH_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    echo "Scheduler $REFRESH_JOB exists -- updating..."
    gcloud scheduler jobs update http "$REFRESH_JOB" "${SCHED_FLAGS[@]}"
    gcloud scheduler jobs resume "$REFRESH_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet || true
  else
    echo "Scheduler $REFRESH_JOB not found -- creating..."
    gcloud scheduler jobs create http "$REFRESH_JOB" "${SCHED_FLAGS[@]}"
  fi
fi

echo ""
echo "Done. Next:"
echo "  backfill : gcloud run jobs execute $BACKFILL_JOB --region=$REGION --project=$PROJECT_ID --async"
echo "  schedule : PROJECT_ID=$PROJECT_ID MODE=scheduler ./deploy/setup_nhl_jobs.sh   (after the backfill finishes)"
echo "  one-off  : gcloud run jobs execute $REFRESH_JOB --region=$REGION --project=$PROJECT_ID --wait"
