#!/usr/bin/env bash
# setup_scheduler_dispatcher.sh -- provision the OPS-15 Tranche 2 scheduler
# dispatcher (memo: gcp-deep-efficiency-2026-11-16.md, section B.3).
#
#   job scheduler-dispatcher            python -m ev.dispatcher.run
#   scheduler scheduler-dispatcher-grid    <grid minutes> * * * *  Etc/UTC
#   scheduler scheduler-dispatcher-audit   5 12 * * *  Etc/UTC (runs --audit)
#
# WHAT: 42 Cloud Scheduler definitions that fire at most twice a day exist
# only to POST a Cloud Run job's :run endpoint at a fixed minute, and each
# bills $0.10/mo of definition rent. The dispatcher replaces them: the grid
# scheduler fires the dispatcher job at every minute-of-hour any absorbed
# entry can use (the union is computed into ev/dispatcher/table.json by
# ev.dispatcher.generate_table), the job reads that checked-in table, and
# runs the due jobs through the Run API at exactly their original minutes
# (fire-set equivalence is proven by ev.dispatcher.prove over a 92-day
# window; see the OPS-15 memo addendum). The audit scheduler runs the
# dispatcher in --audit mode daily: yesterday's intended fires are
# reconciled against actual executions and any gap logs AUDIT MISS.
#
# THIS SCRIPT PROVISIONS ONLY. It deletes nothing. Deleting an absorbed
# definition is the cutover, done wave by wave per the memo addendum
# (archive-first, definition-by-definition diff, rollback = recreate from
# the archive). Overlap is safe while both mechanisms are live: before
# firing, the dispatcher skips any job that already has an execution
# created in the current minute.
#
# IAM: the dispatcher job runs as mlb-betting-sa, which needs
# run.invoker ON EACH ABSORBED JOB to call :run (the same per-job binding
# pattern scheduler-invoker uses). scheduler-invoker needs run.invoker on
# the dispatcher job itself. Both are granted below, idempotently; the
# absorbed job list is read from table.json so it cannot drift from the
# table the dispatcher fires from.
#
# IMAGE: gcr.io/<project>/mlb-betting-feeds (deploy/build_all_images.sh
# with SKIP_PROD=1). The image must contain ev/dispatcher/: a job runs
# whatever is in its image.
#
# COST: 2 scheduler definitions ($0.20/mo) + ~264 grid executions/day of a
# ~5s job (~$0.30/mo) replace 42 definitions ($4.20/mo). Net ~$3.70/mo on
# this absorb set; the memo's $5.20 figure assumed the an-daily family in
# the tail, which the GitHub Actions item (B.2) carries instead.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_scheduler_dispatcher.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_scheduler_dispatcher.sh
# Prove the real path after provisioning (a manual 'jobs execute' uses YOUR
# credentials; the schedulers use scheduler-invoker):
#   gcloud scheduler jobs run scheduler-dispatcher-grid --location=us-central1
#   gcloud run jobs executions list --job=scheduler-dispatcher --region=us-central1 --limit=3
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB="scheduler-dispatcher"
GRID="scheduler-dispatcher-grid"
AUDIT="scheduler-dispatcher-audit"
TZ_NAME="Etc/UTC"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TABLE="${REPO_ROOT}/ev/dispatcher/table.json"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$GRID" --location="$REGION" --project="$PROJECT_ID" --quiet
  gcloud scheduler jobs pause "$AUDIT" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "Paused ${GRID} and ${AUDIT} (absorbed definitions, if still present, are unaffected)."
  exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }
[ -f "$TABLE" ] || { echo "ERROR: $TABLE not found (run ev.dispatcher.generate_table first)."; exit 1; }

GRID_MINUTES="$(python3 -c "import json;print(','.join(str(m) for m in json.load(open('$TABLE'))['grid_minutes_utc']))")"
GRID_CRON="${GRID_MINUTES} * * * *"
echo "grid cron: '${GRID_CRON}' ${TZ_NAME}"

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
       --command="python" --args="-m,ev.dispatcher.run"
       --memory=512Mi --cpu=1 --task-timeout=300 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker must hold run.invoker ON THE DISPATCHER JOB, or every
# grid firing is PERMISSION_DENIED (status code 7).
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

# The dispatcher job's own SA must hold run.invoker on every absorbed job.
python3 -c "import json;[print(e['job']) for e in json.load(open('$TABLE'))['entries']]" | sort -u | \
while read -r target_job; do
  gcloud run jobs add-iam-policy-binding "$target_job" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="roles/run.invoker" --quiet >/dev/null
  echo "granted run.invoker on ${target_job} to ${SA_EMAIL}"
done

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --time-zone="$TZ_NAME" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=180s --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$GRID" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$GRID" "${SFLAGS[@]}" --schedule="$GRID_CRON" --quiet
else
  gcloud scheduler jobs create http "$GRID" "${SFLAGS[@]}" --schedule="$GRID_CRON" --quiet
fi
echo "scheduler ${GRID} @ '${GRID_CRON}' ${TZ_NAME} -> ${JOB}"

AUDIT_BODY='{"overrides":{"containerOverrides":[{"args":["--audit"]}]}}'
if gcloud scheduler jobs describe "$AUDIT" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$AUDIT" "${SFLAGS[@]}" --schedule="5 12 * * *" --message-body="$AUDIT_BODY" --quiet
else
  gcloud scheduler jobs create http "$AUDIT" "${SFLAGS[@]}" --schedule="5 12 * * *" --message-body="$AUDIT_BODY" --quiet
fi
echo "scheduler ${AUDIT} @ '5 12 * * *' ${TZ_NAME} -> ${JOB} --audit"

echo
echo "Provisioned. Nothing was deleted: absorbed definitions still exist and"
echo "still fire; the dispatcher's same-minute guard makes the overlap safe."
echo "Cutover (per wave, per the OPS-15 memo addendum) deletes definitions"
echo "only after the grid has shadow-matched them live."
