"""Organiser actions: rename, adjust, void, settle, invites, open/closed, and the audit log. Offline."""
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, jsonify

from contest import service, store
from contest.api import register_contest_api
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.service import RequestError
from contest.sync import sync_events
from tests._contest_engine import make_engine
from tests.test_contest_service import KICK, espn_event

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
SLUG = "bowls"


@pytest.fixture
def w(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, SLUG, "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    sync_events(engine, cid, [espn_event("1")], NOW)
    service.join(engine, SLUG, "u1", {"inviteCode": "WELCOME", "displayName": "Alex"}, NOW)
    return engine, cid


def pick():
    return {"gameId": 1, "market": "spread", "side": "home", "line": 5.5, "price": -112}


def test_state_lists_entries_invites_games_and_a_clean_ledger(w):
    engine, _ = w
    s = service.admin_state(engine, SLUG)
    assert s["status"] == "open" and s["entries"][0]["display_name"] == "Alex" and s["entries"][0]["user_id"] == "u1"
    assert s["invites"] == [{"code": "WELCOME", "max_uses": 5, "uses": 1}] and s["games"][0]["status"] == "scheduled" and s["ledgerMismatches"] == []


def test_rename_adjust_and_invite_work_and_are_logged_with_the_actor(w):
    engine, _ = w
    s = service.admin_action(engine, SLUG, "boss", "rename", {"entryId": 1, "displayName": "Alexander"}, NOW)
    assert s["entries"][0]["display_name"] == "Alexander"
    s = service.admin_action(engine, SLUG, "boss", "adjust", {"entryId": 1, "cents": -5_000, "note": "duplicate bet refunded"}, NOW)
    assert s["entries"][0]["balance_cents"] == 495_000 and s["ledgerMismatches"] == []
    s = service.admin_action(engine, SLUG, "boss", "invite", {"code": "SECOND", "maxUses": 3}, NOW)
    assert {"code": "SECOND", "max_uses": 3, "uses": 0} in s["invites"]
    assert [(l["actor"], l["action"]) for l in s["log"]] == [("boss", "invite"), ("boss", "adjust"), ("boss", "rename")]
    assert s["log"][1]["detail"]["note"] == "duplicate bet refunded"


def test_an_adjustment_needs_a_reason_and_a_duplicate_invite_is_409(w):
    engine, _ = w
    with pytest.raises(RequestError) as e:
        service.admin_action(engine, SLUG, "boss", "adjust", {"entryId": 1, "cents": 100, "note": " "}, NOW)
    assert e.value.http == 400
    with pytest.raises(RequestError) as e:
        service.admin_action(engine, SLUG, "boss", "invite", {"code": "WELCOME"}, NOW)
    assert e.value.http == 409
    with pytest.raises(RequestError) as e:
        service.admin_action(engine, SLUG, "boss", "nope", {}, NOW)
    assert e.value.http == 404
    with pytest.raises(RequestError) as e:
        service.admin_action(engine, SLUG, "boss", "rename", {"displayName": "x"}, NOW)
    assert e.value.http == 400


def test_voiding_a_game_refunds_its_bets_and_settling_by_hand_pays(w):
    engine, _ = w
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    s = service.admin_action(engine, SLUG, "boss", "void-game", {"gameId": 1}, NOW)
    assert s["entries"][0]["balance_cents"] == 500_000 and s["games"][0]["status"] == "void"
    sync_events(engine, 1, [espn_event("2", kick=KICK + timedelta(hours=3))], NOW)
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [{**pick(), "gameId": 2}], "stakeCents": 10_000, "key": "k2"}, NOW)
    s = service.admin_action(engine, SLUG, "boss", "settle-game", {"gameId": 2, "homeScore": 21, "awayScore": 20}, KICK + timedelta(hours=7))
    assert s["entries"][0]["balance_cents"] == 500_000 + 8_929 and s["ledgerMismatches"] == []


def test_a_closed_contest_takes_no_new_bets_and_reopens(w):
    engine, _ = w
    service.admin_action(engine, SLUG, "boss", "status", {"status": "closed"}, NOW)
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    assert e.value.code == "CONTEST_CLOSED"
    service.admin_action(engine, SLUG, "boss", "status", {"status": "open"}, NOW)
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    with pytest.raises(RequestError):
        service.admin_action(engine, SLUG, "boss", "status", {"status": "bogus"}, NOW)


def test_the_routes_need_the_organiser_header(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_ENABLED", "1")
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    store.create_contest(engine, SLUG, "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    app = Flask("a")
    register_contest_api(app, lambda: engine, lambda req: None if req.headers.get("X-API-Key") == "k" else (jsonify({"error": "no"}), 401), lambda req: None)
    c = app.test_client()
    base = {"X-API-Key": "k", "X-Contest-User": "boss"}
    assert c.get(f"/api/contest/{SLUG}/admin").status_code == 401
    assert c.get(f"/api/contest/{SLUG}/admin", headers=base).status_code == 403
    assert c.get(f"/api/contest/{SLUG}/admin", headers={**base, "X-Contest-Admin": "1"}).status_code == 200
    assert c.post(f"/api/contest/{SLUG}/admin/invite", json={"code": "ABC"}, headers=base).status_code == 403
    r = c.post(f"/api/contest/{SLUG}/admin/invite", json={"code": "ABC", "maxUses": 2}, headers={**base, "X-Contest-Admin": "1"})
    assert r.status_code == 200 and r.json["invites"][0]["code"] == "ABC" and r.json["log"][0]["actor"] == "boss"


def test_feature_switches_show_in_the_state_and_flip_with_a_log_entry(w):
    engine, _ = w
    assert service.admin_state(engine, SLUG)["switches"]["halftime_enabled"] is False
    s = service.admin_action(engine, SLUG, "boss", "rules", {"halftime_enabled": True, "cashout_enabled": False}, NOW)
    assert s["switches"]["halftime_enabled"] is True and s["switches"]["cashout_enabled"] is False
    assert s["log"][0]["action"] == "rules"
    with pytest.raises(RequestError):
        service.admin_action(engine, SLUG, "boss", "rules", {"start_bankroll_cents": 1}, NOW)             # numbers are never edited here
    with pytest.raises(RequestError):
        service.admin_action(engine, SLUG, "boss", "rules", {"halftime_enabled": "yes"}, NOW)
