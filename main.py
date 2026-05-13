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
    SGO_API_KEY             SportsGameOdds API key
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
    elif system == "NRFI":
        from runners.build_nrfi_features import run
        result = run(run_date=run_date)
    elif system == "F5":
        from runners.build_f5_features import run
        result = run(run_date=run_date)
    elif system == "K":
        from runners.build_k_features import run
        result = run(run_date=run_date)
    else:
        result = {"status": "error", "error": f"Unknown system: {system}"}
    return jsonify(result), 200


@app.route("/snapshot-odds", methods=["POST"])
def snapshot_odds_handler():
    """Trigger one SGO slate fetch and write the snapshot to GCS.

    Called by Cloud Scheduler twice daily (15:00 and 21:00 UTC).
    Costs ~N MLB games' worth of SGO objects per call.
    """
    body = request.get_json(silent=True) or {}
    run_date = body.get("run_date", date.today().isoformat())
    try:
        from runners.snapshot_odds import run as snapshot_run
        result = snapshot_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"snapshot-odds failed:\n{tb}")
        try:
            from mlb_core.notify.discord import post_error
            post_error("SGO", f"Snapshot crashed:\n```\n{tb[:1500]}\n```", run_date)
        except Exception:
            pass
        return jsonify({"status": "error", "error": str(e)}), 500
    http_status = 200 if result.get("status") == "ok" else 500
    return jsonify(result), http_status


@app.route("/settle", methods=["POST"])
def settle_handler():
    """Settle yesterday's bets from GCS scoring + Statcast sources.

    Called by Cloud Scheduler nightly at 09:00 UTC (after Statcast refresh).
    Can also be triggered manually with {"settle_date": "YYYY-MM-DD"} to
    re-settle a specific date.
    """
    body = request.get_json(silent=True) or {}
    settle_date = body.get("settle_date")  # optional; defaults to yesterday
    try:
        from runners.settle_bets import run as settle_run
        result = settle_run(settle_date=settle_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"settle failed:\n{tb}")
        try:
            from mlb_core.notify.discord import post_error
            post_error("SETTLE", f"Settlement crashed:\n```\n{tb[:1500]}\n```",
                       settle_date or date.today().isoformat())
        except Exception:
            pass
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify(result), 200


@app.route("/monitor", methods=["POST"])
def monitor_handler():
    """Check rolling performance for all systems. Post Discord alerts if degraded.
    Called by Cloud Scheduler at 09:30 UTC daily (30 min after settle).
    """
    body = request.get_json(silent=True) or {}
    run_date = body.get("run_date", date.today().isoformat())
    try:
        from runners.monitor_performance import run as monitor_run
        result = monitor_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"monitor failed:\n{tb}")
        try:
            from mlb_core.notify.discord import post_error
            post_error("MONITOR", f"Monitor crashed:\n```\n{tb[:1500]}\n```", run_date)
        except Exception:
            pass
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify(result), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
