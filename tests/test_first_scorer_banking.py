"""Tests for the first-scorer price banking stack (nba.odds.first_scorer + scripts/bank_first_scorer.py).

Offline: a fake ParlayAPI client and a fake storage layer stand in, so nothing touches the network,
ParlayAPI credits, or a bucket. The disabled-by-default gate is tripwired: with either gate missing,
zero client constructions, zero API calls and zero storage writes may happen.
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nba import config as C
from nba.odds import first_scorer as FS

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("bank_first_scorer", ROOT / "scripts" / "bank_first_scorer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event_obj(n_players=10, books=("bovada",), market="player_first_basket", hints=True):
    outcomes = []
    for i in range(n_players):
        desc = f"Player {i}"
        if hints:
            desc += " (NY)" if i % 2 == 0 else " (BOS)"
        outcomes.append({"name": "Yes", "description": desc, "price": 500 + 100 * i, "point": None})
    return {
        "id": "ev1", "commence_time": "2026-11-12T00:00:00Z", "home_team": "New York Knicks",
        "away_team": "Boston Celtics",
        "bookmakers": [{"key": b, "markets": [{"key": market, "outcomes": outcomes}]} for b in books],
    }


# -------------------------------------------------------------------------------- snapshot windows
def test_snapshot_windows_match_the_preregistered_design():
    assert FS.snapshot_label(120) == "T-120" and FS.snapshot_label(105) == "T-120" and FS.snapshot_label(135) == "T-120"
    assert FS.snapshot_label(30) == "T-30" and FS.snapshot_label(20) == "T-30" and FS.snapshot_label(40) == "T-30"
    assert FS.snapshot_label(5) == "T-5" and FS.snapshot_label(0) == "T-5" and FS.snapshot_label(10) == "T-5"
    assert FS.snapshot_label(60) is None and FS.snapshot_label(200) is None and FS.snapshot_label(-5) is None
    assert FS.snapshot_label(None) is None
    assert C.FIRST_SCORER_SNAPSHOTS == (120, 30, 5)


def test_minutes_to_tip_parses_z_and_refuses_junk():
    now = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert FS.minutes_to_tip("2026-11-12T00:30:00Z", now) == 30.0
    assert FS.minutes_to_tip("not a time", now) is None
    assert FS.minutes_to_tip(None, now) is None


# -------------------------------------------------------------------------------- completeness gate
def test_completeness_gate_is_eight_listed_outcomes():
    assert C.FIRST_SCORER_MIN_OUTCOMES == 8
    full = FS.evaluate_completeness(event_obj(10), "basketball_nba")
    assert full[0]["outcomes"] == 10 and full[0]["priced"] is True and full[0]["team_coverage"] == "both"
    partial = FS.evaluate_completeness(event_obj(3), "basketball_nba")   # the Bovada 3-player sample shape
    assert partial[0]["outcomes"] == 3 and partial[0]["priced"] is False
    assert FS.game_is_priced(full) is True and FS.game_is_priced(partial) is False
    edge = FS.evaluate_completeness(event_obj(8), "basketball_nba")
    assert edge[0]["priced"] is True                                     # exactly 8 clears the gate
    assert FS.evaluate_completeness(event_obj(7), "basketball_nba")[0]["priced"] is False


def test_completeness_without_team_hints_is_unknown_not_both():
    rec = FS.evaluate_completeness(event_obj(9, hints=False), "basketball_nba")[0]
    assert rec["team_coverage"] == "unknown" and rec["priced"] is True


def test_flatten_keeps_every_book_set_and_dedupes_within_a_book():
    obj = event_obj(4, books=("bovada", "draftkings"))
    obj["bookmakers"][0]["markets"][0]["outcomes"].append(
        {"name": "Yes", "description": "Player 0 (NY)", "price": 999, "point": None})
    rows = FS.flatten_micro_market(obj, "basketball_nba")
    assert len(rows) == 8                                                # 4 per book, duplicate dropped
    assert {r["book"] for r in rows} == {"bovada", "draftkings"}
    assert all(r["market"] == "player_first_basket" for r in rows)


def test_flatten_ignores_unrelated_markets_and_junk():
    obj = event_obj(3)
    obj["bookmakers"][0]["markets"].append({"key": "player_points", "outcomes": [
        {"name": "Over", "description": "Player 0", "price": -110, "point": 20.5}]})
    obj["bookmakers"][0]["markets"][0]["outcomes"].append(
        {"name": "Yes", "description": "{template}", "price": 100, "point": None})
    rows = FS.flatten_micro_market(obj, "basketball_nba")
    assert len(rows) == 3 and all(r["market"] == "player_first_basket" for r in rows)


def test_nfl_market_is_player_1st_td():
    assert C.FIRST_SCORER_MARKETS["americanfootball_nfl"] == ["player_1st_td"]
    obj = event_obj(9, market="player_1st_td")
    rec = FS.evaluate_completeness(obj, "americanfootball_nfl")[0]
    assert rec["market"] == "player_1st_td" and rec["priced"] is True


def test_gcs_keys_are_raw_first_layout():
    assert C.first_scorer_raw_key("basketball_nba", "2026-11-12", "ev1", "T-30") == \
        "FirstScorer/basketball_nba/raw/2026-11-12/ev1_T-30.json"
    assert C.first_scorer_latest_key("americanfootball_nfl") == "FirstScorer/americanfootball_nfl/latest.json"


# -------------------------------------------------------------------------------- accumulate (fakes)
class FakeClient:
    credits_remaining = "99999"
    credits_last = "1"

    def __init__(self, slate, objs):
        self.slate, self.objs, self.prop_calls = slate, objs, []

    def get_slate(self, sport):
        return self.slate

    def get_event_props(self, sport, eid, markets):
        self.prop_calls.append((eid, tuple(markets)))
        return self.objs.get(eid)


class FakeStorage:
    def __init__(self):
        self.writes = []          # (key, bytes) in write order

    def write_bytes(self, data, key):
        self.writes.append((key, data))


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStorage()
    monkeypatch.setattr(FS.storage, "write_bytes", store.write_bytes)
    monkeypatch.setattr(FS.credit_ledger, "record", lambda *a, **k: None)
    return store


def test_accumulate_writes_raw_before_summary_and_flags_priced(fake_store):
    now = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    tip = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    slate = [{"id": "ev1", "commence_time": tip, "home_team": "New York Knicks", "away_team": "Boston Celtics"}]
    client = FakeClient(slate, {"ev1": event_obj(10)})
    res = FS.accumulate_micro("basketball_nba", client=client, guard=False, now=now)
    keys = [k for k, _ in fake_store.writes]
    assert keys[0] == C.first_scorer_raw_key("basketball_nba", "2026-11-12", "ev1", "T-30")   # RAW FIRST
    assert any("summary" in k for k in keys) and keys[-1] == C.first_scorer_latest_key("basketball_nba")
    assert res["games_priced"] == 1 and res["events_banked"] == 1
    summary = json.loads(dict(fake_store.writes)[keys[1]])
    assert summary[0]["priced"] is True and summary[0]["snapshot"] == "T-30"
    assert len(summary[0]["rows"]) == 10


def test_accumulate_skips_events_outside_every_window(fake_store):
    now = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    tip = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")     # between T-120 and T-30
    client = FakeClient([{"id": "ev1", "commence_time": tip}], {"ev1": event_obj(10)})
    res = FS.accumulate_micro("basketball_nba", client=client, guard=False, now=now)
    assert client.prop_calls == [] and res["events_banked"] == 0               # no prop credits spent


def test_accumulate_partial_board_is_banked_but_not_priced(fake_store):
    now = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
    tip = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    client = FakeClient([{"id": "ev1", "commence_time": tip}], {"ev1": event_obj(3)})
    res = FS.accumulate_micro("basketball_nba", client=client, guard=False, now=now)
    assert res["events_banked"] == 1 and res["games_priced"] == 0


# -------------------------------------------------------------------------------- the two-key gate
def test_runner_gate_requires_both_keys(monkeypatch):
    mod = _load_script()
    assert mod.execution_allowed(False, {})[0] is False
    assert mod.execution_allowed(True, {})[0] is False
    assert mod.execution_allowed(False, {"FIRST_SCORER_BANK_ENABLED": "1"})[0] is False
    assert mod.execution_allowed(True, {"FIRST_SCORER_BANK_ENABLED": "1"})[0] is True
    assert mod.execution_allowed(True, {"FIRST_SCORER_BANK_ENABLED": "0"})[0] is False


def test_runner_plan_mode_makes_no_client_and_no_writes(monkeypatch, capsys):
    mod = _load_script()
    calls = []
    monkeypatch.setattr("nba.odds.first_scorer.accumulate_micro", lambda *a, **k: calls.append(1), raising=False)
    assert mod.main(["--sport", "basketball_nba"], environ={}) == 0
    assert mod.main(["--sport", "basketball_nba", "--execute"], environ={}) == 0
    assert mod.main(["--sport", "americanfootball_nfl"], environ={"FIRST_SCORER_BANK_ENABLED": "1"}) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "PLAN ONLY" in out and "player_first_basket" in out and "player_1st_td" in out
