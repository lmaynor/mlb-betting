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
from datetime import date, datetime
from zoneinfo import ZoneInfo
_CT = ZoneInfo("America/Chicago")

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
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())

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
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())

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
    run_date         = body.get("run_date", datetime.now(_CT).date().isoformat())
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
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
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
                       settle_date or datetime.now(_CT).date().isoformat())
        except Exception:
            pass
        return jsonify({"status": "error", "error": str(e)}), 500

    return jsonify(result), 200


@app.route("/refresh-data", methods=["POST"])
def refresh_data_handler():
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
    try:
        from mlb_core.data.weather import weather_nightly_gcs
        from mlb_core.data.umpires import umpires_nightly_gcs
        from mlb_core.config import GCS_BUCKET
        from mlb_core.data.scoring import scoring_nightly_gcs
        from mlb_core.data.statcast import statcast_nightly_gcs
        fetcher_results = {}

        for label, fn, args in [
            ("weather",  weather_nightly_gcs,  (GCS_BUCKET, "Weather/weather_master.csv")),
            ("umpires",  umpires_nightly_gcs,   (GCS_BUCKET, "Umpires/umpscorecards_master.csv")),
        ]:
            try:
                fetcher_results[label] = fn(*args) or {"status": "ok"}
                logger.info(f"refresh-data: {label} ok")
            except Exception as fe:
                logger.error(f"refresh-data: {label} failed: {fe}")
                fetcher_results[label] = {"status": "error", "error": str(fe)}

        for label, fn, args in [
            ("scoring",  scoring_nightly_gcs,  (GCS_BUCKET, "Scoring/scoring_master.csv")),
        ]:
            try:
                fn(*args)
                fetcher_results[label] = {"status": "ok"}
                logger.info(f"refresh-data: {label} ok")
            except Exception as fe:
                logger.error(f"refresh-data: {label} failed: {fe}")
                fetcher_results[label] = {"status": "error", "error": str(fe)}

        if GCS_BUCKET:
            try:
                statcast_nightly_gcs(GCS_BUCKET, "Statcast/statcast_master.csv")
                fetcher_results["statcast"] = {"status": "ok"}
                logger.info("refresh-data: statcast ok")
            except Exception as fe:
                logger.error(f"refresh-data: statcast failed: {fe}")
                fetcher_results["statcast"] = {"status": "error", "error": str(fe)}

        savant_results = {}
        try:
            from mlb_core.data.savant_leaderboards import savant_leaderboards_nightly_all_gcs
            savant_results = savant_leaderboards_nightly_all_gcs()
            ok_count = sum(1 for r in savant_results.values() if isinstance(r, dict) and r.get("status") == "ok")
            logger.info(f"refresh-data: savant leaderboards {ok_count}/6 refreshed")
        except Exception as se:
            logger.error(f"refresh-data: savant_leaderboards failed: {se}")
            savant_results = {"error": str(se)}

        all_ok = all(v.get("status") == "ok" for v in fetcher_results.values())
        result = {
            "status": "ok" if all_ok else "partial",
            "fetchers": fetcher_results,
            "savant_leaderboards": savant_results,
        }
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"refresh-data failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

    return jsonify(result), 200

@app.route("/backfill-data", methods=["POST"])
def backfill_data_handler():
    """
    One-time backfill for weather, scoring, and umpires masters.
    Body:
      start_date: str  (required, e.g. "2026-04-01")
      end_date:   str  (optional, defaults to yesterday)
      systems:    list (optional, e.g. ["weather","scoring","umpires"], defaults to all)

    Weather: loops date-by-date calling _pull_weather_date().
    Scoring: resolves each date to game_pks then calls scoring_backfill_gcs().
    Umpires: calls umpires_backfill_gcs() with the date range directly.

    Runs synchronously -- use only from Cloud Shell proxy, not Scheduler.
    Gunicorn timeout 3600s is adequate for a full season backfill.
    """
    from datetime import date as _date, timedelta as _td
    body       = request.get_json(silent=True) or {}
    start_date = body.get("start_date")
    end_date   = body.get("end_date",
                          (_date.today() - _td(days=1)).isoformat())
    systems    = body.get("systems", ["weather", "scoring", "umpires"])

    if not start_date:
        return jsonify({"status": "error",
                        "error": "start_date required"}), 400

    from mlb_core.config import GCS_BUCKET
    results = {}

    # -- Weather backfill --
    if "weather" in systems:
        try:
            import pandas as pd
            from mlb_core.data.weather import _pull_weather_date
            from google.cloud import storage as _gcs
            WX_KEY = "Weather/weather_master.csv"
            client = _gcs.Client()
            bucket = client.bucket(GCS_BUCKET)
            blob   = bucket.blob(WX_KEY)
            existing = (pd.read_csv(blob.open("r"), low_memory=False)
                        if blob.exists() else pd.DataFrame())
            already  = set(existing["game_pk"].astype(str)) if not existing.empty else set()
            frames   = [existing] if not existing.empty else []
            cur = _date.fromisoformat(start_date)
            end = _date.fromisoformat(end_date)
            fetched, skipped, errors = 0, 0, 0
            while cur <= end:
                ds = cur.isoformat()
                try:
                    day_df = _pull_weather_date(ds, is_forecast=False)
                    if day_df.empty:
                        skipped += 1
                    else:
                        new_rows = day_df[~day_df["game_pk"].astype(str).isin(already)]
                        if not new_rows.empty:
                            frames.append(new_rows)
                            already.update(new_rows["game_pk"].astype(str))
                            fetched += len(new_rows)
                except Exception as de:
                    logger.warning(f"weather backfill: {ds} failed: {de}")
                    errors += 1
                cur += _td(days=1)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined = combined.drop_duplicates(subset=["game_pk"], keep="last")
                tmp = "/tmp/weather_backfill.csv"
                combined.to_csv(tmp, index=False)
                blob.upload_from_filename(tmp, content_type="text/csv", timeout=600)
                results["weather"] = {
                    "status": "ok",
                    "fetched": fetched,
                    "skipped": skipped,
                    "errors":  errors,
                    "total_rows": len(combined),
                }
            else:
                results["weather"] = {"status": "ok", "fetched": 0}
        except Exception as e:
            logger.error(f"backfill-data: weather failed: {e}")
            results["weather"] = {"status": "error", "error": str(e)}

    # -- Scoring backfill --
    if "scoring" in systems:
        try:
            from mlb_core.data.lineups import _get_games_for_date
            from mlb_core.data.scoring import scoring_backfill_gcs
            import pandas as pd
            cur = _date.fromisoformat(start_date)
            end = _date.fromisoformat(end_date)
            game_pks = []
            while cur <= end:
                try:
                    games = _get_games_for_date(cur.isoformat())
                    game_pks.extend(g["game_pk"] for g in games)
                except Exception:
                    pass
                cur += _td(days=1)
            game_pks = list(set(game_pks))
            logger.info(f"backfill-data: scoring {len(game_pks)} game_pks "
                        f"({start_date} -> {end_date})")
            scoring_backfill_gcs(GCS_BUCKET, "Scoring/scoring_master.csv",
                                 game_pks)
            results["scoring"] = {
                "status": "ok",
                "game_pks": len(game_pks),
            }
        except Exception as e:
            logger.error(f"backfill-data: scoring failed: {e}")
            results["scoring"] = {"status": "error", "error": str(e)}

    # -- Umpires backfill --
    if "umpires" in systems:
        try:
            from mlb_core.data.umpires import umpires_backfill_gcs
            df = umpires_backfill_gcs(
                GCS_BUCKET, "Umpires/umpscorecards_master.csv",
                start_date, end_date,
            )
            results["umpires"] = {
                "status": "ok",
                "rows": len(df),
            }
        except Exception as e:
            logger.error(f"backfill-data: umpires failed: {e}")
            results["umpires"] = {"status": "error", "error": str(e)}

    all_ok = all(v.get("status") == "ok" for v in results.values())
    return jsonify({
        "status":     "ok" if all_ok else "partial",
        "start_date": start_date,
        "end_date":   end_date,
        "results":    results,
    }), 200


@app.route("/backfill-savant", methods=["POST"])
def backfill_savant_handler():
    """One-time historical backfill for Savant leaderboard datasets.
    Body: dataset (str, optional), start_year (int), end_year (int), force (bool).
    """
    body       = request.get_json(silent=True) or {}
    dataset    = body.get("dataset")
    start_year = body.get("start_year")
    end_year   = body.get("end_year")
    force      = body.get("force", False)
    logger.info(f"backfill-savant: dataset={dataset or 'ALL'} start={start_year} end={end_year} force={force}")
    try:
        from mlb_core.data.savant_leaderboards import (
            savant_leaderboard_backfill_gcs,
            savant_leaderboards_backfill_all_gcs,
            DATASET_START_YEAR,
        )
        if dataset:
            if dataset not in DATASET_START_YEAR:
                return jsonify({"error": f"Unknown dataset {dataset!r}", "valid": list(DATASET_START_YEAR)}), 400
            results    = savant_leaderboard_backfill_gcs(dataset, start_year=start_year, end_year=end_year, force=force)
            total_rows = sum(v for v in results.values() if isinstance(v, int) and v > 0)
            return jsonify({"status": "ok", "dataset": dataset, "results": {str(k): v for k, v in results.items()}, "total_rows": total_rows}), 200
        else:
            results = savant_leaderboards_backfill_all_gcs(start_year=start_year, end_year=end_year, force=force)
            summary = {}
            for ds, res in results.items():
                if isinstance(res, dict) and "error" not in res:
                    summary[ds] = {"total_rows": sum(v for v in res.values() if isinstance(v, int) and v > 0)}
                else:
                    summary[ds] = res
            return jsonify({"status": "ok", "summary": summary}), 200
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"backfill-savant failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/monitor", methods=["POST"])
def monitor_handler():
    """Rolling performance monitor — model health (ROI, hit rate)."""
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
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
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
    try:
        from runners.monitor_ops import run as monitor_ops_run
        result = monitor_ops_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"monitor-ops failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

    http_status = 200 if result.get("healthy") else 207
    return jsonify(result), http_status

@app.route("/backfill-statcast", methods=["POST"])
def backfill_statcast():
    """Backfill Statcast master for missing dates.
    Body: {"dates": ["2026-05-09", "2026-05-10"]}
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.data.statcast import statcast_backfill_gcs
    body  = request.get_json(silent=True) or {}
    dates = body.get("dates")
    if not dates:
        return jsonify({"error": "dates list required"}), 400
    try:
        results = statcast_backfill_gcs(
            GCS_BUCKET,
            "Statcast/statcast_master.csv",
            dates,
        )
        return jsonify({"status": "ok", "dates": results})
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"backfill-statcast failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/reset-and-run", methods=["POST"])
def reset_and_run():
    """Reset bets for a date and immediately rerun all systems. For recovery use only."""
    err = _auth_required(request)
    if err:
        return err
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import BetTracker
    from runners.run_hr import run as run_hr
    from runners.run_nrfi import run as run_nrfi
    from runners.run_f5 import run as run_f5
    from runners.run_k import run as run_k
    body     = request.get_json(silent=True) or {}
    run_date = body.get("date", datetime.now(_CT).date().isoformat())
    systems  = body.get("systems", ["HR", "NRFI", "F5", "K"])
    bt = BetTracker(os.environ["MLB_DB_URL"], "HR")
    deleted = {}
    for sys in systems + ["OUTS"]:
        with bt.engine.begin() as conn:
            r = conn.execute(text(
                "DELETE FROM bets WHERE game_date = :d AND system = :s"
            ), {"d": run_date, "s": sys})
            deleted[sys] = r.rowcount
    logger.info(f"reset-and-run: deleted {deleted} for {run_date}")
    results = {}
    runner_map = {"HR": run_hr, "NRFI": run_nrfi, "F5": run_f5, "K": run_k}
    for sys in systems:
        if sys in runner_map:
            try:
                results[sys] = runner_map[sys](run_type="morning", run_date=run_date)
            except Exception as e:
                results[sys] = {"error": str(e)}
    return jsonify({"deleted": deleted, "results": results})

@app.route("/reset-bets", methods=["POST"])
def reset_bets():
    """Delete bets by date, system, player, or game_pk. Requires at least date."""
    err = _auth_required(request)
    if err:
        return err
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import BetTracker
    body    = request.get_json(silent=True) or {}
    date    = body.get("date")
    system  = body.get("system")
    player  = body.get("player")
    game_pk = body.get("game_pk")
    if not date:
        return jsonify({"error": "date is required"}), 400
    bt = BetTracker(os.environ["MLB_DB_URL"], "HR")
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
    bt = BetTracker(os.environ["MLB_DB_URL"], "HR")

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

    where_clause = f"WHERE created_at >= NOW() - INTERVAL '{days} days'"
    bind_params: dict = {}
    if system_filter:
        # Whitelist — never interpolate user input into SQL strings.
        _VALID_SYSTEMS_DASH = {"HR","NRFI","F5","K","OUTS","F3","F1H","F7","GAME",
                               "BATTER_K","BATTER_TB","BATTER_HITS","PITCHER_ER"}
        if system_filter.upper() not in _VALID_SYSTEMS_DASH:
            return jsonify({"error": f"Invalid system: {system_filter}"}), 400
        where_clause += " AND system = :system_filter"
        bind_params["system_filter"] = system_filter.upper()
    with bt.engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, system, game_date, player, bet_type, model_prob, market_prob, "
            f"edge, odds, stake, kelly_triggered, lambda_k, proj_k, result, profit "
            f"FROM bets {where_clause} ORDER BY game_date DESC, model_prob DESC LIMIT 500"
        ), bind_params).fetchall()

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



# ---------------------------------------------------------------------------
# Public API -- read-only, authenticated with X-API-Key header
# Used by beezy.vip frontend.
# ---------------------------------------------------------------------------

from runners.public_api import (
    get_pnl_sparkline,
    get_today_picks, get_picks, get_recent_settled,
    get_summary_stats, _require_api_key,
)

SITE_API_KEY  = os.environ.get("SITE_API_KEY", "")
ALLOWED_ORIGIN = os.environ.get("SITE_ORIGIN", "https://beezy.vip")


# Module-level singleton -- one engine per gunicorn worker, not per request.
# BetTracker init runs schema/migration once at startup, not on every API call.
_PUBLIC_API_ENGINE = None

def _get_engine():
    global _PUBLIC_API_ENGINE
    if _PUBLIC_API_ENGINE is None:
        from mlb_core.tracking.bet_tracker import BetTracker
        _PUBLIC_API_ENGINE = BetTracker(os.environ["MLB_DB_URL"], "HR").engine
    return _PUBLIC_API_ENGINE





def _cors_headers():
    return {
        "Access-Control-Allow-Origin":  ALLOWED_ORIGIN,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "X-API-Key, Content-Type",
    }


def _auth_required(req):
    if not SITE_API_KEY:
        return jsonify({"error": "SITE_API_KEY not configured"}), 500
    if not _require_api_key(req.headers, SITE_API_KEY):
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
        picks = get_today_picks(_get_engine())
        resp  = jsonify({"picks": picks, "count": len(picks)})
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
            _get_engine(),
            system=request.args.get("system"),
            date=request.args.get("date"),
            status=request.args.get("status"),
            limit=request.args.get("limit", 50),
            offset=request.args.get("offset", 0),
            book=request.args.get("book"),
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
        picks = get_recent_settled(_get_engine(), limit=limit)
        resp  = jsonify({"picks": picks, "count": len(picks)})
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=120"
        return resp
    except Exception as exc:
        logger.exception("public_picks_recent failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/public/stats/sparkline", methods=["GET", "OPTIONS"])
def public_stats_sparkline():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        days = int(request.args.get("days", 30))
        data = get_pnl_sparkline(_get_engine(), days=days)
        resp = jsonify({"sparkline": data, "days": days})
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    except Exception as exc:
        logger.exception("public_stats_sparkline failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/public/stats/summary", methods=["GET", "OPTIONS"])
def public_stats_summary():
    if request.method == "OPTIONS":
        return "", 204, _cors_headers()
    err = _auth_required(request)
    if err:
        return err
    try:
        stats = get_summary_stats(_get_engine())
        resp  = jsonify(stats)
        resp.headers.update(_cors_headers())
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    except Exception as exc:
        logger.exception("public_stats_summary failed")
        return jsonify({"error": str(exc)}), 500




@app.route("/model-health", methods=["GET", "POST"])
def model_health_handler():
    """
    GET/POST /model-health
    Full model diagnostics per system:
      - AUC (model_prob and market_prob)
      - Brier score and Brier skill score
      - Calibration error (hit_rate - avg_model_prob)
      - ROI and edge-ROI gap
      - CLV mean + t-stat
      - Feature null rates vs training (NRFI only for now)
      - Data master freshness
    """
    import math
    from sqlalchemy import text as _text

    def _auc(probs, outcomes):
        pos = [p for p, o in zip(probs, outcomes) if o == 1]
        neg = [p for p, o in zip(probs, outcomes) if o == 0]
        if not pos or not neg:
            return None
        concordant = sum(1 for p in pos for n in neg if p > n)
        tied       = sum(1 for p in pos for n in neg if p == n)
        return (concordant + 0.5 * tied) / (len(pos) * len(neg))

    def _brier(probs, outcomes):
        if not probs:
            return None
        return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)

    def _brier_naive(outcomes):
        if not outcomes:
            return None
        base = sum(outcomes) / len(outcomes)
        return sum((base - o) ** 2 for o in outcomes) / len(outcomes)

    def _clv_tstat(clvs):
        n = len(clvs)
        if n < 2:
            return None, None
        mu  = sum(clvs) / n
        var = sum((x - mu) ** 2 for x in clvs) / (n - 1)
        se  = math.sqrt(var / n)
        return round(mu, 4), round(mu / se, 3) if se > 0 else 0.0

    def _system_stats(rows):
        scorable = [r for r in rows if r.get("result") in ("win", "loss")]
        n = len(scorable)
        if n == 0:
            return {"n": 0}
        outcomes  = [1 if r["result"] == "win" else 0 for r in scorable]
        hit_rate  = sum(outcomes) / n
        staked    = sum(float(r.get("stake") or 0) for r in scorable)
        profit    = sum(float(r.get("profit") or 0) for r in scorable)
        roi       = profit / staked * 100 if staked > 0 else None

        model_probs  = [float(r["model_prob"])  for r in scorable if r.get("model_prob")  is not None]
        market_probs = [float(r["market_prob"]) for r in scorable if r.get("market_prob") is not None]
        out_m = [o for r, o in zip(scorable, outcomes) if r.get("model_prob")  is not None]
        out_k = [o for r, o in zip(scorable, outcomes) if r.get("market_prob") is not None]

        auc_model  = _auc(model_probs,  out_m)
        auc_market = _auc(market_probs, out_k)
        br         = _brier(model_probs, out_m)
        br_naive   = _brier_naive(out_m)
        brier_skill = round(1 - br / br_naive, 4) if br and br_naive else None

        avg_mp   = sum(model_probs) / len(model_probs) if model_probs else None
        cal_err  = round(hit_rate - avg_mp, 4) if avg_mp is not None else None

        edges = [float(r["edge"]) for r in scorable if r.get("edge") is not None]
        avg_edge = round(sum(edges) / len(edges) * 100, 2) if edges else None
        edge_roi_gap = round(avg_edge - roi, 2) if avg_edge and roi is not None else None

        clvs = [float(r["clv_pct"]) for r in scorable if r.get("clv_pct") is not None]
        mean_clv, clv_t = _clv_tstat(clvs) if clvs else (None, None)

        return {
            "n":            n,
            "hit_rate":     round(hit_rate, 4),
            "avg_model_prob": round(avg_mp, 4) if avg_mp else None,
            "cal_err":      cal_err,
            "auc_model":    round(auc_model,  4) if auc_model  is not None else None,
            "auc_market":   round(auc_market, 4) if auc_market is not None else None,
            "brier":        round(br, 4) if br else None,
            "brier_skill":  brier_skill,
            "roi":          round(roi, 2) if roi is not None else None,
            "avg_edge_pct": avg_edge,
            "edge_roi_gap": edge_roi_gap,
            "mean_clv":     mean_clv,
            "clv_tstat":    clv_t,
            "clv_n":        len(clvs),
            "signal": (
                "strong"   if auc_model and auc_model >= 0.57 else
                "moderate" if auc_model and auc_model >= 0.53 else
                "weak"     if auc_model and auc_model >= 0.50 else
                "inverted" if auc_model and auc_model < 0.50  else
                "unknown"
            ),
        }

    try:
        engine = _get_engine()
        season = datetime.now(_CT).year
        with engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(_text(
                "SELECT system, result, stake, profit, model_prob, market_prob, "
                "edge, clv_pct, game_date FROM bets "
                "WHERE game_date LIKE :y AND kelly_triggered = TRUE "
                "ORDER BY game_date ASC"
            ), {"y": f"{season}%"})]

        systems = {}
        for r in rows:
            s = r["system"]
            systems.setdefault(s, []).append(r)

        results = {}
        for sys_name, sys_rows in sorted(systems.items()):
            results[sys_name] = _system_stats(sys_rows)

        # Data master freshness
        from mlb_core.storage import exists
        from mlb_core.config import GCS_BUCKET as _gcs_bucket
        import datetime as _dt
        now_utc = _dt.datetime.utcnow()
        freshness = {}
        for label, key in [
            ("weather",  "Weather/weather_master.csv"),
            ("scoring",  "Scoring/scoring_master.csv"),
            ("umpires",  "Umpires/umpscorecards_master.csv"),
            ("statcast", "Statcast/statcast_master.csv"),
        ]:
            try:
                from google.cloud import storage as gcs
                client = gcs.Client()
                bucket = client.bucket(_gcs_bucket)
                blob   = bucket.get_blob(key)
                if blob and blob.updated:
                    age_h = round((now_utc - blob.updated.replace(tzinfo=None)).total_seconds() / 3600, 1)
                    freshness[label] = {"age_hours": age_h, "stale": age_h > 26}
                else:
                    freshness[label] = {"age_hours": None, "stale": True}
            except Exception as fe:
                freshness[label] = {"error": str(fe)}

        return jsonify({
            "status":    "ok",
            "run_date":  datetime.now(_CT).date().isoformat(),
            "systems":   results,
            "freshness": freshness,
        }), 200

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"model-health failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/capture-closing", methods=["POST"])
def capture_closing_handler():
    """Capture closing lines for all open bets today. Run at T-5 min per game."""
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
    try:
        from runners.capture_closing_lines import run as capture_run
        result = capture_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"capture-closing failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify(result), 200


@app.route("/monitor-drift", methods=["POST"])
def monitor_drift_handler():
    """PSI feature drift monitor. Run weekly (Monday mornings)."""
    body     = request.get_json(silent=True) or {}
    run_date = body.get("run_date", datetime.now(_CT).date().isoformat())
    try:
        from runners.monitor_drift import run as drift_run
        result = drift_run(run_date=run_date)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"monitor-drift failed:\n{tb}")
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify(result), 200


@app.route("/retrain-outs", methods=["POST"])
def retrain_outs_handler():
    """Trigger OUTS Pro v1 retrain Cloud Run Job."""
    import google.auth
    import google.auth.transport.requests
    import requests as _req_lib

    PROJECT = "concrete-crow-445205-m4"
    REGION  = "us-central1"
    JOB     = "mlb-retrain-outs-v1"
    url     = f"https://{REGION}-run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB}:run"
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        r = _req_lib.post(url, headers={"Authorization": f"Bearer {creds.token}"}, json={}, timeout=30)
        return jsonify({"status": "triggered", "job": JOB, "http": r.status_code}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/retrain-weekly", methods=["POST"])
def retrain_weekly():
    """
    Trigger weekly retrain + calibrate for all four systems.
    Called by mlb-retrain-weekly scheduler every Monday 06:00 UTC.
    Fires retrain jobs first, then calibrate jobs after a delay.
    Uses Cloud Run Jobs API with SA credentials from metadata server.
    """
    import threading
    import time
    import google.auth
    import google.auth.transport.requests

    PROJECT  = "concrete-crow-445205-m4"
    REGION   = "us-central1"
    BASE_URL = f"https://{REGION}-run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs"

    RETRAIN_JOBS  = [
        "mlb-retrain-nrfi-v17",
        "mlb-retrain-f5-v5",      # was mlb-retrain-f5-meta (T11: deprecated shim)
        "mlb-retrain-k-v1",
        "mlb-retrain-hr-v6",      # was mlb-retrain-hr-meta (T11: deprecated shim)
        "mlb-retrain-outs-v1",    # E04: OUTS trained model
    ]
    CALIBRATE_JOBS = [
        "mlb-calibrate-nrfi",
        "mlb-calibrate-f5",
        "mlb-calibrate-k",
        "mlb-calibrate-hr",
    ]
    CALIBRATE_DELAY_S = 1800  # 30 min -- enough for all retrains to finish

    def _get_token():
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        return creds.token

    def _trigger_job(job_name: str, token: str) -> dict:
        import requests as req_lib
        url = f"{BASE_URL}/{job_name}:run"
        r = req_lib.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={},
            timeout=30,
        )
        return {"job": job_name, "status": r.status_code, "ok": r.status_code in (200, 201)}

    def _run_calibrate_after_delay():
        time.sleep(CALIBRATE_DELAY_S)
        try:
            token = _get_token()
            for job in CALIBRATE_JOBS:
                result = _trigger_job(job, token)
                logger.info(f"retrain-weekly calibrate: {result}")
        except Exception as e:
            logger.error(f"retrain-weekly calibrate phase failed: {e}")

    try:
        token = _get_token()
    except Exception as e:
        logger.error(f"retrain-weekly: failed to get credentials: {e}")
        return jsonify({"error": str(e)}), 500

    # Fire all retrain jobs immediately
    retrain_results = []
    for job in RETRAIN_JOBS:
        result = _trigger_job(job, token)
        retrain_results.append(result)
        logger.info(f"retrain-weekly retrain: {result}")

    # Fire calibrate jobs after delay in background thread
    t = threading.Thread(target=_run_calibrate_after_delay, daemon=True)
    t.start()

    return jsonify({
        "status":          "triggered",
        "retrain_jobs":    retrain_results,
        "calibrate_delay": f"{CALIBRATE_DELAY_S}s",
        "calibrate_jobs":  CALIBRATE_JOBS,
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)

