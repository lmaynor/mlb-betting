#!/usr/bin/env bash
# pull_ops_logs.sh — pull today's monitor-ops logs and re-run checks locally.
#
# Usage:
#   chmod +x pull_ops_logs.sh
#   ./pull_ops_logs.sh
#
# Optional: pass a date to pull logs for a different day
#   ./pull_ops_logs.sh 2026-05-24

set -euo pipefail

PROJECT="concrete-crow-445205-m4"
REGION="us-central1"
SERVICE="mlb-betting"
TODAY="${1:-$(date +%Y-%m-%d)}"
TOMORROW=$(date -d "$TODAY + 1 day" +%Y-%m-%d 2>/dev/null || date -v+1d -j -f "%Y-%m-%d" "$TODAY" +%Y-%m-%d)

echo "========================================"
echo " MLB Ops Log Puller — $TODAY"
echo "========================================"
echo ""

# ── 1. Last monitor-ops Cloud Scheduler run status ───────────────────────────
echo "=== Scheduler: mlb-monitor-ops last run ==="
gcloud scheduler jobs describe mlb-monitor-ops \
  --location="$REGION" \
  --project="$PROJECT" \
  --format="table(name,state,lastAttemptTime,status.code,status.message)" \
  2>/dev/null || echo "(gcloud scheduler unavailable)"
echo ""

# ── 2. All scheduler jobs — flag any with non-zero status ────────────────────
echo "=== All scheduler jobs — failures only ==="
gcloud scheduler jobs list \
  --location="$REGION" \
  --project="$PROJECT" \
  --format="table(name.basename(),state,lastAttemptTime,status.code)" \
  2>/dev/null | awk 'NR==1 || ($4 != "" && $4 != "0")' || echo "(none or gcloud unavailable)"
echo ""

# ── 3. Cloud Run logs — monitor-ops invocations today ────────────────────────
echo "=== Cloud Run logs: monitor-ops ($TODAY) ==="
gcloud logging read \
  "resource.type=cloud_run_revision \
   resource.labels.service_name=$SERVICE \
   resource.labels.location=$REGION \
   textPayload:\"monitor_ops\" \
   timestamp>=\"${TODAY}T00:00:00Z\" \
   timestamp<\"${TOMORROW}T00:00:00Z\"" \
  --project="$PROJECT" \
  --limit=100 \
  --order=asc \
  --format="value(timestamp,textPayload)" \
  2>/dev/null || echo "(gcloud logging unavailable)"
echo ""

# ── 4. Cloud Run logs — any ERROR level today ────────────────────────────────
echo "=== Cloud Run ERROR logs ($TODAY) ==="
gcloud logging read \
  "resource.type=cloud_run_revision \
   resource.labels.service_name=$SERVICE \
   resource.labels.location=$REGION \
   severity>=ERROR \
   timestamp>=\"${TODAY}T00:00:00Z\" \
   timestamp<\"${TOMORROW}T00:00:00Z\"" \
  --project="$PROJECT" \
  --limit=50 \
  --order=asc \
  --format="value(timestamp,severity,textPayload,jsonPayload.message)" \
  2>/dev/null || echo "(gcloud logging unavailable)"
echo ""

# ── 5. Re-run monitor-ops via the live API to get fresh failure list ──────────
echo "=== Re-running /monitor-ops via live API ==="
API_URL=$(gcloud run services describe "$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --format="value(status.url)" 2>/dev/null || echo "https://mlb-betting-628109313129.us-central1.run.app")

API_KEY=$(gcloud secrets versions access latest \
  --secret="site-api-key" \
  --project="$PROJECT" 2>/dev/null || echo "${BETTING_API_KEY:-}")

if [ -n "$API_KEY" ]; then
  curl -s -X POST "$API_URL/monitor-ops" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{}' | python3 -m json.tool
else
  echo "  (no API key — set BETTING_API_KEY env var or ensure gcloud secrets access works)"
  echo "  Run manually:  curl -X POST $API_URL/monitor-ops -H 'X-API-Key: YOUR_KEY'"
fi
echo ""

echo "========================================"
echo " Done. Paste the output above to debug."
echo "========================================"
