#!/usr/bin/env bash
# setup_contest_scheduler.sh -- provision the two Cloud Scheduler jobs that drive the Bankroll Challenge (play money):
#
#   contest-refresh   every 20 minutes   POST /contest-refresh {slug, include}              fetch ESPN, refresh the board, grade finals, announce
#   contest-halftime  every minute       POST /contest-refresh {slug, include, halftime}    only does anything while a game is at halftime AND the
#                                                                                           halftime rule + CONTEST_HALFTIME_ENABLED are on
#
# Both call the main Cloud Run service (the contest routes live in main.py). Jobs are created PAUSED unless START=1: nothing runs until
# the owner says so, and the whole surface answers 404 unless CONTEST_ENABLED is on in the service.
#
# START=1 turns EVERYTHING on in one go (owner, 2026-11-17: "trigger everything at once"), in this order:
#   1. Cloud Run: CONTEST_ENABLED=1 on the service (a new revision; traffic goes to it, like deploy_service.sh does)
#   2. contest-setup job: creates the contest and its invite code if they do not exist (idempotent, never edits an existing one), run once
#   3. Vercel (production): CONTEST_ENABLED=1 and CONTEST_SLUG=<slug>, then a redeploy of the latest production deployment so the pages see them
#   4. both scheduler jobs resumed
# PAUSE=1 reverses it: jobs paused, CONTEST_ENABLED=0 on Cloud Run, the Vercel contest variables removed (the pages 404 again after a redeploy).
# The Cloud Run and scheduler steps use gcloud; the Vercel step needs the vercel CLI logged in and beezy-vip linked (`vercel link`), and is
# best-effort (it prints what to do by hand if it fails). NOT yet run against production.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONTEST_SLUG=bowls-2026 bash deploy/setup_contest_scheduler.sh            # create / update the jobs, paused
#   START=1 PROJECT_ID=... CONTEST_SLUG=... CONTEST_INVITE=... bash deploy/setup_contest_scheduler.sh           # turn everything on
#   PAUSE=1 PROJECT_ID=... CONTEST_SLUG=... bash deploy/setup_contest_scheduler.sh                              # turn everything off
#
# Optional: CONTEST_NAME ("Bowl Bankroll"), CONTEST_SPORT (ncaaf), CONTEST_INVITE (random if unset, printed), CONTEST_INVITE_USES (25),
# CONTEST_INCLUDE (round-label regex). Announcements: set CONTEST_NOTIFY=1 and CONTEST_DISCORD_WEBHOOK on the service yourself.
# The halftime window needs the contest rule switched on in the organiser console AND CONTEST_HALFTIME_ENABLED=1 on the service.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
REFRESH_JOB="contest-refresh"
HALFTIME_JOB="contest-halftime"

vercel_dir="$(cd "$(dirname "$0")/.." && pwd)/beezy-vip"
vercel_redeploy() {
  # env vars only reach NEW deployments: redeploy the latest production one
  local url
  url="$(cd "$vercel_dir" && vercel ls --prod 2>/dev/null | grep -o 'https://[^ ]*' | head -1)" || true
  if [ -n "$url" ]; then (cd "$vercel_dir" && vercel redeploy "$url") || echo "!! Vercel redeploy failed: redeploy the latest production deployment from the Vercel dashboard"
  else echo "!! could not find the latest production deployment: redeploy it from the Vercel dashboard so CONTEST_* take effect"; fi
}

if [ "${PAUSE:-0}" = "1" ]; then
  for j in "$REFRESH_JOB" "$HALFTIME_JOB"; do
    gcloud scheduler jobs pause "$j" --location="$REGION" --project="$PROJECT_ID" --quiet || true
  done
  gcloud run services update "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --update-env-vars="CONTEST_ENABLED=0" --quiet \
    && gcloud run services update-traffic "$SERVICE_NAME" --to-latest --region="$REGION" --project="$PROJECT_ID" --quiet \
    || echo "!! could not switch CONTEST_ENABLED off on Cloud Run"
  for v in CONTEST_ENABLED CONTEST_SLUG; do (cd "$vercel_dir" && vercel env rm "$v" production --yes) || true; done
  vercel_redeploy
  echo "contest schedulers paused and the contest switched off."; exit 0
fi

SLUG="${CONTEST_SLUG:?Set CONTEST_SLUG (the contest to drive)}"
INCLUDE="${CONTEST_INCLUDE:-Bowl|Playoff|Championship}"      # round-label regex that keeps the contest to its own games; "" = every game ESPN lists
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

# The scheduler's service account must be allowed to invoke the service or every firing is PERMISSION_DENIED
# (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md). Idempotent.
gcloud run services add-iam-policy-binding "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHEDULER_SA}" --role="roles/run.invoker" --quiet >/dev/null

upsert() {
  local name="$1" cron="$2" body="$3" description="$4" deadline="$5"
  if gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron)..."
    gcloud scheduler jobs update http "$name" --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-refresh" --http-method=POST --message-body="$body" \
      --update-headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="$deadline" --project="$PROJECT_ID" --quiet
  else
    echo "Creating $name ($cron)..."
    gcloud scheduler jobs create http "$name" --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-refresh" --http-method=POST --message-body="$body" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
      --attempt-deadline="$deadline" --description="$description" --project="$PROJECT_ID" --quiet
  fi
  if [ "${START:-0}" = "1" ]; then
    gcloud scheduler jobs resume "$name" --location="$REGION" --project="$PROJECT_ID" --quiet
  else
    gcloud scheduler jobs pause "$name" --location="$REGION" --project="$PROJECT_ID" --quiet
  fi
}

SETUP_JOB="contest-setup"
NAME="${CONTEST_NAME:-Bowl Bankroll}"; SPORT="${CONTEST_SPORT:-ncaaf}"; USES="${CONTEST_INVITE_USES:-25}"
INVITE="${CONTEST_INVITE:-$(openssl rand -hex 4 | tr 'a-f' 'A-F')}"          # (not tr | head: SIGPIPE would kill the script under pipefail)

turn_everything_on() {
  echo "==> 1. Cloud Run: CONTEST_ENABLED=1"
  gcloud run services update "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --update-env-vars="CONTEST_ENABLED=1" --quiet
  gcloud run services update-traffic "$SERVICE_NAME" --to-latest --region="$REGION" --project="$PROJECT_ID" --quiet

  echo "==> 2. Create the contest and its invite code (idempotent)"
  local body="{\"slug\":\"${SLUG}\",\"name\":\"${NAME}\",\"sport\":\"${SPORT}\",\"invite_code\":\"${INVITE}\",\"invite_uses\":${USES}}"
  if gcloud scheduler jobs describe "$SETUP_JOB" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SETUP_JOB" --location="$REGION" --schedule="0 0 1 1 *" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-setup" --http-method=POST --message-body="$body" --update-headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" --attempt-deadline="60s" --project="$PROJECT_ID" --quiet
  else
    gcloud scheduler jobs create http "$SETUP_JOB" --location="$REGION" --schedule="0 0 1 1 *" --time-zone="UTC" \
      --uri="${SERVICE_URL}/contest-setup" --http-method=POST --message-body="$body" --headers="Content-Type=application/json" \
      --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" --attempt-deadline="60s" \
      --description="Bankroll Challenge: one-off contest + invite setup (run by hand, idempotent)" --project="$PROJECT_ID" --quiet
  fi
  gcloud scheduler jobs pause "$SETUP_JOB" --location="$REGION" --project="$PROJECT_ID" --quiet
  gcloud scheduler jobs run "$SETUP_JOB" --location="$REGION" --project="$PROJECT_ID"

  echo "==> 3. Vercel (production): CONTEST_ENABLED=1, CONTEST_SLUG=${SLUG}"
  for v in CONTEST_ENABLED CONTEST_SLUG; do (cd "$vercel_dir" && vercel env rm "$v" production --yes >/dev/null 2>&1) || true; done
  (cd "$vercel_dir" && printf '1' | vercel env add CONTEST_ENABLED production) || echo "!! set CONTEST_ENABLED=1 on Vercel (production) by hand"
  (cd "$vercel_dir" && printf '%s' "$SLUG" | vercel env add CONTEST_SLUG production) || echo "!! set CONTEST_SLUG=${SLUG} on Vercel (production) by hand"
  vercel_redeploy
  echo "Invite code for ${SLUG}: ${INVITE}  (${USES} uses)"
}
if [ "${START:-0}" = "1" ]; then turn_everything_on; fi

upsert "$REFRESH_JOB" "*/20 * * * *" "{\"slug\":\"${SLUG}\",\"include\":\"${INCLUDE}\"}" \
  "Bankroll Challenge: refresh the board from ESPN, grade finals, announce" "120s"
upsert "$HALFTIME_JOB" "* * * * *" "{\"slug\":\"${SLUG}\",\"include\":\"${INCLUDE}\",\"halftime\":true}" \
  "Bankroll Challenge: halftime window poll (no-op unless the halftime rule is on)" "60s"

echo ""
echo "=== Done ==="
if [ "${START:-0}" = "1" ]; then echo "Both jobs are RUNNING."; else echo "Both jobs are PAUSED. Resume with START=1, or: gcloud scheduler jobs resume <name> --location=$REGION"; fi
echo "Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep contest"
