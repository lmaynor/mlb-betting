#!/usr/bin/env bash
# fix_ops_issues.sh — remediate today's 3 monitor-ops failures.
#
# Run from Cloud Shell (already authenticated):
#   chmod +x fix_ops_issues.sh && ./fix_ops_issues.sh
#
# What this does:
#   1. Sets Cloud Run min-instances=1 so settle never hits a cold-start abort
#   2. Adds 3 retry attempts to mlb-settle scheduler (60s backoff)
#   3. Manually triggers settle to clear the 3 stuck HR bets
#   4. Verifies stuck bets are gone

set -euo pipefail

PROJECT="concrete-crow-445205-m4"
REGION="us-central1"
SERVICE="mlb-betting"

SERVICE_URL=$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --project="$PROJECT" \
  --format="value(status.url)")

API_KEY=$(gcloud secrets versions access latest \
  --secret="site-api-key" --project="$PROJECT")

echo "========================================"
echo " Ops Remediation — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo " Service: $SERVICE_URL"
echo "========================================"
echo ""

# ── Fix 1: Cloud Run min-instances=1 ─────────────────────────────────────────
echo "[ 1/3 ] Setting Cloud Run min-instances=1..."
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --min-instances=1 \
  --quiet
echo "  ✅ Done — service will always keep 1 warm instance"
echo ""

# ── Fix 2: Add retry policy to mlb-settle scheduler ──────────────────────────
echo "[ 2/3 ] Adding retry policy to mlb-settle (3 attempts, 60s backoff)..."
gcloud scheduler jobs update http mlb-settle \
  --location="$REGION" \
  --project="$PROJECT" \
  --max-retry-attempts=3 \
  --min-backoff=60s \
  --max-backoff=300s \
  --quiet 2>/dev/null && echo "  ✅ Done" || echo "  ⚠️  Job update failed or job not found — may need manual setup"
echo ""

# ── Fix 3: Re-run settle to clear the 3 stuck HR bets ────────────────────────
echo "[ 3/3 ] Re-running /settle to clear stuck HR bets..."
SETTLE_RESP=$(curl -s -w "\n%{http_code}" -X POST "$SERVICE_URL/settle" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}')

HTTP_CODE=$(echo "$SETTLE_RESP" | tail -1)
BODY=$(echo "$SETTLE_RESP" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
  echo "  ✅ Settle succeeded (HTTP $HTTP_CODE)"
  echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "  ❌ Settle returned HTTP $HTTP_CODE"
  echo "$BODY"
fi
echo ""

# ── Verify: re-run monitor-ops and confirm all clear ─────────────────────────
echo "[ ✓ ] Re-running /monitor-ops to verify..."
sleep 3
VERIFY=$(curl -s -X POST "$SERVICE_URL/monitor-ops" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}')

echo "$VERIFY" | python3 -m json.tool 2>/dev/null || echo "$VERIFY"

FAILURES=$(echo "$VERIFY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('failures',[])))" 2>/dev/null || echo "?")
echo ""
if [ "$FAILURES" = "0" ]; then
  echo "  ✅ All checks pass — ops healthy"
else
  echo "  ⚠️  $FAILURES issue(s) remain — check output above"
fi

echo ""
echo "========================================"
echo " Done."
echo "========================================"
