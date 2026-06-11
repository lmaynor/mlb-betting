#!/usr/bin/env bash
# setup_spike_runsim.sh -- provision throwaway Cloud Run Jobs for the
# first-inning run-distribution VALIDATION SPIKE.
#
# Safety (scope s3 -- production must be untouched):
#   - Builds a SEPARATE image tag (mlb-betting-spike-runsim), NEVER the
#     production gcr.io/PROJECT/mlb-betting image.
#   - Does NOT deploy a service revision (no deploy_service.sh).
#   - Jobs write ONLY to NRFI_Pro_System/experimental/runsim_v1/ in GCS.
#   - Reads production model_features.csv + scoring_master.csv READ-ONLY.
#   - Tear down with: gcloud run jobs delete mlb-spike-runsim-train mlb-spike-runsim-eval
#
# Run from the spike branch (must contain training/spike_runsim_nrfi_*.py):
#   git checkout spike/first-inning-runsim
#   chmod +x deploy/setup_spike_runsim.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_spike_runsim.sh
#
# Then execute (train first, then eval):
#   gcloud run jobs execute mlb-spike-runsim-train --region=us-central1 --wait
#   gcloud run jobs execute mlb-spike-runsim-eval  --region=us-central1 --wait
#   gcloud run jobs executions logs read <EXEC> --region=us-central1   # read PASS/FAIL
#   gsutil cat gs://${BUCKET}/NRFI_Pro_System/experimental/runsim_v1/eval_report.json
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}-spike-runsim"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== First-inning run-sim SPIKE job setup ==="
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE  (separate from production -- safe)"
echo "SA      : $SA_EMAIL"
echo ""

echo "Verifying spike code is present on this checkout..."
test -f training/spike_runsim_nrfi_v1.py   || { echo "ERROR: spike train script missing -- checkout spike branch"; exit 1; }
test -f training/spike_runsim_nrfi_eval.py || { echo "ERROR: spike eval script missing -- checkout spike branch"; exit 1; }

echo "Building spike image (does NOT touch production image)..."
gcloud builds submit --tag "$IMAGE" --project="$PROJECT_ID" .

create_or_update_job () {
  local job="$1" module="$2" timeout="$3"
  if gcloud run jobs describe "$job" --region="$REGION" --quiet >/dev/null 2>&1; then
    echo "Updating job $job..."
    gcloud run jobs update "$job" \
      --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" \
      --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
      --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
      --command="python" --args="-m,${module}" \
      --memory=4Gi --cpu=2 --task-timeout="$timeout" --max-retries=0 --quiet
  else
    echo "Creating job $job..."
    gcloud run jobs create "$job" \
      --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" \
      --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" \
      --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
      --command="python" --args="-m,${module}" \
      --memory=4Gi --cpu=2 --task-timeout="$timeout" --max-retries=0 --quiet
  fi
}

create_or_update_job "mlb-spike-runsim-train" "training.spike_runsim_nrfi_v1"   1800
create_or_update_job "mlb-spike-runsim-eval"  "training.spike_runsim_nrfi_eval" 1800

echo ""
echo "=== Spike jobs ready ==="
echo "1) Train:  gcloud run jobs execute mlb-spike-runsim-train --region=$REGION --wait"
echo "2) Eval:   gcloud run jobs execute mlb-spike-runsim-eval  --region=$REGION --wait"
echo "3) Report: gsutil cat gs://\${BUCKET}/NRFI_Pro_System/experimental/runsim_v1/eval_report.json"
echo ""
echo "Tear down when done (leaves NO production residue):"
echo "  gcloud run jobs delete mlb-spike-runsim-train mlb-spike-runsim-eval --region=$REGION --quiet"
echo "  gcloud container images delete $IMAGE --quiet   # optional"
