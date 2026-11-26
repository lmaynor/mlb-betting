"""Offline unit and integration tests for MAK-03 Novig live maker (built DARK).

All network-shaped surfaces are faked; zero real network calls or credentials.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import ev.novig.client as novig_client_mod
from ev.maker_settlement import DictResolver, Outcome, STATUS_WIN
from ev.novig import maker, maker_bank, signing
from ev.novig.client import (
    PAPER_ENV,
    PROD_ARM_ENV,
    PROD_ENV,
    PROD_READONLY_ENV,
    PROD_URL,
    NovigAPIError,
    NovigClient,
    NovigProductionNotEnabled,
)
from ev.novig.live_maker import (
    ENABLE_ENV,
    LIVE_ENV,
    CandidateView,
    DecisionLog,
    DryRunVenue,
    ExposureBook,
    FileFlagSource,
    GcsFlagSource,
    LiveMakerConfig,
    LiveMakerVenue,
    NovigLiveMaker,
    RUNGS,
    assign_rung,
    build_prod_client,
    build_readonly_client,
    client_id_for_live,
    evaluate_quote,
    load_live_maker_config,
    rung_bid,
    view_from_decision,
)
from ev.novig.map import NovigLeg
from ev.novig.shadow_bank import DirStore
from scripts.novig_live_maker import (
    _parse_balance_response,
    check_live_preconditions,
    main as runner_main,
    run_dry_run,
    run_live,
)

UTC = timezone.utc
NOW_DT = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
NOW = NOW_DT.timestamp()
GK = "nhl:2026-11-26:DET@CHI"


@pytest.fixture(scope="module")
def ed25519_pem() -> bytes:
    fix = Path(__file__).parent / "fixtures" / "ev" / "novig_signing_vectors.json"
    data = json.loads(fix.read_text(encoding="utf-8"))
    return data["keypairs"]["ed25519-test-1"]["private_key_pkcs8_pem"].encode("ascii")


class MemoryFlagSource:
    def __init__(self, enabled: bool = True, stopped: bool = False, readable: bool = True):
        self.enabled = enabled
        self.stopped = stopped
        self.readable = readable

    def flags(self) -> tuple[bool, bool, bool]:
        return (self.enabled, self.stopped, self.readable)


class _Resp:
    def __init__(self, status: int, js: dict | None = None):
        self.status_code = status
        self._js = js if js is not None else {}
        self.headers = {}
        self.content = b"1" if js is not None else b""
        self.text = json.dumps(self._js)

    def json(self):
        return self._js


class RaisingSession:
    def __init__(self):
        self.headers = {}
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("network session must not be touched")


class RecordingSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls: list[tuple] = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, data, headers))
        return self.responses.pop(0)


def _make_leg(
    *,
    game_key: str = GK,
    stat: str = "sog",
    player: str = "Patrick Kane",
    player_norm: str = "patrick kane",
    strike: float = 3.5,
    market_id: str = "mkt-live-1",
    over_id: str = "out-over-1",
    under_id: str = "out-under-1",
    start_utc: datetime | None = None,
) -> NovigLeg:
    return NovigLeg(
        market_id=market_id,
        event_id="ev-1",
        stat=stat,
        player=player,
        player_norm=player_norm,
        strike=strike,
        game_key=game_key,
        start_utc=start_utc or (NOW_DT + timedelta(hours=2)),
        over_id=over_id,
        under_id=under_id,
        fee_coefficient=0.03,
        fee_charged="WHEN_LIVE",
        description=f"{player} {strike} SHOTS_ON_GOAL",
    )


def _make_candidate(**overrides) -> maker.MakerCandidate:
    base = dict(
        league="NHL",
        stat="sog",
        game_key=GK,
        player_norm="patrick kane",
        line=3.5,
        is_alt=False,
        p_cons=0.50,
        p_model=0.53,
        n_books=6,
        disp=0.01,
        novig_bid_over=0.38,
        novig_ask_over=0.52,
        mins_to_start=120.0,
        snapshot_age_sec=30.0,
    )
    base.update(overrides)
    return maker.MakerCandidate(**base)


# ------------------------------------------------------------------ 1. Client gates
def test_client_production_gates(monkeypatch, ed25519_pem):
    monkeypatch.delenv(PROD_ARM_ENV, raising=False)
    # production refused without NOVIG_PROD_ARMED
    with pytest.raises(NovigProductionNotEnabled, match=PROD_ARM_ENV):
        NovigClient(PROD_ENV, "k1", ed25519_pem)

    # production refused without credentials even if armed
    monkeypatch.setenv(PROD_ARM_ENV, "1")
    with pytest.raises(NovigProductionNotEnabled):
        NovigClient(PROD_ENV)

    # production constructs when armed + credentials
    prod = NovigClient(PROD_ENV, "k1", ed25519_pem, session=RaisingSession())
    assert prod.env == PROD_ENV
    assert prod.base == PROD_URL

    # production-readonly refuses without credentials
    with pytest.raises(NovigProductionNotEnabled):
        NovigClient(PROD_READONLY_ENV)

    # production-readonly refuses POST BEFORE touching session
    rs = RaisingSession()
    ro = NovigClient(PROD_READONLY_ENV, "k1", ed25519_pem, session=rs)
    assert ro.base == PROD_URL
    with pytest.raises(NovigProductionNotEnabled):
        ro.place_order("out-1", 0.45, 100, "PO")
    assert rs.calls == 0

    # production-readonly allows signed GET balance
    rec_sess = RecordingSession([_Resp(200, {"balance": "123.45"})])
    ro2 = NovigClient(PROD_READONLY_ENV, "k1", ed25519_pem, session=rec_sess, clock=lambda: 1755000000.0)
    bal = ro2.balance()
    assert bal == {"balance": "123.45"}
    assert len(rec_sess.calls) == 1
    method, url, _data, hdrs = rec_sess.calls[0]
    assert method == "GET"
    assert url == f"{PROD_URL}/v3/account/balance"
    assert "Novig-Signature" in hdrs

    # paper env still constructs
    paper = NovigClient(PAPER_ENV, "k1", ed25519_pem, session=RaisingSession())
    assert paper.env == PAPER_ENV


# ------------------------------------------------------------------ 2. Client factories
def test_build_prod_and_readonly_clients(monkeypatch, ed25519_pem):
    monkeypatch.setenv(PROD_ARM_ENV, "1")
    monkeypatch.delenv(ENABLE_ENV, raising=False)

    with pytest.raises(NovigProductionNotEnabled, match=ENABLE_ENV):
        build_prod_client("k1", ed25519_pem)

    c_prod = build_prod_client("k1", ed25519_pem, env={ENABLE_ENV: "1"})
    assert c_prod.env == PROD_ENV

    c_ro = build_readonly_client("k1", ed25519_pem)
    assert c_ro.env == PROD_READONLY_ENV


# ------------------------------------------------------------------ 3. assign_rung
def test_assign_rung_deterministic_and_balanced():
    cfg = LiveMakerConfig()
    r1 = assign_rung("nhl:2026-11-26:DET@CHI", "NHL", "sog", cfg)
    r2 = assign_rung("nhl:2026-11-26:DET@CHI", "NHL", "sog", cfg)
    assert r1 == r2
    assert r1 in RUNGS

    counts = {r: 0 for r in RUNGS}
    n = 2500
    for i in range(n):
        rung = assign_rung(f"nfl:2026-11-26:G{i}@H{i}", "NFL", ("receptions", "rush_yds", "rec_yds")[i % 3], cfg)
        counts[rung] += 1

    expected = {"rung_1c": 0.30, "rung_2c": 0.30, "rung_3c": 0.20, "control": 0.20}
    for r, target in expected.items():
        share = counts[r] / n
        assert abs(share - target) <= 0.05, f"rung {r} share={share:.4f} outside +/-5pp of {target}"


# ------------------------------------------------------------------ 4. rung_bid
def test_rung_bid_cap_stub_pause_and_behind_book():
    cfg = LiveMakerConfig()
    # 1c rung on cons 0.60 with existing bid 0.50 -> cap = 0.50 + 0.01 = 0.51 applies!
    bid, reason = rung_bid(
        rung="rung_1c", p_cons_side=0.60, fair_floor=0.58, delta_control=0.04,
        novig_bid_side=0.50, disp=0.01, cfg=cfg,
    )
    assert bid == 0.510 and reason == ""

    # Stub lift: existing bid 0.40 vs cons 0.60 (0.60 - 0.15 = 0.45 > 0.40 -> stub) -> 0.590
    bid_stub, reason_stub = rung_bid(
        rung="rung_1c", p_cons_side=0.60, fair_floor=0.58, delta_control=0.04,
        novig_bid_side=0.40, disp=0.01, cfg=cfg,
    )
    assert bid_stub == 0.590 and reason_stub == ""

    # Dispersion pause on clean arms only
    bid_disp, reason_disp = rung_bid(
        rung="rung_2c", p_cons_side=0.60, fair_floor=0.58, delta_control=0.04,
        novig_bid_side=0.40, disp=0.03, cfg=cfg,
    )
    assert bid_disp is None and reason_disp == "DISPERSION_PAUSE"

    # Control ignores clean-arm dispersion pause
    bid_ctrl, reason_ctrl = rung_bid(
        rung="control", p_cons_side=0.60, fair_floor=0.58, delta_control=0.04,
        novig_bid_side=0.40, disp=0.03, cfg=cfg,
    )
    assert bid_ctrl == 0.540 and reason_ctrl == ""

    # Bid behind book -> None / BID_BEHIND_BOOK
    bid_behind, reason_behind = rung_bid(
        rung="rung_3c", p_cons_side=0.52, fair_floor=0.52, delta_control=0.04,
        novig_bid_side=0.50, disp=0.01, cfg=cfg,
    )
    assert bid_behind is None and reason_behind == "BID_BEHIND_BOOK"


# ------------------------------------------------------------------ 5. evaluate_quote
def test_evaluate_quote_gates_and_happy_path():
    cfg = LiveMakerConfig(aggregate_cap_usd=25.0, per_quote_cap_usd=5.0, quote_liability_usd=5.0)
    base_view = CandidateView(
        game_key=GK, league="NHL", stat="sog", side="yes", mode="MAKER_PAPER",
        p_cons_side=0.60, fair_floor=0.58, delta_control=0.04, disp=0.01,
        novig_bid_side=0.40, novig_ask_side=0.65, rung="rung_1c",
    )

    # MODE_TAKER skip
    ev_taker = evaluate_quote(
        CandidateView(**{**base_view.__dict__, "mode": "TAKER"}),
        cfg=cfg, balance=100.0, committed=0.0,
    )
    assert ev_taker.outcome == "SKIPPED" and ev_taker.reason == "MODE_TAKER"

    # CROSSES_ASK (bid 0.59 >= ask 0.55)
    ev_cross = evaluate_quote(
        CandidateView(**{**base_view.__dict__, "novig_ask_side": 0.55}),
        cfg=cfg, balance=100.0, committed=0.0,
    )
    assert ev_cross.outcome == "SKIPPED" and ev_cross.reason == "CROSSES_ASK"

    # SIZE_ZERO (tiny liability)
    tiny_cfg = LiveMakerConfig(aggregate_cap_usd=25.0, per_quote_cap_usd=0.10, quote_liability_usd=0.10)
    ev_zero = evaluate_quote(base_view, cfg=tiny_cfg, balance=100.0, committed=0.0)
    assert ev_zero.outcome == "SKIPPED" and ev_zero.reason == "SIZE_ZERO"

    # Sizing respects min(quote_liability_usd, per_quote_cap_usd)
    mismatch_cfg = LiveMakerConfig(aggregate_cap_usd=25.0, per_quote_cap_usd=5.0, quote_liability_usd=10.0)
    ev_cap_min = evaluate_quote(base_view, cfg=mismatch_cfg, balance=100.0, committed=0.0)
    assert ev_cap_min.outcome == "QUOTED" and ev_cap_min.cost <= 5.0

    # AGGREGATE_CAP_UNSET
    unset_cfg = LiveMakerConfig(aggregate_cap_usd=None)
    ev_unset = evaluate_quote(base_view, cfg=unset_cfg, balance=100.0, committed=0.0)
    assert ev_unset.outcome == "SKIPPED" and ev_unset.reason == "AGGREGATE_CAP_UNSET"

    # AGGREGATE_CAP (committed 22 out of 25 -> headroom 3.0 < cost 4.72)
    ev_agg = evaluate_quote(base_view, cfg=cfg, balance=100.0, committed=22.0)
    assert ev_agg.outcome == "SKIPPED" and ev_agg.reason == "AGGREGATE_CAP"

    # Happy path QUOTED
    ev_ok = evaluate_quote(base_view, cfg=cfg, balance=100.0, committed=10.0)
    assert ev_ok.outcome == "QUOTED" and ev_ok.reason == ""
    assert ev_ok.bid == 0.59
    assert ev_ok.contracts == 8  # int(5.0 / 0.59) == 8
    assert ev_ok.cost == round(8 * 0.59, 6)
    assert ev_ok.committed_after == round(10.0 + ev_ok.cost, 6)


def test_view_from_decision_side_complements():
    cand = _make_candidate(p_cons=0.60, novig_bid_over=0.50, novig_ask_over=0.62)
    dec_no = maker.MakerDecision(mode="MAKER_PAPER", side="no", bid_price=0.39, contracts=12, fair_floor=0.40, delta=0.03)
    view = view_from_decision(cand, dec_no)
    assert view.side == "no"
    assert view.p_cons_side == pytest.approx(0.40)
    assert view.novig_bid_side == pytest.approx(0.38)
    assert view.novig_ask_side == pytest.approx(0.50)


# ------------------------------------------------------------------ 6. ExposureBook
def test_exposure_book_split_accounting():
    eb = ExposureBook()
    eb.add_open("o1", 4.50)
    assert eb.committed() == pytest.approx(4.50)

    # Partial fill moves min(resting, filled_cost)
    eb.apply_fill("o1", 2.00)
    assert eb.orders["o1"]["resting"] == pytest.approx(2.50)
    assert eb.orders["o1"]["filled"] == pytest.approx(2.00)
    assert eb.committed() == pytest.approx(4.50)

    # Release drops resting part, keeps filled part
    eb.release("o1")
    assert eb.orders["o1"]["resting"] == pytest.approx(0.0)
    assert eb.orders["o1"]["filled"] == pytest.approx(2.00)
    assert eb.committed() == pytest.approx(2.00)

    # Settle drops filled part and drops order when both 0
    eb.settle("o1")
    assert "o1" not in eb.orders
    assert eb.committed() == pytest.approx(0.0)


# ------------------------------------------------------------------ 7. Flag sources
def test_flag_sources(tmp_path: Path):
    # GcsFlagSource
    assert GcsFlagSource(exists_fn=lambda k: k.endswith("/ENABLE")).flags() == (True, False, True)
    assert GcsFlagSource(exists_fn=lambda k: False).flags() == (False, False, True)

    def _boom(_k):
        raise OSError("gcs down")

    assert GcsFlagSource(exists_fn=_boom).flags() == (False, True, False)

    # FileFlagSource
    missing_dir = tmp_path / "nope"
    assert FileFlagSource(missing_dir).flags() == (False, True, False)

    flag_dir = tmp_path / "flags"
    flag_dir.mkdir()
    assert FileFlagSource(flag_dir).flags() == (False, False, True)
    (flag_dir / "ENABLE").write_text("1", encoding="ascii")
    assert FileFlagSource(flag_dir).flags() == (True, False, True)
    (flag_dir / "STOP").write_text("1", encoding="ascii")
    assert FileFlagSource(flag_dir).flags() == (True, True, True)


# ------------------------------------------------------------------ 8. LiveMakerVenue
class FakeProdClient:
    def __init__(self, env: str = PROD_ENV):
        self.env = env
        self.posts: list[dict] = []
        self.cancels: list[str] = []
        self.raise_on_post: Exception | None = None
        self.fills_list: list[dict] = []
        self.orders_pages: list[dict] = []

    def place_order(self, outcome_id, price, qty, tif="PO", client_id=None):
        self.posts.append({"outcome_id": outcome_id, "price": price, "qty": qty, "tif": tif, "client_id": client_id})
        if self.raise_on_post is not None:
            raise self.raise_on_post
        return {"orderId": f"ord-{len(self.posts)}"}

    def cancel_order(self, order_id):
        self.cancels.append(order_id)
        return {"status": "CANCELLED"}

    def get_order(self, order_id):
        return {"orderId": order_id, "status": "OPEN"}

    def list_orders(self, **q):
        if self.orders_pages:
            return self.orders_pages.pop(0)
        return {"items": [], "next": None}

    def fills(self, **q):
        return {"items": list(self.fills_list), "next": None}


def test_live_maker_venue_contracts():
    # Rejects paper client
    with pytest.raises(NovigProductionNotEnabled):
        LiveMakerVenue(FakeProdClient(env=PAPER_ENV))

    leg = _make_leg()
    # Happy path PO with deterministic clientId and qty = units * 100
    fc = FakeProdClient()
    v = LiveMakerVenue(fc)
    res = v.place_resting(leg, intent_id="intent-1", side="yes", price=0.412, contracts_units=5)
    assert res["status"] == "OPEN" and res["order_id"] == "ord-1"
    assert len(fc.posts) == 1
    assert fc.posts[0]["tif"] == "PO"
    assert fc.posts[0]["qty"] == 500
    assert fc.posts[0]["price"] == "0.410"
    assert fc.posts[0]["client_id"] == client_id_for_live("intent-1")

    # 4xx -> ERROR with exactly one POST
    fc4 = FakeProdClient()
    fc4.raise_on_post = NovigAPIError(400, "BAD_PRICE", "bad", "POST", "/v3/orders")
    res4 = LiveMakerVenue(fc4).place_resting(leg, intent_id="intent-4", side="yes", price=0.41, contracts_units=5)
    assert res4["status"] == "ERROR" and len(fc4.posts) == 1

    # 5xx with matching fill -> OPEN reconciled, one POST
    fc5_ok = FakeProdClient()
    cid5 = client_id_for_live("intent-5a")
    fc5_ok.raise_on_post = NovigAPIError(503, "UNAVAILABLE", "down", "POST", "/v3/orders")
    fc5_ok.fills_list = [{"fillId": "f1", "orderId": "ord-rec-1", "clientId": cid5, "qty": 500, "cost": "2.05"}]
    res5_ok = LiveMakerVenue(fc5_ok).place_resting(leg, intent_id="intent-5a", side="yes", price=0.41, contracts_units=5)
    assert res5_ok["status"] == "OPEN" and res5_ok.get("reconciled") is True and res5_ok["order_id"] == "ord-rec-1"
    assert len(fc5_ok.posts) == 1

    # 5xx without fills -> UNKNOWN, one POST
    fc5_unk = FakeProdClient()
    fc5_unk.raise_on_post = NovigAPIError(502, "BAD_GW", "gw", "POST", "/v3/orders")
    res5_unk = LiveMakerVenue(fc5_unk).place_resting(leg, intent_id="intent-5b", side="yes", price=0.41, contracts_units=5)
    assert res5_unk["status"] == "UNKNOWN" and len(fc5_unk.posts) == 1

    # Network error without fills -> UNKNOWN, one POST
    fc_net = FakeProdClient()
    fc_net.raise_on_post = requests.Timeout("timed out")
    res_net = LiveMakerVenue(fc_net).place_resting(leg, intent_id="intent-net", side="yes", price=0.41, contracts_units=5)
    assert res_net["status"] == "UNKNOWN" and len(fc_net.posts) == 1


# ------------------------------------------------------------------ 9 & 10. Quoter boot-off and happy path
def _make_quoter(
    tmp_path: Path,
    *,
    venue=None,
    flag_source=None,
    balance_val: float = 100.0,
    cfg: LiveMakerConfig | None = None,
    env: dict | None = None,
    resolver=None,
    clock_box: list[float] | None = None,
):
    cbox = clock_box if clock_box is not None else [NOW]
    dlog = DecisionLog(path=tmp_path / "decisions.jsonl", clock=lambda: cbox[0])
    bal_calls = {"n": 0}

    def bal_fn():
        bal_calls["n"] += 1
        return balance_val

    q = NovigLiveMaker(
        venue=venue or DryRunVenue(),
        flag_source=flag_source or MemoryFlagSource(),
        balance_provider=bal_fn,
        decision_log=dlog,
        cfg=cfg or LiveMakerConfig(aggregate_cap_usd=25.0),
        clock=lambda: cbox[0],
        env=env if env is not None else {ENABLE_ENV: "1"},
        resolver=resolver,
    )
    return q, dlog, bal_calls


def _read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines() if line.strip()]


def test_quoter_boot_off(tmp_path: Path):
    venue = DryRunVenue()
    q, dlog, _ = _make_quoter(
        tmp_path,
        venue=venue,
        flag_source=MemoryFlagSource(enabled=False, stopped=False, readable=True),
        env={},
    )
    q.start()
    ev = q.on_candidate(_make_candidate(), _make_leg())
    assert ev is not None and ev.outcome == "SKIPPED" and ev.reason == "DISABLED"
    assert len(venue.attempts) == 0

    rows = _read_log(Path(dlog.path))
    assert rows[0]["event"] == "SESSION_START" and rows[0]["enabled"] is False
    assert rows[1]["event"] == "EVALUATION" and rows[1]["reason"] == "DISABLED"


def test_quoter_happy_path_and_dedupe(tmp_path: Path):
    venue = DryRunVenue()
    q, dlog, _ = _make_quoter(tmp_path, venue=venue)
    q.start()
    cand = _make_candidate()
    leg = _make_leg()

    ev1 = q.on_candidate(cand, leg)
    assert ev1 is not None and ev1.outcome == "QUOTED"
    assert len(venue.attempts) == 1
    assert q.exposure.committed() == pytest.approx(ev1.cost)
    assert "dryrun-1" in q._open_meta

    # Second identical candidate -> ALREADY_RESTING, no second attempt
    ev2 = q.on_candidate(cand, leg)
    assert ev2 is not None and ev2.outcome == "SKIPPED" and ev2.reason == "ALREADY_RESTING"
    assert len(venue.attempts) == 1


# ------------------------------------------------------------------ 11. STOP cancels all open
def test_quoter_stop_flag_cancels_all(tmp_path: Path):
    venue = DryRunVenue()
    fs = MemoryFlagSource(enabled=True, stopped=False, readable=True)
    q, dlog, _ = _make_quoter(tmp_path, venue=venue, flag_source=fs)
    q.start()
    ev = q.on_candidate(_make_candidate(), _make_leg())
    assert ev.outcome == "QUOTED" and q.exposure.committed() > 0

    fs.stopped = True
    q.poll_open_orders({})
    assert venue.cancels == ["dryrun-1"]
    assert q.exposure.committed() == pytest.approx(0.0)
    rows = _read_log(Path(dlog.path))
    assert any(r["event"] == "CANCEL_ALL" and r["reason"] == "STOP_FLAG" for r in rows)


# ------------------------------------------------------------------ 12. Cancel triggers (TTL & T-5m)
def test_quoter_ttl_and_t_minus_5_cancels(tmp_path: Path):
    cbox = [NOW]
    venue = DryRunVenue()
    q, dlog, _ = _make_quoter(tmp_path, venue=venue, clock_box=cbox)
    q.start()

    # Order 1: far-future commence, posted at NOW -> advances +901s -> TTL expired
    leg1 = _make_leg(market_id="m1", start_utc=NOW_DT + timedelta(hours=4))
    cand1 = _make_candidate(game_key=GK, mins_to_start=240.0)
    ev1 = q.on_candidate(cand1, leg1, now=NOW)
    assert ev1.outcome == "QUOTED"

    # Order 2: commence in 19 mins -> advances +901s -> 4 mins to commence (inside T-5m)
    leg2 = _make_leg(market_id="m2", player="Alex DeBrincat", player_norm="alex debrincat", start_utc=NOW_DT + timedelta(minutes=19))
    cand2 = _make_candidate(game_key="nhl:2026-11-26:BOS@TOR", player_norm="alex debrincat", mins_to_start=19.0)
    ev2 = q.on_candidate(cand2, leg2, now=NOW)
    assert ev2.outcome == "QUOTED"

    cbox[0] = NOW + 901.0
    cancelled = q.poll_open_orders({}, now=cbox[0])
    assert set(cancelled) == {"dryrun-1", "dryrun-2"}
    assert q.exposure.committed() == pytest.approx(0.0)

    rows = _read_log(Path(dlog.path))
    c_events = {r["order_id"]: r["reasons"] for r in rows if r["event"] == "CANCELLED"}
    assert "ttl_expired" in c_events["dryrun-1"]
    assert "t_minus_5_hard_cancel" in c_events["dryrun-2"]


# ------------------------------------------------------------------ 13. sync_fills
class FillableDryRunVenue(DryRunVenue):
    def __init__(self):
        super().__init__()
        self.fills_by_cid: dict[str, list[dict]] = {}

    def fills_for(self, client_id: str) -> list[dict]:
        return list(self.fills_by_cid.get(client_id, []))


def test_quoter_sync_fills(tmp_path: Path):
    venue = FillableDryRunVenue()
    q, dlog, bal_calls = _make_quoter(tmp_path, venue=venue)
    q.start()
    ev = q.on_candidate(_make_candidate(), _make_leg())
    assert ev.outcome == "QUOTED"
    cid = q._open_meta["dryrun-1"]["client_id"]

    # Fill 200 cent-contracts = 2 units at total cost "1.00000" -> p_fill = 0.50
    venue.fills_by_cid[cid] = [{"fillId": "f1", "orderId": "dryrun-1", "clientId": cid, "qty": 200, "cost": "1.00000"}]
    before_calls = bal_calls["n"]
    filled = q.sync_fills()
    assert filled == ["dryrun-1"]
    assert bal_calls["n"] == before_calls + 1
    assert q._open_meta["dryrun-1"]["filled_units"] == pytest.approx(2.0)
    assert q._open_meta["dryrun-1"]["p_fill"] == pytest.approx(0.50)
    assert q.exposure.orders["dryrun-1"]["filled"] == pytest.approx(2.0 * ev.bid)
    assert q.exposure.orders["dryrun-1"]["resting"] == pytest.approx(ev.cost - 2.0 * ev.bid)

    rows = _read_log(Path(dlog.path))
    f_rows = [r for r in rows if r["event"] == "FILLED"]
    assert len(f_rows) == 1 and f_rows[0]["p_fill"] == pytest.approx(0.50)


# ------------------------------------------------------------------ 14. sweep_settlements
def test_quoter_sweep_settlements(tmp_path: Path):
    # Case A: DictResolver settles winning order -> SETTLED logged, filled released, balance refreshed
    venue = FillableDryRunVenue()
    resolver = DictResolver({"dryrun-1": Outcome(1.0, STATUS_WIN, "dict", "won", 4.0)})
    q, dlog, bal_calls = _make_quoter(tmp_path, venue=venue, resolver=resolver)
    q.start()
    ev = q.on_candidate(_make_candidate(), _make_leg())
    cid = q._open_meta["dryrun-1"]["client_id"]
    venue.fills_by_cid[cid] = [{"fillId": "f1", "orderId": "dryrun-1", "clientId": cid, "qty": int(ev.contracts * 100), "cost": f"{ev.cost:.5f}"}]
    q.sync_fills()
    calls_before = bal_calls["n"]
    settled = q.sweep_settlements()
    assert settled == ["dryrun-1"]
    assert bal_calls["n"] == calls_before + 1
    assert q.exposure.committed() == pytest.approx(0.0)
    rows = _read_log(Path(dlog.path))
    s_rows = [r for r in rows if r["event"] == "SETTLED"]
    assert len(s_rows) == 1 and s_rows[0]["won"] is True and s_rows[0]["net_realized_pnl_usd"] is not None

    # Case B: UNRECONCILED stays committed and logs SETTLEMENT_PENDING only once
    venue2 = FillableDryRunVenue()
    q2, dlog2, _ = _make_quoter(tmp_path / "b", venue=venue2, resolver=DictResolver({}))
    q2.start()
    ev2 = q2.on_candidate(_make_candidate(), _make_leg())
    cid2 = q2._open_meta["dryrun-1"]["client_id"]
    venue2.fills_by_cid[cid2] = [{"fillId": "f2", "orderId": "dryrun-1", "clientId": cid2, "qty": 200, "cost": "0.80"}]
    q2.sync_fills()
    committed_before = q2.exposure.committed()
    assert q2.sweep_settlements() == []
    assert q2.sweep_settlements() == []
    assert q2.exposure.committed() == pytest.approx(committed_before)
    rows2 = _read_log(Path(dlog2.path))
    pend = [r for r in rows2 if r["event"] == "SETTLEMENT_PENDING"]
    assert len(pend) == 1


# ------------------------------------------------------------------ 15. DecisionLog
def test_decision_log_flush_with_injected_writer(tmp_path: Path):
    written: list[tuple[bytes, str]] = []
    dlog = DecisionLog(
        path=tmp_path / "d.jsonl",
        storage_prefix="Novig/live_maker/decisions",
        writer=lambda blob, key: written.append((blob, key)),
        clock=lambda: NOW,
    )
    dlog.record({"event": "SESSION_START", "outcome": "SESSION_START"})
    dlog.record({"event": "EVALUATION", "outcome": "SKIPPED", "reason": "DISABLED"})
    n = dlog.flush()
    assert n == 2
    assert len(written) == 1
    blob, key = written[0]
    assert key.startswith("Novig/live_maker/decisions/")
    lines = [json.loads(l) for l in blob.decode("ascii").splitlines() if l.strip()]
    assert len(lines) == 2
    assert all(l["kind"] == "novig_live_maker_decision" for l in lines)


# ------------------------------------------------------------------ 16. Runner preconditions & dry-run
@pytest.mark.parametrize(
    "env_override,flags,expected_substr",
    [
        ({}, (True, False, True), ENABLE_ENV),
        ({ENABLE_ENV: "1"}, (True, False, True), LIVE_ENV),
        ({ENABLE_ENV: "1", LIVE_ENV: "1"}, (True, False, True), PROD_ARM_ENV),
        ({ENABLE_ENV: "1", LIVE_ENV: "1", PROD_ARM_ENV: "1"}, (True, False, True), "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD"),
        (
            {ENABLE_ENV: "1", LIVE_ENV: "1", PROD_ARM_ENV: "1", "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "0"},
            (True, False, True),
            "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD",
        ),
        (
            {ENABLE_ENV: "1", LIVE_ENV: "1", PROD_ARM_ENV: "1", "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "25"},
            (True, False, False),
            "flag store unreadable",
        ),
        (
            {ENABLE_ENV: "1", LIVE_ENV: "1", PROD_ARM_ENV: "1", "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "25"},
            (True, True, True),
            "STOP flag is set",
        ),
        (
            {ENABLE_ENV: "1", LIVE_ENV: "1", PROD_ARM_ENV: "1", "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "25"},
            (False, False, True),
            "ENABLE flag is not set",
        ),
    ],
)
def test_check_live_preconditions_each_missing(env_override, flags, expected_substr):
    fs = MemoryFlagSource(*flags)
    msg = check_live_preconditions(env=env_override, flag_source=fs)
    assert msg is not None
    assert msg.startswith("missing precondition:")
    assert expected_substr in msg


def test_check_live_preconditions_passes_and_run_live_refuses_default(monkeypatch, capsys):
    env_ok = {
        ENABLE_ENV: "1",
        LIVE_ENV: "1",
        PROD_ARM_ENV: "1",
        "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "25.0",
    }
    assert check_live_preconditions(env=env_ok, flag_source=MemoryFlagSource(True, False, True)) is None

    monkeypatch.delenv(ENABLE_ENV, raising=False)
    rc = runner_main(["live"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing precondition:" in err


def _find_game_key_for_rung(target_rung: str, league: str = "NHL", stat: str = "sog") -> str:
    for i in range(1000):
        gk = f"nhl:2026-11-26:T{i}@H{i}"
        if assign_rung(gk, league, stat) == target_rung:
            return gk
    raise AssertionError(f"could not find game_key for {target_rung}")


def test_dry_run_end_to_end_and_zero_client(tmp_path: Path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise AssertionError("NovigClient must never be constructed in dry-run")

    monkeypatch.setattr(novig_client_mod, "NovigClient", _no_client)
    monkeypatch.setattr("scripts.novig_live_maker.NovigClient", _no_client)

    gk_1c = _find_game_key_for_rung("rung_1c")
    gk_ctrl = _find_game_key_for_rung("control")
    gk_2c = _find_game_key_for_rung("rung_2c")

    # Build 3 quote states in a DirStore bank:
    # 1) rung_1c quote (~$4.80 cost) -> QUOTED
    # 2) control quote (~$4.80 cost) -> QUOTED
    # 3) rung_2c quote (~$4.80 cost) -> SKIPPED AGGREGATE_CAP when cap=10.00!
    states = [
        maker_bank.new_quote_state(
            order_id="o-1", client_id="c-1", game_key=gk_1c, game_day="2026-11-26",
            sport="NHL", stat="sog", player_norm="p1", line=2.5, side="yes",
            t_post="2026-11-26T16:00:00+00:00", quote_price=0.40, contracts_units=12,
            novig_bid_at_post=0.35, novig_ask_at_post=0.70, p_cons_at_post=0.60,
            fair_floor=0.58, delta=0.035,
        ),
        maker_bank.new_quote_state(
            order_id="o-2", client_id="c-2", game_key=gk_ctrl, game_day="2026-11-26",
            sport="NHL", stat="sog", player_norm="p2", line=3.5, side="yes",
            t_post="2026-11-26T16:05:00+00:00", quote_price=0.40, contracts_units=12,
            novig_bid_at_post=0.35, novig_ask_at_post=0.70, p_cons_at_post=0.60,
            fair_floor=0.58, delta=0.040,
        ),
        maker_bank.new_quote_state(
            order_id="o-3", client_id="c-3", game_key=gk_2c, game_day="2026-11-26",
            sport="NHL", stat="sog", player_norm="p3", line=4.5, side="yes",
            t_post="2026-11-26T16:10:00+00:00", quote_price=0.40, contracts_units=12,
            novig_bid_at_post=0.35, novig_ask_at_post=0.70, p_cons_at_post=0.60,
            fair_floor=0.58, delta=0.035,
        ),
    ]
    store = DirStore(str(tmp_path))
    maker_bank.bank_rows(store, [("quote_state", s) for s in states], run_id="r1", now=NOW_DT)

    out_json = tmp_path / "summary.json"
    cfg = LiveMakerConfig(aggregate_cap_usd=10.00, per_quote_cap_usd=5.00, quote_liability_usd=5.00)
    summary = run_dry_run(bank_dir=str(tmp_path), balance=250.0, cfg=cfg, out_path=str(out_json))

    assert summary["zero_network"] is True
    assert summary["states_seen"] == 3
    assert summary["would_quote_count"] == 2
    assert summary["would_quote_by_rung"] == {"control": 1, "rung_1c": 1}
    assert summary["skips_by_reason"] == {"AGGREGATE_CAP": 1}
    assert summary["committed_final"] == summary["total_cost_would_commit"] <= 10.00
    assert out_json.exists()


def test_load_live_maker_config_and_balance_parsing():
    cfg = load_live_maker_config(
        {
            "NOVIG_LIVE_MAKER_PER_QUOTE_CAP_USD": "3.5",
            "NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD": "40.0",
            "NOVIG_LIVE_MAKER_RUNG_OFFSETS_C": "1.5,2.5,3.5",
            "NOVIG_LIVE_MAKER_BOXSCORES_CSV": "/tmp/box.csv",
        }
    )
    assert cfg.per_quote_cap_usd == 3.5
    assert cfg.aggregate_cap_usd == 40.0
    assert cfg.rung_offsets_c == (1.5, 2.5, 3.5)
    assert cfg.boxscores_csv == "/tmp/box.csv"

    assert _parse_balance_response({"balance": "123.45"}) == pytest.approx(123.45)
    assert _parse_balance_response({"available": 50.0}) == pytest.approx(50.0)
    assert _parse_balance_response({"cash": "12.34"}) == pytest.approx(12.34)
    assert _parse_balance_response({"balanceCents": 12345}) == pytest.approx(123.45)
    with pytest.raises(RuntimeError):
        _parse_balance_response({})
