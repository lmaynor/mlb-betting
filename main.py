"""
Cloud Run entrypoint v2.

Exposes a Flask HTTP server. Cloud Scheduler POSTs to /run with a JSON
body specifying which systems to run:

    {"systems": ["NRFI", "HR", "F5", "K"], "run_type": "morning"}

Run types:
    morning  — nightly data refresh + predictions + bet signals (default)
    evening  — predictions + bet signals only (no data refresh)
    data     — data refresh only, no predictions

Health check:
    GET /healthz  → 200 OK

Environment variables required:
    MLB_GCS_BUCKET          GCS bucket name
    MLB_DB_URL              Cloud SQL connection string
    DISCORD_WEBHOOK_URL     Discord webhook (or per-system variants)
    GCP_PROJECT             GCP project id
    MLB_BASE_DATA           (optional, for local dev)
"""
import os
import sys
import json
import logging
import traceback
from datetime import date

from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VALID_SYSTEMS = {"NRFI", "HR", "F5", "K"}


def _run_system(system: str, run_type: str, run_date: str) -> dict:
    """Dispatch to the correct system runner. Returns result dict."""
    try:
        if system == "NRFI":
            from runners.run_nrfi import run
        elif system == "HR":
            from runners.run_hr import run
        elif system == "F5":
            from runners.run_f5 import run
        elif system == "K":
            from runners.run_k import run
        else:
            return {"system": system, "status": "error", "error": "unknown system"}

        result = run(run_type=run_type, run_date=run_date)
        return {"system": system, "status": "ok", **result}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"{system} runner failed:\n{tb}")

        # Notify Discord of the failure
        try:
            from mlb_core.notify.discord import post_error
            post_error(system, f"Runner crashed:\n```\n{tb[:1500]}\n```", run_date)
        except Exception:
            pass

        return {"system": system, "status": "error", "error": str(e)}


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/run", methods=["POST"])
def run_handler():
    body = request.get_json(silent=True) or {}
    systems  = body.get("systems", list(VALID_SYSTEMS))
    run_type = body.get("run_type", "morning")
    run_date = body.get("run_date", date.today().isoformat())

    # Validate
    unknown = set(systems) - VALID_SYSTEMS
    if unknown:
        return jsonify({"error": f"Unknown systems: {unknown}"}), 400

    logger.info(f"Starting run | systems={systems} type={run_type} date={run_date}")

    results = []
    for system in systems:
        logger.info(f"Running {system}...")
        result = _run_system(system, run_type, run_date)
        results.append(result)
        status = result.get("status")
        logger.info(f"{system} finished: {status}")

    errors = [r for r in results if r["status"] == "error"]
    http_status = 207 if errors else 200   # 207 = multi-status (partial success)

    return jsonify({"results": results, "date": run_date}), http_status

@app.route("/build-features", methods=["POST"])
def build_features_handler():
    from datetime import date
    body     = request.get_json(silent=True) or {}
    system   = body.get("system", "HR")
    run_date = body.get("run_date", date.today().isoformat())
    if system == "HR":
        from runners.build_hr_features import run
        result = run(run_date=run_date)
    else:
        result = {"status": "error", "error": f"Unknown system: {system}"}
    return jsonify(result), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
