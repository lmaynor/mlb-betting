#!/usr/bin/env bash
# deploy_service.sh — full build + deploy + smoke-test.
# Always preserves the Cloud SQL binding.
#
# Usage: ./deploy/deploy_service.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
REGION="us-central1"
SERVICE="mlb-betting"
INSTANCE="${PROJECT_ID}:${REGION}:mlb-betting-db"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}:latest"
SERVICE_URL="https://mlb-betting-628109313129.us-central1.run.app"


echo "==> 0. Stamp CONTEXT.md"
python3 - << 'PYEOF'
from pathlib import Path
from datetime import datetime
import zoneinfo, re
cst = zoneinfo.ZoneInfo("America/Chicago")
ts = datetime.now(cst).strftime("%Y-%m-%d %H:%M CST")
p = Path("CONTEXT.md")
txt = p.read_text()
txt = re.sub(r"_Last updated:.*?_", f"_Last updated: {ts}_", txt)
p.write_text(txt)
print(f"  CONTEXT.md stamped: {ts}")
PYEOF
git add CONTEXT.md
git commit -m "docs: update CONTEXT.md timestamp" --allow-empty
git push

echo "==> 0.5. Run tests"
python3 -m compileall -q mlb_core/ runners/ main.py
pytest tests/ -q
if [ $? -ne 0 ]; then
  echo "Tests failed -- aborting deploy"
  exit 1
fi

echo "==> 1. Build"
gcloud builds submit --tag="$IMAGE" --project="$PROJECT_ID"

echo "==> 2. Deploy (preserves --add-cloudsql-instances)"
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --image="$IMAGE" \
  --add-cloudsql-instances="$INSTANCE" \
  --project="$PROJECT_ID"

NEW_REV=$(gcloud run services describe "$SERVICE" --region="$REGION" \
            --format="value(status.latestReadyRevisionName)" --project="$PROJECT_ID")
echo "==> New revision: $NEW_REV"

echo "==> 3. Smoke test via proxy"
pkill -f "run services proxy" 2>/dev/null || true
sleep 1
gcloud run services proxy "$SERVICE" --region="$REGION" --port=8081 \
  --project="$PROJECT_ID" >/dev/null 2>&1 &
PROXY_PID=$!
sleep 4
trap "kill $PROXY_PID 2>/dev/null || true" EXIT

echo "  /healthz ..."
curl -fs http://localhost:8081/healthz >/dev/null

echo "  /monitor-ops ..."
curl -fs -X POST http://localhost:8081/monitor-ops -o /tmp/monitor-ops.json
python3 -c "import json; d=json.load(open('/tmp/monitor-ops.json')); print('  healthy=', d.get('healthy'))"

echo "==> Deploy complete: $NEW_REV"
