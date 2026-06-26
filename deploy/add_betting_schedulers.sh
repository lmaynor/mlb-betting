#!/usr/bin/env bash
# deploy/add_betting_schedulers.sh -- pair a scoring (/run) pass to EVERY odds
# snapshot, not just morning + evening.
#
# Why: snapshots are pulled 4x/day (morning, afternoon, evening, pregame) but
# scoring only ran 2x (morning, evening). Late-appearing markets -- evening-game
# props posted mid-afternoon, lineups confirmed near first pitch -- were never
# scored. This adds mlb-betting-afternoon and mlb-betting-pregame so each odds
# pull is followed ~5 min later by a scoring pass.
#
# Dedup safety: BetTracker.log_bet() is strict first-wins on
# (system, game_date, game_pk, bet_type) -- see CONTEXT.md "Bet dedup contract".
# Extra runs only log brand-NEW markets; they never double-log or re-price an
# existing bet. No code change to dedup is required.
#
# Design: this clones the config of an existing, known-good scoring job
# (mlb-betting-evening) -- its URI, request body (systems list), OIDC auth, and
# attempt deadline -- and only changes the name + schedule. That keeps the new
# jobs correct even though the committed docs/scripts have drifted from live
# (e.g. body systems list, snapshot times). Idempotent: re-running updates.
#
# Usage (from Cloud Shell, after the new revision is deployed):
#   chmod +x deploy/add_betting_schedulers.sh
#   PROJECT_ID=concrete-crow-445205-m4 ./deploy/add_betting_schedulers.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
TEMPLATE_JOB="${TEMPLATE_JOB:-mlb-betting-evening}"

_describe() {
  gcloud scheduler jobs describe "$1" \
    --location="$REGION" --project="$PROJECT_ID" --format="$2"
}

# --- 1. Clone the known-good evening scoring job's config -----------------
echo "Cloning config from template job: $TEMPLATE_JOB"
URI=$(_describe "$TEMPLATE_JOB" "value(httpTarget.uri)")
# body is stored base64-encoded in the describe output
BODY=$(_describe "$TEMPLATE_JOB" "value(httpTarget.body)" | base64 --decode)
OIDC_SA=$(_describe "$TEMPLATE_JOB" "value(httpTarget.oidcToken.serviceAccountEmail)")
OIDC_AUD=$(_describe "$TEMPLATE_JOB" "value(httpTarget.oidcToken.audience)")
DEADLINE=$(_describe "$TEMPLATE_JOB" "value(attemptDeadline)")
DEADLINE="${DEADLINE:-180s}"

echo "  uri:      $URI"
echo "  body:     $BODY"
echo "  oidc_sa:  $OIDC_SA"
echo "  deadline: $DEADLINE"

# --- 2. Derive each new schedule as <snapshot cron> + 5 minutes -----------
# Falls back to the documented defaults if the snapshot job is absent.
_snapshot_plus5() {
  local snap_job="$1" fallback="$2" cron
  if ! cron=$(_describe "$snap_job" "value(schedule)" 2>/dev/null) || [ -z "$cron" ]; then
    echo "$fallback"; return
  fi
  # cron = "M H dom mon dow"; add 5 to minute, carry into hour (mod 24).
  local m h rest
  m=$(echo "$cron" | awk '{print $1}')
  h=$(echo "$cron" | awk '{print $2}')
  rest=$(echo "$cron" | awk '{print $3" "$4" "$5}')
  # only shift when both are plain integers; otherwise keep fallback
  if ! [[ "$m" =~ ^[0-9]+$ && "$h" =~ ^[0-9]+$ ]]; then echo "$fallback"; return; fi
  m=$((m + 5)); h=$((h + m / 60)); m=$((m % 60)); h=$((h % 24))
  echo "$m $h $rest"
}

AFTERNOON_CRON=$(_snapshot_plus5 "mlb-snapshot-afternoon" "5 19 * * *")
PREGAME_CRON=$(_snapshot_plus5   "mlb-snapshot-pregame"   "35 23 * * *")

# --- 3. Upsert the two new scoring jobs -----------------------------------
_upsert_betting_job() {
  local name="$1" cron="$2" description="$3"
  if gcloud scheduler jobs describe "$name" \
       --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Updating $name ($cron)..."
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="$URI" --http-method=POST \
      --message-body="$BODY" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$OIDC_SA" \
      --oidc-token-audience="$OIDC_AUD" \
      --attempt-deadline="$DEADLINE" \
      --project="$PROJECT_ID" --quiet
  else
    echo "Creating $name ($cron)..."
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" --schedule="$cron" --time-zone="UTC" \
      --uri="$URI" --http-method=POST \
      --message-body="$BODY" \
      --headers="Content-Type=application/json" \
      --oidc-service-account-email="$OIDC_SA" \
      --oidc-token-audience="$OIDC_AUD" \
      --attempt-deadline="$DEADLINE" \
      --description="$description" \
      --project="$PROJECT_ID" --quiet
  fi
}

_upsert_betting_job "mlb-betting-afternoon" "$AFTERNOON_CRON" \
  "Scoring /run paired to the afternoon SGO snapshot (catches evening-game props)"
_upsert_betting_job "mlb-betting-pregame" "$PREGAME_CRON" \
  "Scoring /run paired to the pregame SGO snapshot (catches late-confirmed lineups)"

echo ""
echo "=== Done ==="
echo "Scoring now runs 4x/day, ~5 min after each odds snapshot:"
echo "  mlb-betting-morning   (existing)"
echo "  mlb-betting-afternoon $AFTERNOON_CRON  (new)"
echo "  mlb-betting-evening   (existing, template)"
echo "  mlb-betting-pregame   $PREGAME_CRON  (new)"
echo ""
echo "Dedup is strict first-wins -- extra runs only add new markets."
echo ""
echo "Verify:   gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
echo "Trigger:  gcloud scheduler jobs run mlb-betting-afternoon --location=$REGION --project=$PROJECT_ID"
