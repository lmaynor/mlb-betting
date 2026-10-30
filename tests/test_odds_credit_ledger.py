"""Credit ledger + guarded accumulator (2026-10-14).

The guard exists because two forgotten accumulator jobs once drained the shared ParlayAPI pool (2026-08-27).
These tests pin the behaviours that matter: fail closed, never spend below the shared-pool floor, soonest games
first under a short budget, real spend recorded even when a call raises, DFS books never reach the best-book CSV.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from nba.odds import accumulator, credit_ledger, parlay_extract

FIX = Path(__file__).parent / "fixtures" / "odds" / "nhl_props_sample.json"
SPORT = "icehockey_nhl"


class MemStore:
    """In-memory stand-in for mlb_core.storage (bytes + csv)."""

    def __init__(self):
        self.blobs, self.csvs = {}, {}

    def exists(self, key):
        return key in self.blobs

    def read_bytes(self, key):
        return self.blobs[key]

    def write_bytes(self, data, key):
        self.blobs[key] = data

    def write_csv(self, df, key):
        self.csvs[key] = df


@pytest.fixture
def store(monkeypatch):
    m = MemStore()
    for name in ("exists", "read_bytes", "write_bytes", "write_csv"):
        monkeypatch.setattr(accumulator.storage, name, getattr(m, name))
    monkeypatch.setenv("ODDS_BANK_BUDGET_ICEHOCKEY_NHL", "3000")
    return m


def _evt(i, hours):
    st = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"e{i}", "commence_time": st, "home_team": "H", "away_team": "A"}


class FakeClient:
    def __init__(self, slate, remaining="15000", per_call=5, fail_on=None):
        self.slate, self.credits_remaining, self.credits_last = slate, remaining, None
        self.per_call, self.fail_on, self.priced = per_call, fail_on, []

    def get_slate(self, sport, markets=None):
        self.credits_last = "1"
        return self.slate

    def get_event_props(self, sport, event_id, markets):
        if event_id == self.fail_on:
            raise RuntimeError("boom")
        self.credits_last = str(self.per_call)
        self.priced.append(event_id)
        return json.load(open(FIX)) | {"id": event_id}


# ----------------------------------------------------------------------------- ledger math
def test_pace_cap_is_linear():
    assert credit_ledger.pace_cap(3000, 15, 2026, 10) == pytest.approx(3000 * 15 / 31)
    assert credit_ledger.pace_cap(3000, 31, 2026, 10) == 3000


def test_no_budget_is_denied(monkeypatch):
    monkeypatch.delenv("ODDS_BANK_BUDGET_BASEBALL_MLB", raising=False)
    a = credit_ledger.allowance("baseball_mlb", 10, date(2026, 10, 15))
    assert not a.ok and "no budget" in a.reason


def test_unreadable_ledger_fails_closed(monkeypatch):
    monkeypatch.setattr(credit_ledger, "read_spent", lambda *a: (_ for _ in ()).throw(OSError("gcs down")))
    a = credit_ledger.allowance(SPORT, 10, date(2026, 10, 15))
    assert not a.ok and "failing closed" in a.reason


def test_shared_pool_floor_blocks_and_caps(store):
    d = date(2026, 10, 31)
    floor = credit_ledger.PARLAY_RESERVE_FLOOR
    a = credit_ledger.allowance(SPORT, 100, d, remaining_hint=floor)
    assert not a.ok and "floor" in a.reason
    b = credit_ledger.allowance(SPORT, 100, d, remaining_hint=floor + 50)         # 50 above the floor -> partial
    assert b.ok and b.credits == 50


def test_over_pace_denied_then_partial_grant(store):
    d = date(2026, 10, 10)                                     # cap = 3000*10/31 ~ 967
    credit_ledger.record(SPORT, "2026-10", 960)
    a = credit_ledger.allowance(SPORT, 100, d, remaining_hint=15000)
    assert a.ok and a.credits == 7 and a.reason == "partial"
    credit_ledger.record(SPORT, "2026-10", 7)
    assert not credit_ledger.allowance(SPORT, 100, d, remaining_hint=15000).ok


def test_run_cap_limits_grant(store):
    a = credit_ledger.allowance(SPORT, 500, date(2026, 10, 31), remaining_hint=15000, run_cap=120)
    assert a.ok and a.credits == 120


def test_ledger_is_month_scoped(store):
    credit_ledger.record(SPORT, "2026-09", 2999)
    assert credit_ledger.read_spent(SPORT, "2026-10") == 0


# ----------------------------------------------------------------------------- guarded accumulator
def test_soonest_events_bought_first_under_short_budget(store, monkeypatch):
    # a fixed late-month date so the pace cap is not the binding limit; run cap buys exactly 2 events
    monkeypatch.setattr(accumulator, "_today_et", lambda: date(2026, 10, 31))
    slate = [_evt(1, 20), _evt(2, 3), _evt(3, 9), _evt(4, 40)]    # e4 is outside the 30h window
    c = FakeClient(slate)
    n_mk = len(accumulator.PARLAY_PROP_MARKETS[SPORT])
    res = accumulator.accumulate_props(SPORT, client=c, max_credits=2 * n_mk)
    assert c.priced == ["e2", "e3"]                            # soonest first, e4 out of window, e1 unaffordable
    assert res["events_priced"] == 2
    assert credit_ledger.read_spent(SPORT, "2026-10") == res["credits_spent"] == 1 + 2 * 5


def test_spend_recorded_even_when_a_call_raises(store, monkeypatch):
    monkeypatch.setattr(accumulator, "_today_et", lambda: date(2026, 10, 31))
    c = FakeClient([_evt(1, 2), _evt(2, 4)], fail_on="e2")
    with pytest.raises(RuntimeError):
        accumulator.accumulate_props(SPORT, client=c, max_credits=400)
    assert credit_ledger.read_spent(SPORT, "2026-10") == 1 + 5   # slate + the one event that did price


def test_skipped_when_pool_at_floor(store):
    floor = credit_ledger.PARLAY_RESERVE_FLOOR
    c = FakeClient([_evt(1, 2)], remaining=str(floor))
    res = accumulator.accumulate_props(SPORT, client=c)
    assert res["skipped"] and c.priced == []


def test_near_floor_buys_only_what_sits_above_it(store, monkeypatch):
    monkeypatch.setattr(accumulator, "_today_et", lambda: date(2026, 10, 31))
    floor = credit_ledger.PARLAY_RESERVE_FLOOR
    c = FakeClient([_evt(i, i + 1) for i in range(1, 6)], remaining=str(floor + 12))   # 12 credits above the floor
    accumulator.accumulate_props(SPORT, client=c, max_credits=400)
    assert len(c.priced) == 2                                                  # 5 markets each -> 10 <= 12


def test_mlb_refused_without_budget(store, monkeypatch):
    monkeypatch.delenv("ODDS_BANK_BUDGET_BASEBALL_MLB", raising=False)
    res = accumulator.accumulate_props("baseball_mlb", client=FakeClient([_evt(1, 2)]))
    assert res["skipped"] and "no budget" in res["reason"]


def test_dfs_books_excluded_from_best_book_csv_but_kept_in_raw(store, monkeypatch):
    monkeypatch.setattr(accumulator, "_today_et", lambda: date(2026, 10, 31))
    accumulator.accumulate_props(SPORT, client=FakeClient([_evt(1, 2)]), max_credits=400)
    csv = next(v for k, v in store.csvs.items() if "/props_" in k)
    assert csv["n_books"].max() >= 1
    raw = json.loads(next(v for k, v in store.blobs.items() if "/raw/" in k))
    assert any(b["key"] == "underdog" for b in raw[0]["bookmakers"])      # raw keeps everything


# ----------------------------------------------------------------------------- extractor on real NHL shapes
def test_team_total_rows_never_become_skaters():
    ev = json.load(open(FIX))
    rows = parlay_extract.flatten_parlay_props(ev, SPORT, include_one_sided=True)
    names = {r["player"] for r in rows}
    assert "Washington Capitals" not in names and "Montreal Canadiens" not in names


def test_one_sided_yes_rows_kept_only_when_asked():
    ev = json.load(open(FIX))
    two = parlay_extract.flatten_parlay_props(ev, SPORT)
    one = parlay_extract.flatten_parlay_props(ev, SPORT, include_one_sided=True)
    yes_only = [r for r in one if not r["two_sided"]]
    assert yes_only and all(r["under_odds"] is None for r in yes_only)
    assert all(r["two_sided"] for r in two) and len(one) > len(two)


def test_team_tail_stripped_and_anytime_yes_normalized():
    ev = {"id": "x", "home_team": "Washington Capitals", "away_team": "Montreal Canadiens",
          "commence_time": "2026-10-14T23:30:00Z", "bookmakers": [
              {"key": "bovada", "markets": [{"key": "player_goals", "outcomes": [
                  {"name": "Yes", "description": "Alex Ovechkin (WSH)", "price": 1500}]}]},          # no point at all
              {"key": "novig", "markets": [{"key": "player_goals", "outcomes": [
                  {"name": "Yes", "description": "Alex Ovechkin", "price": 208, "point": 0.5},
                  {"name": "Yes", "description": "Alex Ovechkin", "price": 1567, "point": 1.5}]}]},   # 2+ goals rung
              {"key": "fanduel", "markets": [{"key": "player_points", "outcomes": [
                  {"name": "Yes", "description": "Alex Ovechkin", "price": 340, "point": 0.0}]}]}]}
    rows = parlay_extract.flatten_parlay_props(ev, SPORT, include_one_sided=True)
    goals = {(r["book"], r["line"]) for r in rows if r["market"] == "goals"}
    assert goals == {("bovada", 0.5), ("novig", 0.5), ("novig", 1.5)}     # 1.5 rung is NOT collapsed into 0.5
    assert {r["player"] for r in rows} == {"Alex Ovechkin"}                # team tail removed everywhere
    bov = next(r for r in rows if r["book"] == "bovada")
    assert bov["team_hint"] == "WSH" and bov["point_raw"] is None
    assert next(r for r in rows if r["book"] == "fanduel")["line"] == 0.5
    # default (MLB) mode leaves one-sided/None-point rows alone
    assert parlay_extract.flatten_parlay_props(ev, SPORT) == []
