"""contest.api: the Flask routes (auth, the OFF switch, JSON in and out) over SQLite. No network."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from tests._contest_engine import make_engine
from flask import Flask, jsonify

from contest import store
from contest.api import contest_enabled, register_contest_api
from contest.rules import ContestRules
from contest.sync import sync_events
from tests.test_contest_service import KICK, espn_event

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KEY = "site-key"


def make_app(tmp_path):
    engine = make_engine(tmp_path)
    app = Flask("t")
    from flask import request

    def auth(req):
        return None if req.headers.get("X-API-Key") == KEY else (jsonify({"error": "Unauthorized"}), 401)

    def sched(req):
        return None if req.headers.get("Authorization") == "Bearer ok" else (jsonify({"error": "no"}), 401)

    register_contest_api(app, lambda: engine, auth, sched)
    from contest.schema import ensure_schema
    ensure_schema(engine)
    cid = store.create_contest(engine, "bowls", "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    sync_events(engine, cid, [espn_event("1")], NOW)
    return app, engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_ENABLED", "1")
    app, _ = make_app(tmp_path)
    return app.test_client()


H = {"X-API-Key": KEY, "X-Contest-User": "u1"}


def test_the_switch_accepts_the_usual_falsy_spellings():
    for v in ("", "0", "false", "off", "no", "OFF", " No "):
        assert contest_enabled({"CONTEST_ENABLED": v}) is False
    assert contest_enabled({}) is False and contest_enabled({"CONTEST_ENABLED": "1"}) and contest_enabled({"CONTEST_ENABLED": "true"})


def test_off_is_404_before_anything_else(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_ENABLED", "off")
    app, _ = make_app(tmp_path)
    assert app.test_client().get("/api/contest/bowls/feed", headers=H).status_code == 404
    assert app.test_client().post("/contest-refresh", json={"slug": "bowls"}, headers={"Authorization": "Bearer ok"}).status_code == 404


def test_the_site_key_is_required_and_so_is_a_user(client):
    assert client.get("/api/contest/bowls/feed").status_code == 401
    assert client.get("/api/contest/bowls/feed", headers={"X-API-Key": KEY}).status_code == 401           # a key but no signed-in user
    assert client.get("/api/contest/bowls/feed", headers={"X-API-Key": "wrong", "X-Contest-User": "u1"}).status_code == 401


def test_feed_join_place_cashout_round_trip(client):
    r = client.get("/api/contest/bowls/feed?name=alex", headers=H)
    assert r.status_code == 200 and r.json["me"]["joined"] is False and r.json["me"]["suggestedName"] == "alex"
    r = client.post("/api/contest/bowls/join", json={"inviteCode": "WELCOME", "displayName": "Alex"}, headers=H)
    assert r.status_code == 200 and r.json["me"]["joined"] and r.json["balanceCents"] == 500_000
    pick = {"gameId": 1, "market": "spread", "side": "home", "line": 5.5, "price": -112}
    r = client.post("/api/contest/bowls/place", json={"kind": "straight", "picks": [pick], "stakeCents": 10_000, "key": "k1"}, headers=H)
    assert r.status_code == 200 and r.json["balanceCents"] == 490_000 and r.json["myBets"][0]["status"] == "pending"
    stale = client.post("/api/contest/bowls/place", json={"kind": "straight", "picks": [{**pick, "price": -150}], "stakeCents": 10_000, "key": "k2"}, headers=H)
    assert stale.status_code == 409 and stale.json["error"]["code"] == "PRICE_CHANGED" and stale.json["error"]["detail"]["price"] == -112
    bet_id = int(r.json["myBets"][0]["id"])
    wrong = client.post("/api/contest/bowls/cashout", json={"betIds": [bet_id], "expectedCents": 1}, headers=H)
    assert wrong.status_code == 409 and wrong.json["error"]["code"] == "VALUE_CHANGED"
    ok = client.post("/api/contest/bowls/cashout", json={"betIds": [bet_id], "expectedCents": wrong.json["error"]["detail"]["cents"]}, headers=H)
    assert ok.status_code == 200 and ok.json["myBets"][0]["status"] == "cashed_out"


def test_errors_are_json_with_the_right_status(client):
    assert client.get("/api/contest/missing/feed", headers=H).status_code == 404
    assert client.post("/api/contest/bowls/nope", json={}, headers=H).status_code == 404
    assert client.post("/api/contest/bowls/place", data="not json", headers=H).status_code == 400
    assert client.post("/api/contest/bowls/place", json={"kind": "straight"}, headers=H).status_code in (400, 403)
    r = client.post("/api/contest/bowls/join", json={"inviteCode": "WRONG", "displayName": "Alex"}, headers=H)
    assert r.status_code == 403 and r.json["error"]["code"] == "INVITE_INVALID"


def test_refresh_needs_the_scheduler_and_runs_a_pass_with_an_injected_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_ENABLED", "1")
    app, engine = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/contest-refresh", json={"slug": "bowls"}).status_code == 401
    assert c.post("/contest-refresh", json={}, headers={"Authorization": "Bearer ok"}).status_code == 400
    assert c.post("/contest-refresh", json={"slug": "nope"}, headers={"Authorization": "Bearer ok"}).status_code == 404
    from contest import refresh as R
    monkeypatch.setattr(R, "http_json", lambda url, params=None, attempts=3: {"events": []})
    monkeypatch.setattr(R, "fetch_events", lambda sport, start, end, http=None: [espn_event("9", kick=datetime.now(timezone.utc) + timedelta(days=2))])
    r = c.post("/contest-refresh", json={"slug": "bowls"}, headers={"Authorization": "Bearer ok"})
    assert r.status_code == 200 and r.json["games"] == 1 and r.json["errors"] == []


def test_rate_limits_answer_429_with_retry_after_and_are_per_user_and_bucket(tmp_path, monkeypatch):
    from contest.ratelimit import RateLimiter
    monkeypatch.setenv("CONTEST_ENABLED", "1")
    engine = make_engine(tmp_path)
    from contest.schema import ensure_schema
    ensure_schema(engine)
    cid = store.create_contest(engine, "bowls", "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    clock = {"t": 0.0}
    app = Flask("rl")
    register_contest_api(app, lambda: engine, lambda req: None, lambda req: None, RateLimiter({"join": (2, 600), "read": (3, 60)}, lambda: clock["t"]))
    c = app.test_client()
    for _ in range(2):
        assert c.post("/api/contest/bowls/join", json={"inviteCode": "WRONG", "displayName": "Alex"}, headers=H).status_code == 403
    r = c.post("/api/contest/bowls/join", json={"inviteCode": "WELCOME", "displayName": "Alex"}, headers=H)
    assert r.status_code == 429 and r.json["error"]["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
    assert c.post("/api/contest/bowls/join", json={"inviteCode": "WELCOME", "displayName": "Blake"}, headers={**H, "X-Contest-User": "u2"}).status_code == 200   # another user is not limited
    assert c.get("/api/contest/bowls/feed", headers=H).status_code == 200                          # another bucket is not limited
    clock["t"] = 601.0
    assert c.post("/api/contest/bowls/join", json={"inviteCode": "WELCOME", "displayName": "Alex"}, headers=H).status_code == 200   # the window passed


def test_setup_creates_the_contest_and_invite_once_and_never_changes_them(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_ENABLED", "1")
    app, engine = make_app(tmp_path)
    c = app.test_client()
    ok = {"Authorization": "Bearer ok"}
    assert c.post("/contest-setup", json={"slug": "new", "name": "New"}).status_code == 401
    assert c.post("/contest-setup", json={"slug": "new"}, headers=ok).status_code == 400
    assert c.post("/contest-setup", json={"slug": "new", "name": "New", "sport": "nfl"}, headers=ok).status_code == 400
    first = c.post("/contest-setup", json={"slug": "new", "name": "New", "sport": "ncaab", "invite_code": "GO", "invite_uses": 7}, headers=ok).json
    assert first["created"] and first["invite_created"]
    again = c.post("/contest-setup", json={"slug": "new", "name": "Changed", "sport": "ncaaf", "invite_code": "GO", "invite_uses": 99}, headers=ok).json
    assert not again["created"] and not again["invite_created"] and again["contest_id"] == first["contest_id"]
    got = store.contest_by_slug(engine, "new")
    assert got["name"] == "New" and got["rules"].sport == "ncaab" and got["rules"].min_stake_cents == 5_000
    assert store.invites_of(engine, first["contest_id"]) == [{"code": "GO", "max_uses": 7, "uses": 0}]
