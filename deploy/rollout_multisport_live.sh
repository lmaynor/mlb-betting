#!/usr/bin/env bash
# rollout_multisport_live.sh -- one gated pass over RUNBOOKS.md s7-s8 (multi-sport rollout).
#
# What it does, in order (each step is independently skippable and every mutating step honours DRY_RUN):
#   1. BUILD      bash deploy/build_all_images.sh           (skip with SKIP_BUILD=1; it has its own clean-tree/origin-main guards)
#   2. V4 CHECK   read-only: compares every Cloud Run Job's LATEST execution image digest and the service's serving revision(s)
#                 against the set of digests known to contain bets.sport + idx_bets_dedup_v4, then prints whether
#                 `DROP INDEX IF EXISTS idx_bets_dedup_v3;` is safe. THIS SCRIPT NEVER DROPS THE INDEX: Cloud SQL's socket only
#                 exists inside Cloud Run (CONTEXT.md s5), so the DROP is a manual `gcloud sql connect` step (RUNBOOKS.md s7a).
#   3. SIGNALS    deploy/setup_props_alerts.sh, then give ev-nfl-props-sweep the bot URL + secret (daily thread under #nfl-card).
#   4. NHL        RESUME_NHL=1: resume odds-bank-nhl-* FIRST, then props-alerts-nhl-*, and print their STATE table.
#                 Resuming banking SPENDS shared ParlayAPI credits (pool is 20k/mo; the ledger caps NHL at its own budget).
#   5. BP BACKFILL START_BP_BACKFILL=1: provision bp-backfill-{nfl,nhl,nba} (deploy/setup_backfill_bettingpros_props.sh) and
#                 execute each --async. A job that already has a RUNNING execution is SKIPPED (never double-crawl).
#
# Safety:
#   DRY_RUN=1      print every mutating command instead of running it (read-only gcloud calls still run)
#   ASSUME_YES=1   skip the single y/N confirmation shown before the first mutating step
#   Nothing here touches Discord, the DB, or any ParlayAPI endpoint directly.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/rollout_multisport_live.sh                  # build + checks + alerts
#   PROJECT_ID=... DRY_RUN=1 RESUME_NHL=1 START_BP_BACKFILL=1 bash deploy/rollout_multisport_live.sh
#   PROJECT_ID=... SKIP_BUILD=1 RESUME_NHL=1 bash deploy/rollout_multisport_live.sh
# V4_MIN_REVISION (default 308): service revisions numbered below this are not trusted as post-v4 (00308-tas was verified).
# Extra v4-known digests (e.g. an image you verified by hand): V4_EXTRA_DIGESTS="sha256:abc... sha256:def..."
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mlb-betting}"
DRY_RUN="${DRY_RUN:-0}"
cd "$(dirname "$0")/.."

CONFIRMED=0
say()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

confirm_once() {
  [ "$CONFIRMED" = "1" ] && return 0
  if [ "$DRY_RUN" = "1" ] || [ "${ASSUME_YES:-0}" = "1" ]; then CONFIRMED=1; return 0; fi
  printf 'About to run MUTATING steps against project %s (%s). Continue? [y/N] ' "$PROJECT_ID" "$REGION"
  read -r ans
  case "$ans" in y|Y|yes|YES) CONFIRMED=1 ;; *) echo "Aborted."; exit 1 ;; esac
}

# run <cmd...>: a mutating command. Printed (not run) under DRY_RUN.
run() {
  if [ "$DRY_RUN" = "1" ]; then printf 'DRY_RUN> %s\n' "$*"; return 0; fi
  confirm_once
  "$@"
}

digest_of() { grep -oE 'sha256:[0-9a-f]{64}' | head -1 || true; }

# ---- 1. build ---------------------------------------------------------------------------------------------------------
BUILT=0
if [ "${SKIP_BUILD:-0}" = "1" ]; then
  say "1/5 build: SKIP_BUILD=1 -- not building"
else
  say "1/5 build: deploy/build_all_images.sh (guards: clean tree, HEAD == origin/main)"
  if [ "$DRY_RUN" = "1" ]; then
    DRY_RUN=1 PROJECT_ID="$PROJECT_ID" bash deploy/build_all_images.sh
  else
    confirm_once
    PROJECT_ID="$PROJECT_ID" bash deploy/build_all_images.sh
    BUILT=1
  fi
fi

# ---- 2. v4 readiness for DROP INDEX idx_bets_dedup_v3 -----------------------------------------------------------------
say "2/5 v4 check: is every consumer of the bets table on a post-v4 image? (read-only)"
KNOWN=" ${V4_EXTRA_DIGESTS:-} "
# (a) what the service serves now: a revision >= the one verified post-v4 (mlb-betting-00308-tas) is the reference.
SVC_DIGESTS=""
for rev in $(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" \
               --format='value(status.traffic.revisionName)' 2>/dev/null | tr ';,' '  '); do
  d="$(gcloud run revisions describe "$rev" --region="$REGION" --project="$PROJECT_ID" --format=yaml 2>/dev/null | digest_of)"
  n="$(printf '%s' "$rev" | sed -nE 's/.*-0*([0-9]+)-[a-z0-9]+$/\1/p')"
  if [ -n "$n" ] && [ "$n" -lt "${V4_MIN_REVISION:-308}" ]; then
    echo "  service revision $rev is OLDER than the verified post-v4 revision (${V4_MIN_REVISION:-308}): not counted as v4"
    continue
  fi
  echo "  service revision $rev serves ${d:-<unknown digest>}"
  SVC_DIGESTS="$SVC_DIGESTS $d"
done
SVC_COUNT="$(printf '%s\n' $SVC_DIGESTS | grep -c sha256 || true)"
# (b) after a build THIS run, the four tags are one digest built from this checkout (which carries v4).
if [ "$BUILT" = "1" ]; then
  for repo in mlb-betting mlb-betting-feeds; do
    d="$(gcloud container images describe "gcr.io/${PROJECT_ID}/${repo}:latest" --project="$PROJECT_ID" --format=yaml 2>/dev/null | digest_of)"
    [ -n "$d" ] && KNOWN="$KNOWN $d "
  done
fi
# The service's serving digest counts as v4-known ONLY when this checkout is itself post-v4 (it is: the v4 code is in main).
KNOWN="$KNOWN $SVC_DIGESTS "
STALE=0; UNKNOWN=0; TOTAL=0
printf '  %-34s %-12s %s\n' JOB LAST_EXEC IMAGE_DIGEST
for J in $(gcloud run jobs list --region="$REGION" --project="$PROJECT_ID" --format='value(metadata.name)' 2>/dev/null); do
  TOTAL=$((TOTAL + 1))
  E="$(gcloud run jobs executions list --job="$J" --region="$REGION" --project="$PROJECT_ID" --limit=1 --format='value(metadata.name)' 2>/dev/null || true)"
  if [ -z "$E" ]; then printf '  %-34s %-12s %s\n' "$J" "(none)" "never executed -> resolves the current tag at first run"; continue; fi
  D="$(gcloud run jobs executions describe "$E" --region="$REGION" --project="$PROJECT_ID" --format=yaml 2>/dev/null | digest_of)"
  if [ -z "$D" ]; then
    UNKNOWN=$((UNKNOWN + 1)); printf '  %-34s %-12s %s\n' "$J" "${E##*-}" "UNKNOWN (no resolved digest in the execution spec)"
  elif [[ "$KNOWN" == *" $D "* ]]; then
    printf '  %-34s %-12s %s ok\n' "$J" "${E##*-}" "${D:0:19}"
  else
    STALE=$((STALE + 1)); printf '  %-34s %-12s %s STALE (not a known post-v4 digest)\n' "$J" "${E##*-}" "${D:0:19}"
  fi
done
echo "  jobs checked: $TOTAL, stale: $STALE, unknown: $UNKNOWN, service revisions with a digest: $SVC_COUNT"
if [ "$STALE" = "0" ] && [ "$UNKNOWN" = "0" ] && [ "$SVC_COUNT" -ge 1 ]; then
  echo "  VERDICT: every job's last execution and the serving revision(s) are on a known post-v4 digest."
  echo "           DROP INDEX IF EXISTS idx_bets_dedup_v3;  is safe to run (manually; this script will not run it)."
  echo "           Caveat: a job's NEXT execution resolves its tag again, so a job still pointed at an old repo could regress;"
  echo "           the table above only proves the LAST execution."
else
  echo "  VERDICT: NOT SAFE to drop idx_bets_dedup_v3 yet -- resolve the STALE/UNKNOWN rows above (re-run those jobs on the"
  echo "           new image, or pass V4_EXTRA_DIGESTS for an image you verified by hand), then re-run this check."
fi
echo "  Manual step (RUNBOOKS.md s7a): gcloud sql connect, then  DROP INDEX IF EXISTS idx_bets_dedup_v3;"

# ---- 3. props signals -> a daily thread under each sport's card channel (via the bot) ----------------------------------------
# The sweeps post into ONE thread per day under #nfl-card / #nhl-card / #nba-card through beezy-bot (no channels of their own).
# They need the bot URL + secret. DISCORD_WEBHOOK_ALERTS (if that secret exists) is only the bot-down fallback.
say "3/5 props signals: bot access for the sweeps"
BOT_URL="${BEEZY_BOT_URL:-https://beezy-discord-bot-628109313129.us-central1.run.app}"
run env PROJECT_ID="$PROJECT_ID" bash deploy/setup_props_alerts.sh
if gcloud run jobs describe ev-nfl-props-sweep --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  run gcloud run jobs update ev-nfl-props-sweep --region="$REGION" --project="$PROJECT_ID" \
    --update-secrets=BEEZY_BOT_SECRET=beezy-bot-secret:latest --update-env-vars="BEEZY_BOT_URL=${BOT_URL}"
  if gcloud secrets describe discord-webhook-alerts --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    run gcloud run jobs update ev-nfl-props-sweep --region="$REGION" --project="$PROJECT_ID" \
      --update-secrets=DISCORD_WEBHOOK_ALERTS=discord-webhook-alerts:latest
  fi
else
  warn "job ev-nfl-props-sweep not found; skipped giving it bot access"
fi

# ---- 4. resume NHL --------------------------------------------------------------------------------------------------------
if [ "${RESUME_NHL:-0}" = "1" ]; then
  say "4/5 NHL: resume banking FIRST (the alerts sweep refuses a banked snapshot older than 45 min), then alerts"
  warn "resuming odds-bank-nhl-* spends SHARED ParlayAPI credits (20k/mo pool; the per-sport ledger caps NHL)."
  for PAT in '^odds-bank-nhl-' '^props-alerts-nhl-'; do
    for S in $(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --format='value(ID.basename())' | grep -E "$PAT" || true); do
      run gcloud scheduler jobs resume "$S" --location="$REGION" --project="$PROJECT_ID"
    done
  done
  echo "  scheduler STATE (must read ENABLED after a real run):"
  gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" \
    --filter='name~odds-bank-nhl OR name~props-alerts-nhl' --format='table(ID.basename():label=JOB,state,schedule)' || true
else
  say "4/5 NHL: RESUME_NHL not set -- schedulers left as they are"
fi

# ---- 5. BettingPros backfill --------------------------------------------------------------------------------------------
if [ "${START_BP_BACKFILL:-0}" = "1" ]; then
  say "5/5 BettingPros backfill: provision + execute --async (skips any job with a RUNNING execution)"
  run env PROJECT_ID="$PROJECT_ID" REGION="$REGION" bash deploy/setup_backfill_bettingpros_props.sh
  for SPORT in nfl nhl nba; do
    JOB="bp-backfill-$SPORT"
    # Fail CLOSED: if the running-execution check itself errors, treat the job as running and skip it.
    if ! RUNNING="$(gcloud run jobs executions list --job="$JOB" --region="$REGION" --project="$PROJECT_ID" \
                    --filter='status.runningCount>0' --format='value(metadata.name)' 2>/dev/null)"; then
      warn "$JOB: could not check for running executions; skipping (start it by hand)"; continue
    fi
    if [ -n "$RUNNING" ]; then echo "  $JOB already running ($RUNNING) -- skipped"; continue; fi
    run gcloud run jobs execute "$JOB" --region="$REGION" --project="$PROJECT_ID" --async
  done
else
  say "5/5 BettingPros backfill: START_BP_BACKFILL not set -- skipped"
fi

say "done$( [ "$DRY_RUN" = "1" ] && echo ' (DRY_RUN: nothing was changed)' )"
