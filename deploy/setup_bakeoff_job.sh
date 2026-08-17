#!/usr/bin/env bash
# setup_bakeoff_job.sh -- one-off Cloud Run Job for the model bake-off.
#
# Built because Cloud Shell's own VM kept getting reclaimed mid-run (3x) -- tmux
# only protects against a CLIENT disconnect, not the underlying VM disappearing,
# which takes /tmp and the tmux server with it. A Cloud Run Job runs entirely
# server-side: closing every browser tab does nothing to it.
#
# NOT a recurring schedule (unlike setup_weekly_survival.sh, which this is modeled
# on) -- this is triggered manually, once (or a few times, if --max-retries isn't
# enough), for this specific analysis exercise. GCS only (no DB). Reuses the
# mlb-betting image + SA.
#
# optuna is installed at container start (`pip install`), not baked into the
# image via requirements.txt -- this is still an exploratory, one-off analysis
# job; adding a new prod dependency and rebuilding+redeploying the LIVE SERVICE
# for it is a bigger, riskier change than a ~15s pip install per execution.
#
# IMPORTANT: building a fresh image (below) does NOT touch the live Cloud Run
# SERVICE -- `gcloud builds submit` only refreshes the `:latest` tag; the running
# service stays pinned to whatever digest it was last explicitly deployed with
# (`gcloud run services update`), which this script never calls.
#
# Prereq: a `:latest` image containing the bake-off code (bakeoff_tuning.py,
# bakeoff_persist.py, --resume/--notify flags, etc.) -- build it first:
#   gcloud builds submit --tag gcr.io/concrete-crow-445205-m4/mlb-betting:latest
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 MODEL_RUN=2026-06-01_4abef3d_221339 \
#     CUTOFF=2026-06-01 bash ./deploy/setup_bakeoff_job.sh
#   gcloud run jobs execute mlb-bakeoff --region=us-central1
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB_NAME="mlb-bakeoff"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# The existing partially-completed run to resume (model_bakeoff.py), and the
# cutoff for hr_model_bakeoff.py's fresh run -- override via env vars for a
# different exercise without editing this script.
MODEL_RUN="${MODEL_RUN:-2026-06-01_4abef3d_221339}"
CUTOFF="${CUTOFF:-2026-06-01}"

# Two-phase HR invocation (finding B4.1, fixed 2026-08-17): this job's own
# --max-retries below meant a mid-run retry restarted HR's entire 7-candidate
# tuning exercise from scratch, defeating the whole reason this Cloud Run Job
# exists. HR_RUN_ID is deterministic per EXECUTION -- $CLOUD_RUN_EXECUTION is a
# Cloud Run Jobs builtin env var, stable across this execution's own automatic
# retry attempts but different across separate `gcloud run jobs execute`
# invocations. It's deliberately inside single quotes below (unexpanded by
# THIS script) so it's resolved by the CONTAINER's bash at run time, not by
# this deploy script's shell at provisioning time -- $CUTOFF, by contrast, IS
# expanded now (same as always) since it only needs to be stable, not
# execution-scoped. hr_model_bakeoff.py's new --resume + --create-if-missing
# (added for this finding): the first attempt finds nothing persisted at that
# id yet and starts fresh using it; the automatic retry computes the SAME id
# and actually resumes.
BAKEOFF_CMD='pip install optuna --break-system-packages -q'
BAKEOFF_CMD+=' && (PYTHONPATH=. python3 -m mlb.analysis.model_bakeoff --resume "'"${MODEL_RUN}"'" --notify)'
BAKEOFF_CMD+=' && HR_RUN_ID="'"${CUTOFF}"'_${CLOUD_RUN_EXECUTION:-manual}"'
BAKEOFF_CMD+=' && (PYTHONPATH=. python3 -m mlb.analysis.hr_model_bakeoff --cutoff "'"${CUTOFF}"'"'
BAKEOFF_CMD+=' --tune --tune-trials 30 --tune-folds 3 --min-books 4 --max-spread 0.10'
BAKEOFF_CMD+=' --resume "$HR_RUN_ID" --create-if-missing --notify)'

echo "=== Bake-off Cloud Run Job setup ==="
echo "Job: $JOB_NAME (one-off, no schedule)  MODEL_RUN=$MODEL_RUN  CUTOFF=$CUTOFF"

gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: $IMAGE not found. Build it first:"; \
       echo "  gcloud builds submit --tag $IMAGE"; exit 1; }

JOB_FLAGS=(
  --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
  --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_OPS=discord-ops-webhook-url:latest"
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
  --command="bash" --args="-c" --args="$BAKEOFF_CMD"
  --memory=8Gi --cpu=4 --task-timeout=21600 --max-retries=1 --quiet
)
if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --quiet >/dev/null 2>&1; then
  echo "Job exists -- updating..."; gcloud run jobs update "$JOB_NAME" "${JOB_FLAGS[@]}"
else
  echo "Job not found -- creating..."; gcloud run jobs create "$JOB_NAME" "${JOB_FLAGS[@]}"
fi

echo ""
echo "Trigger it (returns immediately; runs detached from this shell entirely --"
echo "safe to close every Cloud Shell tab the moment this returns):"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION"
echo ""
echo "Check status any time, from anywhere:"
echo "  gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --limit=5"
echo "  gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME' \\"
echo "    --limit=100 --order=asc --format='value(textPayload)' --project=$PROJECT_ID"
echo ""
echo "Or just wait for the Discord #ops-alerts ping (--notify is wired into both scripts)."
