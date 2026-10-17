#!/usr/bin/env bash
# setup_bettingpros_live.sh -- provision the LIVE BettingPros prop crawl for the NFL / NHL / NBA picks boards.
#
#   bp-live-nhl   python -m mlb.runners.bettingpros_live_props --sport nhl   hourly at :05
#   bp-live-nfl   python -m mlb.runners.bettingpros_live_props --sport nfl   hourly at :10 (Sunday pre-kickoff every 30 min)
#   bp-live-nba   python -m mlb.runners.bettingpros_live_props --sport nba   hourly at :15
#
# Each run writes Odds/bettingpros_live/{sport}/latest.json (ParlayAPI event shape, real two-sided sportsbooks + Novig /
# ProphetX only; DFS apps, Kalshi and the BettingPros consensus pseudo-book are dropped) which ev.daily_projections.load_events
# unions with the banked ParlayAPI lines. BettingPros is a FREE API: this spends no ParlayAPI credits.
#
# MEASURED 2026-10-17 (real slates, single-threaded, 0.8 s +-40% between offer pages, 1.0 s between markets):
#   nhl  3 days ahead, 31 games listed / 14 priced   121 requests   137-145 s   ~6.3k quotes
#   nfl  7 days ahead, 14 games / 13 priced           75 requests    61-98 s    ~7.0k quotes (incl. anytime TD)
#   nba  7 days ahead, 36 games / 13 priced           99 requests    99-102 s   ~0.8k quotes (preseason: few books price props yet)
# The offers endpoint caps a page at 10 offers, so a busy slate is ~100-250 requests; at ~110 requests per sport per hour the
# load is far below a polite ceiling. Cloud Run task timeout 900 s (>5x the slowest measured cycle, room for the 429/5xx
# back-off that bettingpros.api_get already does); max-retries=1 because a crawl is free and idempotent -- but a failed crawl
# never overwrites the last good latest.json (exit 1), so a retry only helps.
#
# CADENCE: hourly (:05/:10/:15, America/Chicago), staggered so two crawls never hit BettingPros at once, and placed BEFORE the
# projections job (:25 / :55, deploy/setup_daily_projections_job.sh) so every board recompute sees fresh lines. Months are
# the seasons so the offseason makes no requests: NFL Sep-Feb, NHL / NBA Oct-Jun. NFL Sunday: every 30 min 06:00-12:59 CT
# (kickoffs from 12:00 ET) -- the one place intraday line movement before the games is worth the extra requests.
#
# IMAGE: gcr.io/<project>/mlb-betting-feeds (NOT mlb-betting:latest; docs/solutions/conventions/cloud-run-job-image-tag-resolution.md).
# The feeds image must contain ev/bettingpros_events.py -- a job runs whatever is in its image -- so the script refuses to
# continue unless CONFIRM_IMAGE_HAS_LIVE_LINES=1 is set. Needs only the bucket secret (no API key, no DB). Idempotent.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 CONFIRM_IMAGE_HAS_LIVE_LINES=1 bash deploy/setup_bettingpros_live.sh
#   PAUSE=1 PROJECT_ID=... bash deploy/setup_bettingpros_live.sh     # pause every bp-live-* scheduler
#   Test one run now:  gcloud run jobs execute bp-live-nba --region=us-central1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# job|sport|scheduler tag|cron (America/Chicago)
ROWS=(
  "bp-live-nhl|nhl|hourly|5 * * 10-12,1-6 *"
  "bp-live-nfl|nfl|weekly|10 * * 9-12,1-2 1-6"
  "bp-live-nfl|nfl|sun-am|10,40 6-12 * 9-12,1-2 0"
  "bp-live-nfl|nfl|sun-rest|10 0-5,13-23 * 9-12,1-2 0"
  "bp-live-nba|nba|hourly|15 * * 10-12,1-6 *"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for s in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep '^bp-live-'); do
    gcloud scheduler jobs pause "$s" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "All bp-live-* schedulers paused."; exit 0
fi

[ "${CONFIRM_IMAGE_HAS_LIVE_LINES:-0}" = "1" ] || {
  echo "REFUSING: set CONFIRM_IMAGE_HAS_LIVE_LINES=1 once $IMAGE contains ev/bettingpros_events.py."
  echo "(An older image has no mlb.runners.bettingpros_live_props: every execution would fail.)"; exit 1; }

gcloud iam service-accounts describe "$SA_EMAIL" --quiet >/dev/null || { echo "ERROR: $SA_EMAIL not found."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 || { echo "ERROR: $IMAGE not found."; exit 1; }

JOBS_DONE=" "     # space-delimited set (portable: no associative arrays, so it also runs under macOS bash 3.2)
for row in "${ROWS[@]}"; do
  IFS='|' read -r JOB SPORT TAG CRON <<<"$row"
  if [[ "$JOBS_DONE" != *" $JOB "* ]]; then
    FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL"
           --set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"
           --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}"
           --command="python" --args="-m,mlb.runners.bettingpros_live_props,--sport,${SPORT}"
           --memory=512Mi --cpu=1 --task-timeout=900 --max-retries=1 --quiet)
    if gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      gcloud run jobs update "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
    else
      gcloud run jobs create "$JOB" "${FLAGS[@]}" --project="$PROJECT_ID"
    fi
    # scheduler-invoker must hold run.invoker ON THE JOB, or every firing is PERMISSION_DENIED (status code 7) and the job
    # silently never runs. docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
    gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
      --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null
    JOBS_DONE="${JOBS_DONE}${JOB} "
  fi
  SJOB="${JOB}-${TAG}"
  URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
  SFLAGS=(--location="$REGION" --schedule="$CRON" --time-zone="America/Chicago" --uri="$URI" --http-method=POST
          --oauth-service-account-email="$SCHED_SA" --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
          --attempt-deadline=320s --project="$PROJECT_ID")
  if gcloud scheduler jobs describe "$SJOB" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SJOB" "${SFLAGS[@]}" --quiet
  else
    gcloud scheduler jobs create http "$SJOB" "${SFLAGS[@]}" --quiet
  fi
  echo "scheduler ${SJOB} @ '${CRON}' America/Chicago -> ${JOB}"
done

echo
echo "Done. Verify:  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep bp-live"
echo "First data now (free): for s in nhl nfl nba; do gcloud run jobs execute bp-live-\$s --region=$REGION --wait; done"
echo "Prove the scheduler path (status.code must be empty): gcloud scheduler jobs run bp-live-nhl-hourly --location=$REGION"
echo "Result:        gsutil cat gs://<bucket>/Odds/bettingpros_live/nhl/latest.json | head -c 600"
