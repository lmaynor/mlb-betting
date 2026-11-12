#!/usr/bin/env bash
# setup_alt_ladder_sink.sh -- run the alt-ladder sink extractor (job ev-alt-ladder-extract) every 15 min.
#
# WHY: the NFL sweep's consensus path counts one-sided quotes (n_onesided) and discards them, and no
# rung-level table of the ladders inside the banked raw payloads existed (Novig two-sided ladders,
# Bovada milestones, DFS ladders). The 2026-11-12 ladder spike found a whole class of pricing tests
# impossible for that reason. ev/alt_ladder_sink.py extracts the discarded stream from the raw
# payloads (EVSweep/nfl_props/raw/) into EVSweep/nfl_props/alt_ladder/{ET day}.jsonl.gz and sets an
# event-based hold on every raw payload it processes, so the source payloads are retained permanently.
#
# Cadence: the sweep fires every 2 h (Mon-Sat) and every 30 min on Sunday mornings; a 15-min
# extractor keeps the sink current within one sweep cycle in every cadence. Each firing re-extracts
# the trailing 3 ET days and OVERWRITES those day files (idempotent; a re-run repairs, never
# duplicates). Zero ParlayAPI credits: the extractor reads only banked payloads.
#
# IMAGE: the job pins the branch-tagged feeds image the PR was deployed with
# (mlb-betting-feeds:alt-ladder-20261112). Cloud Run jobs resolve their tag at every execution, so
# do NOT repoint this job at :latest until the sink code has merged to main and a main roll has run.
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_alt_ladder_sink.sh
#         PAUSE=1 PROJECT_ID=... bash deploy/setup_alt_ladder_sink.sh     # pause the scheduler
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
JOB="ev-alt-ladder-extract"
SJOB="${JOB}-q15"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds:alt-ladder-20261112}"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SA="mlb-betting-sa@${PROJECT_ID}.iam.gserviceaccount.com"

if [ "${PAUSE:-0}" = "1" ]; then
  gcloud scheduler jobs pause "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  echo "alt-ladder extractor scheduler paused."; exit 0
fi

# Create the job if missing; otherwise pin args/env/image (leaves the rest of the spec alone).
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" --region="$REGION" --project="$PROJECT_ID" \
    --image="$IMAGE" --args="-m,ev.alt_ladder_sink" --max-retries=0 --task-timeout=900 --quiet
else
  gcloud run jobs create "$JOB" --region="$REGION" --project="$PROJECT_ID" \
    --image="$IMAGE" --command=python3 --args="-m,ev.alt_ladder_sink" \
    --service-account="$SA" --max-retries=0 --task-timeout=900 --cpu=1 --memory=1Gi \
    --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
    --update-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest" --quiet
fi

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7); see
# docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SFLAGS=(--location="$REGION" --schedule="*/15 * * * *" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
        --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
        --attempt-deadline=320s --project="$PROJECT_ID")
if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
else
  gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
fi
echo "scheduler ${SJOB} @ '*/15 * * * *' America/Chicago -> ${JOB} (image ${IMAGE})"
echo "Verify: gcloud run jobs executions list --job=${JOB} --region=${REGION} --project=${PROJECT_ID} --limit=3"
