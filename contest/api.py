"""Flask routes for the Bankroll Challenge. A thin layer: authenticate, parse, call `contest.service`, serialise.

    GET  /api/contest/<slug>/feed?name=<suggested>     the page's whole state for the signed-in player
    POST /api/contest/<slug>/place | join | rename | cashout     each returns the fresh feed
    POST /contest-refresh                              scheduler: fetch ESPN, update the board, grade finals (body: {slug, include?})

Auth: the Next server calls these with the site API key (X-API-Key) and the Clerk user id it already verified (X-Contest-User). The
browser never reaches this service. The whole surface is OFF unless CONTEST_ENABLED is on (0 / false / off / no / empty all mean off)
and answers 404 when it is. The clock is the server's: no request body can set `now`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from contest import refresh as refresh_mod
from contest import service
from contest.schema import ensure_schema

logger = logging.getLogger(__name__)
_OFF = {"", "0", "false", "off", "no"}
_ACTIONS = {"place": service.place, "join": service.join, "rename": service.rename, "cashout": service.cash_out}


def contest_enabled(env=os.environ) -> bool:
    return (env.get("CONTEST_ENABLED") or "").strip().lower() not in _OFF


def register_contest_api(app, get_engine, auth_required, scheduler_auth_required) -> None:
    """Attach the routes. `get_engine` -> SQLAlchemy engine; the two auth callables return None when allowed, else a Flask response."""
    bp = Blueprint("contest", __name__)                            # built per call so a test can register it on many apps
    state = {"schema": False}

    def engine():
        e = get_engine()
        if not state["schema"]:
            ensure_schema(e)                                       # idempotent; first request creates the tables
            state["schema"] = True
        return e

    def gate():
        if not contest_enabled():
            return jsonify({"error": {"code": "OFF", "message": "the contest is not enabled"}}), 404
        return auth_required(request)

    def user_id():
        return (request.headers.get("X-Contest-User") or "").strip()

    def run(fn, *args):
        try:
            return jsonify(fn(*args))
        except service.RequestError as e:
            return jsonify(e.body()), e.http
        except Exception:                                          # noqa: BLE001
            logger.exception("contest request failed")
            return jsonify({"error": {"code": "SERVER", "message": "something went wrong"}}), 500

    @bp.route("/api/contest/<slug>/feed", methods=["GET"])
    def feed_route(slug):
        err = gate()
        if err:
            return err
        if not user_id():
            return jsonify({"error": {"code": "NO_USER", "message": "sign in first"}}), 401
        return run(lambda: service.feed(engine(), slug, user_id(), datetime.now(timezone.utc), (request.args.get("name") or "")[:40]))

    @bp.route("/api/contest/<slug>/<action>", methods=["POST"])
    def action_route(slug, action):
        err = gate()
        if err:
            return err
        fn = _ACTIONS.get(action)
        if fn is None:
            return jsonify({"error": {"code": "NO_ROUTE", "message": f"no action {action!r}"}}), 404
        if not user_id():
            return jsonify({"error": {"code": "NO_USER", "message": "sign in first"}}), 401
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": {"code": "BAD_REQUEST", "message": "a JSON object body is required"}}), 400
        return run(lambda: fn(engine(), slug, user_id(), body, datetime.now(timezone.utc)))

    @bp.route("/contest-refresh", methods=["POST"])
    def refresh_route():
        if not contest_enabled():
            return jsonify({"error": {"code": "OFF", "message": "the contest is not enabled"}}), 404
        err = scheduler_auth_required(request)
        if err:
            return err
        body = request.get_json(silent=True) or {}
        slug = str(body.get("slug") or "")
        if not slug:
            return jsonify({"error": {"code": "BAD_REQUEST", "message": "slug is required"}}), 400
        try:
            res = refresh_mod.refresh(engine(), slug, datetime.now(timezone.utc), include=body.get("include") or None,
                                      days_back=int(body.get("days_back", 2)), days_ahead=int(body.get("days_ahead", 14)))
        except ValueError as e:
            return jsonify({"error": {"code": "NO_CONTEST", "message": str(e)}}), 404
        except Exception:                                          # noqa: BLE001
            logger.exception("contest refresh failed")
            return jsonify({"error": {"code": "SERVER", "message": "refresh failed"}}), 500
        return jsonify(res)

    app.register_blueprint(bp)
