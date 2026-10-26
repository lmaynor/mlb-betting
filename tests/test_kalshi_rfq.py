"""Offline tests for the Kalshi RFQ maker: client invariants, stream, pricer, risk gate, last-look shield."""
import asyncio
import json
import os
from dataclasses import replace

import pytest

from ev.kalshi_rfq_pricer import (
    InMemoryFairBook, KalshiRFQPricer, LegFairValue, PricerConfig, blend_true_prob, floor_to_tick, leg_from_row,
    load_feed_rows, parse_rfq,
)
from mlb.execution.kalshi.client import KalshiAPIError, ProductionNotEnabled
from mlb.execution.kalshi.rfq_client import (
    KalshiRFQStream, QuoteInvariantError, RFQClient, backoff_delay, validate_quote_prices, ws_headers,
)
from mlb.execution.kalshi.rfq_maker import (
    RFQConfig, RFQLedger, RFQMaker, RFQRecorder, RFQRiskGate, RFQTape,
)

NOW = 1_800_000_000.0
GK = "nfl:2026-10-25:BUF@KC"


def mk_leg(ticker="T1", gk=GK, player="p1", stat="rec_yds", p=0.5, n=5, disp=0.01, ts=NOW, **kw):
    return LegFairValue(ticker=ticker, sport="nfl", game_key=gk, market_class="player_prop", player_norm=player,
                        stat=stat, strike=49.5, p_cons_yes=p, n_books=n, disp=disp, p_model_yes=None, pooled=False,
                        updated_ts=ts, **kw)


def book_of(*legs):
    b = InMemoryFairBook(clock=lambda: NOW)
    b.replace_all(legs)
    return b


def three_cross_game(p=0.5):
    return [mk_leg(f"T{i}", f"nfl:2026-10-25:A{i}@B{i}", f"p{i}", p=p) for i in (1, 2, 3)]


def combo_rfq(tickers, rfq_id="r1", contracts=50.0, sides=None):
    sides = sides or ["yes"] * len(tickers)
    return {"id": rfq_id, "market_ticker": "KXMVE-" + rfq_id, "contracts_fp": f"{contracts:.2f}",
            "mve_collection_ticker": "KXMVECOLL", "mve_selected_legs": [
                {"event_ticker": "E", "market_ticker": t, "side": s} for t, s in zip(tickers, sides)]}


# ------------------------------------------------------------------------------------------ client
def test_quote_invariants():
    assert validate_quote_prices("0", "0.81") == ("0.0000", "0.8100")
    with pytest.raises(QuoteInvariantError):
        validate_quote_prices("0", "0")                       # cannot decline both
    with pytest.raises(QuoteInvariantError):
        validate_quote_prices("0.60", "0.40")                 # both > 0 and sum > 0.99
    with pytest.raises(QuoteInvariantError):
        validate_quote_prices("0", "1.00")                    # above 0.99
    with pytest.raises(QuoteInvariantError):
        validate_quote_prices("-0.1", "0.5")
    with pytest.raises(QuoteInvariantError):
        validate_quote_prices("abc", "0.5")
    assert validate_quote_prices("0.45", "0.54") == ("0.4500", "0.5400")


@pytest.fixture(scope="module")
def pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


class FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._b, self.text, self.headers = status, body, text, {}
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, responses):
        self.headers, self.calls, self._r = {}, [], list(responses)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self._r.pop(0)


def test_rfq_client_refuses_production(pem):
    with pytest.raises(ProductionNotEnabled):
        RFQClient("production", "k", pem)
    with pytest.raises(ProductionNotEnabled):
        RFQClient("public-data", None, None)
    with pytest.raises(ProductionNotEnabled):
        KalshiRFQStream("production", "k", None, lambda *a: None)


def test_create_and_confirm_wire_format(pem):
    s = FakeSession([FakeResp(201, {"id": "q-1"}), FakeResp(204)])
    c = RFQClient("demo", "kid", pem, session=s)
    c.min_interval_s = 0.0
    assert c.create_quote("rfq-9", "0", "0.8200") == {"id": "q-1"}
    m, url, kw = s.calls[0]
    assert (m, url.endswith("/communications/quotes")) == ("POST", True)
    assert kw["json"] == {"rfq_id": "rfq-9", "yes_bid": "0.0000", "no_bid": "0.8200", "rest_remainder": False}
    assert "replace_existing" not in kw["json"]
    assert {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"} <= set(kw["headers"])
    c.confirm_quote("q-1", "rfq-9")
    m, url, kw = s.calls[1]
    assert m == "PUT" and url.endswith("/communications/rfqs/rfq-9/quotes/q-1/confirm")
    assert kw["timeout"] == 2.0


def test_confirm_never_retries_a_429(pem):
    s = FakeSession([FakeResp(429, None, "slow down"), FakeResp(204)])
    c = RFQClient("demo", "kid", pem, session=s)
    with pytest.raises(KalshiAPIError) as e:
        c.confirm_quote("q", "r")
    assert e.value.status == 429 and len(s.calls) == 1        # a retry would burn the 3 s window


def test_create_quote_invariant_blocks_before_network(pem):
    s = FakeSession([])
    c = RFQClient("demo", "kid", pem, session=s)
    with pytest.raises(QuoteInvariantError):
        c.create_quote("r", "0", "0")
    assert s.calls == []


def test_ws_headers_sign_the_ws_path(pem):
    from mlb.execution.kalshi.client import load_private_key
    h = ws_headers("kid", load_private_key(pem), ts_ms=123)
    assert h["KALSHI-ACCESS-TIMESTAMP"] == "123" and h["KALSHI-ACCESS-KEY"] == "kid" and h["KALSHI-ACCESS-SIGNATURE"]


def test_backoff_grows_and_caps():
    d = [backoff_delay(a, jitter=0.0) for a in range(8)]
    assert d[:3] == [0.5, 1.0, 2.0] and d[-1] == 30.0
    assert 0.375 <= backoff_delay(0, rng=lambda: 0.0) <= 0.625


class _Conn:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


class _WS:
    def __init__(self, frames, fail=False):
        self.frames, self.fail, self.sent = frames, fail, []

    async def send(self, m):
        self.sent.append(json.loads(m))

    def __aiter__(self):
        async def gen():
            for f in self.frames:
                yield f
            if self.fail:
                raise ConnectionError("dropped")
        return gen()


def test_stream_subscribes_dispatches_and_reconnects_with_backoff(pem):
    from mlb.execution.kalshi.client import load_private_key
    wss = [_WS([json.dumps({"type": "rfq_created", "msg": {"id": "a"}}), "not json",
                json.dumps({"type": "ok"})], fail=True),
           _WS([json.dumps({"type": "quote_accepted", "msg": {"quote_id": "q"}})])]
    got, sleeps = [], []

    async def sleep(d):
        sleeps.append(d)

    def on_event(t, msg, ts):
        got.append((t, msg))
        if len(got) == 2:
            st.stop()

    st = KalshiRFQStream("demo", "kid", load_private_key(pem), on_event, sleep=sleep,
                         connect=lambda url, headers: _Conn(wss.pop(0)))
    asyncio.run(st.run())
    assert got == [("rfq_created", {"id": "a"}), ("quote_accepted", {"quote_id": "q"})]
    assert st.connects == 2 and len(sleeps) == 1 and sleeps[0] > 0
    assert wss == []


# ------------------------------------------------------------------------------------------ fair book
def test_leg_from_sweep_row_and_feed(tmp_path):
    row = {"ticker": "KXNFLRECYDS-X", "game_key": GK, "stat": "rec_yds", "player_norm": "a b", "strike": 49.5,
           "p_over": 0.42, "n_books": 4, "disp": 0.01, "consensus_src": "pooled", "ts": "2026-10-25T18:00:00+00:00"}
    lf = leg_from_row(row, NOW)
    assert lf.sport == "nfl" and lf.pooled and lf.p_cons_yes == 0.42 and lf.p_true_yes == 0.42
    assert leg_from_row({"ticker": "x", "p_over": 1.0}) is None and leg_from_row({"p_over": 0.5}) is None
    f = tmp_path / "latest.json"
    f.write_text(json.dumps({"ts": "2026-10-25T18:00:00+00:00", "rows": [row]}))
    rows, pub = load_feed_rows(str(f))
    assert len(rows) == 1 and pub and pub > 0


def test_blend_defaults_to_market_and_guards_divergence():
    assert blend_true_prob(0.40, 0.45, "nfl", "rec_yds") == 0.40            # c=0 for every NFL stat
    shifted = blend_true_prob(0.50, 0.56, "nba", "rebounds")                # validated weight exists
    assert 0.50 < shifted < 0.56
    assert blend_true_prob(0.50, 0.70, "nba", "rebounds") == 0.50           # > 0.15 apart: winner's curse guard


def test_hot_flag_applies_per_game_and_expires():
    b = book_of(mk_leg("A"), mk_leg("B", gk="nfl:x:C@D"))
    b.mark_hot(GK, now=NOW, ttl=60)
    assert b.get("A", NOW).is_hot_scratch and not b.get("B", NOW).is_hot_scratch
    assert not b.get("A", NOW + 61).is_hot_scratch
    assert b.get("nope") is None


def test_floor_to_tick_is_float_safe():
    assert floor_to_tick(0.8239) == 0.82 and floor_to_tick(0.82) == 0.82 and floor_to_tick(0.1 + 0.2 + 0.5) == 0.8


# ------------------------------------------------------------------------------------------ single leg
def single(rfq_contracts=100.0, ticker="T1"):
    return {"id": "s1", "market_ticker": ticker, "contracts_fp": str(rfq_contracts)}


def test_single_leg_off_by_default():
    p = KalshiRFQPricer(book_of(mk_leg()))
    assert p.price(single(), NOW).reason == "REJ_SINGLE_LEG_OFF"


def test_single_leg_two_sided_quote_with_edge():
    p = KalshiRFQPricer(book_of(mk_leg(p=0.50)), PricerConfig(quote_single_leg=True))
    d = p.price(single(0), NOW)
    assert d.quoted and (d.yes_bid, d.no_bid) == (0.48, 0.48)
    assert d.yes_bid + d.no_bid <= 1.0 - 2 * 0.02 + 1e-9


def test_single_leg_size_addon_and_inventory_skew():
    cfg = PricerConfig(quote_single_leg=True)
    flat = KalshiRFQPricer(book_of(mk_leg(p=0.50)), cfg).price(single(5000), NOW)         # addon capped at 3c
    assert flat.yes_bid == 0.47 - 0.0 or flat.yes_bid <= 0.47
    assert flat.notes["size_addon"] == pytest.approx(0.03)
    long_yes = KalshiRFQPricer(book_of(mk_leg(p=0.50)), cfg, inventory_fn=lambda t: 500.0).price(single(0), NOW)
    assert long_yes.yes_bid < 0.48 < long_yes.no_bid + 0.0001                       # long YES: shade the YES bid down
    assert long_yes.yes_bid + long_yes.no_bid <= 0.96 + 1e-9


@pytest.mark.parametrize("leg,reason", [
    (None, "REJ_UNMAPPED_TICKER"),
    (mk_leg(n=1), "REJ_LOW_BOOKS"),
    (mk_leg(disp=0.09), "REJ_HIGH_DISPERSION"),
    (mk_leg(ts=NOW - 1501), "REJ_STALE_CONSENSUS"),
])
def test_single_leg_fail_closed_gates(leg, reason):
    p = KalshiRFQPricer(book_of(*([leg] if leg else [])), PricerConfig(quote_single_leg=True))
    d = p.price(single(), NOW)
    assert not d.quoted and d.reason == reason


def test_single_leg_hot_scratch_rejects():
    b = book_of(mk_leg())
    b.mark_hot(GK, now=NOW)
    d = KalshiRFQPricer(b, PricerConfig(quote_single_leg=True)).price(single(), NOW)
    assert d.reason == "REJ_HOT_LINEUP_SCRATCH"


# ------------------------------------------------------------------------------------------ combos
def test_three_leg_cross_game_combo_lays_only():
    p = KalshiRFQPricer(book_of(*three_cross_game()))
    d = p.price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert d.quoted and d.kind == "combo"
    assert d.yes_bid == 0.0 and d.yes_bid_fp == "0.0000"
    assert d.p_yes_true == pytest.approx(0.125)
    assert d.p_yes_cons == pytest.approx(0.508 ** 3)
    assert d.no_bid <= 0.83 and d.no_bid == floor_to_tick((1 - 0.508 ** 3) - 0.045)
    assert d.ev_per_contract == pytest.approx((1 - 0.125) - d.no_bid)
    assert d.roi_on_collateral >= 0.03
    assert d.collateral == pytest.approx(50 * d.no_bid)


def test_combo_rejects_when_roi_below_floor():
    p = KalshiRFQPricer(book_of(*three_cross_game()), PricerConfig(min_collateral_roi=0.30))
    d = p.price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert not d.quoted and d.reason == "REJ_LOW_ROI" and d.roi_on_collateral < 0.30


def test_combo_edge_compounds_with_leg_count():
    legs = [mk_leg(f"T{i}", f"nfl:2026-10-25:A{i}@B{i}", f"p{i}", p=0.4) for i in range(1, 5)]
    d4 = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3", "T4"]), NOW)
    assert d4.edge == pytest.approx(4 * 0.015)
    d2 = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2"]), NOW)
    assert d2.edge == pytest.approx(0.035)                                  # k * 1.5c = 3c < the 3.5c floor


def test_combo_no_leg_contributes_complement():
    legs = three_cross_game(p=0.5)
    legs[0] = mk_leg("T1", legs[0].game_key, "p1", p=0.2)
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3"], sides=["no", "yes", "yes"]), NOW)
    assert d.p_yes_true == pytest.approx(0.8 * 0.5 * 0.5)


def test_combo_not_a_longshot_is_rejected():
    legs = three_cross_game(p=0.8)
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert d.reason == "REJ_COMBO_NOT_LONGSHOT"


@pytest.mark.parametrize("mutate,why", [
    (lambda ls: ls[:2], "missing leg"),
    (lambda ls: [replace(ls[0], n_books=1)] + ls[1:], "thin"),
    (lambda ls: [mk_leg("T1", ls[0].game_key, "p1", ts=NOW - 1600)] + ls[1:], "stale"),
    (lambda ls: [mk_leg("T1", ls[0].game_key, "p1", disp=0.09)] + ls[1:], "dispersed"),
])
def test_combo_any_bad_leg_rejects_everything(mutate, why):
    legs = mutate(three_cross_game())
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert d.reason == "REJ_COMBO_LEG_UNVALIDATED", why


def test_combo_hot_leg_and_unknown_side_and_duplicates():
    b = book_of(*three_cross_game())
    b.mark_hot("nfl:2026-10-25:A2@B2", now=NOW)
    assert KalshiRFQPricer(b).price(combo_rfq(["T1", "T2", "T3"]), NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"
    ok = KalshiRFQPricer(book_of(*three_cross_game()))
    assert ok.price(combo_rfq(["T1", "T2", "T3"], sides=["yes", "maybe", "yes"]), NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"
    assert ok.price(combo_rfq(["T1", "T1", "T3"]), NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"


def same_game_book():
    return book_of(mk_leg("P", GK, "qb", stat="pass_yds"), mk_leg("R", GK, "wr", stat="rec_yds"),
                   mk_leg("X", "nfl:2026-10-25:A9@B9", "zz", stat="rec_yds"))


def test_same_game_combo_disabled_by_default():
    d = KalshiRFQPricer(same_game_book()).price(combo_rfq(["P", "R", "X"]), NOW)
    assert d.reason == "REJ_SGP_DISABLED"


def test_same_game_combo_adds_positive_correlation_and_buffer():
    cfg = PricerConfig(allow_same_game_combos=True)
    d = KalshiRFQPricer(same_game_book(), cfg).price(combo_rfq(["P", "R", "X"]), NOW)
    assert d.quoted and d.notes["same_game"]
    independent = 0.5 * 0.5 * 0.5
    assert d.p_yes_true > independent + 0.025                              # copula lift + pair buffer
    assert d.p_yes_true <= 0.5 * 0.5                                       # Frechet: never above the weakest pair
    assert d.p_yes_cons > d.p_yes_true


def test_same_game_negative_table_correlation_is_clamped_to_zero():
    b = book_of(mk_leg("R", GK, "rb", stat="rush_yds"), mk_leg("P", GK, "qb", stat="pass_yds"),
                mk_leg("X", "nfl:2026-10-25:A9@B9", "zz"))
    d = KalshiRFQPricer(b, PricerConfig(allow_same_game_combos=True)).price(combo_rfq(["R", "P", "X"]), NOW)
    assert d.p_yes_true >= (0.5 * 0.5 + 0.025) * 0.5 - 1e-9                # rho clamped to 0: independence + pair buffer, never below


def test_same_player_in_one_game_rejected():
    b = book_of(mk_leg("A", GK, "same", stat="rec_yds"), mk_leg("B", GK, "same", stat="receptions"),
                mk_leg("X", "nfl:2026-10-25:A9@B9", "zz"))
    d = KalshiRFQPricer(b, PricerConfig(allow_same_game_combos=True)).price(combo_rfq(["A", "B", "X"]), NOW)
    assert d.reason == "REJ_SGP_SAME_PLAYER"


def test_parse_rfq_accepts_ws_and_rest_shapes():
    r = parse_rfq(combo_rfq(["T1", "T2"], contracts=12))
    assert r.is_combo and r.contracts == 12.0 and [l.ticker for l in r.legs] == ["T1", "T2"]
    s = parse_rfq({"id": "z", "market_ticker": "M", "target_cost_dollars": "25.00"})
    assert not s.is_combo and s.target_cost == 25.0 and s.contracts is None


def test_combo_contracts_estimated_from_target_cost():
    rfq = combo_rfq(["T1", "T2", "T3"])
    del rfq["contracts_fp"]
    rfq["target_cost_dollars"] = "10.00"
    d = KalshiRFQPricer(book_of(*three_cross_game())).price(rfq, NOW)
    assert d.quoted and d.contracts == pytest.approx(10.0 / (1.0 - d.no_bid))


# ------------------------------------------------------------------------------------------ risk gate + maker
class FakeClient:
    def __init__(self, create_exc=None, confirm_exc=None):
        self.created, self.confirmed, self.deleted = [], [], []
        self.create_exc, self.confirm_exc = create_exc, confirm_exc

    def create_quote(self, rfq_id, y, n, **kw):
        if self.create_exc:
            raise self.create_exc
        self.created.append((rfq_id, y, n))
        return {"id": f"q-{rfq_id}"}

    def confirm_quote(self, quote_id, rfq_id=None):
        if self.confirm_exc:
            raise self.confirm_exc
        self.confirmed.append((quote_id, rfq_id))
        return {}

    def delete_quote(self, quote_id):
        self.deleted.append(quote_id)
        return {}


def make_maker(legs, mode="shadow", client=None, cfg=None, pcfg=None, clock=None):
    ledger = RFQLedger(":memory:")
    book = book_of(*legs)
    pricer = KalshiRFQPricer(book, pcfg or PricerConfig())
    cfg = cfg or RFQConfig()
    gate = RFQRiskGate(cfg, ledger, mode)
    m = RFQMaker(pricer, gate, ledger, client, mode, cfg, clock=clock or (lambda: NOW), env={"KALSHI_RFQ_LIVE": "1"})
    return m, book, ledger, gate


def test_live_mode_requires_env_flag_and_client():
    ledger = RFQLedger(":memory:")
    pricer = KalshiRFQPricer(book_of())
    gate = RFQRiskGate(RFQConfig(), ledger, "live")
    with pytest.raises(RuntimeError):
        RFQMaker(pricer, gate, ledger, FakeClient(), "live", env={})
    with pytest.raises(RuntimeError):
        RFQMaker(pricer, gate, ledger, None, "live", env={"KALSHI_RFQ_LIVE": "1"})


def test_kill_switch_uses_its_own_key_and_is_manual_reset():
    m, _, ledger, gate = make_maker(three_cross_game())
    gate.trip("test")
    assert ledger.kv_get("kill_reason:kalshi_rfq") == "test" and ledger.kv_get("kill_reason") is None
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"])) is None
    assert ledger.histogram(m._day()) == {"REJ_KILLED": 1}
    gate.reset()
    assert not gate.killed


def test_player_exposure_capped_across_open_combo_quotes():
    legs = three_cross_game() + [mk_leg("T4", "nfl:2026-10-25:A4@B4", "p4"), mk_leg("T5", "nfl:2026-10-25:A5@B5", "p5")]
    m, _, ledger, gate = make_maker(legs)
    d1 = m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 50.0))
    assert d1.quoted and d1.collateral <= 50.0
    d2 = m.on_rfq_created(combo_rfq(["T1", "T4", "T5"], "r2", 50.0))      # p1 again: ~41 + ~41 > $75
    assert not d2.quoted and d2.reason == "REJ_RISK_PLAYER"
    d3 = m.on_rfq_created(combo_rfq(["T4", "T5", "T2"], "r3", 50.0))      # p2 again via T2 -> also blocked
    assert d3.reason in ("REJ_RISK_PLAYER", "REJ_RISK_GAME")
    games, players = gate.exposure()
    assert players["p1"] == pytest.approx(d1.collateral) and set(players) == {"p1", "p2", "p3"}


def test_per_quote_liability_game_and_open_quote_caps():
    m, *_ = make_maker(three_cross_game())
    big = m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 200.0))        # ~$164 collateral > $50
    assert big.reason == "REJ_RISK_SIZE"
    m2, *_ = make_maker(three_cross_game(), cfg=RFQConfig(max_open_quotes=1))
    assert m2.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "a", 10.0)).quoted
    assert m2.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "b", 10.0)).reason == "REJ_RISK_OPEN_QUOTES"
    m3, *_ = make_maker(three_cross_game(), cfg=RFQConfig(max_exposure_per_game_dollars=30.0))
    assert m3.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "a", 50.0)).reason == "REJ_RISK_GAME"
    no_size = combo_rfq(["T1", "T2", "T3"], "c")
    del no_size["contracts_fp"]
    assert m2.on_rfq_created(no_size).reason in ("REJ_RISK_SIZE_UNKNOWN", "REJ_RISK_OPEN_QUOTES")


def test_duplicate_rfq_is_not_quoted_twice():
    m, _, ledger, _ = make_maker(three_cross_game())
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0)).quoted
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0)) is None
    assert len(ledger.quotes_in("shadow", ("open",))) == 1


def accepted(rfq_id="r1", side="no", q=None):
    return {"quote_id": q or f"shadow-{rfq_id}", "rfq_id": rfq_id, "accepted_side": side}


def test_last_look_confirms_when_consensus_is_stable_live():
    cl = FakeClient()
    m, _, ledger, _ = make_maker(three_cross_game(), mode="live", client=cl)
    d = m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 20.0))
    assert d.quoted and cl.created == [("r1", "0.0000", f"{d.no_bid:.4f}")]
    assert m.on_quote_accepted(accepted("r1", q="q-r1"), NOW) == "CONFIRMED"
    assert cl.confirmed == [("q-r1", "r1")]
    assert ledger.get_quote("q-r1")["state"] == "confirmed"
    assert m.on_quote_executed({"quote_id": "q-r1", "order_id": "o1"}) == "FILLED"
    assert ledger.get_quote("q-r1")["state"] == "filled"


def test_last_look_aborts_on_adverse_move_over_1_5_cents():
    cl = FakeClient()
    legs = three_cross_game()
    m, book, ledger, _ = make_maker(legs, mode="live", client=cl)
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 20.0))
    book.upsert(mk_leg("T2", legs[1].game_key, "p2", p=0.50 + 0.016))      # leg got likelier: bad for the NO side
    assert m.on_quote_accepted(accepted("r1", q="q-r1"), NOW) == "ABORT_ADVERSE_MOVE"
    assert cl.confirmed == [] and ledger.get_quote("q-r1")["state"] == "aborted"


def test_last_look_tolerates_small_and_favourable_moves():
    cl = FakeClient()
    legs = three_cross_game()
    m, book, _, _ = make_maker(legs, mode="live", client=cl)
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 20.0))
    book.upsert(mk_leg("T1", legs[0].game_key, "p1", p=0.50 + 0.010))      # within 1.5c
    book.upsert(mk_leg("T3", legs[2].game_key, "p3", p=0.50 - 0.04))       # leg got LESS likely: good for us
    assert m.on_quote_accepted(accepted("r1", q="q-r1"), NOW) == "CONFIRMED"


def test_last_look_aborts_on_hot_scratch_cancels_game_quotes_and_cools_down():
    cl = FakeClient()
    legs = three_cross_game()
    m, book, ledger, _ = make_maker(legs, mode="live", client=cl)
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 20.0))
    other = [mk_leg("T9", legs[0].game_key, "p9", stat="rec_yds")]
    for l in other:
        book.upsert(l)
    # a second open quote that shares game A1@B1 through another player
    m.pricer.cfg  # noqa: B018 -- documentation only
    book.mark_hot(legs[1].game_key, now=NOW)
    assert m.on_quote_accepted(accepted("r1", q="q-r1"), NOW) == "ABORT_HOT_SCRATCH"
    assert cl.confirmed == []
    again = m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r2", 20.0))
    assert not again.quoted                                                  # hot leg rejects first ...
    book._hot.clear()
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r3", 20.0)).reason == "REJ_GAME_COOLDOWN"


def test_cooldown_expires_after_120s():
    t = [NOW]
    legs = three_cross_game()
    m, book, _, _ = make_maker(legs, clock=lambda: t[0])
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    book.mark_hot(legs[0].game_key, now=NOW, ttl=1)
    assert m.on_quote_accepted(accepted("r1"), NOW) == "ABORT_HOT_SCRATCH"
    t[0] = NOW + 121
    book.upsert(mk_leg("T1", legs[0].game_key, "p1", ts=t[0]))
    for l in legs[1:]:
        book.upsert(mk_leg(l.ticker, l.game_key, l.player_norm, ts=t[0]))
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r4", 10.0)).quoted


def test_last_look_aborts_on_wrong_side_late_or_unknown():
    m, _, _, _ = make_maker(three_cross_game())
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    assert m.on_quote_accepted(accepted("r1", side="yes"), NOW) == "ABORT_SIDE_MISMATCH"   # we bid 0 on yes
    m2, *_ = make_maker(three_cross_game())
    m2.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    assert m2.on_quote_accepted(accepted("r1"), NOW - 5.0) == "ABORT_TOO_LATE"
    m3, *_ = make_maker(three_cross_game())
    m3.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    assert m3.on_quote_accepted({"quote_id": "shadow-r1"}, NOW) == "CONFIRMED"           # one live side: inferred
    assert m3.on_quote_accepted({"quote_id": "nope"}, NOW) == "IGNORED"


def test_last_look_aborts_when_a_leg_goes_stale_or_vanishes():
    legs = three_cross_game()
    m, book, _, _ = make_maker(legs)
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    book.replace_all(legs[:2])
    assert m.on_quote_accepted(accepted("r1"), NOW) == "ABORT_LEG_INVALID"


def test_confirm_failure_trips_kill_and_429_cancels_open_quotes():
    cl = FakeClient(confirm_exc=KalshiAPIError(429, "limit", "PUT", "/x"))
    m, _, ledger, gate = make_maker(three_cross_game(), mode="live", client=cl,
                                    cfg=RFQConfig(max_exposure_per_player_dollars=500.0))
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    assert m.on_quote_accepted(accepted("r1", q="q-r1"), NOW) == "CONFIRM_FAILED"
    assert gate.killed and "429" in ledger.kv_get("kill_reason:kalshi_rfq") or "missed confirmation" in gate.kill_reason
    assert ledger.get_quote("q-r1")["state"] == "confirm_failed"


def test_submit_429_trips_kill_switch_and_cancels_everything():
    cl = FakeClient()
    m, _, ledger, gate = make_maker(three_cross_game() + [mk_leg("T4", "nfl:x:C@D", "p4"), mk_leg("T5", "nfl:y:E@F", "p5")],
                                    mode="live", client=cl)
    assert m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0)).quoted
    cl.create_exc = KalshiAPIError(429, "slow down", "POST", "/communications/quotes")
    d = m.on_rfq_created(combo_rfq(["T4", "T5", "T3"], "r2", 10.0))
    assert d.reason == "REJ_SUBMIT_ERROR" and gate.killed
    assert cl.deleted == ["q-r1"] and ledger.get_quote("q-r1")["state"] == "cancelled"


def test_repeated_aborts_trip_the_kill_switch():
    legs = three_cross_game()
    m, book, _, gate = make_maker(legs, cfg=RFQConfig(max_aborts_per_window=2, game_cooldown_sec=0.0))
    for i in range(2):
        m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], f"r{i}", 10.0))
        book.mark_hot(legs[0].game_key, now=NOW, ttl=1)
        m.on_quote_accepted(accepted(f"r{i}"), NOW)
        book._hot.clear()
    assert gate.killed and "last-look aborts" in gate.kill_reason


def test_rfq_deleted_expires_open_quote():
    m, _, ledger, _ = make_maker(three_cross_game())
    m.on_rfq_created(combo_rfq(["T1", "T2", "T3"], "r1", 10.0))
    assert m.on_event("rfq_deleted", {"id": "r1"}) == "EXPIRED"
    assert ledger.get_quote("shadow-r1")["state"] == "expired"


def test_single_leg_inventory_flows_into_pricer():
    m, _, ledger, _ = make_maker([mk_leg("T1", p=0.5)], pcfg=PricerConfig(quote_single_leg=True))
    m.pricer.inventory_fn = m.inventory
    assert m.on_rfq_created({"id": "s1", "market_ticker": "T1", "contracts_fp": "100"}).quoted
    assert m.on_quote_accepted({"quote_id": "shadow-s1", "accepted_side": "yes"}, NOW) == "CONFIRMED"
    assert m.inventory("T1") == 100.0


# ------------------------------------------------------------------------------------------ recorder / tape
def test_recorder_writes_tape_and_schedules_followup(tmp_path):
    class TapeClient:
        def request(self, method, path, **kw):
            assert method == "GET" and path == "/markets/trades" and kw["auth"] is False
            return {"trades": [{"yes_price_dollars": "0.1500", "count_fp": "5.00"}]}

    scheduled = []
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    rec = RFQRecorder(KalshiRFQPricer(book_of(*three_cross_game())), tape, TapeClient(),
                      schedule=lambda delay, fn: scheduled.append((delay, fn)), clock=lambda: NOW)
    msg = combo_rfq(["T1", "T2", "T3"], "r1", 10.0)
    assert rec.on_event("rfq_created", msg, NOW).quoted
    assert rec.on_event("rfq_created", msg, NOW) is None                      # de-duplicated
    assert scheduled[0][0] == 15.0
    scheduled[0][1]()
    lines = [json.loads(l) for l in open(tape.path())]
    assert [l["kind"] for l in lines] == ["rfq", "followup"]
    assert lines[0]["plan"] == "QUOTE" and lines[0]["yes_bid"] == 0.0
    assert lines[1]["trades"][0]["count_fp"] == "5.00"


# ------------------------------------------------------------------------------------------ hygiene
@pytest.mark.parametrize("path", ["mlb/execution/kalshi/rfq_client.py", "mlb/execution/kalshi/rfq_maker.py",
                                  "ev/kalshi_rfq_pricer.py", "scripts/kalshi_rfq_daemon.py", "tests/test_kalshi_rfq.py", "ev/kalshi_game_legs.py", "ev/kalshi_rfq_props.py", "ev/kalshi_props.py", "ev/sports/__init__.py"])
def test_new_files_are_ascii_only(path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        text = fh.read()
    assert text.isascii(), [c for c in text if ord(c) > 127][:5]


# ------------------------------------------------------------------------------------------ read-only production
def _daemon():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("kalshi_rfq_daemon", os.path.join(root, "scripts", "kalshi_rfq_daemon.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readonly_prod_client_cannot_send_anything(pem):
    from mlb.execution.kalshi.client import KalshiClient, PROD_READONLY_ENV
    from mlb.execution.kalshi.rfq_client import RFQReadOnlyClient
    s = FakeSession([FakeResp(200, {"rfqs": [{"id": "r"}], "cursor": ""})])
    c = RFQReadOnlyClient("kid", pem, session=s)
    assert c.env == PROD_READONLY_ENV and "external-api.kalshi.com" in c.base_url
    assert c.get_rfqs() == [{"id": "r"}] and s.calls[0][0] == "GET"
    for name in ("create_quote", "confirm_quote", "delete_quote"):
        assert not hasattr(RFQReadOnlyClient, name)                                           # no quoting API at all
    for method, path in (("POST", "/communications/quotes"), ("PUT", "/communications/quotes/q/confirm"),
                         ("DELETE", "/communications/quotes/q"), ("POST", "/portfolio/events/orders")):
        with pytest.raises(ProductionNotEnabled):
            c.request(method, path, body={})
    with pytest.raises(ProductionNotEnabled):
        c.create_order(ticker="T", client_order_id="x", book_side="bid", count=1, price=0.5)   # inherited helper, same guard
    assert len(s.calls) == 1                                                                   # nothing but the one GET
    with pytest.raises(ProductionNotEnabled):
        KalshiClient(PROD_READONLY_ENV, None, None)                                           # needs credentials


def test_plain_production_env_is_still_refused(pem):
    from mlb.execution.kalshi.client import KalshiClient
    with pytest.raises(ProductionNotEnabled):
        KalshiClient("production", "k", pem)
    with pytest.raises(ProductionNotEnabled):
        RFQClient("production-readonly", "k", pem)               # the QUOTING client never accepts the prod env


def test_readonly_prod_stream_is_receive_only_url(pem):
    from mlb.execution.kalshi.client import load_private_key
    st = KalshiRFQStream("production-readonly", "kid", load_private_key(pem), lambda *a: None)
    assert st.url == "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


class _Args:
    def __init__(self, env=None, mode=None):
        self.env, self.mode = env, mode


def test_daemon_env_flips_and_refusals():
    d = _daemon()
    assert d.resolve_run(_Args(), {}) == ("demo", "record")
    assert d.resolve_run(_Args(), {"KALSHI_RFQ_ENV": "production-readonly"}) == ("production-readonly", "record")
    assert d.resolve_run(_Args(), {"KALSHI_RFQ_MODE": "shadow"}) == ("demo", "shadow")
    assert d.resolve_run(_Args(mode="record"), {"KALSHI_RFQ_MODE": "shadow"}) == ("demo", "record")     # CLI wins
    for env, mode in (("production-readonly", "shadow"), ("production-readonly", "live")):
        with pytest.raises(SystemExit):
            d.resolve_run(_Args(env, mode), {"KALSHI_RFQ_LIVE": "1"})                  # prod can never quote, flag or not
    with pytest.raises(SystemExit):
        d.resolve_run(_Args(), {"KALSHI_RFQ_ENV": "production"})
    with pytest.raises(SystemExit):
        d.resolve_run(_Args(mode="live"), {})                                           # live is not armed
    with pytest.raises(SystemExit):
        d.resolve_run(_Args(mode="live"), {"KALSHI_RFQ_LIVE": "0"})
    assert d.resolve_run(_Args(mode="live"), {"KALSHI_RFQ_LIVE": "1"}) == ("demo", "live")
    assert d.resolve_run(_Args(), {"KALSHI_RFQ_MODE": "live", "KALSHI_RFQ_LIVE": "1"}) == ("demo", "live")


def test_daemon_prod_credentials_are_separate_from_demo(tmp_path, monkeypatch):
    d = _daemon()
    for k in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_PROD_API_KEY_ID", "KALSHI_PROD_PRIVATE_KEY_PATH",
              "KEXEC_ENV_FILE", "KALSHI_ENV"):
        monkeypatch.delenv(k, raising=False)
    key = tmp_path / "k.pem"
    key.write_bytes(b"PEM")
    env_file = tmp_path / ".env"
    env_file.write_text(f"KALSHI_API_KEY_ID=demo-id\nKALSHI_PRIVATE_KEY_PATH={key}\n")
    with pytest.raises(SystemExit):                                                     # demo creds must NOT satisfy prod
        d.load_credentials(str(env_file), "production-readonly")
    env_file.write_text(f"KALSHI_PROD_API_KEY_ID=prod-id\nKALSHI_PROD_PRIVATE_KEY_PATH={key}\n")
    assert d.load_credentials(str(env_file), "production-readonly") == ("prod-id", b"PEM")
    with pytest.raises(SystemExit):                                                     # and prod creds don't satisfy demo
        d.load_credentials(str(env_file), "demo")


# ------------------------------------------------------------------------------------------ GCP recorder
def test_tape_chunks_to_storage_and_keeps_records_when_a_write_fails():
    t = [NOW]
    writes, fail = [], [True]

    def writer(data, key):
        if fail[0]:
            raise OSError("gcs down")
        writes.append((key, data))

    tape = RFQTape(clock=lambda: t[0], storage_prefix="Kalshi/rfq_tape/production", writer=writer, flush_sec=30)
    tape.append({"kind": "rfq", "n": 1})
    assert tape.flush() == 0 and writes == []                       # failed write: nothing lost
    tape.append({"kind": "rfq", "n": 2})
    fail[0] = False
    assert tape.flush() == 2
    key, data = writes[0]
    assert key.startswith("Kalshi/rfq_tape/production/2027-01-15/") and key.endswith("_00002.jsonl")
    assert [json.loads(l)["n"] for l in data.decode().splitlines()] == [1, 2]
    assert tape.flush() == 0                                        # empty buffer: no empty objects
    t[0] += 31                                                      # time-based flush on append
    tape.append({"kind": "rfq", "n": 3})
    assert len(writes) == 2


def test_tape_local_mode_unchanged_and_flush_is_noop(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    tape.append({"a": 1})
    assert tape.flush() == 0 and os.path.exists(tape.path())


def test_daemon_reads_pem_from_env_for_cloud_run(monkeypatch):
    d = _daemon()
    for k in ("KALSHI_PROD_API_KEY_ID", "KALSHI_PROD_PRIVATE_KEY_PATH", "KEXEC_ENV_FILE", "KALSHI_PROD_PRIVATE_KEY_PEM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KALSHI_PROD_API_KEY_ID", "prod-id")
    monkeypatch.setenv("KALSHI_PROD_PRIVATE_KEY_PEM", "-----BEGIN-----\nabc\n-----END-----")
    assert d.load_credentials(None, "production-readonly") == ("prod-id", b"-----BEGIN-----\nabc\n-----END-----")
    with pytest.raises(SystemExit):                                  # the PEM env var is prod-only, never satisfies demo
        d.load_credentials(None, "demo")


def test_recorder_job_script_and_image_wiring():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sh = open(os.path.join(root, "deploy", "setup_kalshi_rfq_recorder.sh"), encoding="utf-8").read()
    assert sh.isascii()
    assert "production-readonly" in sh and "KALSHI_RFQ_MODE=record" in sh
    assert "KALSHI_RFQ_LIVE" not in sh                                # no way to arm quoting from the provisioning script
    assert "--max-retries=0" in sh
    assert "kalshi-demo-api-key-id" in sh and 'SCHED=""' in sh      # demo variant: own secrets, no scheduler
    assert "kalshi_rfq_daemon.py" in open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert "websockets==" in open(os.path.join(root, "requirements.txt"), encoding="utf-8").read()


# ------------------------------------------------------------------------------------------ unpriced legs / diagnostics
def test_widened_gates_accept_borderline_legs():
    legs = [mk_leg("T1", "nfl:2026-10-25:A1@B1", "p1", n=2, disp=0.075, ts=NOW - 1400)] + three_cross_game()[1:]
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert d.quoted                                                          # 2 books / 7.5pp / 23 min old now pass


def test_every_combo_decision_records_per_leg_status():
    legs = three_cross_game()[:2] + [mk_leg("T3", "nfl:2026-10-25:A3@B3", "p3", n=1)]
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq(["T1", "T2", "T3", "TX"]), NOW)
    assert d.reason == "REJ_COMBO_LEG_UNVALIDATED"
    assert [x["status"] for x in d.notes["legs_status"]] == ["OK", "OK", "REJ_LOW_BOOKS", "REJ_UNMAPPED_TICKER"]
    assert d.notes["n_priced"] == 2 and d.notes["n_unpriced"] == 2


def test_unpriced_legs_are_dropped_as_an_upper_bound_up_to_the_limit():
    legs = three_cross_game()[:2]                                           # T3 is not in the book
    rfq = combo_rfq(["T1", "T2", "T3"])
    assert KalshiRFQPricer(book_of(*legs)).price(rfq, NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"      # strict by default
    p = KalshiRFQPricer(book_of(*legs), PricerConfig(max_unpriced_legs=1))
    d = p.price(rfq, NOW)
    assert d.quoted and d.notes["bounded"] and d.notes["n_unpriced"] == 1
    assert d.p_yes_true == pytest.approx(0.25)                              # the unknown leg contributes a factor of 1.0
    assert d.p_yes_cons == pytest.approx(0.508 ** 2)
    assert d.no_bid == floor_to_tick((1 - 0.508 ** 2) - 0.045)              # edge still scales with ALL 3 legs
    assert d.legs and len(d.legs) == 2                                      # last-look baseline covers only priced legs
    two_missing = KalshiRFQPricer(book_of(*legs[:1]), PricerConfig(max_unpriced_legs=1))
    assert two_missing.price(rfq, NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"                          # over the limit
    none_priced = KalshiRFQPricer(book_of(), PricerConfig(max_unpriced_legs=9))
    assert none_priced.price(rfq, NOW).reason == "REJ_COMBO_LEG_UNVALIDATED"                          # nothing to anchor on


def test_hot_leg_always_rejects_even_when_unpriced_legs_are_allowed():
    b = book_of(*three_cross_game()[:2])
    b.mark_hot("nfl:2026-10-25:A2@B2", now=NOW)
    d = KalshiRFQPricer(b, PricerConfig(max_unpriced_legs=5)).price(combo_rfq(["T1", "T2", "T3"]), NOW)
    assert d.reason == "REJ_COMBO_LEG_UNVALIDATED"
    assert "REJ_HOT_LINEUP_SCRATCH" in [x["status"] for x in d.notes["legs_status"]]


def test_long_combo_with_a_few_unpriced_legs_quotes_a_conservative_lay():
    legs = [mk_leg(f"T{i}", f"nfl:2026-10-25:A{i}@B{i}", f"p{i}", p=0.6) for i in range(1, 13)]
    tickers = [l.ticker for l in legs] + ["X1", "X2"]                       # 14 legs, 2 unpriceable
    d = KalshiRFQPricer(book_of(*legs), PricerConfig(max_unpriced_legs=2)).price(combo_rfq(tickers), NOW)
    assert d.quoted and d.notes["n_unpriced"] == 2 and d.no_bid >= 0.9


def test_combo_edge_is_capped_so_long_combos_can_fill():
    legs = [mk_leg(f"T{i}", f"nfl:2026-10-25:A{i}@B{i}", f"p{i}", p=0.5) for i in range(1, 9)]
    d = KalshiRFQPricer(book_of(*legs)).price(combo_rfq([l.ticker for l in legs]), NOW)
    assert d.edge == pytest.approx(0.06)                                    # 8 legs * 1.5c = 12c, capped
    assert d.no_bid >= 0.93


def test_tape_records_notes(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    rec = RFQRecorder(KalshiRFQPricer(book_of(*three_cross_game()[:2])), tape, clock=lambda: NOW)
    rec.on_event("rfq_created", combo_rfq(["T1", "T2", "T3"], "r1", 10.0), NOW)
    line = json.loads(open(tape.path()).readline())
    assert line["notes"]["n_unpriced"] == 1 and line["notes"]["legs_status"][2]["status"] == "REJ_UNMAPPED_TICKER"


# ------------------------------------------------------------------------------------------ game-winner legs
def _slate_event(home="Kansas City Chiefs", away="Buffalo Bills", commence="2026-10-26T00:20:00Z", books=None):
    def book(key, ph, pa):
        return {"key": key, "markets": [{"key": "h2h", "outcomes": [{"name": home, "price": ph}, {"name": away, "price": pa}]}]}
    return {"id": "e1", "home_team": home, "away_team": away, "commence_time": commence,
            "bookmakers": books if books is not None else [book("draftkings", -150, 130), book("fanduel", -155, 135),
                                                            book("betmgm", -145, 125), book("kalshi", -300, 250),
                                                            book("prizepicks", -110, -110)]}


def test_slate_rows_build_kalshi_tickers_and_leave_kalshi_and_dfs_out():
    from ev.kalshi_game_legs import slate_rows
    rows, stats = slate_rows([_slate_event()], "nfl", NOW)
    by = {r["ticker"]: r for r in rows}
    # 2026-10-26T00:20Z is the evening of Oct 25 in ET: the Kalshi event date is the ET date
    assert set(by) == {"KXNFLGAME-26OCT25BUFKC-KC", "KXNFLGAME-26OCT25BUFKC-BUF"}
    kc, buf = by["KXNFLGAME-26OCT25BUFKC-KC"], by["KXNFLGAME-26OCT25BUFKC-BUF"]
    assert kc["n_books"] == 3 and kc["market_class"] == "game_ml" and kc["game_key"] == "nfl:2026-10-25:BUF@KC"
    assert kc["p_yes"] + buf["p_yes"] == pytest.approx(1.0) and 0.55 < kc["p_yes"] < 0.65   # ~-150 favourite, de-vigged
    assert stats["games_priced"] == 1
    lf = leg_from_row(kc, NOW)
    assert lf.player_norm is None and lf.stat == "ml" and lf.sport == "nfl"


def test_slate_rows_fail_closed_on_unknown_team_three_way_and_no_books():
    from ev.kalshi_game_legs import slate_rows
    unknown = _slate_event(home="Springfield Isotopes")
    three_way = _slate_event(books=[{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Kansas City Chiefs", "price": -150}, {"name": "Buffalo Bills", "price": 130}, {"name": "Draw", "price": 900}]}]}])
    only_kalshi = _slate_event(books=[{"key": "kalshi", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Kansas City Chiefs", "price": -150}, {"name": "Buffalo Bills", "price": 130}]}]}])
    rows, stats = slate_rows([unknown, three_way, only_kalshi], "nfl", NOW)
    assert rows == [] and stats["dropped_unknown_team"] == 1 and stats["dropped_thin"] == 2


def test_game_rows_feed_the_pricer_end_to_end():
    from ev.kalshi_game_legs import slate_rows
    rows, _ = slate_rows([_slate_event(), _slate_event(home="Dallas Cowboys", away="Philadelphia Eagles")], "nfl", NOW)
    b = InMemoryFairBook(clock=lambda: NOW)
    b.load_rows(rows, NOW)
    t1, t2 = "KXNFLGAME-26OCT25BUFKC-BUF", "KXNFLGAME-26OCT25PHIDAL-PHI"
    assert b.get(t1, NOW) is not None and b.get(t2, NOW) is not None
    d = KalshiRFQPricer(b).price(combo_rfq([t1, t2]), NOW)
    assert d.notes["n_priced"] == 2 and d.reason in ("PLAN", "REJ_COMBO_NOT_LONGSHOT", "REJ_LOW_ROI")


def test_fetch_game_rows_skips_a_failing_sport():
    from ev.kalshi_game_legs import fetch_game_rows

    class C:
        def get_slate(self, key, markets=None):
            if key == "basketball_nba":
                raise RuntimeError("boom")
            return [_slate_event()]
    rows, stats = fetch_game_rows(C(), ["nfl", "nba"], NOW)
    assert len(rows) == 2 and "error" in stats["nba"] and stats["nfl"]["games_priced"] == 1


def test_daemon_game_sports_parsing_and_feed_state(monkeypatch):
    d = _daemon()
    monkeypatch.delenv("PARLAY_API_KEY", raising=False)
    assert d.make_game_client("nfl:nba")[1] == ()                           # no key: disabled, not a crash
    assert d.make_game_client("")[1] == ()
    book = InMemoryFairBook(clock=lambda: NOW)

    class C:
        def get_slate(self, key, markets=None):
            return [_slate_event()]
    fs = d.FeedState(book, "unused", None, C(), ("nfl",))
    fs.props_rows = [{"ticker": "KXNFLRECYDS-X", "p_over": 0.4, "n_books": 3, "disp": 0.01, "game_key": GK}]
    assert fs.refresh_games() == 3                                          # 1 prop leg + 2 game legs in ONE swap
    fs.props_rows = []
    assert fs.refresh_games() == 2


# ------------------------------------------------------------------------------------------ multi-sport props + spreads/totals
def test_parse_prop_ticker_real_kalshi_grammar():
    from ev.kalshi_props import MapFail, parse_prop_ticker
    p = parse_prop_ticker("KXNBA3PT-26OCT25DETPHI-DETDROBINSON55-1")
    assert (p.sport, p.team, p.player_code, p.jersey, p.stat, p.n, p.strike) == ("nba", "DET", "drobinson", "55", "threes", 1, 0.5)
    assert p.game_key == "nba:2026-10-25:DET@PHI"
    p = parse_prop_ticker("KXNBAAST-26OCT25LACOKC-OKCSGILGEOUSALEXANDER2-8")           # hyphen in the surname vanishes
    assert (p.team, p.player_code, p.strike) == ("OKC", "sgilgeousalexander", 7.5)
    p = parse_prop_ticker("KXNFLRECYDS-26OCT25KCSEA-SEAJSMITHNJIGBA11-50")
    assert (p.sport, p.team, p.stat, p.strike, p.game_key) == ("nfl", "SEA", "rec_yds", 49.5, "nfl:2026-10-25:KC@SEA")
    p = parse_prop_ticker("KXNHLPTS-26OCT26DETEDM-DETMBRANDSEGGNYGRD28-2")
    assert (p.sport, p.stat, p.player_code, p.strike) == ("nhl", "points", "mbrandseggnygrd", 1.5)
    assert parse_prop_ticker("KXNFLTD-26OCT25KCSEA-KCPMAHOMES15-1").strike == 0.5
    for bad in ("KXNFLTD-26OCT26DALPHI-DALLSCHOONMAKER86-2",          # only the 1+ rung maps for anytime TD
                "KXNBAGAME-26OCT25DETPHI-PHI",                        # not a prop ticker
                "KXNHLSOG-26OCT26DETEDM-DETMBRANDSEGGNYGRD28-3",      # series that does not exist on Kalshi
                "KXNBAPTS-26OCT25DETPHI-BOSJTATUM0-25",               # player token starts with neither team
                "KXNBAPTS-26OCT25DETPHI-DETDROBINSON55-0",
                "KXNBAPTS-26XXX25DETPHI-DETDROBINSON55-5"):
        assert isinstance(parse_prop_ticker(bad), MapFail), bad


def test_match_player_code_fails_closed():
    from ev.kalshi_props import kalshi_player_codes, match_player_code
    assert match_player_code("PHI", "tmaxey", ["tyrese maxey", "jared mccain"]) == "tyrese maxey"
    assert match_player_code("OKC", "sgilgeousalexander", ["shai gilgeous alexander"]) == "shai gilgeous alexander"
    assert match_player_code("DET", "jwilliams", ["jaylin williams", "jalen williams"]) is None        # two J. Williams: never guess
    assert match_player_code("DET", "zzz", ["jalen brunson"]) is None
    assert match_player_code("DET", "", ["jalen brunson"]) is None
    # Kalshi drops non-ASCII letters (Nygard -> NYGRD) where the folded name keeps the 'a'
    assert match_player_code("DET", "mbrandseggnygrd", ["michael brandsegg nygard"],
                             {"michael brandsegg nygard": "Michael Brandsegg-Nyg\u00e5rd"}) == "michael brandsegg nygard"
    assert "jjacksonjr" in kalshi_player_codes("jaren jackson", "Jaren Jackson Jr.")


def _nba_event(home, away, player, stat_key, points, books=("draftkings", "fanduel", "betmgm"), over=-110, under=-110, prices=None):
    out = []
    for b in books:
        outs = []
        for pt in points:
            o, u = (prices or {}).get(pt, (over, under))
            outs += [{"name": "Over", "description": player, "price": o, "point": pt},
                     {"name": "Under", "description": player, "price": u, "point": pt}]
        out.append({"key": b, "markets": [{"key": stat_key, "outcomes": outs}]})
    return {"id": "x", "home_team": home, "away_team": away, "commence_time": "2026-10-27T00:00:00Z", "bookmakers": out}


def _prop_book(events_by_sport, now_dt):
    from ev.kalshi_rfq_props import build_sport_prop_index
    b = InMemoryFairBook(clock=lambda: NOW)
    idx = {sp: build_sport_prop_index(sp, evs, now_dt, NOW)[0] for sp, evs in events_by_sport.items()}
    b.attach_prop_indices(idx)
    return b


def test_prop_index_resolves_exact_curve_and_thin_rungs_from_the_ticker():
    from datetime import datetime, timezone
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    ev = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jayson Tatum", "player_points", [22.5, 24.5, 26.5],
                    prices={22.5: (-160, 135), 24.5: (-110, -110), 26.5: (135, -160)})
    ev["bookmakers"][2]["markets"][0]["outcomes"] = [o for o in ev["bookmakers"][2]["markets"][0]["outcomes"] if o["point"] == 24.5]
    thin = _nba_event("Milwaukee Bucks", "Boston Celtics", "Derrick White", "player_points", [12.5], books=("fanduel",))
    ev["bookmakers"] += thin["bookmakers"]
    ev["bookmakers"][-1]["markets"][0]["outcomes"] = [dict(o, description="Derrick White") for o in ev["bookmakers"][-1]["markets"][0]["outcomes"]]
    b = _prop_book({"nba": [ev]}, now_dt)
    exact = b.get("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25", NOW)                        # 25+ == Over 24.5
    assert exact.stat == "points" and not exact.pooled and exact.n_books == 3 and exact.p_cons_yes == pytest.approx(0.5)
    assert exact.player_norm == "jayson tatum" and exact.market_class == "player_prop"
    far = b.get("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-28", NOW)                          # 27.5: only the curve can speak (anchors 22.5..26.5)
    assert far is not None and far.pooled and far.n_books >= 2 and far.p_cons_yes <= 0.5
    assert b.get("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-60", NOW) is None                  # far beyond the curve's span: unmapped
    assert b.why_unmapped("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-60") == "no_rung"
    assert b.get("KXNBAPTS-26OCT26BOSMIL-BOSDWHITE9-13", NOW).n_books == 1             # thin leg is returned so the gate says LOW_BOOKS
    assert b.why_unmapped("KXNBAPTS-26OCT26BOSMIL-BOSXNOBODY1-10") == "no_player"
    assert b.why_unmapped("KXNBAPTS-26OCT27BOSMIL-BOSJTATUM0-25") == "no_game"
    assert b.why_unmapped("KXNBAREB-26OCT26BOSMIL-BOSJTATUM0-8") == "no_stat"


def test_prop_index_team_hint_and_started_games_fail_closed():
    from datetime import datetime, timezone
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    ev = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jayson Tatum (BOS)", "player_points", [24.5])
    b = _prop_book({"nba": [ev]}, now_dt)
    assert b.get("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25", NOW) is not None
    assert b.get("KXNBAPTS-26OCT26BOSMIL-MILJTATUM0-25", NOW) is None                  # books say BOS; the ticker says MIL
    assert b.why_unmapped("KXNBAPTS-26OCT26BOSMIL-MILJTATUM0-25") == "team_mismatch"
    late = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)                              # tip-off has passed: no live pricing
    assert _prop_book({"nba": [ev]}, late).get("KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25", NOW) is None


def test_two_players_sharing_a_code_on_one_team_are_not_guessed():
    from datetime import datetime, timezone
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    ev = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jaylin Williams", "player_points", [10.5])
    other = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jalen Williams", "player_points", [18.5])
    ev["bookmakers"][0]["markets"][0]["outcomes"] += other["bookmakers"][0]["markets"][0]["outcomes"]
    b = _prop_book({"nba": [ev]}, now_dt)
    assert b.get("KXNBAPTS-26OCT26BOSMIL-BOSJWILLIAMS5-11", NOW) is None
    assert b.why_unmapped("KXNBAPTS-26OCT26BOSMIL-BOSJWILLIAMS5-11") == "ambiguous_player"


def test_nba_prop_and_game_winner_combo_is_quoted_with_nothing_unpriced():
    from datetime import datetime, timezone
    from ev.kalshi_game_legs import slate_rows
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    e1 = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jayson Tatum", "player_points", [24.5])
    e2 = _nba_event("Denver Nuggets", "Los Angeles Lakers", "Nikola Jokic", "player_rebounds", [11.5])
    b = _prop_book({"nba": [e1, e2]}, now_dt)
    game = {"id": "g", "home_team": "Phoenix Suns", "away_team": "Golden State Warriors", "commence_time": "2026-10-27T02:00:00Z",
            "bookmakers": [{"key": k, "markets": [{"key": "h2h", "outcomes": [{"name": "Phoenix Suns", "price": 140},
                                                                              {"name": "Golden State Warriors", "price": -160}]}]}
                           for k in ("draftkings", "fanduel", "betmgm")]}
    rows, _ = slate_rows([game], "nba", NOW)
    b.load_rows(rows, NOW)
    legs = ["KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25", "KXNBAREB-26OCT26LALDEN-DENNJOKIC15-12", "KXNBAGAME-26OCT26GSWPHX-PHX"]
    d = KalshiRFQPricer(b).price(combo_rfq(legs, contracts=20), NOW)
    assert d.quoted and d.notes["n_unpriced"] == 0 and d.notes["n_priced"] == 3
    assert d.yes_bid == 0.0 and d.no_bid > 0.0


def test_unmapped_leg_status_carries_the_reason():
    from datetime import datetime, timezone
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    b = _prop_book({"nba": [_nba_event("Milwaukee Bucks", "Boston Celtics", "Jayson Tatum", "player_points", [24.5])]}, now_dt)
    d = KalshiRFQPricer(b).price(combo_rfq(["KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25", "KXNBAPTS-26OCT26BOSMIL-BOSZZZ1-10"]), NOW)
    assert [x["status"] for x in d.notes["legs_status"]] == ["OK", "REJ_UNMAPPED_TICKER"]
    assert d.notes["legs_status"][1]["why"] == "no_player"


def _full_event(home, away, commence="2026-10-26T00:20:00Z"):
    def book(key):
        return {"key": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": -150}, {"name": away, "price": 130}]},
            {"key": "spreads", "outcomes": [{"name": home, "price": -110, "point": -3.5}, {"name": away, "price": -110, "point": 3.5},
                                            {"name": home, "price": 150, "point": -3.0}, {"name": away, "price": -170, "point": 3.0}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 47.5}, {"name": "Under", "price": -110, "point": 47.5},
                                           {"name": "Over", "price": -105, "point": 48.0}, {"name": "Under", "price": -115, "point": 48.0}]}]}
    return {"id": "e", "home_team": home, "away_team": away, "commence_time": commence,
            "bookmakers": [book(k) for k in ("draftkings", "fanduel", "betmgm")]}


def test_slate_rows_emit_spreads_and_totals_with_kalshi_grammar():
    from ev.kalshi_game_legs import slate_rows
    rows, stats = slate_rows([_full_event("Kansas City Chiefs", "Buffalo Bills")], "nfl", NOW)
    by = {r["ticker"]: r for r in rows}
    assert "KXNFLGAME-26OCT25BUFKC-KC" in by
    sp = by["KXNFLSPREAD-26OCT25BUFKC-KC4"]                          # KC -3.5  ==  "KC wins by over 3.5"  == strike N=4
    assert sp["market_class"] == "game_spread" and sp["strike"] == 3.5 and sp["n_books"] == 3
    assert sp["p_yes"] == pytest.approx(0.5)                           # -110/-110 de-vigs to a coin flip
    tot = by["KXNFLTOTAL-26OCT25BUFKC-48"]                           # Over 47.5 == strike N=48
    assert tot["market_class"] == "game_total" and tot["strike"] == 47.5 and tot["p_yes"] == pytest.approx(0.5)
    assert not any(t.endswith("-3") or t.endswith("-KC3") or t.endswith("-49") for t in by)    # whole-number lines never map
    assert stats["spread_rows"] == 1 and stats["total_rows"] == 1
    legs = [leg_from_row(r, NOW) for r in rows]
    assert {l.market_class for l in legs} == {"game_ml", "game_spread", "game_total"}


def test_nhl_totals_are_never_emitted_and_the_crosswalk_maps_los_angeles():
    from ev.kalshi_game_legs import slate_rows
    from ev.sports import kalshi_code
    assert kalshi_code("nhl", "LAK") == "LA" and kalshi_code("nhl", "TBL") == "TB" and kalshi_code("nhl", "VGK") == "VGK"
    assert kalshi_code("nhl", "XXX") is None and kalshi_code("nfl", "JAC") == "JAC"
    rows, stats = slate_rows([_full_event("Los Angeles Kings", "Toronto Maple Leafs")], "nhl", NOW)
    tickers = {r["ticker"] for r in rows}
    assert not any(t.startswith("KXNHLTOTAL") for t in tickers) and stats["total_rows"] == 0
    assert any(t.startswith("KXNHLGAME-26OCT25TORLA-") for t in tickers)             # Kalshi code LA, not LAK
    assert any(t.startswith("KXNHLSPREAD-26OCT25TORLA-LA4") for t in tickers)


def test_fetch_game_rows_requests_all_markets_by_default():
    from ev.kalshi_game_legs import fetch_game_rows
    seen = []

    class C:
        def get_slate(self, key, markets=None):
            seen.append(markets)
            return []
    fetch_game_rows(C(), ["nfl"], NOW)
    fetch_game_rows(C(), ["nfl"], NOW, markets=("h2h",))
    assert seen == [["h2h", "spreads", "totals"], ["h2h"]]


def test_feed_state_loads_prop_indices_and_survives_a_missing_props_file():
    d = _daemon()
    book = InMemoryFairBook(clock=lambda: NOW)
    calls = []

    def loader(sports):
        calls.append(tuple(sports))
        return {}, {"nba": {"events": 0}}
    fs = d.FeedState(book, "/nonexistent/latest.json", None, prop_sports=("nba", "nhl"), prop_loader=loader)
    assert fs.refresh_props() == 0 and calls == [("nba", "nhl")]                     # props file missing: logged, not fatal
    strict = d.FeedState(book, "/nonexistent/latest.json", None)
    with pytest.raises(Exception):
        strict.refresh_props()                                                       # no prop sports and no file: nothing to load


def test_attach_prop_indices_clears_the_resolution_cache():
    from datetime import datetime, timezone
    now_dt = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    b = _prop_book({}, now_dt)
    t = "KXNBAPTS-26OCT26BOSMIL-BOSJTATUM0-25"
    assert b.get(t, NOW) is None
    from ev.kalshi_rfq_props import build_sport_prop_index
    ev = _nba_event("Milwaukee Bucks", "Boston Celtics", "Jayson Tatum", "player_points", [24.5])
    b.attach_prop_indices({"nba": build_sport_prop_index("nba", [ev], now_dt, NOW)[0]})
    assert b.get(t, NOW) is not None                                                 # the earlier miss was not remembered
