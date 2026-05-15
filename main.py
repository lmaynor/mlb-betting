"""
Cloud Run entrypoint v3.

Routes:
    GET  /healthz          Health check
    POST /run              Score all systems + post bets
    POST /build-features   Build features for one system
    POST /snapshot-odds    Fetch SGO slate → GCS
    POST /settle           Settle pending bets
    POST /refresh-data     Nightly weather + umpire master refresh
    POST /monitor          Rolling performance monitor (model health)
    POST /monitor-ops      Infrastructure health monitor (schedulers, GCS)

Environment variables required:
    MLB_GCS_BUCKET          GCS bucket name
    MLB_DB_URL              Cloud SQL connection string
    DISCORD_WEBHOOK_URL     Discord webhook (or per-system variants)
    GCP_PROJECT             GCP project id
    MLB_BASE_DATA           (optional, for local dev)
    SGO_API_KEY             SportsGameOdds API key
"""
import os
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
    body     = request.get_json(silent=True) or {}
    systems  = body.get("systems", list(VALID_SYSTEMS))
    run_type = body.get("run_type", "morning")
    run_date = body.get("run_date", date.today().isoformat())

    unknown = set(systems) - VALID_SYSTEMS
    if unknown:
        return jsonify({"error": f"Unknown systems: {unknown}"}), 400

    logger.info(f"Starting run | systems={systems} type={run_type} date={run_date}")

    results = []
    for system in systems:
        logger.info(f"Running {system}...")
        result = _run_system(system, run_type, run_date)
        results.append(result)
        logger.info(f"{system} finished: {result.get('status')}")

    errors      = [r for r in results if r["status"] == "error"]
    http_status = 207 if errors else 200

    return jsonify({"results": results, "date": run_date}), http_status


@app.route("/build-features", methods=["POST"])
def build_features_handler():
    body     = request.get_json(silent=True) or {}
    system   = body.get("system", "HR")
    run_date = body.get("run_date", date.today().isoformat())

    if system == "HR":
        from runners.build_hr_features import run
    elif system == "NRFI":
        from runners.build_nrfi_features import run
    elif system == "F5":
        from runners.build_f5_features import run
    elif system == "K":
        from runners.build_k_features import run
    else:
        return jsonify({"status": "error", "error": f"Unknown system: {system}"}), 400

    result = run(run_date=run_date)
    return jsonify(result), 200


@app.route("/build-all-features", methods=["POST"])
def build_all_features_handler():
    """Run all feature builders sequentially in dependency order.

    Dependency order: HR -> NRFI -> K -> F5 (F5 reads NRFI pitcher_start_features.csv).
    HR and K are independent of each other and NRFI.

    Body (all optional):
        systems: list[str]  -- subset to run, default all four in order
        continue_on_error: bool -- default false (fail fast)
    Returns HTTP 207 if any system errored, 200 if all clean.
    """
    import time
    body             = request.get_json(silent=True) or {}
    run_date         = body.get("run_date", date.today().isoformat())
    continue_on_err  = body.get("continue_on_error", False)
    default_order    = ["HR", "NRFI", "K", "F5"]
    systems          = body.get("systems", default_order)
    # Enforce dependency order even if caller passes a subset
    ordered = [s for s in default_order if s in [x.upper() for x in systems]]

    builders = {
        "HR":   "runners.build_hr_features",
        "NRFI": "runners.build_nrfi_features",
        "K":    "runners.build_k_features",
        "F5":   "runners.build_f5_features",
    }

    results = []
    any_error = False
    for sys_name in ordered:
        t0 = time.time()
        logger.info(f"build-all-features: starting {sys_name}")
        try:
            import importlib
            mod = importlib.import_module(builders[sys_name])
            result = mod.run(run_date=run_date)
            duration = round(time.time() - t0, 1)
            results.append({"system": sys_name, "status": "ok",
                            "duration_sec": duration, "result": result})
            logger.info(f"build-all-features: {sys_name} done in {duration}s")
        except Exception as e:
            duration = round(time.time() - t0, 1)
            tb = traceback.format_exc()
            logger.error(f"build-all-features: {sys_name} failed: {tb[:500]}")
            results.append({"system": sys_name, "status": "error",
                            "duration_sec": duration, "error": str(e)})
            any_error = True
            if not continue_on_err:
                break

    http_status = 207 if any_error else 200
    return jsonify({"status": "error" if any_error else "ok",
                    "results": results}), http_status


@app.route("/snapshot-odds", methods=["POST"])
def snapshot_odds_handler():
    body     = request.get_json(silent=True) or {}
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
    if result.get("status") == "error":
        try:
            from mlb_core.notify.discord import post_error
            post_error("SGO", f"Snapshot returned error:\n```\n{result.get('error', '?')}\n```", run_date)
        except Exception:
            pass
    return jsonify(result), http_status


@app.route("/settle", methods=["POST"])
def settle_handler():
    body        = request.get_json(silent=True) or {}
    settle_date = body.get("settle_date", None)  # optional; defaults to yesterday
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


@app.route("/refresh-data", methods=["POST"])
def refresh_data_handler():
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", date.today().isoformat())
    try:
        from mlb_core.data.weather import weather_nightly_gcs
        from mlb_core.data.umpires import umpires_nightly_gcs
        from mlb_core.config import GCS_BUCKET
        from mlb_core.data.scoring import scoring_nightly_gcs
        wx_result  = weather_nightly_gcs(GCS_BUCKET, "Weather/weather_master.csv")
        ump_result = umpires_nightly_gcs(GCS_BUCKET, "Umpires/umpscorecards_master.csv")
        scoring_nightly_gcs(GCS_BUCKET, "Scoring/scoring_master.csv")
        result = {"status": "ok"}
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"refresh-data failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

    return jsonify(result), 200


@app.route("/monitor", methods=["POST"])
def monitor_handler():
    """Rolling performance monitor — model health (ROI, hit rate)."""
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", date.today().isoformat())
    try:
        from runners.monitor_performance import run as monitor_run
        result = monitor_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"monitor failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

    return jsonify(result), 200


@app.route("/monitor-ops", methods=["POST"])
def monitor_ops_handler():
    """Infrastructure health monitor — schedulers, GCS freshness, model artifacts."""
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", date.today().isoformat())
    try:
        from runners.monitor_ops import run as monitor_ops_run
        result = monitor_ops_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"monitor-ops failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

    http_status = 200 if result.get("healthy") else 207
    return jsonify(result), http_status

@app.route("/reset-bets", methods=["POST"])
def reset_bets():
    """Delete bets by date, system, player, or game_pk. Requires at least date."""
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import BetTracker
    body    = request.get_json(silent=True) or {}
    date    = body.get("date")
    system  = body.get("system")
    player  = body.get("player")
    game_pk = body.get("game_pk")
    if not date:
        return jsonify({"error": "date is required"}), 400
    bt = BetTracker(os.environ["DB_URL"], "HR")
    where = "WHERE game_date = :date"
    params = {"date": date}
    if system:
        where += " AND system = :system"
        params["system"] = system.upper()
    if player:
        where += " AND player ILIKE :player"
        params["player"] = f"%{player}%"
    if game_pk:
        where += " AND game_pk = :game_pk"
        params["game_pk"] = int(game_pk)
    with bt.engine.begin() as conn:
        result = conn.execute(text(f"DELETE FROM bets {where}"), params)
        deleted = result.rowcount
    logger.info(f"reset-bets: deleted {deleted} rows for {body}")
    return jsonify({"deleted": deleted, "params": body})





@app.route("/dashboard", methods=["GET"])
def dashboard():
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import BetTracker
    system_filter = request.args.get("system", None)
    days = int(request.args.get("days", 7))
    bt = BetTracker(os.environ["DB_URL"], "HR")

    systems = ["HR", "NRFI", "F5", "K", "OUTS"]
    summary_rows = ""
    for sys in systems:
        with bt.engine.connect() as conn:
            r = conn.execute(text(
                "SELECT "
                "COUNT(*) FILTER (WHERE result IS NOT NULL AND kelly_triggered=true) as settled, "
                "SUM(profit) FILTER (WHERE result IS NOT NULL AND kelly_triggered=true) as pnl, "
                "AVG(edge) FILTER (WHERE kelly_triggered=true) as avg_edge, "
                "COUNT(*) FILTER (WHERE result IS NULL AND kelly_triggered=true) as pending "
                "FROM bets WHERE system=:s "
                "AND game_date >= TO_CHAR(NOW() - INTERVAL '30 days', 'YYYY-MM-DD')"
            ), {"s": sys}).fetchone()
        settled  = r[0] or 0
        pnl      = round(r[1] or 0, 2)
        avg_edge = f"{r[2]*100:.1f}%" if r[2] else "-"
        pending  = r[3] or 0
        summary_rows += (
            f"<tr><td>{sys}</td><td>{settled}</td><td>{pending}</td>"
            f"<td>${pnl:+.2f}</td><td>{avg_edge}</td></tr>"
        )

    where = f"WHERE created_at >= NOW() - INTERVAL '{days} days'"
    if system_filter:
        where += f" AND system = '{system_filter}'"
    with bt.engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, system, game_date, player, bet_type, model_prob, market_prob, "
            f"edge, odds, stake, kelly_triggered, lambda_k, proj_k, result, profit "
            f"FROM bets {where} ORDER BY game_date DESC, model_prob DESC LIMIT 500"
        )).fetchall()

    bet_rows_html = ""
    for b in [dict(r._mapping) for r in rows]:
        prob  = b.get("model_prob") or 0
        edge  = b.get("edge") or 0
        flag  = ' style="background:#3d0000"' if (prob >= 0.95 or edge >= 0.40) else ''
        kt    = "Y" if b.get("kelly_triggered") else "n"
        lk    = f'{b["lambda_k"]:.2f}' if b.get("lambda_k") is not None else "-"
        pk    = f'{b["proj_k"]:.2f}' if b.get("proj_k") is not None else "-"
        res   = b.get("result") or "-"
        pnl_s = f'${b["profit"]:+.2f}' if b.get("profit") is not None else "-"
        bet_rows_html += (
            f"<tr{flag}>"
            f"<td>{b.get('game_date','')}</td>"
            f"<td>{b.get('system','')}</td>"
            f"<td>{b.get('player','')}</td>"
            f"<td>{b.get('bet_type','')}</td>"
            f"<td>{prob:.1%}</td>"
            f"<td>{edge:+.1%}</td>"
            f"<td>{b.get('odds','')}</td>"
            f"<td>${b.get('stake') or 0:.0f}</td>"
            f"<td>{kt}</td><td>{lk}</td><td>{pk}</td>"
            f"<td>{res}</td><td>{pnl_s}</td>"
            f"</tr>"
        )

    html = (
        '<!DOCTYPE html><html><head><title>MLB Dashboard</title>'
        '<style>'
        'body{background:#111;color:#eee;font-family:monospace;padding:20px}'
        'h1,h2{color:#f90;margin:16px 0 8px}'
        'table{border-collapse:collapse;width:100%;margin-bottom:30px}'
        'th,td{border:1px solid #444;padding:5px 9px;font-size:12px}'
        'th{background:#222;color:#f90}'
        'tr:hover{background:#1a1a1a}'
        'a{color:#f90;margin-right:8px}'
        '</style></head><body>'
        '<h1>MLB Betting Dashboard</h1>'
        '<div>Days: '
        '<a href="/dashboard?days=1">1</a>'
        '<a href="/dashboard?days=3">3</a>'
        '<a href="/dashboard?days=7">7</a>'
        '<a href="/dashboard?days=30">30</a>'
        '&nbsp;| System: '
        '<a href="/dashboard">All</a>'
        '<a href="/dashboard?system=K">K</a>'
        '<a href="/dashboard?system=NRFI">NRFI</a>'
        '<a href="/dashboard?system=F5">F5</a>'
        '<a href="/dashboard?system=HR">HR</a>'
        '<a href="/dashboard?system=OUTS">OUTS</a>'
        '</div>'
        '<h2>30-day Summary</h2>'
        '<table><tr><th>System</th><th>Settled</th><th>Pending</th>'
        '<th>P&amp;L</th><th>Avg Edge</th></tr>'
        f'{summary_rows}</table>'
        f'<h2>Recent Bets (last {days}d)'
        ' -- <span style="color:#f66">red = prob&gt;95% or edge&gt;40%</span></h2>'
        '<table><tr>'
        '<th>Date</th><th>Sys</th><th>Player</th><th>Type</th>'
        '<th>Prob</th><th>Edge</th><th>Odds</th><th>Stake</th>'
        '<th>KT</th><th>Lambda</th><th>ProjK</th><th>Result</th><th>P&amp;L</th>'
        f'</tr>{bet_rows_html}</table>'
        '</body></html>'
    )
    return html, 200, {"Content-Type": "text/html"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
