"""Single-event consensus refresh: sweep helper + executor ConsRefresher / run_pass wiring (fakes only, no network)."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from ev import props_exec as px
from ev import sweep_nfl_props as sw
from ev.kalshi_props import map_prop_market
from ev.sports.nfl import TEAM_NAMES
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import PaperVenue
from tests.test_ev_props_exec import FakeData, row

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ev")
KMK = json.load(open(os.path.join(FIX, "kalshi_prop_markets.json")))
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
COMM = "2026-10-04T17:00:00Z"
GK = "nfl:2026-10-04:IND@WAS"


def _payload(price=(-110, -110), books=("draftkings", "fanduel", "betmgm")):
    leg = map_prop_market(KMK["KXNFLRECYDS"])
    bks = [{"key": b, "markets": [{"key": "player_reception_yds", "outcomes": [
        {"name": f"Over {leg.player}", "description": leg.player, "price": price[0], "point": leg.strike},
        {"name": f"Under {leg.player}", "description": leg.player, "price": price[1], "point": leg.strike}]}]}
        for b in books]
    return leg, {"bookmakers": bks}


class FakePC:
    def __init__(self, props=None, slate=None):
        self.props, self.slate = props, slate or []
        self.prop_calls, self.slate_calls = [], 0

    def get_event_props(self, sport, event_id, markets):
        self.prop_calls.append((sport, event_id, tuple(markets)))
        return self.props

    def get_slate(self, sport, markets=None):
        self.slate_calls += 1
        return self.slate


# ---------------- sweep helper ----------------
def test_helper_pulls_one_event_for_the_five_markets_and_stamps_fetch_time():
    leg, props = _payload()
    pc = FakePC(props)
    entry = sw.refresh_single_event_consensus(pc, sw.SPORT, "evt9", COMM, leg.game_key, NOW)
    assert pc.prop_calls == [(sw.SPORT, "evt9", tuple(sw.PARLAY_MARKETS))]
    assert len(sw.PARLAY_MARKETS) == 5                                          # the "5 credits" the executor budgets
    assert entry["game_key"] == leg.game_key and entry["event_id"] == "evt9" and entry["ts"] == NOW.isoformat()
    pt = next(p for p in entry["points"] if p["stat"] == leg.stat and p["point"] == leg.strike)
    assert pt["n_books"] == 3 and pt["p_over"] == pytest.approx(0.5, abs=1e-6)


def test_helper_returns_none_when_the_pull_is_empty():
    assert sw.refresh_single_event_consensus(FakePC(None), sw.SPORT, "e", COMM, GK, NOW) is None
    assert sw.refresh_single_event_consensus(FakePC({}), sw.SPORT, "e", COMM, GK, NOW) is None


def test_published_snapshot_now_carries_the_event_id():
    leg, props = _payload()
    ev = {"id": "evt7", "away_team": {v: k for k, v in TEAM_NAMES.items()}[leg.away],
          "home_team": {v: k for k, v in TEAM_NAMES.items()}[leg.home], "commence_time": COMM}
    snap, _ = sw.consensus_snapshot([{"event": ev, "props": props}])
    assert snap[0]["event_id"] == "evt7" and snap[0]["points"]


# ---------------- ConsRefresher ----------------
def _refresher(pc, **kw):
    r = px.ConsRefresher(pc, after_min=5.0, max_credits=kw.pop("max_credits", 300), **kw)
    r.update_events([{"game_key": GK, "commence": COMM, "event_id": "evt1"}])
    return r


def test_refresher_caches_per_game_until_the_entry_is_stale_again():
    leg, props = _payload()
    pc = FakePC(props)
    r = _refresher(pc)
    e1 = r.fresh(GK, NOW)
    assert e1 is not None and r.fresh(GK, NOW + timedelta(minutes=4)) is e1 and len(pc.prop_calls) == 1
    assert r.fresh(GK, NOW + timedelta(minutes=6)) is not e1 and len(pc.prop_calls) == 2
    assert r.spent == 10 and r.refreshes == 2


def test_refresher_budget_exhaustion_returns_none_without_pulling():
    leg, props = _payload()
    pc = FakePC(props)
    r = _refresher(pc, max_credits=7)
    assert r.fresh(GK, NOW) is not None                                          # 5 of 7
    assert r.fresh(GK, NOW + timedelta(minutes=10)) is None                       # needs 5, 2 left
    assert len(pc.prop_calls) == 1 and r.spent == 5


def test_refresher_falls_back_to_one_slate_call_when_snapshot_has_no_event_id():
    leg, props = _payload()
    away = {v: k for k, v in TEAM_NAMES.items()}
    slate = [{"id": "evt-from-slate", "away_team": away[leg.away], "home_team": away[leg.home], "commence_time": COMM}]
    pc = FakePC(props, slate=slate)
    r = px.ConsRefresher(pc, after_min=5.0, max_credits=300)                     # no update_events: old snapshot
    assert r.fresh(leg.game_key, NOW) is not None
    assert pc.slate_calls == 1 and pc.prop_calls[0][1] == "evt-from-slate" and r.spent == 1 + 5
    assert r.fresh("nfl:2026-10-04:XXX@YYY", NOW) is None and pc.slate_calls == 1  # cached slate, unknown game -> None


def test_refresher_swallows_a_failed_pull():
    class Boom(FakePC):
        def get_event_props(self, *a):
            raise RuntimeError("net down")
    assert _refresher(Boom()).fresh(GK, NOW) is None


# ---------------- run_pass integration ----------------
class StubRefresher:
    after_min, spent = 5.0, 0

    def __init__(self, entry):
        self.entry, self.calls = entry, 0

    def fresh(self, gk, now):
        self.calls += 1
        return self.entry


def _entry(p_over=0.60, n_books=4, disp=0.01, strike=49.5):
    return {"ts": NOW.isoformat(), "event_id": "e", "game_key": GK, "commence": COMM, "curves": [],
            "points": [{"player_norm": "terry mclaurin", "stat": "rec_yds", "point": strike, "p_over": p_over,
                        "n_books": n_books, "disp": disp, "books": [], "n_onesided": 0, "n_dfs": 0,
                        "team_hints": ["WAS"], "book_fair": {}}]}


def _run_stale(tmp_path, refresher):
    cfg = px.props_config(env={})
    ledger = Ledger(str(tmp_path / "l.sqlite"))
    data = FakeData()
    stale_ts = NOW - timedelta(minutes=cfg.cons_max_age_min + 5)                  # past the hard age gate
    clock = lambda: NOW + timedelta(seconds=0.5)
    s = px.run_pass(latest={"ts": stale_ts.isoformat(), "rows": [row()]}, cfg=cfg, data_client=data,
                    paper=PaperVenue(data.get_orderbook), ledger=ledger, risk=RiskGate(cfg, ledger, "shadow"),
                    clock=clock, refresher=refresher)
    return s, ledger


def test_stale_consensus_is_rejected_without_a_refresher(tmp_path):
    s, _ = _run_stale(tmp_path, None)
    assert s["hist"] == {"REJ_CONS_STALE": 1} and s["cons_refreshed"] == 0


def test_fresh_pull_rescues_a_stale_candidate_and_is_recorded_in_the_ledger(tmp_path):
    rf = StubRefresher(_entry())
    s, ledger = _run_stale(tmp_path, rf)
    assert s["hist"].get("EXECUTED") == 1 and s["cons_refreshed"] == 1 and rf.calls == 1
    with ledger._lock:
        inputs = ledger._c.execute("SELECT inputs_json FROM decisions").fetchone()[0]
    assert json.loads(inputs)["cons_refreshed"] is True


def test_fresh_consensus_that_dropped_the_point_rejects_instead_of_reusing_the_old_one(tmp_path):
    s, _ = _run_stale(tmp_path, StubRefresher(_entry(strike=59.5)))               # fresh book view has other strike only
    assert s["hist"] == {"REJ_CONS_THIN": 1}


def test_fresh_but_thin_or_moved_consensus_is_gated_on_the_new_numbers(tmp_path):
    assert _run_stale(tmp_path, StubRefresher(_entry(n_books=1)))[0]["hist"] == {"REJ_CONS_THIN": 1}
    # the book market moved against the bet: fair 0.40 < ask 0.52 on YES, NO side not +EV either -> no PLAN
    moved = _run_stale(tmp_path, StubRefresher(_entry(p_over=0.46)))[0]["hist"]
    assert "EXECUTED" not in moved


def test_failed_refresh_leaves_the_age_gate_to_reject(tmp_path):
    rf = StubRefresher(None)
    s, _ = _run_stale(tmp_path, rf)
    assert s["hist"] == {"REJ_CONS_STALE": 1} and rf.calls == 1 and s["cons_refreshed"] == 0


def test_refresher_is_not_consulted_while_consensus_is_fresh(tmp_path):
    cfg = px.props_config(env={})
    ledger = Ledger(str(tmp_path / "l.sqlite"))
    data, rf = FakeData(), StubRefresher(_entry())
    s = px.run_pass(latest={"ts": NOW.isoformat(), "rows": [row()]}, cfg=cfg, data_client=data,
                    paper=PaperVenue(data.get_orderbook), ledger=ledger, risk=RiskGate(cfg, ledger, "shadow"),
                    clock=lambda: NOW + timedelta(seconds=0.5), refresher=rf)
    assert rf.calls == 0 and s["cons_refreshed"] == 0
