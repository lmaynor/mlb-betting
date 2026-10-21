#!/usr/bin/env bash
# setup_nba_game_board.sh -- provision the NBA "our line vs market" games board (nba/games/board.py).
#
#   job        nba-game-board         python -m nba.games.board            (writes EVSweep/nba_games/latest.json; NO Discord)
#   schedulers nba-game-board-1750    17:50 UTC   (15 min after the 17:35 game-line bank)
#              nba-game-board-2250    22:50 UTC   (after the 22:35 bank)
#              nba-game-board-0115    01:15 UTC   (after the 01:00 bank)
#
# NOT RUN by the PR that added it -- provisioning is a deploy decision. Idempotent.
#
# WHAT IT PUBLISHES: our spread / total / win probability and a fair alt-line ladder next to the market consensus, labelled
# UNVALIDATED, with NO edge / EV fields. docs/analysis/nba_game_models_2026-10-20.md: the closing line beat these models on
# moneyline, spread and totals in every 2021-25 season. Rows are `reliable: false` until both teams have 15 games.
#
# DISCORD IS OFF. The job runs without --post and without any Discord secret. Turning the once-a-day #nba-alerts digest on is a
# separate decision: add `--post` to --args and mount the bot / DISCORD_WEBHOOK_CARD_NBA secrets (see nba/games/board_post.py).
#
# IMAGE: gcr.io/<project>/mlb-betting-feeds (NOT mlb-betting:latest -- docs/solutions/conventions/cloud-run-job-image-tag-resolution.md),
# rebuilt from merged main so it contains nba/games/ (deploy/build_all_images.sh, SKIP_PROD=1). A job runs whatever is in its image:
# the script refuses unless CONFIRM_IMAGE_HAS_NBA_GAMES=1. The API route /api/public/nba-games is in the web service (main.py): it
# is live only after the SERVICE is redeployed from merged main; until then the Next.js proxy gets a 404 and the tab shows empty.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_NBA_GAMES=1 bash deploy/setup_nba_game_board.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_nba_game_board.sh          # pause all three schedulers
#   One run now:  gcloud run jobs execute nba-game-board --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
JOB="nba-game-board"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULES=("nba-game-board-1750|50 17 * * *" "nba-game-board-2250|50 22 * * *" "nba-game-board-0115|15 1 * * *")

if [ "${PAUSE:-0}" = "1" ]; then
  for s in "${SCHEDULES[@]}"; do gcloud scheduler jobs pause "${s%%|*}" --location="$REGION" --project="$PROJECT_ID" --quiet; done
  echo "Paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_NBA_GAMES:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_NBA_GAMES=1 once $IMAGE contains nba/games/board.py."; exit 1; }
gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
       --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
       --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
       --command="python" --args="-m,nba.games.board"
       --memory=2Gi --cpu=1 --task-timeout=600 --max-retries=0 --quiet)
if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
else
  gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
fi

# scheduler-invoker must hold run.invoker ON THE JOB (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md).
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
for s in "${SCHEDULES[@]}"; do
  name="${s%%|*}"; cron="${s#*|}"
  SFLAGS=(--location="$REGION" --schedule="$cron" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
          --attempt-deadline=320s --project="$PROJECT_ID")
  if gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$name" "${SFLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$name" "${SFLAGS[@]}" --quiet
  fi
  echo "scheduler ${name} @ '${cron}' UTC -> ${JOB}"
done
echo "Done. Check: gs://<bucket>/EVSweep/nba_games/latest.json  (ts advances; status ok|no_games)"
