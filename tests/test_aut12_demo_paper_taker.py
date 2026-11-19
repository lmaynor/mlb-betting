"""tests.test_aut12_demo_paper_taker -- AUT-12 demo paper taker safety + math tests.

The load-bearing test is the demo-host assertion: the runner must refuse to start against
anything but the Kalshi DEMO host. The rest cover the sizing / P&L math and the scanner gate.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import replace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_runner():
    for p in (ROOT, os.path.join(ROOT, "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(
        "kalshi_demo_paper_taker", os.path.join(ROOT, "scripts", "kalshi_demo_paper_taker.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load_runner()


class _StubClient:
    def __init__(self, env, base_url):
        self.env = env
        self.base_url = base_url


def test_assert_demo_client_accepts_demo():
    runner.assert_demo_client(_StubClient("demo", "https://external-api.demo.kalshi.co/trade-api/v2"))


@pytest.mark.parametrize("env,url", [
    ("production-readonly", "https://external-api.kalshi.com/trade-api/v2"),
    ("demo", "https://external-api.kalshi.com/trade-api/v2"),          # demo env, prod host override
    ("public-data", "https://api.elections.kalshi.com/trade-api/v2"),
    ("demo", "https://evil.example.com/trade-api/v2"),
])
def test_assert_demo_client_refuses_everything_else(env, url):
    with pytest.raises(SystemExit):
        runner.assert_demo_client(_StubClient(env, url))


def test_build_demo_client_refuses_prod_env_flags():
    with pytest.raises(SystemExit):
        runner.build_demo_client({"KALSHI_RFQ_ENV": "production-readonly",
                                  "KALSHI_API_KEY_ID": "x", "KALSHI_PRIVATE_KEY_PEM": "y"})
    with pytest.raises(SystemExit):
        runner.build_demo_client({"KALSHI_ENV": "prod",
                                  "KALSHI_API_KEY_ID": "x", "KALSHI_PRIVATE_KEY_PEM": "y"})


def test_build_demo_client_refuses_missing_credentials():
    with pytest.raises(SystemExit):
        runner.build_demo_client({})


def test_contracts_for_cap():
    assert runner.contracts_for_cap(15.0, 0.60) == 25
    assert runner.contracts_for_cap(15.0, 0.99) == 15
    assert runner.contracts_for_cap(15.0, 0.0) == 0
    assert runner.contracts_for_cap(15.0, 1.0) == 15


def test_no_fill_pnl():
    from mlb.execution.kalshi.fees import taker_fee
    win = runner.no_fill_pnl(10, 0.60, yes_won=False, fee_mult=1.0)
    assert win == pytest.approx(10 * 0.40 - taker_fee(0.60, 10, 1.0))
    loss = runner.no_fill_pnl(10, 0.60, yes_won=True, fee_mult=1.0)
    assert loss == pytest.approx(-10 * 0.60 - taker_fee(0.60, 10, 1.0))
    assert win > 0 > loss


class _FakeInnerPricer:
    """Minimal stand-in: always QUOTEs a combo at no_bid 0.60; book unused (scanner stubbed)."""

    def __init__(self):
        self.book = object()

    def price(self, rfq, now=None):
        from ev.kalshi_rfq_pricer import QuoteDecision
        return QuoteDecision("QUOTE", "PLAN", getattr(rfq, "rfq_id", "r1"), "combo",
                             0.0, 0.60, 0.10, 0.12, 0.03, 0.05, 0.08, 10.0, 6.0, ())


def _fake_rfq():
    from ev.kalshi_rfq_pricer import RFQ, RFQLeg
    return RFQ(rfq_id="r1", market_ticker="KXMVE-X",
               legs=(RFQLeg("KXNBA-A", "yes"), RFQLeg("KXNFL-B", "yes")),
               contracts=25.0, target_cost=10.0)


def test_scanner_gate_blocks_non_firing_quote(monkeypatch):
    from ev.combo_taker_scanner import ComboNoSignal
    monkeypatch.setattr(runner, "candidate_from_legs",
                        lambda **kw: (object(), "OK"))
    monkeypatch.setattr(runner, "evaluate_combo",
                        lambda cand: ComboNoSignal("c", "kalshi", 0.40, 0.1, 0.12, 0.01,
                                                   False, False, False, ("ROI_BELOW_0.030",)))
    gated = runner.ScannerGatedPricer(_FakeInnerPricer())
    d = gated.price(_fake_rfq(), now=1000.0)
    assert not d.quoted and d.reason == "SCANNER_GATE"


def test_scanner_gate_passes_firing_quote(monkeypatch):
    from ev.combo_taker_scanner import ComboNoSignal
    monkeypatch.setattr(runner, "candidate_from_legs",
                        lambda **kw: (object(), "OK"))
    monkeypatch.setattr(runner, "evaluate_combo",
                        lambda cand: ComboNoSignal("c", "kalshi", 0.40, 0.1, 0.12, 0.09,
                                                   True, False, True, ("QUALIFIED_NO_TAKER",)))
    gated = runner.ScannerGatedPricer(_FakeInnerPricer())
    d = gated.price(_fake_rfq(), now=1000.0)
    assert d.quoted and d.no_bid == 0.60
    assert d.notes.get("scanner_roi") == 0.09


def test_bucket_kill_check(monkeypatch):
    monkeypatch.setattr(runner.storage, "exists", lambda key: key == runner.KILL_OBJECT)

    class _Args:
        mode = "shadow"
        runtime_dir = "/tmp/aut12-test-runtime"
        db = "/tmp/aut12-test-runtime/ledger.sqlite"
        feed = "EVSweep/nfl_props/latest.json"

    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", "unused-by-constructor-guard")
    # RFQClient would try to parse the PEM: stub the client build instead.
    monkeypatch.setattr(runner, "build_demo_client",
                        lambda env=None: _StubClient("demo", "https://external-api.demo.kalshi.co/trade-api/v2"))
    monkeypatch.setattr(runner.RFQMaker, "__init__", lambda self, *a, **k: None)
    taker = runner.DemoPaperTaker.__new__(runner.DemoPaperTaker)
    assert taker.bucket_killed() is True
    monkeypatch.setattr(runner.storage, "exists", lambda key: False)
    assert taker.bucket_killed() is False


def test_summary_does_not_deadlock_on_ledger_lock(tmp_path):
    # Regression: _summary once called quotes_in() while holding the ledger's non-reentrant lock,
    # deadlocking every flush after its chunk write. Run it on a thread with a hard timeout.
    import threading
    from mlb.execution.kalshi.rfq_maker import RFQLedger
    taker = runner.DemoPaperTaker.__new__(runner.DemoPaperTaker)
    taker.ledger = RFQLedger(str(tmp_path / "led.sqlite"))
    taker.mode = "live"
    taker.stats = {"rfqs_seen": 0, "priced": 0, "quotes_submitted": 0, "ioc_orders": 0,
                   "book_probes": 0, "books_with_bid": 0}
    taker.settlements_done = set()
    taker.ioc_fills = []
    taker.balance_start = None
    taker.state = {}
    taker.settled_pnl_total = 0.0
    taker._last_balance = None

    class _Gate:
        killed = False
        kill_reason = None
    taker.gate = _Gate()
    out = {}
    th = threading.Thread(target=lambda: out.update(taker._summary([])), daemon=True)
    th.start()
    th.join(10)
    assert not th.is_alive(), "_summary deadlocked on the ledger lock"
    assert out["settled_pnl_total"] == 0.0 and out["open_exposure"] == 0.0
