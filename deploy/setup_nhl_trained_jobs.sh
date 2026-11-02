#!/usr/bin/env bash
# setup_nhl_trained_jobs.sh -- provision the servable trained NHL prop models (saves, goals, assists, points) and their
# forward paper-tracking. NHL only. Nothing here touches the production service, mlb-betting:latest or any existing job.
#
#   job nhl-trained-train     python -m nhl.props.serve_trained train --write     weekly   (publishes Models/nhl_props/{version} + latest.json)
#   job nhl-trained-score     python -m nhl.props.serve_trained score             3x/day   (writes EVSweep/nhl_props/trained_latest.json)
#   job nhl-paper-snapshot    python -m nhl.props.paper_track snapshot            :10/:40  (logs served prediction x live line, pre-game)
#   job nhl-paper-settle      python -m nhl.props.paper_track settle              daily    (attaches real box-score results)
#
# IMAGE: gcr.io/<project>/mlb-betting-nhl-trained:<TAG>, a DEDICATED repository pinned by an explicit tag (never :latest). A Cloud
# Run JOB resolves its image tag at every execution (docs/solutions/conventions/cloud-run-job-image-tag-resolution.md), and
# deploy/build_all_images.sh re-couples every shared image to origin/main. These jobs must not change when someone builds production,
# and they must not run merged-but-unreviewed code; so they get their own repository and a tag per reviewed commit. Build it with
#   gcloud builds submit --config=cloudbuild.yaml --substitutions=_IMAGE=gcr.io/<project>/mlb-betting-nhl-trained --project=<project> .
#   gcloud container images add-tag gcr.io/<project>/mlb-betting-nhl-trained:latest gcr.io/<project>/mlb-betting-nhl-trained:<TAG>
# TRAIN INSIDE THIS IMAGE (nhl/props/trained.py: a bundle trained under another xgboost loads but predicts differently; the loader's
# canary refuses it). Never publish a bundle trained on a laptop.
#
# ORDER (the schedulers are OFF unless SCHEDULERS=1, so a first run can be watched): create the jobs, execute `nhl-trained-train` once
# (it publishes the bundle), execute `nhl-trained-score` once and check the predictions file, then re-run with SCHEDULERS=1.
# The board flag NHL_TRAINED_PROPS is NOT set here: see docs/analysis/nhl_trained_serving_2026-11-01.md for when to flip it.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 TAG=nhl-abc1234 bash deploy/setup_nhl_trained_jobs.sh                  # jobs only
#   PROJECT_ID=... TAG=... SCHEDULERS=1 bash deploy/setup_nhl_trained_jobs.sh                                 # + schedulers
#   PROJECT_ID=... PAUSE=1 bash deploy/setup_nhl_trained_jobs.sh                                              # pause every scheduler
# Idempotent.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
REPO="gcr.io/${PROJECT_ID}/mlb-betting-nhl-trained"
TZ_NAME="America/Chicago"

# name | args | memory | cpu | timeout(s) | cron (Central)
# CT cadence: nhl-refresh-data lands yesterday's games ~06:30 CT, so train (Sun) and score run after it; daily-projections refreshes the
# board at :25/:55, so the paper snapshot runs at :10/:40 and always sees a fresh board. Score exits cleanly when no game is in the window.
JOBS=(
  "nhl-trained-train|-m,nhl.props.serve_trained,train,--write|8Gi|4|3300|0 8 * * 0"
  "nhl-trained-score|-m,nhl.props.serve_trained,score,--hours,72|8Gi|4|1800|30 9,13 * * *;0 17 * * *"
  "nhl-paper-snapshot|-m,nhl.props.paper_track,snapshot|1Gi|1|300|10,40 10-20 * * *"
  "nhl-paper-settle|-m,nhl.props.paper_track,settle|1Gi|1|900|30 7 * * *"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for spec in "${JOBS[@]}"; do
    IFS='|' read -r name _ _ _ _ _ <<<"$spec"
    for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format="value(name.basename())" | grep "^${name}-sched"); do
      gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet && echo "paused $s"
    done
  done
  exit 0
fi

TAG="${TAG:?Set TAG (the image tag to pin, e.g. nhl-abc1234); never latest}"
[ "$TAG" != "latest" ] || { echo "REFUSING: pin a per-commit tag, not :latest."; exit 1; }
IMAGE="${REPO}:${TAG}"
gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found (build it first, see header)."; exit 1; }

for spec in "${JOBS[@]}"; do
  IFS='|' read -r name args mem cpu timeout crons <<<"$spec"
  FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
         --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
         --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
         --command="python" --args="$args"
         --memory="$mem" --cpu="$cpu" --task-timeout="$timeout" --max-retries=0 --quiet)
  if gcloud run jobs describe "$name" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud run jobs update "$name" "${FLAGS[@]}" --project="$PROJECT_ID"
  else
    gcloud run jobs create "$name" "${FLAGS[@]}" --project="$PROJECT_ID"
  fi
  # scheduler-invoker must hold run.invoker ON THE JOB or every firing is PERMISSION_DENIED
  # (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md).
  gcloud run jobs add-iam-policy-binding "$name" --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
  echo "job ${name} -> ${IMAGE} (${mem}, ${cpu} cpu, ${timeout}s, max-retries 0)"

  if [ "${SCHEDULERS:-0}" = "1" ]; then
    URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${name}:run"
    i=0
    IFS=';' read -ra CRONS <<<"$crons"
    for cron in "${CRONS[@]}"; do
      i=$((i + 1))
      sj="${name}-sched${i}"
      SFLAGS=(--location="$REGION" --schedule="$cron" --time-zone="$TZ_NAME" --uri="$URI" --http-method=POST
              --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
              --attempt-deadline=320s --project="$PROJECT_ID")
      if gcloud scheduler jobs describe "$sj" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
        gcloud scheduler jobs update http "$sj" "${SFLAGS[@]}" --quiet
      else
        gcloud scheduler jobs create http "$sj" "${SFLAGS[@]}" --quiet
      fi
      echo "scheduler ${sj} @ '${cron}' ${TZ_NAME} -> ${name}"
    done
  fi
done

echo
echo "Done. Next:"
echo "  gcloud run jobs execute nhl-trained-train --region=${REGION} --project=${PROJECT_ID} --wait      # publishes the bundle"
echo "  gcloud logging read 'resource.labels.job_name=\"nhl-trained-train\" textPayload:\"RESOURCES\"' --limit=3 --freshness=3h --format='value(textPayload)'"
echo "  gcloud run jobs execute nhl-trained-score --region=${REGION} --project=${PROJECT_ID} --wait      # writes EVSweep/nhl_props/trained_latest.json"
echo "  SCHEDULERS=1 PROJECT_ID=${PROJECT_ID} TAG=${TAG} bash deploy/setup_nhl_trained_jobs.sh              # then turn the schedulers on"
