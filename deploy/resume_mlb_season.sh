#!/usr/bin/env bash
# resume_mlb_season.sh -- resume all MLB Cloud Scheduler jobs for the in-season (March 20).
# Opening Day is 4 days later (March 24).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/resume_mlb_season.sh
#   DRY_RUN=1 PROJECT_ID=concrete-crow-445205-m4 bash deploy/resume_mlb_season.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
DRY_RUN="${DRY_RUN:-0}"

# Suffixes/names that should remain paused (one-time or legacy jobs):
EXCLUDE_PAUSED_REGEX='^(mlb-reset-and-run-once|mlb-reset-today-bets|mlb-kalshi-rfq-recorder-demo-once|parlay-accum-mlb-.*)$'

echo "=== Resuming MLB Scheduler Jobs for Season (Project: $PROJECT_ID, Region: $REGION) ==="

RESUMED_COUNT=0
SKIPPED_COUNT=0

while IFS=$'\t' read -r job state; do
  [ -z "$job" ] && continue

  if [[ "$job" =~ ^mlb- ]]; then
    if [[ "$job" =~ $EXCLUDE_PAUSED_REGEX ]]; then
      echo "KEEPING PAUSED (Legacy/One-time): $job"
      continue
    fi

    if [ "$state" = "ENABLED" ]; then
      echo "ALREADY ENABLED: $job"
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    if [ "$DRY_RUN" = "1" ]; then
      echo "DRY_RUN> Would resume: $job"
    else
      echo "RESUMING: $job ..."
      gcloud scheduler jobs resume "$job" --location="$REGION" --project="$PROJECT_ID" --quiet
    fi
    RESUMED_COUNT=$((RESUMED_COUNT + 1))
  fi
done < <(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename(),state)')

echo ""
echo "=== Done ==="
echo "Resumed: $RESUMED_COUNT | Already Enabled: $SKIPPED_COUNT"
if [ "$DRY_RUN" = "1" ]; then
  echo "(DRY_RUN mode: no actual changes made)"
fi
