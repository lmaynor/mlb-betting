"""End-to-end pass of mlb.execution.kalshi.runner.run_once with fakes (no network, no GCS)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.consensus import ConsensusStore
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.runner import run_once
from mlb.execution.kalshi.venue import Ack, PaperVenue

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 23, 40, tzinfo=UTC)          # 7:40pm ET; first pitch 8:30pm ET
GAME_PK = 849830


def _market(ticker_suffix="SD", title="San Diego wins", sub="San Diego", yes_ask="0.36", no_ask="0.65",
            event="KXMLBGAME-26OCT032030SDMIL"):
    return {
        "ticker": f"{event}-{ticker_suffix}", "event_ticker": event, "title": title, "yes_sub_title": sub,
        "rules_primary": f"If {sub} wins the game", "exchange_index": 3,
        "yes_ask_dollars": yes_ask, "no_ask_dollars": no_ask,
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    }


class FakeData:
    """Public-data client double: one SD-ML market whose yes ask is 36c (yes_bid 35c)."""
    def __init__(self, markets, yes_ask=0.36):
        self._markets, self._ya = markets, yes_ask
        self.book_fetches = 0

    def get_markets(self, series):
        return self._markets if series == "KXMLBGAME" else []

    fee_multiplier = 0.5

    def get_series(self, series):
        return {"ticker": series, "fee_multiplier": self.fee_multiplier}

    def get_orderbook(self, ticker):
        self.book_fetches += 1
        return Book(ticker, [(round(self._ya - 0.01, 2), 5000.0)], [(round(1 - self._ya, 2), 5000.0)],
                    fetched_at=NOW.timestamp())


def _consensus(away_p=0.40, prev=0.36, n=8, now=NOW):
    rows = []
    for ts, p in ((now - timedelta(minutes=95), prev), (now - timedelta(minutes=4), away_p)):
        for i in range(n):
            rows.append(dict(market="game_ml", book=f"b{i}", selection="AWAY", line=None, fair_prob=p,
                             snapshot_ts=ts.strftime("%Y-%m-%d %H:%M:%S"), game_pk=GAME_PK))
    rows.append(dict(market="game_ml", book="kalshi", selection="AWAY", line=None, fair_prob=0.355,
                     snapshot_ts=(now - timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S"), game_pk=GAME_PK))
    return ConsensusStore.from_frames({"game_ml": pd.DataFrame(rows)})


def _setup(cfg=None, markets=None, yes_ask=0.36, cons=None, mode="shadow"):
    cfg = cfg or ExecConfig(series=("KXMLBGAME",))
    ledger = Ledger(":memory:")
    data = FakeData(markets if markets is not None else [_market()], yes_ask)
    venue = PaperVenue(data.get_orderbook, cfg.taker_multiplier)
    risk = RiskGate(cfg, ledger, mode)
    kw = dict(cfg=cfg, mode=mode, data_client=data, venue=venue,
              consensus=cons or _consensus(), ledger=ledger, risk=risk,
              clock=lambda: NOW, resolve_game_pk=lambda d, a, h: GAME_PK)
    return kw, data, ledger, risk


def test_full_pass_executes_a_lagging_market_then_dedups_next_pass():
    kw, data, ledger, risk = _setup()
    s = run_once(**kw)
    assert s["histogram"].get("EXECUTED") == 1, s
    o = s["orders"][0]
    assert o["bet"] == "AWAY" and o["side"] == "yes" and o["lag"] == "CONSENSUS_MOVED"
    assert o["ev"] >= 0.03 and o["cost"] > 0 and o["filled"] == o["contracts"]
    assert ledger.orders_today("2026-10-03", "shadow") == 1
    assert 0 < ledger.deployed_today("2026-10-03", "shadow") <= ExecConfig().per_game_cap

    again = run_once(**kw)                                    # same bet, same day
    assert again["histogram"].get("REJ_DUP") == 1 and "EXECUTED" not in again["histogram"]
    assert ledger.orders_today("2026-10-03", "shadow") == 1   # idempotent: no second order


def test_decision_ledger_records_inputs_and_outcome():
    kw, _, ledger, _ = _setup()
    run_once(**kw)
    row = ledger._c.execute("SELECT * FROM decisions").fetchone()
    assert row["outcome"] == "EXECUTED" and row["market"] == "game_ml" and row["game_pk"] == GAME_PK
    assert row["lag_class"] == "CONSENSUS_MOVED" and row["p_cons"] == pytest.approx(0.40)
    assert row["p_hat"] < row["p_cons"] and row["ev"] >= 0.03 and row["inputs_json"]


def test_dry_run_demo_plans_but_does_not_send():
    kw, _, ledger, _ = _setup(mode="demo")
    s = run_once(send=False, **kw)
    assert s["histogram"].get("PLAN_NOT_SENT") == 1 and ledger.orders_today("2026-10-03") == 0


def test_game_started_unmapped_and_no_consensus_are_counted_not_traded():
    started = _market(event="KXMLBGAME-26OCT031300SDMIL")                    # 1:00pm ET game, long gone
    unmapped = _market(ticker_suffix="NYY", title="New York wins", sub="New York")
    kw, data, ledger, _ = _setup(markets=[started, unmapped])
    s = run_once(**kw)
    assert s["histogram"].get("REJ_GAME_STARTED") == 1
    assert s["histogram"].get("REJ_UNMAPPED") == 1
    assert data.book_fetches == 0 and ledger.orders_today("2026-10-03") == 0

    nocons, data2, _, _ = _setup(cons=ConsensusStore.from_frames({}))
    assert run_once(**nocons)["histogram"].get("REJ_CONS_THIN") == 1


def test_prefilter_skips_orderbook_fetch_when_no_edge_anywhere():
    kw, data, ledger, _ = _setup(markets=[_market(yes_ask="0.43", no_ask="0.62")])    # consensus .40/.60: no edge
    s = run_once(**kw)
    assert s["histogram"].get("PREFILTER_NO_EDGE") == 1 and data.book_fetches == 0


def test_fee_eaten_edge_is_rejected_net_of_fee():
    # book ask 38c vs consensus 40.5% -- a gross edge that the ~4% fee + shrink erases
    kw, data, ledger, _ = _setup(cons=_consensus(away_p=0.405, prev=0.40), yes_ask=0.38,
                                 markets=[_market(yes_ask="0.38", no_ask="0.63")])
    data.fee_multiplier = 1.0                                   # full fee: this edge does not survive it
    s = run_once(**kw)
    assert s["histogram"].get("REJ_EV_LOW") == 1 and ledger.orders_today("2026-10-03") == 0


def test_kill_switch_stops_the_pass_and_blocks_orders():
    kw, _, ledger, risk = _setup()
    risk.trip("manual test")
    s = run_once(**kw)
    assert s["killed"] and ledger.orders_today("2026-10-03") == 0


def test_risk_budget_caps_the_order_size():
    cfg = ExecConfig(series=("KXMLBGAME",), per_game_cap_pct=0.005)            # $5 per game
    kw, _, ledger, _ = _setup(cfg=cfg)
    s = run_once(**kw)
    assert s["histogram"].get("EXECUTED") == 1 and s["orders"][0]["cost"] <= 5.0 + 1e-9


class ErrVenue:
    def submit(self, intent):
        return Ack(None, "coid", "ERROR", error="HTTP 500")


def test_submit_errors_are_recorded_and_trip_the_breaker():
    cfg = ExecConfig(series=("KXMLBGAME",), max_consecutive_rejects=1)
    kw, _, ledger, risk = _setup(cfg=cfg)
    kw["venue"] = ErrVenue()
    s = run_once(**kw)
    assert s["histogram"].get("ERR_SUBMIT") == 1 and risk.killed
    assert ledger.orders_today("2026-10-03") == 1                              # failure is on the record
    assert run_once(**kw)["killed"]                                            # and nothing more is sent


def test_run_uses_cash_cap_when_demo_balance_is_small():
    kw, _, ledger, _ = _setup(mode="demo")
    s = run_once(cash_fn=lambda shard: 4.0, **kw)                                    # only $4 of demo cash
    assert s["histogram"].get("EXECUTED") == 1 and s["orders"][0]["cost"] <= 4.0 + 1e-9


def test_cash_is_tracked_per_exchange_shard():
    seen = []
    def cash_fn(shard):
        seen.append(shard)
        return 6.0 if shard == 3 else 0.0                       # only shard 3 holds cash
    kw, _, ledger, _ = _setup(mode="demo")
    s = run_once(cash_fn=cash_fn, **kw)
    assert seen == [3] and s["histogram"].get("EXECUTED") == 1 and s["orders"][0]["cost"] <= 6.0 + 1e-9
    kw2, _, ledger2, _ = _setup(mode="demo")
    s2 = run_once(cash_fn=lambda shard: 0.0, **kw2)             # cash on the wrong shard -> nothing sized
    assert "EXECUTED" not in s2["histogram"] and ledger2.orders_today("2026-10-03") == 0


def test_series_fee_multiplier_flows_into_sizing_and_paper_fill():
    from mlb.execution.kalshi.fees import taker_fee
    kw, data, ledger, _ = _setup()
    s = run_once(**kw)
    o = s["orders"][0]
    row = ledger._c.execute("SELECT fee, fill_count FROM orders").fetchone()
    assert row["fee"] == pytest.approx(taker_fee(0.36, row["fill_count"], 0.5))      # 0.5x, not 1.0x
    kw2, data2, ledger2, _ = _setup()
    data2.fee_multiplier = 1.0
    s2 = run_once(**kw2)
    assert s2["orders"][0]["contracts"] <= o["contracts"]                          # costlier fees never size up


def test_fee_above_model_trips_kill_switch_below_model_does_not():
    from mlb.execution.kalshi.runner import _check_fee
    from mlb.execution.kalshi.venue import FillRec
    from collections import Counter
    fill = lambda fee: FillRec("f", "o", "T", "yes", 100.0, 0.45, fee, "")
    model = 0.07 * 100 * 0.45 * 0.55 * 0.5                                   # 0.7... at M=.5 -> 0.8663 / 2
    risk = RiskGate(ExecConfig(), Ledger(":memory:"), "demo"); h = Counter()
    a = Ack("o", "c", "FILLED", 100.0, 45 + model, model, [fill(model)])
    _check_fee(a, 0.5, risk, h); assert not risk.killed                     # exact match
    cheap = Ack("o", "c", "FILLED", 100.0, 45.0, model / 2, [fill(model / 2)])
    _check_fee(cheap, 0.5, risk, h); assert not risk.killed                 # cheaper than model: safe side
    pricey = Ack("o", "c", "FILLED", 100.0, 46.0, model * 2, [fill(model * 2)])
    _check_fee(pricey, 0.5, risk, h)
    assert risk.killed and h["WARN_FEE_ABOVE_MODEL"] == 1 and "EV overstated" in risk.kill_reason


def test_pnl_feed_trips_kill_switch_before_any_order():
    kw, _, ledger, risk = _setup(mode="demo")
    s = run_once(pnl_fn=lambda: -75.0, **kw)                 # worse than the $50 daily-loss cap
    assert risk.killed and "max daily loss" in risk.kill_reason
    assert s["killed"] and ledger.orders_today("2026-10-03") == 0
    assert ledger.kv_get("last_pnl") == "-75.0000"


def test_pnl_feed_within_limit_allows_trading_and_failure_is_non_fatal():
    kw, _, ledger, risk = _setup(mode="demo")
    assert run_once(pnl_fn=lambda: -10.0, **kw)["histogram"].get("EXECUTED") == 1 and not risk.killed
    kw2, _, ledger2, risk2 = _setup(mode="demo")
    def boom(): raise RuntimeError("positions endpoint down")
    s2 = run_once(pnl_fn=boom, **kw2)                         # feed failure must not crash or silently kill
    assert s2["histogram"].get("EXECUTED") == 1 and not risk2.killed
