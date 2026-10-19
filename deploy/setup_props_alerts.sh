#!/usr/bin/env bash
# setup_props_alerts.sh -- provision the NHL/NBA soft-line alerts sweep, its grader, and the NBA roster crosswalk.
#
#   props-alerts-{nhl,nba}   python -m ev.sweep_props_alerts --sport X   runs 5 min AFTER each odds-bank props run
#   props-grade-{nhl,nba}    python -m ev.grade_props --sport X          daily, grades yesterday's alerts vs real results
#   nba-espn-players         python -m nba.data.espn_players             daily, refreshes NBA/players_xwalk.csv
#
# COST: the sweep spends ZERO ParlayAPI credits (it reads the snapshot the odds banker already bought) -- so adding
# or removing these schedulers cannot affect the credit budget. It REFUSES a snapshot older than 45 min, so a missed
# banker run means "no alerts", never alerts on stale quotes.
#
# DISCORD: alerts post through beezy-bot into ONE daily thread per sport under its card channel (#nhl-card / #nba-card): no channels
# of their own, access follows the sport pass. The jobs need BEEZY_BOT_URL + BEEZY_BOT_SECRET. DISCORD_WEBHOOK_ALERTS (secret
# discord-webhook-alerts, optional) is only the bot-down fallback. With neither the sweep logs instead of posting; it never falls
# back to #ops-alerts (failures only) or a +EV channel (tests/test_ev_props_alerts.py pins that).
#
# Prereqs: the feeds image (see IMAGE below -- NOT the production mlb-betting image) rebuilt with this code; odds banking provisioned
# (deploy/setup_odds_banking.sh) -- the sweep has nothing to read before that. Idempotent.
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_props_alerts.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_props_alerts.sh      # pause all props-* / nba-espn-players schedulers
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
# IMAGE: a Cloud Run JOB resolves its image tag at EVERY EXECUTION (verified 2026-10-14: mlb-build-all-features' spec says
# :latest but each run records the digest it resolved at start). Pointing these jobs at the production
# gcr.io/.../mlb-betting:latest would therefore (a) run whatever is in prod :latest and (b) mean any later prod build silently
# changes what they run. They default to the separate, purpose-built feeds image instead; override with IMAGE=... only on purpose.
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep -E '^(props-alerts|props-grade|nba-espn-players)'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "Paused."; exit 0
fi

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

BOT_URL="${BEEZY_BOT_URL:-https://beezy-discord-bot-628109313129.us-central1.run.app}"
SECRETS="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_OPS=discord-ops-webhook-url:latest,BEEZY_BOT_SECRET=beezy-bot-secret:latest"
if gcloud secrets describe discord-webhook-alerts --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  SECRETS="${SECRETS},DISCORD_WEBHOOK_ALERTS=discord-webhook-alerts:latest"
fi
# Member-channel delivery (#nhl-alerts / #nba-alerts highlights, Beezy-Score-gated; mlb_core/alerts/sport_alerts.py). Each card
# secret is mounted only if it exists, so provisioning order does not matter: no secret = no gated highlight for that sport
# (the daily signals thread, which goes through the bot, is unaffected).
for pair in "DISCORD_WEBHOOK_CARD_NHL=discord-webhook-card-nhl" "DISCORD_WEBHOOK_CARD_NBA=discord-webhook-card-nba"; do
  if gcloud secrets describe "${pair#*=}" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    SECRETS="${SECRETS},${pair}:latest"
  fi
done

# job_name | module args (comma-separated for --args) | cron(UTC) | timeout(s)
ROWS=(
  "props-alerts-nhl|-m,ev.sweep_props_alerts,--sport,nhl|35 17,35 22 * * *|300"
  "props-alerts-nba|-m,ev.sweep_props_alerts,--sport,nba|35 17,35 22 * * *|300"
  "props-grade-nhl|-m,ev.grade_props,--sport,nhl,--date,yesterday|30 12 * * *|600"
  "props-grade-nba|-m,ev.grade_props,--sport,nba,--date,yesterday|40 12 * * *|600"
  "nba-espn-players|-m,nba.data.espn_players|0 11 * * *|600"
)
for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB ARGS CRON TMO <<<"$row"
  FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" --set-secrets="$SECRETS"
         --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},BEEZY_BOT_URL=${BOT_URL}" --command="python" --args="$ARGS"
         --memory=1Gi --cpu=1 --task-timeout="$TMO" --max-retries=0 --quiet)
  if gcloud run jobs describe "$JOB" --region="$REGION" --quiet >/dev/null 2>&1; then
    gcloud run jobs update "$JOB" "${FLAGS[@]}"
  else
    gcloud run jobs create "$JOB" "${FLAGS[@]}"
  fi
  # a cron with a comma-hour list ("35 17,35 22 ...") is two schedules: split on the pattern "M H,M H" manually
  if [[ "$CRON" == *","* && "$CRON" == *" * * *" ]]; then
    IFS=',' read -r C1 C2 <<<"${CRON% * * *}"
    CRONS=("${C1} * * *" "${C2} * * *")
  else
    CRONS=("$CRON")
  fi
  i=0
  for C in "${CRONS[@]}"; do
    i=$((i + 1)); SJOB="${JOB}-${i}"
    URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
    SFLAGS=(--location="$REGION" --schedule="$C" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
            --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
            --attempt-deadline=320s --project="$PROJECT_ID")
    # scheduler-invoker must hold run.invoker ON THE JOB, or every scheduler firing is PERMISSION_DENIED (status code 7)
    # and the job silently never runs. Missed on 11 jobs provisioned 2026-10-14 (docs/solutions/runtime-errors/
    # scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
    gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
      --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
    if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}"
    else
      gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}"
    fi
    echo "scheduler ${SJOB} @ '${C}' UTC -> ${JOB}"
  done
done
echo "Done. Verify: gcloud scheduler jobs list --location=$REGION | grep -E 'props-|nba-espn'"
