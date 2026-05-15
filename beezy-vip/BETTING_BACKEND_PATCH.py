"""
Run this from Cloud Shell in ~/mlb-betting to add the public API endpoints.

    cd ~/mlb-betting
    cat main.py | grep -n "def run\|def settle\|def monitor\|def refresh\|def snapshot\|def build"
    # Verify the route names match what you see, then:
    python3 BETTING_BACKEND_PATCH.py
    python3 -m compileall main.py
    git diff main.py

This script patches main.py to add:
  GET /api/public/picks/today
  GET /api/public/picks
  GET /api/public/picks/recent
  GET /api/public/stats/summary
  GET /api/public/stats/overall

And adds to runners/public_api.py (new file).
"""

import os
import sys

# ---------------------------------------------------------------------------
# 1. Create runners/public_api.py
# ---------------------------------------------------------------------------

PUBLIC_API_PY = '''\
"""
Read-only query helpers for the public API endpoints in main.py.
All queries use text() wrapper -- required for pg8000 named params (CONTEXT.md ss8).
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _require_api_key(request_headers: dict, site_api_key: str) -> bool:
    """Return True if the X-API-Key header matches the configured key."""
    return request_headers.get("X-API-Key", "").strip() == site_api_key


def get_today_picks(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
            "FROM bets "
            "WHERE game_date = CURRENT_DATE AND kelly_triggered = true "
            "ORDER BY system, created_at DESC"
        )).mappings().all()
    return [dict(r) for r in rows]


def get_picks(engine, system=None, date=None, status=None, limit=50, offset=0):
    conditions = []
    params = {}

    if system:
        conditions.append("system = :system")
        params["system"] = system

    if date == "today":
        conditions.append("game_date = CURRENT_DATE")
    elif date == "yesterday":
        conditions.append("game_date = CURRENT_DATE - INTERVAL \'1 day\'")
    elif date == "last7":
        conditions.append("game_date >= CURRENT_DATE - INTERVAL \'7 days\'")
    elif date:
        conditions.append("game_date = :game_date")
        params["game_date"] = date

    if status == "pending":
        conditions.append("result IS NULL")
    elif status == "settled":
        conditions.append("result IS NOT NULL")
    elif status == "won":
        conditions.append("result = \'win\'")
    elif status == "lost":
        conditions.append("result = \'loss\'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params["limit"] = int(limit)
    params["offset"] = int(offset)

    sql = text(
        "SELECT id, system, game_date, game_pk, bet_type, player, "
        "away_team, home_team, odds, stake, model_prob, market_prob, "
        "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
        f"FROM bets {where} "
        "ORDER BY game_date DESC, created_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def get_recent_settled(engine, limit=20):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
            "FROM bets WHERE result IS NOT NULL "
            "ORDER BY game_date DESC, created_at DESC "
            "LIMIT :limit"
        ), {"limit": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


def get_summary_stats(engine):
    with engine.connect() as conn:
        by_system = conn.execute(text(
            "SELECT system, "
            "COUNT(*) AS total_bets, "
            "COUNT(*) FILTER (WHERE result = \'win\') AS wins, "
            "COUNT(*) FILTER (WHERE result = \'loss\') AS losses, "
            "COUNT(*) FILTER (WHERE result = \'push\') AS pushes, "
            "ROUND(COUNT(*) FILTER (WHERE result = \'win\')::numeric / "
            "  NULLIF(COUNT(*) FILTER (WHERE result IN (\'win\',\'loss\')), 0) * 100, 1) AS win_rate, "
            "ROUND(SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2) AS roi, "
            "ROUND(SUM(profit), 2) AS total_pnl, "
            "ROUND(AVG(model_prob - market_prob) * 100, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != \'void\' "
            "GROUP BY system ORDER BY roi DESC"
        )).mappings().all()

        overall = conn.execute(text(
            "SELECT COUNT(*) AS total_bets, "
            "ROUND(COUNT(*) FILTER (WHERE result = \'win\')::numeric / "
            "  NULLIF(COUNT(*) FILTER (WHERE result IN (\'win\',\'loss\')), 0) * 100, 1) AS win_rate, "
            "ROUND(SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2) AS roi, "
            "ROUND(AVG(model_prob - market_prob) * 100, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != \'void\'"
        )).mappings().first()

    return {
        "overall":  dict(overall) if overall else {},
        "bySystem": [dict(r) for r in by_system],
    }
'''

os.makedirs("runners", exist_ok=True)
with open("runners/public_api.py", "w") as f:
    f.write(PUBLIC_API_PY)
print("wrote runners/public_api.py")

# ---------------------------------------------------------------------------
# 2. Patch main.py -- add import + 5 routes
# ---------------------------------------------------------------------------

with open("main.py", "r") as f:
    content = f.read()

# Check for em-dashes or non-ASCII that would break str.replace
if "public_api" in content:
    print("ERROR: public_api already in main.py -- aborting to avoid double-patch")
    sys.exit(1)

# Find the last @app.route line to insert after existing routes
# We'll add the import near the top and the routes at the end before if __name__

IMPORT_LINE = "from runners.public_api import get_today_picks, get_picks, get_recent_settled, get_summary_stats, _require_api_key\n"

# Insert import after the last 'from runners' import line
import re
# Find the last 'from runners' line
last_runner_import = None
for line in content.splitlines():
    if line.startswith("from runners.") or line.startswith("import runners"):
        last_runner_import = line

if last_runner_import is None:
    print("ERROR: could not find a 'from runners.' import line in main.py")
    sys.exit(1)

content = content.replace(
    last_runner_import + "\n",
    last_runner_import + "\n" + IMPORT_LINE,
    1
)

# Add routes before the 'if __name__' block (or at end of file)
NEW_ROUTES = '''

# ---------------------------------------------------------------------------
# Public API -- read-only, authenticated with X-API-Key header
# Used by beezy.vip frontend (no direct DB access from Vercel).
# ---------------------------------------------------------------------------

SITE_API_KEY = os.environ.get("SITE_API_KEY", "")
ALLOWED_ORIGIN = "https://beezy.vip"


def _cors_headers():
    return {
        "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "X-API-Key, Content-Type",
    }


def _auth_required(request):
    if not SITE_API_KEY:
        return jsonify({"error": "SITE_API_KEY not configured"}), 500
    if not _require_api_key(dict(request.headers), SITE_API_KEY):
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.route("/api/public/picks/today", methods=["GET", "OPTIONS"])
def public_picks_today():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        picks = get_today_picks(engine)
        resp = jsonify({"picks": picks, "count": len(picks)})
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    except Exception as exc:
        logger.exception("public_picks_today failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/public/picks", methods=["GET", "OPTIONS"])
def public_picks():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        picks = get_picks(
            engine,
            system=request.args.get("system"),
            date=request.args.get("date"),
            status=request.args.get("status"),
            limit=request.args.get("limit", 50),
            offset=request.args.get("offset", 0),
        )
        resp = jsonify({"picks": picks, "count": len(picks)})
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    except Exception as exc:
        logger.exception("public_picks failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/public/picks/recent", methods=["GET", "OPTIONS"])
def public_picks_recent():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        limit = int(request.args.get("limit", 20))
        picks = get_recent_settled(engine, limit=limit)
        resp = jsonify({"picks": picks, "count": len(picks)})
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=120"
        return resp
    except Exception as exc:
        logger.exception("public_picks_recent failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/public/stats/summary", methods=["GET", "OPTIONS"])
def public_stats_summary():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        stats = get_summary_stats(engine)
        resp = jsonify(stats)
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    except Exception as exc:
        logger.exception("public_stats_summary failed")
        return jsonify({"error": str(exc)}), 500

'''

# Insert before 'if __name__' or append if not found
if 'if __name__' in content:
    content = content.replace('if __name__', NEW_ROUTES + 'if __name__', 1)
else:
    content = content + NEW_ROUTES

with open("main.py", "w") as f:
    f.write(content)

print("patched main.py -- verify with:")
print("  python3 -m compileall main.py runners/public_api.py")
print("  grep -n 'public_picks\\|public_stats\\|SITE_API_KEY' main.py")
