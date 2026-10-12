"""ev.novig.exec: Novig shadow venue via the Kalshi executor's gates/sizing/ledger (fakes, no network)."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from ev.novig import exec as nx
from ev.novig.client import Book
from ev.novig.map import event_index, map_prop_market
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_markets.json")))
EVENTS = event_index(FX["events"])
M = next(m for m in FX["markets"] if m["marketType"] == "RECEIVING_YARDS")
LEG = map_prop_market(M, EVENTS)
T0 = LEG.start_utc - timedelta(hours=5)


class FakeNC:
    def __init__(self, over_bid, under_bid, qty=100_000):
        self.b = {LEG.over_id: [{"price": str(over_bid), "qty": qty}], LEG.under_id: [{"price": str(under_bid), "qty": qty}]}
        self.calls = 0
    def get_book(self, market_id, depth=10):
        self.calls += 1
        return Book(market_id, self.b, 1, T0.timestamp())


def point(p_over=0.55, n=4, disp=0.01):
    return {"player_norm": LEG.player_norm, "stat": LEG.stat, "point": LEG.strike, "p_over": p_over, "n_books": n,
            "disp": disp, "books": ["a", "b", "c", "d"], "n_onesided": 0, "n_dfs": 0, "team_hints": []}


def run(tmp_path, nc, p=None, clock_off=0.5):
    cfg = props_config(env={})
    ledger = Ledger(str(tmp_path / "l.sqlite"))
    risk = RiskGate(cfg, ledger, nx.MODE)
    s = nx.run_pass(snap_ts=T0.isoformat(), cands=[(LEG, p or point(), 300.0)], cfg=cfg, nc=nc, ledger=ledger,
                    risk=risk, clock=lambda: T0 + timedelta(seconds=clock_off), pace_s=0)
    return s, ledger, cfg


def test_to_kbook_scales_units_and_maps_over_to_yes():
    nc = FakeNC(0.45, 0.50, qty=250_000)
    kb = nx.to_kbook(nc.get_book("m"), LEG)
    assert kb.yes_bids == [(0.45, 2500.0)] and kb.no_bids == [(0.5, 2500.0)]
    assert kb.best_ask("yes") == pytest.approx(0.50) and kb.best_ask("no") == pytest.approx(0.55)   # 1 - other side's bid


def test_dust_levels_under_one_unit_are_dropped():
    nc = FakeNC(0.45, 0.50, qty=99)
    kb = nx.to_kbook(nc.get_book("m"), LEG)
    assert kb.yes_bids == [] and kb.no_bids == []


def test_zero_fee_pregame_and_paper_fill_recorded(tmp_path):
    # Over ask = 1 - 0.40(Under bid) = 0.60?  use bids so Over ask is 0.52 with consensus 0.60
    nc = FakeNC(over_bid=0.50, under_bid=0.48)                # Over ask 0.52 (spread 0.02), Under ask 0.50
    s, ledger, cfg = run(tmp_path, nc)
    assert s["hist"].get("EXECUTED") == 1, s
    e = s["executed"][0]
    assert e["side"] == "yes" and e["ask"] == pytest.approx(0.52) and e["filled"] > 0
    # ZERO fee: cost is exactly price * units (Kalshi would add 0.07*p*(1-p))
    with ledger._lock:
        o = [tuple(r) for r in ledger._c.execute("SELECT filled_cost, fill_count, fee, mode FROM orders")][0]
    assert o[2] == 0.0 and o[3] == "novig" and o[0] == pytest.approx(o[1] * 0.52, abs=1e-3)
    assert s["alerts"] and s["alerts"][0]["venue"] == "novig"


def test_group_held_prevents_second_position_and_no_edge_is_rejected(tmp_path):
    nc = FakeNC(over_bid=0.50, under_bid=0.48)
    cfg = props_config(env={})
    ledger = Ledger(str(tmp_path / "l2.sqlite")); risk = RiskGate(cfg, ledger, nx.MODE)
    kw = dict(snap_ts=T0.isoformat(), cands=[(LEG, point(), 300.0)], cfg=cfg, nc=nc, ledger=ledger, risk=risk,
              clock=lambda: T0 + timedelta(seconds=0.5), pace_s=0)
    assert nx.run_pass(**kw)["hist"].get("EXECUTED") == 1
    assert nx.run_pass(**kw)["hist"].get("REJ_GROUP_HELD") == 1
    s, _, _ = run(tmp_path / "x" if False else tmp_path, FakeNC(0.30, 0.30), p=point(p_over=0.50))   # asks 0.70/0.70
    assert not s["executed"]


def test_candidates_filters_thin_dispersed_and_near_kick():
    cfg = props_config(env={})
    legs = {(LEG.game_key, LEG.stat, LEG.player_norm, LEG.strike): LEG}
    snap = [{"game_key": LEG.game_key, "commence": LEG.start_utc.isoformat(), "points": [point(), point(n=1), point(disp=0.2)]}]
    now = LEG.start_utc - timedelta(hours=3)
    assert len(nx.candidates(snap, legs, cfg, now)) == 1
    assert nx.candidates(snap, legs, cfg, LEG.start_utc - timedelta(minutes=5)) == []
    assert nx.candidates(snap, {}, cfg, now) == []
