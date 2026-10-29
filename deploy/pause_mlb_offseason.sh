#!/usr/bin/env bash
# pause_mlb_offseason.sh -- pause all MLB Cloud Scheduler jobs for the offseason (Oct 29 - March 19).
# Resumes on March 20, 4 days before Opening Day.
#
# NOTE: Multi-sport tweet jobs (mlb-tweet-picks-schedule, mlb-tweet-recap-schedule,
# mlb-tweet-reply-ammo-schedule) are EXCLUDED and remain ENABLED because they
# service NFL, NBA, and NHL.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/pause_mlb_offseason.sh
#   DRY_RUN=1 PROJECT_ID=concrete-crow-445205-m4 bash deploy/pause_mlb_offseason.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
DRY_RUN="${DRY_RUN:-0}"

# Suffixes/names of multi-sport jobs that MUST stay active for NFL/NBA/NHL:
EXCLUDE_REGEX='^(mlb-tweet-picks-schedule|mlb-tweet-recap-schedule|mlb-tweet-reply-ammo-schedule|mlb-settle)$'

echo "=== Pausing MLB Scheduler Jobs for Off-Season (Project: $PROJECT_ID, Region: $REGION) ==="

PAUSED_COUNT=0
SKIPPED_COUNT=0

# Fetch all jobs and their states in one call
while IFS=$'\t' read -r job state; do
  [ -z "$job" ] && continue

  if [[ "$job" =~ ^mlb- || "$job" =~ ^parlay-accum-mlb- ]]; then
    if [[ "$job" =~ $EXCLUDE_REGEX ]]; then
      echo "KEEPING ENABLED (Multi-sport): $job"
      continue
    fi

    if [ "$state" = "PAUSED" ]; then
      echo "ALREADY PAUSED: $job"
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    if [ "$DRY_RUN" = "1" ]; then
      echo "DRY_RUN> Would pause: $job"
    else
      echo "PAUSING: $job ..."
      gcloud scheduler jobs pause "$job" --location="$REGION" --project="$PROJECT_ID" --quiet
    fi
    PAUSED_COUNT=$((PAUSED_COUNT + 1))
  fi
done < <(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename(),state)')

echo ""
echo "=== Done ==="
echo "Paused: $PAUSED_COUNT | Already Paused: $SKIPPED_COUNT"
if [ "$DRY_RUN" = "1" ]; then
  echo "(DRY_RUN mode: no actual changes made)"
fi
