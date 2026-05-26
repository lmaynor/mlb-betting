#!/usr/bin/env bash
# fix_ops_issues_2.sh — round 2 remediation.
#
# Addresses the 3 issues still showing after fix_ops_issues.sh:
#   1. mlb-settle code=13: re-trigger scheduler now (min-instances=1 is set)
#   2. BATTER_HITS sentinel: redeploy + trigger build
#   3. HR stuck bets: show which game_pks, check MLB API, void if postponed
#
# Run from Cloud Shell:
#   chmod +x fix_ops_issues_2.sh && ./fix_ops_issues_2.sh

set -euo pipefail

PROJECT="concrete-crow-445205-m4"
REGION="us-central1"
SERVICE="mlb-betting"
SERVICE_URL="https://mlb-betting-628109313129.us-central1.run.app"

API_KEY=$(gcloud secrets versions access latest \
  --secret="site-api-key" --project="$PROJECT")

echo "========================================"
echo " Ops Remediation Round 2 — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================"
echo ""

# ── Fix 1: Re-trigger mlb-settle scheduler to clear code=13 ──────────────────
echo "[ 1/3 ] Re-triggering mlb-settle scheduler to clear code=13..."
gcloud scheduler jobs run mlb-settle \
  --location="$REGION" \
  --project="$PROJECT"
echo "  Waiting 15s for settle to complete..."
sleep 15
# Check new status
NEW_CODE=$(gcloud scheduler jobs describe mlb-settle \
  --location="$REGION" --project="$PROJECT" \
  --format="value(status.code)")
echo "  mlb-settle status.code now: '${NEW_CODE:-0 (ok)}'"
echo ""

# ── Fix 2: Redeploy with storage.py sentinel fix, then trigger BATTER_HITS build
echo "[ 2/3 ] Redeploying to pick up storage.py sentinel path fix..."
PROJECT_ID="$PROJECT" ./deploy/deploy_service.sh
echo ""
echo "  Triggering BATTER_HITS feature build..."
curl -s -X POST "$SERVICE_URL/build-features" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"system": "BATTER_HITS"}' | python3 -m json.tool 2>/dev/null || \
curl -s -X POST "$SERVICE_URL/build-all-features" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool 2>/dev/null || echo "  (check /build endpoint name in main.py)"
echo ""

# ── Fix 3: Identify and void stuck HR bets ───────────────────────────────────
echo "[ 3/3 ] Checking stuck HR bets..."
echo ""

# Query the DB directly to show which game_pks are stuck
python3 - <<'PYEOF'
import os, sys
from datetime import date, timedelta

try:
    from mlb_core.tracking.bet_tracker import _make_engine
    from sqlalchemy import text

    cutoff = (date.today() - timedelta(days=3)).isoformat()
    engine = _make_engine(db_path="unused")
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, game_date, game_pk, bet_type, odds, stake, player,
                       away_team, home_team
                FROM bets
                WHERE result IS NULL
                  AND system = 'HR'
                  AND game_date <= :cutoff
                ORDER BY game_date
            """),
            {"cutoff": cutoff},
        ).fetchall()

    if not rows:
        print("  ✅ No stuck HR bets found in DB")
        sys.exit(0)

    print(f"  Found {len(rows)} stuck HR bet(s):")
    print(f"  {'ID':<6} {'DATE':<12} {'GAME_PK':<10} {'PLAYER':<25} {'MATCHUP':<16} {'ODDS':<6} {'STAKE'}")
    print(f"  {'-'*90}")
    game_pks = set()
    for r in rows:
        matchup = f"{r.away_team}@{r.home_team}"
        print(f"  {r.id:<6} {str(r.game_date):<12} {r.game_pk:<10} {str(r.player or ''):<25} {matchup:<16} {r.odds:<6} {r.stake:.2f}u")
        game_pks.add(int(r.game_pk))

    print(f"\n  Unique game_pks: {sorted(game_pks)}")
    print(f"\n  Checking MLB Stats API for game status...")

    import urllib.request, json
    for gp in sorted(game_pks):
        try:
            url = f"https://statsapi.mlb.com/api/v1/game/{gp}/linescore"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            # Game status is in the feed, try schedule endpoint instead
            url2 = f"https://statsapi.mlb.com/api/v1.1/game/{gp}/feed/live?fields=gameData,status,abstractGameState,detailedState,codedGameState"
            with urllib.request.urlopen(url2, timeout=5) as resp2:
                data2 = json.loads(resp2.read())
            status = data2.get("gameData", {}).get("status", {})
            state  = status.get("abstractGameState", "?")
            detail = status.get("detailedState", "?")
            print(f"    game_pk={gp}: {state} / {detail}")
        except Exception as e:
            print(f"    game_pk={gp}: API error — {e}")

    print()
    print("  If games show 'Postponed' or 'Cancelled', void them:")
    ids = [str(r.id) for r in rows]
    print(f"  python3 void_stuck_bets.py {' '.join(ids)}")

except Exception as e:
    print(f"  ❌ DB error: {e}")
    print("  Check DATABASE_URL / DB env vars are set")
PYEOF

echo ""

# ── Verify ────────────────────────────────────────────────────────────────────
echo "[ ✓ ] Re-running /monitor-ops to verify..."
sleep 2
curl -s -X POST "$SERVICE_URL/monitor-ops" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool

echo ""
echo "========================================"
echo " Done."
echo "========================================"
