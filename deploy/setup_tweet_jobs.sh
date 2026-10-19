#!/usr/bin/env bash
# setup_tweet_jobs.sh -- provision the three tweet_drafter.py Cloud Run Jobs.
#
# mlb-tweet-picks (TWEET_MODE=picks, 17:00 UTC), mlb-tweet-recap
# (TWEET_MODE=recap, 10:00 UTC) and mlb-tweet-reply-ammo (TWEET_MODE=reply_ammo,
# 22:45 UTC) run the SAME script (tweet_drafter.py, COPY'd into the image root
# by Dockerfile) with different MODE env vars. All three post to Discord
# #tweet-queue only (Typefully was removed 2026-10-19).
#
# reply_ammo reads the NHL/NBA sweep snapshots, which the props-alerts jobs
# refresh at 17:35 and 22:35 UTC, and the API drops a snapshot older than 120
# min -- so it runs 10 min after the 22:35 sweep (~6:45pm ET). A 21:15 UTC run
# would see a 3.5h-old snapshot and silently lose NHL/NBA. If the odds-bank /
# props-alerts schedulers are PAUSED, only NFL + MLB rows can appear.
#
# This script did not exist before 2026-09-02 -- both jobs were hand-created,
# and RUNBOOKS.md's own "one-shot Cloud Shell update" snippet used
# `--set-env-vars` (full replace) for what was meant to be a two-var partial
# patch, which silently dropped `TWEET_MODE` off mlb-tweet-recap at some
# point -- it defaulted back to "picks" and ran the wrong code path for
# 8+ days before anyone noticed (see
# docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md).
# Re-run THIS script (idempotent) instead of hand-crafting `gcloud run jobs
# update` calls for these two jobs going forward.
#
# Prereq: image built with the current tweet_drafter.py (./deploy/deploy_service.sh).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_tweet_jobs.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== Tweet drafter job setup ==="
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Run ./deploy/deploy_service.sh first."; exit 1; }

_upsert_job() {  # job_name  tweet_mode
  local job_name="$1" mode="$2"
  local job_flags=(
    --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
    --set-secrets="SITE_API_KEY=site-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,DISCORD_WEBHOOK_OPS=discord-ops-webhook-url:latest"
    --set-env-vars="BEEZY_API_URL=https://api.beezy.fyi,BEEZY_SITE_URL=https://beezy.fyi,TWEET_MODE=${mode}"
    --command="python3" --args="tweet_drafter.py"
    --memory=512Mi --cpu=1 --task-timeout=300 --max-retries=3 --quiet
  )
  if gcloud run jobs describe "$job_name" --region="$REGION" --quiet >/dev/null 2>&1; then
    echo "Job $job_name exists -- updating (TWEET_MODE=$mode)..."
    gcloud run jobs update "$job_name" "${job_flags[@]}"
  else
    echo "Job $job_name not found -- creating (TWEET_MODE=$mode)..."
    gcloud run jobs create "$job_name" "${job_flags[@]}"
  fi
  # scheduler-invoker must hold run.invoker ON THE JOB or every scheduler
  # firing is PERMISSION_DENIED (code 7) -- see docs/solutions/runtime-errors/
  # scheduler-permission-denied-on-new-jobs.md. Idempotent -- safe on update too.
  gcloud run jobs add-iam-policy-binding "$job_name" --region="$REGION" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
}

_upsert_job "mlb-tweet-picks" "picks"
_upsert_job "mlb-tweet-recap" "recap"
_upsert_job "mlb-tweet-reply-ammo" "reply_ammo"

_upsert_sched() {  # sched_name  cron  job_name  description
  local name="$1" cron="$2" job_name="$3" desc="$4" action=create
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job_name}:run"
  gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" \
    >/dev/null 2>&1 && action=update
  echo "${action} $name ($cron) -> $job_name"
  gcloud scheduler jobs "$action" http "$name" \
    --location="$REGION" --schedule="$cron" --time-zone="Etc/UTC" \
    --uri="$uri" --http-method=POST \
    --oauth-service-account-email="$SCHED_SA" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --attempt-deadline=320s --project="$PROJECT_ID" --quiet \
    ${desc:+--description="$desc"}
}

_upsert_sched "mlb-tweet-picks-schedule" "0 17 * * *" "mlb-tweet-picks" "Games card + picks tweet draft"
_upsert_sched "mlb-tweet-recap-schedule" "0 10 * * *" "mlb-tweet-recap" "Recap tweet draft after overnight settle"
_upsert_sched "mlb-tweet-reply-ammo-schedule" "45 22 * * *" "mlb-tweet-reply-ammo" "Link-free reply snippets, 10 min after the 22:35 props sweep"

echo ""
echo "=== Done: 3 jobs, 3 schedules ==="
echo "Verify env vars stuck:"
echo "  gcloud run jobs describe mlb-tweet-recap --region=$REGION --format='yaml(spec.template.spec.template.spec.containers[0].env)'"
echo "Run once now:"
echo "  gcloud run jobs execute mlb-tweet-recap --region=$REGION --project=$PROJECT_ID --wait"
