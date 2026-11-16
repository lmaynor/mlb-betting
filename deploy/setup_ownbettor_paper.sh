#!/usr/bin/env bash
# setup_ownbettor_paper.sh -- provision the AUT-10 paper operation of the AUT-09
# own-account bettor (ev/ownbettor/paper_job.py).
#
#   job  ownbettor-paper   python -m ev.ownbettor.paper_job
#
# WHAT IT DOES (ev/ownbettor/paper_job.py): one firing restores the bettor's audit
# dir from gs://<bucket>/OwnBettor/audit/, books settlements for decided paper
# fills from Kalshi's PUBLIC market results, runs every new actionable (FILLED)
# signal from the AUT-01 shadow ledger (Autopilot/shadow_ledger/latest.jsonl.gz,
# READ-ONLY) through the unchanged AUT-09 pipeline (Kelly sizing -> rule gate ->
# risk gate -> verified-table resolution -> paper fill against Kalshi's live
# public book), then banks the audit dir back plus a create-only run summary
# under OwnBettor/runs/.
#
# PAPER ONLY: OWNBETTOR_LIVE_ARMED is set NOWHERE in this job.  The paper job
# refuses to run (exit 2) if a live-armed config is ever loaded, and the live
# adapter's four locks stay exactly as AUT-09 built them.  This operation arms,
# unlocks, and weakens none of them.
#
# ARMING: OWNBETTOR_PAPER_ARMED=1 is set ONLY in this job's environment -- that
# env var is the operation's kill switch (paper_job exits 3 without it).
# Removing it (or pausing the scheduler) disarms the paper operation.
#
# CREDENTIALS: exactly one secret, MLB_GCS_BUCKET (secret mlb-gcs-bucket, used by
# every feeds job).  GCS access is the job's service account (mlb-betting-sa).
# Kalshi access is the UNSIGNED public data host only -- no Kalshi credentials
# exist in this job.  The script fails loudly if mlb-gcs-bucket is missing
# instead of creating or guessing a secret.
#
# TIMING: every 30 minutes at :17/:47 America/Chicago (offset from the :05/:35
# executor passes and the :07/:37 RES-11 shadow).  The signal ledger it consumes
# is re-derived by the AUT-01 shadow job (daily 06:30 CT), so most firings find
# no new signals; a firing costs seconds then.  WINDOW: stood up Nov 16, 2026 at
# Luc's direction; the AUT-01 signal window ends Nov 30, 2026 and is scored
# Dec 1, 2026.  REVIEW/DISABLE: Dec 2, 2026 (decide the operation's next form
# alongside the Dec 1 scoring and the AUT-09 switch-on question).
#
# IMAGE: build a TAG from this branch first (the runner code is not in :latest):
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds,_TAG=ownbettor-paper-20261116 --project=$PROJECT_ID .
#   IMAGE=gcr.io/$PROJECT_ID/mlb-betting-feeds:ownbettor-paper-20261116 PROJECT_ID=... bash deploy/setup_ownbettor_paper.sh
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_ownbettor_paper.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_ownbettor_paper.sh          # pause the scheduler (disarm)
#   Fire via the scheduler now:  gcloud scheduler jobs run ownbettor-paper-30m --location=us-central1 --project=$PROJECT_ID
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="mlb-betting"
JOB="ownbettor-paper"
SJOB="${JOB}-30m"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${SCHEDULE:-17,47 * * * *}"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Ownbettor paper scheduler paused (paper operation disarmed at the scheduler; the job env still carries the arm flag)."
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
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},OWNBETTOR_PAPER_ARMED=1,OWNBETTOR_AUDIT_DIR=/tmp/ownbettor/audit"
  --command="python" --args="-m,ev.ownbettor.paper_job"
  --memory=2Gi --cpu=1 --task-timeout=900 --max-retries=0 --parallelism=1 --tasks=1 --quiet
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
        --description="AUT-10 paper operation of the AUT-09 own-account bettor (dry-run only; live locks untouched; consumes the AUT-01 shadow ledger read-only). Stood up Nov 16, 2026. Review/disable Dec 2, 2026, after the Dec 1 AUT-01 scoring."
        --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '${SCHEDULE}' America/Chicago -> ${JOB}"

echo ""
echo "=== Done: job $JOB (${IMAGE}) + scheduler $SJOB ==="
echo "Fire via scheduler: gcloud scheduler jobs run $SJOB --location=$REGION --project=$PROJECT_ID"
echo "Logs:               gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"' --project=$PROJECT_ID --limit=40 --freshness=2h"
echo "Bank:               gcloud storage ls gs://<bucket>/OwnBettor/"
echo "Not enabled:        OWNBETTOR_LIVE_ARMED (set nowhere), any Kalshi credential, AUT-01 / PRD-04 / RES-11 contact (none)."
