#!/usr/bin/env bash
# setup_odds_banking.sh -- provision the NHL + NBA ParlayAPI odds-banking Cloud Run Jobs + Schedulers.
#
# WHY: ParlayAPI has no historical props, so every unbanked day is lost backtest/model-training data.
# This banks raw payloads + best-book CSVs to gs://<bucket>/OddsAccum/{sport}/ (see nba/odds/accumulator.py).
#
# CREDIT SAFETY (read before changing cadence): every run is metered by nba/odds/credit_ledger.py -- a per-sport
# monthly budget with a LINEAR pace cap, a per-run cap, and a floor on the shared pool's x-requests-remaining.
# Defaults: 3000 credits/mo per sport (env ODDS_BANK_BUDGET_<SPORTKEY>), floor 2500 (PARLAY_RESERVE_FLOOR).
# The schedule below is deliberately a CEILING on cadence -- the guard skips runs that are over pace, so adding
# a scheduler can never overspend the budget, but it CAN crowd out later-in-the-day runs. The 2026-08-27
# incident (zombie accumulator schedulers draining the pool) is why the jobs are named odds-bank-* and why
# `gcloud scheduler jobs list | grep odds-bank` should be the one place to look for them.
#
# Cost model (core markets): props ~ 1 + 5 x n_events credits per run; lines = 3 per run.
#   per sport/day: 2 props runs (~45-55 each) + 3 line runs (3 each) ~ 110 -> ~3.3k/mo -> the pace guard trims it.
#
# Cadence (UTC) -- NHL/NBA puck-drop/tip is mostly 23:00-03:00 UTC (7-10pm ET) with some 17:00-20:00 matinees:
#   props: 17:30 (early lines for the whole slate)  and  22:30 (closing-ish, before the evening block)
#   lines: 17:30, 22:30, 01:00 (captures late/West-coast closing)
#
# Prereqs: the IMAGE (default gcr.io/<project>/mlb-betting-feeds, NOT the production mlb-betting image) must contain
# nba/odds/credit_ledger.py -- a job runs whatever is in its image, so provisioning against an older image runs the OLD,
# unguarded accumulator. The script refuses to continue unless CONFIRM_IMAGE_HAS_LEDGER=1 is set, as a speed bump for
# exactly that mistake. Do NOT build into mlb-betting:latest to satisfy this: every existing scheduled MLB job re-resolves
# that tag on its next execution.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LEDGER=1 bash deploy/setup_odds_banking.sh
#   ... PAUSE=1 bash deploy/setup_odds_banking.sh      # pause every odds-bank-* scheduler (offseason / emergency)
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

# tag|sport_key|kind|cron(UTC)
SCHEDULES=(
  "nhl|icehockey_nhl|props|30 17 * * *"
  "nhl|icehockey_nhl|props|30 22 * * *"
  "nhl|icehockey_nhl|game_lines|35 17 * * *"
  "nhl|icehockey_nhl|game_lines|35 22 * * *"
  "nhl|icehockey_nhl|game_lines|0 1 * * *"
  "nba|basketball_nba|props|30 17 * * *"
  "nba|basketball_nba|props|30 22 * * *"
  "nba|basketball_nba|game_lines|35 17 * * *"
  "nba|basketball_nba|game_lines|35 22 * * *"
  "nba|basketball_nba|game_lines|0 1 * * *"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^odds-bank-'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "All odds-bank-* schedulers paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_LEDGER:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_LEDGER=1 once $IMAGE contains nba/odds/credit_ledger.py."
  echo "(An older image would run the UNGUARDED accumulator.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

JOBS_DONE=" "     # space-delimited set (portable: no associative arrays, so it also runs under macOS bash 3.2)
for row in "${SCHEDULES[@]}"; do
  IFS='|' read -r TAG SPORT KIND CRON <<<"$row"
  KIND_TAG="$(printf '%s' "$KIND" | tr '_' '-')"
  JOB="odds-bank-${TAG}-${KIND_TAG}"
  if [[ "$JOBS_DONE" != *" $JOB "* ]]; then
    ARGS="-m,nba.odds.accumulator,--sport,${SPORT},--kind,${KIND},--max-credits,400"
    FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
           --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest,PARLAY_API_KEY=parlay-api-key:latest"
           --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
           --command="python" --args="$ARGS"
           --memory=1Gi --cpu=1 --task-timeout=900 --max-retries=0 --quiet)
    # max-retries=0: a retried props run would re-spend credits; the ledger records the first attempt either way.
    if gcloud run jobs describe "$JOB" --region="$REGION" --quiet >/dev/null 2>&1; then
      gcloud run jobs update "$JOB" "${FLAGS[@]}"
    else
      gcloud run jobs create "$JOB" "${FLAGS[@]}"
    fi
    JOBS_DONE="${JOBS_DONE}${JOB} "
  fi
  HHMM="$(echo "$CRON" | awk '{printf "%02d%02d", $2, $1}')"
  SJOB="${JOB}-${HHMM}"
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="Etc/UTC" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA"
          --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
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
  echo "scheduler ${SJOB} @ '${CRON}' UTC -> ${JOB}"
done

echo
echo "Done. Verify:  gcloud scheduler jobs list --location=$REGION | grep odds-bank"
echo "One-off probe: gcloud run jobs execute odds-bank-nhl-props --region=$REGION   (spends ~40 credits)"
echo "Ledger:        gsutil cat gs://<bucket>/OddsAccum/icehockey_nhl/_credits/\$(date +%Y-%m).json"
