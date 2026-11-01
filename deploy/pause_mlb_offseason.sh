#!/usr/bin/env bash
# pause_mlb_offseason.sh -- pause the MLB-only Cloud Scheduler jobs for the offseason (see mlb_core.config.is_mlb_offseason:
# the MLB cutoff is Nov 5 by default, MLB_OFFSEASON_START=MM-DD overrides it; in-season again from Mar 20).
#
# Every scheduler job THIS script pauses is stamped with a marker in its description ("[offseason-paused:YYYY-MM-DD]").
# resume_mlb_season.sh resumes ONLY marked jobs, so a job somebody paused on purpose earlier (a one-off, a legacy job, a
# credit-burning loop that was shut off) is never turned back on by the spring resume. A job that is already PAUSED and carries
# no marker is left alone and NOT claimed.
#
# KEPT ENABLED (they serve NFL / NBA / NHL, verified against the code of each job, 2026-11):
#   mlb-tweet-picks-schedule, mlb-tweet-recap-schedule, mlb-tweet-reply-ammo-schedule   multi-sport tweet drafter
#   mlb-settle                  settles NFL/NBA/NHL bets (mlb.runners.settle_bets._settle_winter_bets) and posts the recap
#   mlb-free-plays              mlb.runners.free_plays: NHL/NBA/NFL sweep outliers feed #daily-free-plays
#   mlb-monitor-ops             alerts on the NFL/NBA/NHL refresh jobs and picks-board freshness (mlb.runners.monitor_ops)
#   mlb-kalshi-rfq-recorder-daily   records NFL/NBA/NHL game + prop RFQs (scripts/kalshi_rfq_daemon.py)
# PAUSED (MLB-only code paths: sport='mlb' filters / MLB-only feeds): mlb-snapshot-*, mlb-betting-*, mlb-capture-closing,
#   mlb-odds-alert, mlb-fast-alert-*, mlb-kalshi-capture/-alert/-backfill, mlb-monitor, mlb-monitor-drift, mlb-refresh-data,
#   mlb-build-*, mlb-retrain-*, mlb-calibrate-*, mlb-track-bettingpros, mlb-weekly-survival, parlay-accum-mlb-*, ...
# If you add a scheduler job whose code touches another sport, add it to EXCLUDE_REGEX below (tests/test_offseason.py pins it).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/pause_mlb_offseason.sh
#   DRY_RUN=1 PROJECT_ID=concrete-crow-445205-m4 bash deploy/pause_mlb_offseason.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
DRY_RUN="${DRY_RUN:-0}"
MARKER_PREFIX="[offseason-paused:"

# Names of multi-sport jobs that MUST stay active for NFL/NBA/NHL:
EXCLUDE_REGEX='^(mlb-tweet-picks-schedule|mlb-tweet-recap-schedule|mlb-tweet-reply-ammo-schedule|mlb-settle|mlb-free-plays(-[a-z0-9-]+)?|mlb-monitor-ops|mlb-kalshi-rfq-recorder-daily)$'

echo "=== Pausing MLB Scheduler Jobs for Off-Season (Project: $PROJECT_ID, Region: $REGION) ==="

PAUSED_COUNT=0
SKIPPED_COUNT=0

# One call: id, state, description (tab-separated). The description carries the marker from earlier runs.
while IFS=$'\t' read -r job state desc; do
  [ -z "$job" ] && continue

  if [[ "$job" =~ ^mlb- || "$job" =~ ^parlay-accum-mlb- ]]; then
    if [[ "$job" =~ $EXCLUDE_REGEX ]]; then
      echo "KEEPING ENABLED (Multi-sport): $job"
      continue
    fi

    if [ "$state" = "PAUSED" ]; then
      echo "ALREADY PAUSED (not claimed; resume_mlb_season.sh will leave it paused unless it carries our marker): $job"
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    # Stamp BEFORE pausing: if the pause fails the marker is harmless (resume just strips it from an ENABLED job).
    base="${desc%% ${MARKER_PREFIX}*}"
    base="${base:0:440}"
    new_desc="${base:+$base }${MARKER_PREFIX}$(date -u +%F)]"
    if [ "$DRY_RUN" = "1" ]; then
      echo "DRY_RUN> Would mark and pause: $job"
    else
      echo "PAUSING: $job ..."
      gcloud scheduler jobs update http "$job" --location="$REGION" --project="$PROJECT_ID" \
        --description="$new_desc" --quiet
      gcloud scheduler jobs pause "$job" --location="$REGION" --project="$PROJECT_ID" --quiet
    fi
    PAUSED_COUNT=$((PAUSED_COUNT + 1))
  fi
done < <(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename(),state,description)')

echo ""
echo "=== Done ==="
echo "Paused: $PAUSED_COUNT | Already Paused: $SKIPPED_COUNT"
if [ "$DRY_RUN" = "1" ]; then
  echo "(DRY_RUN mode: no actual changes made)"
fi
