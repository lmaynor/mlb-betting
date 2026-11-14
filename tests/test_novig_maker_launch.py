"""
tests.test_novig_maker_launch -- unit tests for NOV-03 maker launch runner, venue, and study bank.
All fakes, zero network calls.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from ev.novig import maker, maker_bank, maker_study, maker_venue
from ev.novig import exec as nx
from ev.novig import shadow_bank as sb
from ev.novig.client import Book, NovigAPIError
from ev.novig.liability import LiabilityConfig
from ev.novig.maker_runner import AssembledCandidate, MAKER_MODE, run
from ev.novig.maker_venue import MakerPaperVenue, client_id_for_maker
from ev.novig.map import NovigLeg
from ev.novig.paper_orders import PaperRiskGate, leg_key
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger

UTC = timezone.utc
T0 = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)


# ------------------------------------------------------------------ fakes
class FakePaperClient:
    def __init__(self):
        self.placed_orders: list[dict] = []
        self.cancelled_orders: list[str] = []
        self.fills_list: list[dict] = []
        self.raise_on_place: Exception | None = None
        self.raise_on_cancel: Exception | None = None
        self.positions_called = False
        self.order_counter = 0

    def positions(self):
        self.positions_called = True
        return {}

    def place_order(self, outcome_id: str, price: str, qty: int, tif: str = "PO", client_id: str | None = None) -> dict:
        if self.raise_on_place is not None:
            raise self.raise_on_place
        self.order_counter += 1
        oid = f"ord-{self.order_counter}"
        rec = {
            "orderId": oid,
            "id": oid,
            "outcomeId": outcome_id,
            "price": price,
            "qty": qty,
            "tif": tif,
            "clientId": client_id,
            "status": "OPEN",
        }
        self.placed_orders.append(rec)
        return rec

    def cancel_order(self, order_id: str) -> dict:
        if self.raise_on_cancel is not None:
            raise self.raise_on_cancel
        self.cancelled_orders.append(order_id)
        return {"orderId": order_id, "status": "CANCELLED"}

    def get_order(self, order_id: str) -> dict:
        for o in self.placed_orders:
            if o["orderId"] == order_id:
                st = "CANCELLED" if order_id in self.cancelled_orders else o["status"]
                return dict(o, status=st)
        return {"orderId": order_id, "status": "CANCELLED"}

    def list_orders(self, **q) -> dict:
        items = [dict(o, status="CANCELLED" if o["orderId"] in self.cancelled_orders else o["status"])
                 for o in self.placed_orders if o["orderId"] not in self.cancelled_orders]
        return {"items": items, "next": None}

    def fills(self, **q) -> dict:
        return {"items": list(self.fills_list), "next": None}

    def list_events(self, league: str, **q) -> list:
        return []

    def list_markets(self, league: str, **q) -> list:
        return []


class FakePublicClient:
    def __init__(self, books: dict[str, Book] | None = None):
        self.books = dict(books or {})

    def get_book(self, market_id: str, depth: int = 10) -> Book:
        if market_id in self.books:
            return self.books[market_id]
        return Book(market_id, {}, seq=1, fetched_at=1700000000.0)

    def list_events(self, league: str, **q) -> list:
        return []

    def list_markets(self, league: str, **q) -> list:
        return []


def _make_leg(game_key="nhl:2026-11-14:DET@CHI", stat="sog", player="Patrick Kane",
              strike=3.5, market_id="mkt-prod-1", over_id="out-over-1", under_id="out-under-1"):
    return NovigLeg(
        market_id=market_id,
        event_id="ev-1",
        stat=stat,
        player=player,
        player_norm="patrick kane",
        strike=strike,
        game_key=game_key,
        start_utc=T0 + timedelta(hours=2),
        over_id=over_id,
        under_id=under_id,
        fee_coefficient=0.0,
        fee_charged="WHEN_LIVE",
        description=f"{player} Over/Under {strike} {stat}",
    )


def _make_book(market_id: str, over_id: str, under_id: str, bid_over: float | None = 0.40,
               bid_under: float | None = 0.48) -> Book:
    orders = {}
    if bid_over is not None:
        orders[over_id] = [{"price": str(bid_over), "qty": 1000}]
    if bid_under is not None:
        orders[under_id] = [{"price": str(bid_under), "qty": 1000}]
    return Book(market_id, orders, seq=1, fetched_at=1700000000.0)


# ------------------------------------------------------------------ 1. venue tests
def test_venue_no_market_sends_nothing():
    fake = FakePaperClient()
    venue = MakerPaperVenue(fake, paper_legs={})
    prod_leg = _make_leg()
    res = venue.place_resting(prod_leg, intent_id="intent-1", side="yes", price=0.45, contracts_units=10)
    assert res["status"] == "NO_MARKET"
    assert len(fake.placed_orders) == 0


def test_venue_po_tif_and_qty_and_snap_price():
    fake = FakePaperClient()
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1", over_id="paper-over-1", under_id="paper-under-1")
    venue = MakerPaperVenue(fake, paper_legs={leg_key(prod_leg): paper_leg})

    res = venue.place_resting(prod_leg, intent_id="intent-2", side="yes", price=0.412, contracts_units=8.0)
    assert res["status"] == "OPEN"
    assert len(fake.placed_orders) == 1
    call = fake.placed_orders[0]
    assert call["tif"] == "PO"
    assert call["qty"] == 800  # 8 units * 100
    assert call["price"] == "0.410"  # snapped down from 0.412
    assert call["outcomeId"] == "paper-over-1"
    assert call["clientId"] == client_id_for_maker("intent-2")


def test_venue_ambiguous_5xx_with_fills_reconciles_open():
    fake = FakePaperClient()
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1")
    venue = MakerPaperVenue(fake, paper_legs={leg_key(prod_leg): paper_leg})

    cid = client_id_for_maker("intent-3")
    fake.raise_on_place = NovigAPIError(500, "INTERNAL", "Server error", "POST", "/v3/orders")
    fake.fills_list = [{"fillId": "f-1", "orderId": "ord-reconciled", "clientId": cid, "qty": 800, "cost": "328.0", "ts": 1700000000000}]

    res = venue.place_resting(prod_leg, intent_id="intent-3", side="yes", price=0.41, contracts_units=8)
    assert res["status"] == "OPEN"
    assert res["reconciled"] is True
    assert res["order_id"] == "ord-reconciled"


def test_venue_ambiguous_5xx_without_fills_returns_unknown():
    fake = FakePaperClient()
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1")
    venue = MakerPaperVenue(fake, paper_legs={leg_key(prod_leg): paper_leg})

    fake.raise_on_place = requests.RequestException("connection timeout")
    fake.fills_list = []

    res = venue.place_resting(prod_leg, intent_id="intent-4", side="yes", price=0.41, contracts_units=8)
    assert res["status"] == "UNKNOWN"
    assert res["order_id"] is None
    assert "no fill found" in res["error"]


def test_venue_definite_4xx_returns_error():
    fake = FakePaperClient()
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1")
    venue = MakerPaperVenue(fake, paper_legs={leg_key(prod_leg): paper_leg})

    fake.raise_on_place = NovigAPIError(400, "BAD_REQUEST", "Price crosses book", "POST", "/v3/orders")
    res = venue.place_resting(prod_leg, intent_id="intent-5", side="yes", price=0.41, contracts_units=8)
    assert res["status"] == "ERROR"
    assert "Price crosses book" in res["error"]


# ------------------------------------------------------------------ 2. runner once-cycle
def test_runner_once_cycle_posts_order_and_banks(tmp_path):
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1", over_id="paper-over-1", under_id="paper-under-1")
    book = _make_book("mkt-prod-1", "out-over-1", "out-under-1", bid_over=0.40, bid_under=0.48)  # ask 0.52 -> spread 0.12 >= 0.08
    public_nc = FakePublicClient({"mkt-prod-1": book})
    paper_client = FakePaperClient()
    venue = MakerPaperVenue(paper_client, paper_legs={leg_key(prod_leg): paper_leg})
    store = sb.DirStore(str(tmp_path))

    def fake_nhl_snap():
        proj = {
            "ts": T0.isoformat(),
            "rows": [{
                "stat": "sog", "has_line": True, "line": 3.5, "p_over_model": 0.52,
                "commence": (T0 + timedelta(hours=2)).isoformat(), "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane", "player": "Patrick Kane", "team": "DET"
            }]
        }
        board = {
            "rows": [{
                "stat": "sog", "line": 3.5, "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane",
                "books": [{"book": "fanduel", "over": -110, "under": -110},
                          {"book": "draftkings", "over": -110, "under": -110},
                          {"book": "pinnacle", "over": -110, "under": -110},
                          {"book": "caesars", "over": -110, "under": -110},
                          {"book": "betrivers", "over": -110, "under": -110}]
            }]
        }
        return proj, board

    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )

    exit_code = run(
        args, store=store, public_nc=public_nc, paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0, sleep=lambda s: None,
        snapshot_fns={"NHL": fake_nhl_snap, "nhl_goalies": lambda: None},
        legs_by_league={"NHL": {leg_key(prod_leg): prod_leg}},
        paper_legs_by_league={"NHL": {leg_key(prod_leg): paper_leg}},
    )

    assert exit_code == 0
    assert len(paper_client.placed_orders) == 1
    placed = paper_client.placed_orders[0]
    assert placed["tif"] == "PO"
    assert placed["price"] == "0.410"

    # Check banked quote_states
    states = maker_bank.load_quote_states(store)
    assert len(states) == 1
    st = list(states.values())[0]
    assert st["terminal_status"] == "RESTING"
    assert st["stat"] == "sog"
    assert st["sport"] == "NHL"
    assert st["player_norm"] == "patrick kane"
    assert st["quote_price"] == 0.410

    # Second cycle with same candidate -> posts NOTHING new (hygiene / dedup)
    exit_code_2 = run(
        args, store=store, public_nc=public_nc, paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0 + timedelta(seconds=30), sleep=lambda s: None,
        snapshot_fns={"NHL": fake_nhl_snap, "nhl_goalies": lambda: None},
        legs_by_league={"NHL": {leg_key(prod_leg): prod_leg}},
        paper_legs_by_league={"NHL": {leg_key(prod_leg): paper_leg}},
    )
    assert exit_code_2 == 0
    assert len(paper_client.placed_orders) == 1  # still 1, no new order


# ------------------------------------------------------------------ 3. cancel path
def test_runner_cancel_path_on_consensus_velocity(tmp_path):
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1", over_id="paper-over-1", under_id="paper-under-1")
    book = _make_book("mkt-prod-1", "out-over-1", "out-under-1", bid_over=0.40, bid_under=0.48)
    public_nc = FakePublicClient({"mkt-prod-1": book})
    paper_client = FakePaperClient()
    venue = MakerPaperVenue(paper_client, paper_legs={leg_key(prod_leg): paper_leg})
    store = sb.DirStore(str(tmp_path))

    # Pre-seed banked resting state with p_cons_at_post = 0.50
    st_initial = maker_bank.new_quote_state(
        order_id="ord-resting-1",
        client_id=client_id_for_maker("intent-cancel-test"),
        intent_id="intent-cancel-test",
        game_key="nhl:2026-11-14:DET@CHI",
        game_day="2026-11-14",
        sport="NHL",
        stat="sog",
        player_norm="patrick kane",
        player="Patrick Kane",
        line=3.5,
        side="yes",
        t_post=T0.isoformat(),
        quote_price=0.410,
        contracts_units=12,
        qty=1200,
        p_cons_at_post=0.50,
        terminal_status="RESTING",
        commence=(T0 + timedelta(hours=2)).isoformat(),
    )
    maker_bank.bank_rows(store, [("quote_state", st_initial)], now=T0)
    # Register order as open in fake paper client
    paper_client.placed_orders.append({
        "orderId": "ord-resting-1",
        "id": "ord-resting-1",
        "outcomeId": "paper-over-1",
        "price": "0.410",
        "qty": 1200,
        "tif": "PO",
        "clientId": client_id_for_maker("intent-cancel-test"),
        "status": "OPEN",
    })

    # Consensus moves against our side by >= 1.5pp (e.g. from 0.50 to 0.48, drop of 2pp)
    def moved_nhl_snap():
        proj = {
            "ts": (T0 + timedelta(minutes=5)).isoformat(),
            "rows": [{
                "stat": "sog", "has_line": True, "line": 3.5, "p_over_model": 0.48,
                "commence": (T0 + timedelta(hours=2)).isoformat(), "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane", "player": "Patrick Kane", "team": "DET"
            }]
        }
        board = {
            "rows": [{
                "stat": "sog", "line": 3.5, "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane",
                "books": [{"book": "fanduel", "over": +110, "under": -130},  # p_over ~ 0.47
                          {"book": "draftkings", "over": +110, "under": -130},
                          {"book": "pinnacle", "over": +110, "under": -130},
                          {"book": "caesars", "over": +110, "under": -130}]
            }]
        }
        return proj, board

    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )

    exit_code = run(
        args, store=store, public_nc=public_nc, paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0 + timedelta(minutes=5), sleep=lambda s: None,
        snapshot_fns={"NHL": moved_nhl_snap, "nhl_goalies": lambda: None},
        legs_by_league={"NHL": {leg_key(prod_leg): prod_leg}},
        paper_legs_by_league={"NHL": {leg_key(prod_leg): paper_leg}},
    )

    assert exit_code == 0
    assert "ord-resting-1" in paper_client.cancelled_orders

    # State in bank is now CANCELLED with consensus_velocity_against
    states = maker_bank.load_quote_states(store)
    st = states["ord-resting-1"]
    assert st["terminal_status"] == "CANCELLED"
    assert "consensus_velocity_against" in st["cancel_reason"]


# ------------------------------------------------------------------ 4. kill switch
def test_runner_kill_switch_cancels_resting_and_stops(tmp_path):
    store = sb.DirStore(str(tmp_path))
    ledger = Ledger(":memory:")
    cfg = props_config(bankroll=1000.0)
    gate = PaperRiskGate(cfg, ledger, MAKER_MODE)
    gate.trip("manual halt")

    # Bank a resting quote
    st = maker_bank.new_quote_state(
        order_id="ord-kill-1",
        client_id="cid-kill-1",
        intent_id="intent-kill-1",
        game_key="nhl:2026-11-14:DET@CHI",
        sport="NHL",
        stat="sog",
        player_norm="patrick kane",
        line=3.5,
        side="yes",
        t_post=T0.isoformat(),
        quote_price=0.410,
        contracts_units=10,
        qty=1000,
        p_cons_at_post=0.50,
        terminal_status="RESTING",
        commence=(T0 + timedelta(hours=2)).isoformat(),
    )
    maker_bank.bank_rows(store, [("quote_state", st)], now=T0)

    paper_client = FakePaperClient()
    paper_client.placed_orders.append({
        "orderId": "ord-kill-1", "id": "ord-kill-1", "outcomeId": "out-1",
        "price": "0.410", "qty": 1000, "tif": "PO", "clientId": "cid-kill-1", "status": "OPEN"
    })
    venue = MakerPaperVenue(paper_client, paper_legs={})

    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )

    exit_code = run(
        args, store=store, public_nc=FakePublicClient(), paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0, sleep=lambda s: None,
        snapshot_fns={"NHL": lambda: (None, None)},
        ledger=ledger
    )

    assert exit_code == 1
    assert "ord-kill-1" in paper_client.cancelled_orders

    # State updated to CANCELLED with paper_risk_killed
    states = maker_bank.load_quote_states(store)
    st_after = states["ord-kill-1"]
    assert st_after["terminal_status"] == "CANCELLED"
    assert "paper_risk_killed:manual halt" in st_after["cancel_reason"]


# ------------------------------------------------------------------ 5. STUDY_ARMED gate
def test_runner_refuses_when_study_not_armed(monkeypatch, tmp_path):
    monkeypatch.setattr(maker_study, "STUDY_ARMED", False)
    paper_client = FakePaperClient()
    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )
    exit_code = run(
        args, store=sb.DirStore(str(tmp_path)), public_nc=FakePublicClient(),
        paper_client=paper_client, clock=lambda: T0, sleep=lambda s: None
    )
    assert exit_code == 2
    assert paper_client.positions_called is False
    assert len(paper_client.placed_orders) == 0


# ------------------------------------------------------------------ 6. liability cap
def test_runner_liability_cap_restricts_orders(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVIG_LIAB_BANKROLL", "20.0")  # $20 bankroll -> 1% per-bet cap is $0.20 < $2.00 min_stake
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1")
    book = _make_book("mkt-prod-1", "out-over-1", "out-under-1", bid_over=0.40, bid_under=0.48)
    public_nc = FakePublicClient({"mkt-prod-1": book})
    paper_client = FakePaperClient()
    venue = MakerPaperVenue(paper_client, paper_legs={leg_key(prod_leg): paper_leg})
    store = sb.DirStore(str(tmp_path))

    def fake_nhl_snap():
        proj = {
            "ts": T0.isoformat(),
            "rows": [{
                "stat": "sog", "has_line": True, "line": 3.5, "p_over_model": 0.52,
                "commence": (T0 + timedelta(hours=2)).isoformat(), "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane", "player": "Patrick Kane", "team": "DET"
            }]
        }
        board = {
            "rows": [{
                "stat": "sog", "line": 3.5, "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane",
                "books": [{"book": "fanduel", "over": -110, "under": -110},
                          {"book": "draftkings", "over": -110, "under": -110},
                          {"book": "pinnacle", "over": -110, "under": -110},
                          {"book": "caesars", "over": -110, "under": -110}]
            }]
        }
        return proj, board

    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )

    exit_code = run(
        args, store=store, public_nc=public_nc, paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0, sleep=lambda s: None,
        snapshot_fns={"NHL": fake_nhl_snap, "nhl_goalies": lambda: None},
        legs_by_league={"NHL": {leg_key(prod_leg): prod_leg}},
        paper_legs_by_league={"NHL": {leg_key(prod_leg): paper_leg}},
    )

    assert exit_code == 0
    # The oversized quote was rejected by the liability cap, so no order placed
    assert len(paper_client.placed_orders) == 0


# ------------------------------------------------------------------ 7. capture after terminal
def test_runner_captures_close_for_terminal_state_from_prior_firing(tmp_path):
    """A quote cancelled in an earlier firing still owes the study its T-15m closing consensus:
    the capture watch list must pick it up in a later firing (winner's-curse denominator)."""
    prod_leg = _make_leg()
    paper_leg = _make_leg(market_id="mkt-paper-1", over_id="paper-over-1", under_id="paper-under-1")
    book = _make_book("mkt-prod-1", "out-over-1", "out-under-1", bid_over=0.40, bid_under=0.48)
    public_nc = FakePublicClient({"mkt-prod-1": book})
    paper_client = FakePaperClient()
    venue = MakerPaperVenue(paper_client, paper_legs={leg_key(prod_leg): paper_leg})
    store = sb.DirStore(str(tmp_path))

    commence = T0 + timedelta(minutes=10)          # inside the T-15m close window at T0
    st_initial = maker_bank.new_quote_state(
        order_id="ord-cap-1",
        client_id=client_id_for_maker("intent-cap-test"),
        intent_id="intent-cap-test",
        game_key="nhl:2026-11-14:DET@CHI",
        game_day="2026-11-14",
        sport="NHL",
        stat="sog",
        player_norm="patrick kane",
        player="Patrick Kane",
        line=3.5,
        side="yes",
        t_post=(T0 - timedelta(hours=1)).isoformat(),
        t_end=(T0 - timedelta(minutes=30)).isoformat(),
        quote_price=0.410,
        contracts_units=12,
        qty=1200,
        p_cons_at_post=0.50,
        terminal_status="CANCELLED",
        cancel_reason="ttl_expired",
        commence=commence.isoformat(),
    )
    maker_bank.bank_rows(store, [("quote_state", st_initial)], now=T0 - timedelta(minutes=30))

    def fake_nhl_snap():
        proj = {
            "ts": T0.isoformat(),
            "rows": [{
                "stat": "sog", "has_line": True, "line": 3.5, "p_over_model": 0.52,
                "commence": commence.isoformat(), "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane", "player": "Patrick Kane", "team": "DET"
            }]
        }
        board = {
            "rows": [{
                "stat": "sog", "line": 3.5, "game_key": "nhl:2026-11-14:DET@CHI",
                "player_norm": "patrick kane",
                "books": [{"book": "fanduel", "over": -110, "under": -110},
                          {"book": "draftkings", "over": -110, "under": -110},
                          {"book": "pinnacle", "over": -110, "under": -110},
                          {"book": "caesars", "over": -110, "under": -110}]
            }]
        }
        return proj, board

    args = argparse.Namespace(
        store=f"dir:{tmp_path}", bucket=None, poll_sec=0.1, max_runtime_sec=10.0,
        pace_s=0.0, horizon_hours=30.0, leagues="NHL", once=True
    )
    exit_code = run(
        args, store=store, public_nc=public_nc, paper_client=paper_client,
        paper_venue=venue, clock=lambda: T0, sleep=lambda s: None,
        snapshot_fns={"NHL": fake_nhl_snap, "nhl_goalies": lambda: None},
        legs_by_league={"NHL": {leg_key(prod_leg): prod_leg}},
        paper_legs_by_league={"NHL": {leg_key(prod_leg): paper_leg}},
    )
    assert exit_code == 0
    states = maker_bank.load_quote_states(store)
    st = states["ord-cap-1"]
    assert st["terminal_status"] == "CANCELLED"
    assert st["p_cons_close"] is not None and abs(st["p_cons_close"] - 0.50) < 0.02
