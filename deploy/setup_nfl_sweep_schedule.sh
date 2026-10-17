#!/usr/bin/env bash
# setup_nfl_sweep_schedule.sh -- run the NFL props sweep (job ev-nfl-props-sweep) on a schedule, one cycle per firing.
#
# WHY: the job was a manual, hard-coded-window loop (--until 2026-10-05). Last run 2026-10-15, so beezy.fyi/picks/nfl had
# projections but no lines ("no NFL raw prop payloads banked for today/tomorrow").
#
# Each firing = ONE cycle: slate (1 credit) + 5 credits per game that kicks off within 40 h (--horizon-hours default).
# That horizon is what makes the weekday cadence cheap: Tue-Wed nothing is inside it (1 credit per firing), the Thursday
# game enters Wed night, and the Sunday slate enters from Fri night (first kickoff Sun ~08:30 CT). Cost per firing is
# 1 + 5 x games, ~76 credits with a full 15-game Sunday slate; roughly 2.2-2.5k credits a week, ~10k a month, from the
# SHARED ParlayAPI pool (MLB snapshots and the NHL/NBA bankers draw from it too). --max-credits caps ONE firing.
#
# Schedules (America/Chicago, so kickoff-relative cadence survives DST):
#   Mon-Sat  every 2 h                              0 */2 * * 1-6
#   Sun      every 30 min 06:00-11:30 (pre-kickoff) */30 6-11 * * 0
#   Sun      00,02,04 then hourly 12:00-20:00, 22:00 0 0,2,4,12-20,22 * * 0
#
# max-retries=0: a retried cycle would re-spend credits for the same snapshot.
# The startup Discord ping only fires for looping runs once the ev image carries the matching ev/sweep_nfl_props.py change;
# until then every firing posts "NFL props SHADOW sweep running" to the admin channel.
#
# Usage:  PROJECT_ID=concrete-crow-445205-m4 bash deploy/setup_nfl_sweep_schedule.sh
#         PAUSE=1 PROJECT_ID=... bash deploy/setup_nfl_sweep_schedule.sh     # pause all three (offseason / emergency)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
JOB="ev-nfl-props-sweep"
SCHED_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
MAX_CREDITS="${MAX_CREDITS:-150}"

SCHEDULES=(
  "weekly|0 */2 * * 1-6"
  "sun-am|*/30 6-11 * * 0"
  "sun-rest|0 0,2,4,12-20,22 * * 0"
)

if [ "${PAUSE:-0}" = "1" ]; then
  for row in "${SCHEDULES[@]}"; do
    gcloud scheduler jobs pause "${JOB}-${row%%|*}" --location="$REGION" --project="$PROJECT_ID" --quiet
  done
  echo "NFL sweep schedulers paused."; exit 0
fi

gcloud run jobs describe "$JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null \
  || { echo "ERROR: job $JOB not found (create it per deploy/build_all_images.sh / docs/designs/multisport_ev_sweep.md)."; exit 1; }

# One cycle per execution; keeps the job's image, secrets and service account as they are.
gcloud run jobs update "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --args="-m,ev.sweep_nfl_props,--max-credits,${MAX_CREDITS}" --max-retries=0 --task-timeout=900 --quiet

# scheduler-invoker needs run.invoker ON THE JOB or every firing is PERMISSION_DENIED (code 7); see
# docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md. Idempotent.
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker" --quiet >/dev/null

URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
for row in "${SCHEDULES[@]}"; do
  IFS='|' read -r TAG CRON <<<"$row"
  SJOB="${JOB}-${TAG}"
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
echo "Verify: gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID | grep ev-nfl"
