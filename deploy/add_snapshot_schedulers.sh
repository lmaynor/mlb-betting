#!/usr/bin/env bash
# deploy/add_snapshot_schedulers.sh — register the MLB odds snapshot schedulers.
#
# Cadence (post-ParlayAPI-cutover): 8 snapshots/day. ParlayAPI is pulled on every
# run (covered markets); SGO inning markets are fetched on only 4 of them
# (include_sgo=true) and CARRIED FORWARD on the other 4 (include_sgo=false), so
# SGO stays ~4x/day within its ~2500 entities/mo free tier while ParlayAPI runs
# 8x/day toward the 20k credit budget (the snapshot's implicit credit guard caps
# it under PARLAY_CREDIT_CEILING). Late-night runs use day_offset=1 to bank the
# next day's slate (lines post ~9pm ET).
#
# !! ORDER MATTERS !! Only register the 8-job cadence AFTER flipping
#    ODDS_PRIMARY=parlay on the service. While ODDS_PRIMARY=sgo, EVERY run does a
#    full SGO fetch -> 8x/day would exceed the SGO free tier. Pre-cutover, keep
#    just the two legacy SGO jobs (set LEGACY=1).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/add_snapshot_schedulers.sh
#   LEGACY=1 PROJECT_ID=... ./deploy/add_snapshot_schedulers.sh   # pre-cutover (2 SGO jobs)

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
LEGACY="${LEGACY:-0}"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "Cloud Run service URL: $SERVICE_URL"

_upsert_job() {  # name cron body description
  local name="$1" cron="$2" body="$3" description="$4"
  local action=create
  gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" \
    >/dev/null 2>&1 && action=update
  echo "${action^} $name ($cron) body=$body"
  gcloud scheduler jobs "$action" http "$name" \
    --location="$REGION" --schedule="$cron" \
    --uri="${SERVICE_URL}/snapshot-odds" \
    --message-body="$body" \
    --headers="Content-Type=application/json" \
    --oidc-service-account-email="$SCHEDULER_SA" \
    --oidc-token-audience="$SERVICE_URL" \
    --time-zone="UTC" --project="$PROJECT_ID" --quiet \
    ${description:+--description="$description"}
}

if [ "$LEGACY" = "1" ]; then
  # Pre-cutover: two SGO snapshots only (~900 SGO objects/mo).
  _upsert_job "mlb-snapshot-morning" "55 14 * * *" '{}' "SGO slate snapshot (pre-15:00 run)"
  _upsert_job "mlb-snapshot-evening" "55 20 * * *" '{}' "SGO slate snapshot (pre-21:00 run)"
  echo "=== Done (legacy 2 SGO jobs) ==="
  exit 0
fi

# Post-cutover: 8 ParlayAPI snapshots/day; include_sgo=true on 4 (SGO inning
# fetch), false on 4 (carry inning forward). day_offset=1 on the two late-night
# runs to bank tomorrow's slate.
_upsert_job "mlb-snapshot-1455" "55 14 * * *" '{"include_sgo":true}'  "pre-morning run (SGO inning)"
_upsert_job "mlb-snapshot-1655" "55 16 * * *" '{"include_sgo":false}' "parlay-only (carry inning)"
_upsert_job "mlb-snapshot-1855" "55 18 * * *" '{"include_sgo":false}' "parlay-only (carry inning)"
_upsert_job "mlb-snapshot-2055" "55 20 * * *" '{"include_sgo":true}'  "pre-evening run (SGO inning)"
_upsert_job "mlb-snapshot-2255" "55 22 * * *" '{"include_sgo":false}' "parlay-only (carry inning)"
_upsert_job "mlb-snapshot-2355" "55 23 * * *" '{"include_sgo":true}'  "closing lines (SGO inning)"
_upsert_job "mlb-snapshot-0155" "55  1 * * *" '{"include_sgo":false,"day_offset":1}' "tomorrow slate (lines posting)"
_upsert_job "mlb-snapshot-0355" "55  3 * * *" '{"include_sgo":true,"day_offset":1}'  "tomorrow slate (SGO inning)"

echo ""
echo "=== Done: 8 ParlayAPI snapshots/day (4 with SGO inning, 2 next-day) ==="
echo "Pre-cutover? re-run with LEGACY=1 and keep ODDS_PRIMARY=sgo."
echo "List: gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep snapshot"
