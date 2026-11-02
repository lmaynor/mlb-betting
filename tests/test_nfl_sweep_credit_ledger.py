"""The NFL props sweep draws from the shared 100k ParlayAPI pool, so every pull is metered through nba.odds.credit_ledger under
NFL_SWEEP_LEDGER_SPORT (it used to spend outside any ledger). In-memory storage, fake ParlayAPI client, no network."""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ev import sweep_nfl_props as sw
from ev.props_config import props_config
from nba import config as C
from nba.odds import credit_ledger

REAL_SWEEP_LEDGER = True                # tests/conftest.py stubs the sweep's ledger unless a module opts out like this

KEY = C.NFL_SWEEP_LEDGER_SPORT
N_MK = len(sw.PARLAY_MARKETS)


class MemStore:
    def __init__(self):
        self.d = {}

    def exists(self, key):
        return key in self.d

    def read_bytes(self, key):
        return self.d[key]

    def write_bytes(self, data, key):
        self.d[key] = data


@pytest.fixture
def store(monkeypatch):
    m = MemStore()
    for name in ("exists", "read_bytes", "write_bytes"):
        monkeypatch.setattr(credit_ledger.storage, name, getattr(m, name))
    monkeypatch.setattr(credit_ledger, "_blob_for", lambda key: None)      # plain read-modify-write, no GCS generations
    monkeypatch.delenv("ODDS_BANK_BUDGET_" + KEY.upper(), raising=False)
    monkeypatch.setattr(sw, "_write", lambda data, key: None)
    monkeypatch.setattr(sw, "_read", lambda key: None)
    return m


def _event(i, hours):
    st = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"e{i}", "commence_time": st, "home_team": "Washington Commanders", "away_team": "Indianapolis Colts"}


class FakePC:
    def __init__(self, slate, remaining="50000"):
        self.slate, self.credits_remaining, self.credits_last = slate, remaining, None
        self.priced, self.slate_calls = [], 0

    def get_slate(self, sport, markets=None):
        self.slate_calls += 1
        self.credits_last = "1"
        return self.slate

    def get_event_props(self, sport, event_id, markets):
        self.credits_last = str(len(markets))
        self.priced.append(event_id)
        return {"id": event_id, "bookmakers": []}


class DeadKalshi:
    def get_series(self, s):
        raise RuntimeError("HTTP 403")

    get_markets = get_series


def _month():
    now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))       # the sweep keys its ledger month on the ET date
    return f"{now.year:04d}-{now.month:02d}"


def _spent():
    return credit_ledger.read_spent(KEY, _month())


def _run(pc, cycle=0):
    return sw.run_cycle(pc, DeadKalshi(), props_config(env={}), cycle, 40.0, {})


def test_cycle_records_the_slate_call_and_every_event_pull(store):
    pc = FakePC([_event(1, 3), _event(2, 5)])
    _run(pc)
    assert pc.priced == ["e1", "e2"] and _spent() == 1 + 2 * N_MK


def test_budget_that_covers_part_of_the_slate_buys_the_soonest_games_first(store, monkeypatch):
    monkeypatch.setenv("ODDS_BANK_BUDGET_" + KEY.upper(), str(1 + 2 * N_MK))      # slate + exactly two games
    pc = FakePC([_event(1, 30), _event(2, 2), _event(3, 9)])
    _run(pc)
    assert pc.priced == ["e2", "e3"] and _spent() == 1 + 2 * N_MK


def test_exhausted_budget_skips_the_cycle_without_calling_the_api(store, monkeypatch):
    monkeypatch.setenv("ODDS_BANK_BUDGET_" + KEY.upper(), "100")
    credit_ledger.record(KEY, _month(), 100)
    pc = FakePC([_event(1, 3)])
    out = _run(pc)
    assert pc.slate_calls == 0 and pc.priced == [] and "over budget" in out["skipped"]


def test_pool_at_the_reserve_floor_skips_the_cycle(store):
    pc = FakePC([_event(1, 3)], remaining=str(C.PARLAY_RESERVE_FLOOR))
    out = _run(pc)
    assert pc.slate_calls == 0 and "floor" in out["skipped"]


def test_unreadable_ledger_fails_closed(store, monkeypatch):
    monkeypatch.setattr(credit_ledger, "read_spent", lambda *a: (_ for _ in ()).throw(OSError("gcs down")))
    pc = FakePC([_event(1, 3)])
    out = _run(pc)
    assert pc.slate_calls == 0 and "failing closed" in out["skipped"]


def test_spend_is_recorded_even_when_a_pull_raises(store):
    class Boom(FakePC):
        def get_event_props(self, sport, event_id, markets):
            if event_id == "e2":
                raise RuntimeError("boom")
            return super().get_event_props(sport, event_id, markets)

    pc = Boom([_event(1, 2), _event(2, 4)])
    with pytest.raises(RuntimeError):
        _run(pc)
    assert _spent() == 1 + N_MK                 # the slate call + the one game that priced


def test_lumpy_sunday_burst_early_in_the_month_is_not_treated_as_over_pace(store):
    # linear pace on day 4 of a 30-day month would allow budget*4/30 (~3900 at 29k); the sweep's cap is the plain monthly budget
    a = credit_ledger.allowance(KEY, 5000, date(2026, 11, 4), remaining_hint=50000, pace=False)
    assert a.ok and a.credits == 5000
    assert credit_ledger.allowance(KEY, 5000, date(2026, 11, 4), remaining_hint=50000).credits < 5000   # paced would clip it


def test_sweep_budget_is_the_config_constant_and_env_overridable(monkeypatch):
    monkeypatch.delenv("ODDS_BANK_BUDGET_" + KEY.upper(), raising=False)
    assert credit_ledger.monthly_budget(KEY) == C.NFL_SWEEP_MONTHLY_BUDGET == 29_000
    monkeypatch.setenv("ODDS_BANK_BUDGET_" + KEY.upper(), "4000")
    assert credit_ledger.monthly_budget(KEY) == 4000


def test_on_demand_refresh_is_metered_and_refused_when_unaffordable(store, monkeypatch):
    pc = FakePC([])
    pc.get_event_props = lambda sport, eid, markets: (setattr(pc, "credits_last", str(len(markets))) or
                                                       {"bookmakers": []})
    now = datetime.now(timezone.utc)
    sw.refresh_single_event_consensus(pc, sw.SPORT, "evt", "2026-10-04T17:00:00Z", "nfl:2026-10-04:IND@WAS", now)
    assert _spent() == N_MK
    monkeypatch.setenv("ODDS_BANK_BUDGET_" + KEY.upper(), str(N_MK))                # now at budget
    pulled = []
    pc.get_event_props = lambda *a: pulled.append(a) or {"bookmakers": []}
    assert sw.refresh_single_event_consensus(pc, sw.SPORT, "evt2", "2026-10-04T17:00:00Z", "g", now) is None
    assert pulled == []
