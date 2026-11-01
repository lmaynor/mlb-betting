#!/usr/bin/env bash
# resume_mlb_season.sh -- resume ONLY the scheduler jobs that deploy/pause_mlb_offseason.sh paused (they carry the description
# marker "[offseason-paused:YYYY-MM-DD]"). A job that was paused on purpose before (one-off, legacy, credit-burning) has no marker
# and stays paused -- the old version resumed every paused mlb-* job except a hard-coded list. Run it ~March 20, 4 days before
# Opening Day.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/resume_mlb_season.sh
#   DRY_RUN=1 PROJECT_ID=concrete-crow-445205-m4 bash deploy/resume_mlb_season.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="${REGION:-us-central1}"
DRY_RUN="${DRY_RUN:-0}"
MARKER_PREFIX="[offseason-paused:"

echo "=== Resuming MLB Scheduler Jobs paused by pause_mlb_offseason.sh (Project: $PROJECT_ID, Region: $REGION) ==="

RESUMED_COUNT=0
LEFT_COUNT=0

while IFS=$'\t' read -r job state desc; do
  [ -z "$job" ] && continue

  if [[ "$desc" != *"$MARKER_PREFIX"* ]]; then
    if [ "$state" = "PAUSED" ]; then
      echo "LEAVING PAUSED (not paused by the offseason script): $job"
      LEFT_COUNT=$((LEFT_COUNT + 1))
    fi
    continue
  fi

  # strip the marker from the description (an empty description is not accepted by every gcloud version)
  new_desc="$(printf '%s' "$desc" | sed -E 's/ ?\[offseason-paused:[0-9-]+\]//')"
  [ -z "$new_desc" ] && new_desc="Resumed by resume_mlb_season.sh"

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN> Would resume (state=$state) and clear marker: $job"
  else
    if [ "$state" = "PAUSED" ]; then
      echo "RESUMING: $job ..."
      gcloud scheduler jobs resume "$job" --location="$REGION" --project="$PROJECT_ID" --quiet
    else
      echo "ALREADY ENABLED (clearing stale marker): $job"
    fi
    gcloud scheduler jobs update http "$job" --location="$REGION" --project="$PROJECT_ID" \
      --description="$new_desc" --quiet
  fi
  RESUMED_COUNT=$((RESUMED_COUNT + 1))
done < <(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename(),state,description)')

echo ""
echo "=== Done ==="
echo "Resumed: $RESUMED_COUNT | Left paused (not ours): $LEFT_COUNT"
if [ "$DRY_RUN" = "1" ]; then
  echo "(DRY_RUN mode: no actual changes made)"
fi
