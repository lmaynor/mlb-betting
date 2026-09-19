#!/usr/bin/env bash
# setup_fast_alert.sh -- provision the fast +EV alert loop (the pager).
#
# Cloud Run Job runs mlb.runners.fast_alert_loop every 15 minutes inside the
# 19:00-23:45 UTC strike window: lineup-event detection -> free BettingPros
# snapshot -> Pinnacle-anchored outlier scan -> Discord alert on NEW +EV
# quotes only (per-day dedup, capped per run). Off-window nothing runs.
#
# Free data source, so 20 runs/day costs only the job compute (~1 min each on
# 1Gi). Also logs every posted alert into the bets table (system="EV") for
# profitability tracking (mlb_core.tracking.BetTracker), so -- like
# mlb-fit-calibrators -- this job needs Cloud SQL access (--set-cloudsql-
# instances + MLB_DB_URL secret) in addition to the GCS bucket secret. Fixed
# 2026-09-18: this job (and mlb-kalshi-alert) never had that wiring, so
# BetTracker's DB_URL-empty fallback (mlb_core/tracking/bet_tracker.py's
# _make_engine) silently wrote every "logged" EV bet to an ephemeral sqlite
# file inside the container instead of Postgres -- gone the moment the job
# exited, since the feature launched on 2026-08-20. See
# docs/solutions/runtime-errors/ev-alert-jobs-missing-db-wiring.md.
#
# Prereq: image rebuilt with mlb/runners/fast_alert_loop.py
# (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_fast_alert.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-fast-alert"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"

# every 15 min, 19:00-23:45 UTC (2-7pm ET: lineups post -> closing window)
SCHEDULE="*/15 19-23 * * *"
# opener watch: every 2h overnight/morning -- tomorrow's lines post overnight
# and are the softest of the day; scan them before the market wakes up
SCHEDULE_NIGHT="0 0-16/2 * * *"

echo "=== Fast alert loop setup ==="
echo "Job: $JOB_NAME  schedule='$SCHEDULE' UTC (20 runs/day in strike window)"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

# BP_MARKETS commas -> ^@^ alternate delimiter
ENVV="^@^GCP_PROJECT=${PROJECT_ID}@GCP_REGION=${REGION}@BP_MARKETS=player@BP_DAYS=2@FAL_DAYS=2@FAL_MIN_EV=0.03@FAL_MIN_BOOKS=4@FAL_MAX_POSTS=10"
JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  # DISCORD_WEBHOOK_ALERTS would route to a dedicated #soft-line-alerts
  # channel once that secret exists -- it does NOT yet (confirmed 2026-09-18:
  # `gcloud secrets describe discord-webhook-alerts` -> NOT_FOUND), so it is
  # deliberately left off --set-secrets below. Referencing a nonexistent
  # secret here fails the ENTIRE `gcloud run jobs update` call (silently
  # blocking every other flag in this same invocation, including the
  # MLB_DB_URL fix below) -- discovered 2026-09-18 trying to apply that fix.
  # _alert_webhook() in-code already falls back to DISCORD_WEBHOOK_URL
  # (#daily-picks) when DISCORD_WEBHOOK_ALERTS is unset, so this is a no-op
  # behavior change. Add it back here (and to setup_kalshi_alert_job.sh)
  # once the secret is actually created.
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest,MLB_DB_URL=mlb-db-url:latest"
  --set-cloudsql-instances="$INSTANCE"
  --set-env-vars="$ENVV"
  --command="python3" --args="-m,mlb.runners.fast_alert_loop"
  --memory=1Gi --cpu=1 --task-timeout=840 --max-retries=0 --quiet
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
sjob="${JOB_NAME}-loop"
flags=(
  --location="$REGION" --schedule="$SCHEDULE" --time-zone="Etc/UTC"
  --uri="$URI" --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=900s --project="$PROJECT_ID"
)
if gcloud scheduler jobs describe "$sjob" --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$sjob" "${flags[@]}"
else
  gcloud scheduler jobs create http "$sjob" "${flags[@]}"
fi
echo "Scheduler set: $sjob @ '$SCHEDULE' UTC"

njob="${JOB_NAME}-night"
nflags=(
  --location="$REGION" --schedule="$SCHEDULE_NIGHT" --time-zone="Etc/UTC"
  --uri="$URI" --http-method=POST
  --oauth-service-account-email="$SCHED_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=900s --project="$PROJECT_ID"
)
if gcloud scheduler jobs describe "$njob" --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$njob" "${nflags[@]}"
else
  gcloud scheduler jobs create http "$njob" "${nflags[@]}"
fi
echo "Scheduler set: $njob @ '$SCHEDULE_NIGHT' UTC (overnight opener watch)"

echo ""
echo "Run once now:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --wait"
echo "Pause off-season / to stop pings:"
echo "  gcloud scheduler jobs pause $sjob --location=$REGION"
