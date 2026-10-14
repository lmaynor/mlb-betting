"""ev.novig.paper_orders + run_pass paper routing: fakes only, no network, no orders."""
import dataclasses
import json
import os
import uuid
from datetime import timedelta

import pytest
import requests

from ev.novig import exec as nx
from ev.novig import paper_orders as po
from ev.novig.client import Book, NovigAPIError
from ev.novig.map import event_index, map_prop_market
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import OrderIntent

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_markets.json")))
EVENTS = event_index(FX["events"])
M = next(m for m in FX["markets"] if m["marketType"] == "RECEIVING_YARDS")
LEG = map_prop_market(M, EVENTS)
PLEG = dataclasses.replace(LEG, market_id="paper-mkt", over_id="paper-over", under_id="paper-under")
T0 = LEG.start_utc - timedelta(hours=5)


class FakeTC:
    """Stands in for the PAPER NovigClient (trading key)."""
    def __init__(self, fill_qty=200, fill_cost="1.06000", place_exc=None, fills_exc=None, order_resp=None):
        self.placed, self.fill_calls = [], 0
        self.fill_qty, self.fill_cost = fill_qty, fill_cost
        self.place_exc, self.fills_exc = place_exc, fills_exc
        self.order_resp = {"orderId": "ord-1"} if order_resp is None else order_resp

    def place_order(self, outcome_id, price, qty, tif="IOC", client_id=None):
        self.placed.append({"outcome_id": outcome_id, "price": price, "qty": qty, "tif": tif, "client_id": client_id})
        if self.place_exc:
            raise self.place_exc
        return self.order_resp

    def fills(self, **q):
        self.fill_calls += 1
        if self.fills_exc:
            raise self.fills_exc
        if not self.placed or not self.fill_qty:
            return {"items": []}
        cid = self.placed[-1]["client_id"]
        return {"items": [
            {"fillId": "f-other", "orderId": "x", "clientId": "someone-else", "qty": 5, "cost": "0.05", "taker": True, "ts": 1},
            {"fillId": "f-1", "orderId": "ord-1", "clientId": cid, "marketId": "paper-mkt", "outcomeId": "paper-over",
             "qty": self.fill_qty, "cost": self.fill_cost, "taker": True, "ts": 1791946617933}]}


def venue(tc, legs=None):
    return po.NovigPaperVenue(tc, {po.leg_key(LEG): PLEG} if legs is None else legs, sleep=lambda s: None)


def intent(side="yes", units=2, limit=0.5373, iid="abc"):
    return OrderIntent(iid, LEG.market_id, side, units, limit, nx.NOVIG_RANGES, None, fee_multiplier=0.0)


# ------------------------------------------------------------------ the venue
def test_yes_goes_to_over_outcome_with_cent_contract_qty_and_snapped_price():
    tc = FakeTC()
    ack = venue(tc).submit(intent("yes", units=2, limit=0.5373), LEG)
    p = tc.placed[0]
    assert p["outcome_id"] == "paper-over" and p["qty"] == 200 and p["tif"] == "IOC"
    assert p["price"] == "0.535"                                   # snapped DOWN onto the 0.005 band, never above the limit
    assert ack.status == "FILLED"


def test_no_goes_to_under_outcome():
    tc = FakeTC()
    venue(tc).submit(intent("no"), LEG)
    assert tc.placed[0]["outcome_id"] == "paper-under"


def test_client_id_is_a_deterministic_uuid_of_the_intent():
    tc = FakeTC()
    venue(tc).submit(intent(iid="same"), LEG)
    venue(FakeTC()).submit(intent(iid="same"), LEG)
    cid = tc.placed[0]["client_id"]
    assert cid == po.client_id_for("same") and str(uuid.UUID(cid)) == cid
    assert po.client_id_for("same") != po.client_id_for("other")


def test_fill_price_is_derived_from_cost_and_qty_and_foreign_fills_are_ignored():
    ack = venue(FakeTC(fill_qty=200, fill_cost="1.06000")).submit(intent(units=2), LEG)
    assert ack.fill_count == pytest.approx(2.0) and ack.filled_cost == pytest.approx(1.06)
    assert len(ack.fills) == 1 and ack.fills[0].price == pytest.approx(0.53) and ack.fills[0].fee == 0.0
    assert ack.order_id == "ord-1" and ack.status == "FILLED"


def test_partial_and_no_fill_statuses():
    assert venue(FakeTC(fill_qty=100, fill_cost="0.53")).submit(intent(units=2), LEG).status == "PARTIAL"
    ack = venue(FakeTC(fill_qty=0)).submit(intent(units=2), LEG)
    assert ack.status == "NO_FILL" and ack.fill_count == 0


def test_no_paper_market_sends_nothing():
    tc = FakeTC()
    ack = venue(tc, legs={}).submit(intent(), LEG)
    assert ack.status == "NO_MARKET" and tc.placed == []


def test_definite_rejection_is_an_error_and_is_never_retried():
    tc = FakeTC(place_exc=NovigAPIError(400, "BAD_PRICE", "off grid", "POST", "/v3/orders"))
    ack = venue(tc).submit(intent(), LEG)
    assert ack.status == "ERROR" and len(tc.placed) == 1 and tc.fill_calls == 0


@pytest.mark.parametrize("exc", [requests.ConnectionError("reset"), requests.Timeout("slow"),
                                 NovigAPIError(503, "UNAVAILABLE", "x", "POST", "/v3/orders")])
def test_ambiguous_failure_reconciles_by_client_id_and_never_resends(exc):
    tc = FakeTC(place_exc=exc)                                      # the order "actually landed": fills exist
    ack = venue(tc).submit(intent(units=2), LEG)
    assert len(tc.placed) == 1                                      # ONE post, no retry (Novig has no idempotency)
    assert ack.status == "FILLED" and ack.raw["reconciled"] is True


def test_ambiguous_failure_with_no_fill_is_unknown_not_resent():
    tc = FakeTC(place_exc=requests.Timeout("slow"), fill_qty=0)
    ack = venue(tc).submit(intent(), LEG)
    assert ack.status == "UNKNOWN" and len(tc.placed) == 1 and "not re-sent" in ack.error


def test_ambiguous_failure_and_reconcile_failure_is_unknown():
    tc = FakeTC(place_exc=requests.Timeout("slow"), fills_exc=requests.ConnectionError("down"))
    ack = venue(tc).submit(intent(), LEG)
    assert ack.status == "UNKNOWN" and len(tc.placed) == 1


# ------------------------------------------------------------------ routing through run_pass
class FakeNC:
    def __init__(self, over_bid, under_bid, qty=100_000):
        self.b = {LEG.over_id: [{"price": str(over_bid), "qty": qty}], LEG.under_id: [{"price": str(under_bid), "qty": qty}]}

    def get_book(self, market_id, depth=10):
        return Book(market_id, self.b, 1, T0.timestamp())


def point(p_over=0.55):
    return {"player_norm": LEG.player_norm, "stat": LEG.stat, "point": LEG.strike, "p_over": p_over, "n_books": 4,
            "disp": 0.01, "books": list("abcd"), "n_onesided": 0, "n_dfs": 0, "team_hints": [],
            "book_fair": {b: p_over for b in "abcd"}}


def run(tmp_path, *, tc=None, paper_killed=False, ledger=None, legs=None):
    cfg = props_config(env={})
    ledger = ledger or Ledger(str(tmp_path / "l.sqlite"))
    risk, prisk = RiskGate(cfg, ledger, nx.MODE), po.PaperRiskGate(cfg, ledger, nx.PAPER_MODE)
    if paper_killed:
        prisk.trip("test")
    pv = venue(tc, legs) if tc is not None else None
    s = nx.run_pass(snap_ts=T0.isoformat(), cands=[(LEG, point(), 300.0)], cfg=cfg, nc=FakeNC(0.50, 0.48), ledger=ledger,
                    risk=risk, clock=lambda: T0 + timedelta(seconds=0.5), pace_s=0,
                    paper_venue=pv, paper_risk=prisk if pv else None)
    return s, ledger


def paper_rows(ledger):
    with ledger._lock:
        return [tuple(r) for r in ledger._c.execute(
            "SELECT mode, ticker, side, status, intent_id FROM orders WHERE mode=?", (nx.PAPER_MODE,))]


def test_a_plan_is_mirrored_to_the_paper_sandbox_and_recorded_in_its_own_mode(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, tc=tc)
    assert s["hist"].get("EXECUTED") == 1 and len(tc.placed) == 1
    rows = paper_rows(ledger)
    assert len(rows) == 1 and rows[0][1] == "paper-mkt" and rows[0][2] == "yes" and rows[0][3] in ("FILLED", "PARTIAL")
    assert rows[0][4].startswith("p-")
    assert s["paper_orders"] and s["paper_orders"][0]["status"] in ("FILLED", "PARTIAL")
    # the production shadow row is untouched and separate
    with ledger._lock:
        modes = [r[0] for r in ledger._c.execute("SELECT mode FROM orders")]
    assert sorted(modes) == [nx.MODE, nx.PAPER_MODE]


def test_an_intent_is_never_sent_twice(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, tc=tc)
    run(tmp_path, tc=tc, ledger=ledger)                              # same day, same leg, same decision again
    assert len(tc.placed) == 1 and len(paper_rows(ledger)) == 1


def test_without_a_paper_venue_nothing_is_sent_and_nothing_recorded(tmp_path):
    s, ledger = run(tmp_path, tc=None)
    assert s["hist"].get("EXECUTED") == 1 and s["paper_orders"] == [] and paper_rows(ledger) == []


def test_killed_paper_gate_stops_orders_but_not_the_shadow_pass(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, tc=tc, paper_killed=True)
    assert s["hist"].get("EXECUTED") == 1 and tc.placed == [] and paper_rows(ledger) == []
    assert not RiskGate(props_config(env={}), ledger, nx.MODE).killed      # a paper kill never halts the shadow venue


def test_unmatched_leg_is_skipped_without_counting_as_an_exchange_error(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, tc=tc, legs={})
    assert tc.placed == [] and paper_rows(ledger) == []
    assert s["paper_orders"] == [{"description": LEG.description, "status": "NO_MARKET"}]


def test_repeated_paper_errors_trip_only_the_paper_kill_switch(tmp_path):
    cfg = props_config(env={})
    ledger = Ledger(str(tmp_path / "l.sqlite"))
    prisk = po.PaperRiskGate(cfg, ledger, nx.PAPER_MODE)
    tc = FakeTC(place_exc=NovigAPIError(400, "X", "bad", "POST", "/v3/orders"))
    for i in range(cfg.max_consecutive_rejects):
        d = type("D", (), {"intent_id": f"i{i}", "side": "yes",
                           "plan": type("P", (), {"contracts": 1, "limit_price": 0.5})()})()
        xl = type("X", (), {"game_pk": "g"})()
        nx._paper_attempt(venue(tc), prisk, ledger, "2026-10-14", LEG, xl, d)
    assert prisk.killed and not RiskGate(cfg, ledger, nx.MODE).killed
