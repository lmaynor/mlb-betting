"""Offline unit and integration tests for MAK-02 $21 RFQ fill-test path."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ev.kalshi_rfq_filter import FilterConfig, RFQFilterState, classify_rfq_record
from ev.kalshi_rfq_pricer import InMemoryFairBook, KalshiRFQPricer, LegFairValue, PricerConfig, QuoteDecision
from mlb.execution.kalshi.client import (
    PROD_QUOTES_ARM_ENV,
    PROD_QUOTES_ENV,
    PROD_QUOTES_URL,
    KalshiClient,
    ProductionNotEnabled,
)
from mlb.execution.kalshi.espn_tipoff import (
    EspnGameStatePoller,
    GameState,
    GameStateSnapshot,
    TipoffGate,
    parse_scoreboard,
)
from mlb.execution.kalshi.rfq_client import WS_URLS, KalshiRFQStream, RFQProdQuoteClient
from mlb.execution.kalshi.rfq_fill_test import (
    ENABLE_ENV,
    DecisionLog,
    DryRunClient,
    ExposureBook,
    FileFlagSource,
    FillTestConfig,
    FillTestQuoter,
    FireView,
    GcsFlagSource,
    build_classification_record,
    build_prod_client,
    evaluate_fire,
)
from mlb.execution.kalshi.rfq_maker import RFQConfig, RFQLedger, RFQMaker, RFQRiskGate, RFQTape

NOW = 1_800_000_000.0
GK = "nfl:2026-11-26:CHI@DET"
GK2 = "nfl:2026-11-26:KC@BUF"


@pytest.fixture(scope="module")
def pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


class CountingClient:
    def __init__(self, balance: float = 21.00):
        self.balance = float(balance)
        self.create_calls: list[dict] = []
        self.confirm_calls: list[dict] = []
        self.delete_calls: list[str] = []

    def create_quote(self, rfq_id: str, yes_bid_fp: str, no_bid_fp: str, **kw) -> dict:
        self.create_calls.append({"rfq_id": rfq_id, "yes_bid_fp": yes_bid_fp, "no_bid_fp": no_bid_fp, **kw})
        return {"id": f"q-{len(self.create_calls)}"}

    def confirm_quote(self, quote_id: str, rfq_id: str | None = None) -> dict:
        self.confirm_calls.append({"quote_id": quote_id, "rfq_id": rfq_id})
        return {}

    def delete_quote(self, quote_id: str) -> dict:
        self.delete_calls.append(quote_id)
        return {}

    def get_balance(self) -> dict:
        return {"balance_dollars": f"{self.balance:.2f}"}


class MemoryFlagSource:
    def __init__(self, enabled: bool = True, stopped: bool = False, readable: bool = True):
        self.enabled = enabled
        self.stopped = stopped
        self.readable = readable

    def flags(self) -> tuple[bool, bool, bool]:
        return (self.enabled, self.stopped, self.readable)


class StubPricer:
    """Minimal pricer matching RFQMaker/FillTestQuoter contract."""

    def __init__(self, book: InMemoryFairBook, decisions_by_rfq: dict[str, QuoteDecision] | None = None):
        self.book = book
        self.decisions = decisions_by_rfq or {}
        self.default_decision: QuoteDecision | None = None

    def leg_gate(self, leg, now: float):
        return None if leg is not None else "REJ_UNMAPPED_TICKER"

    def price(self, rfq, now: float) -> QuoteDecision:
        if rfq.rfq_id in self.decisions:
            return self.decisions[rfq.rfq_id]
        assert self.default_decision is not None
        return self.default_decision


def _view(
    rfq_id: str = "rfq-1",
    *,
    primary_class: str = "DENOMINATOR",
    disposition: str = "ADMITTED_FIRE",
    fire_tier: str | None = "A",
    taper_weight: float | None = 1.0,
    flag_names: tuple[str, ...] = (),
    collateral: float | None = 4.00,
    contracts: float | None = 5.0,
    no_bid: float | None = 0.80,
    p_yes_true: float | None = 0.05,
    game_keys: tuple[str, ...] = (GK,),
    ts: float = NOW,
) -> FireView:
    return FireView(
        rfq_id=rfq_id,
        primary_class=primary_class,
        disposition=disposition,
        fire_tier=fire_tier,
        taper_weight=taper_weight,
        flag_names=flag_names,
        collateral=collateral,
        contracts=contracts,
        no_bid=no_bid,
        p_yes_true=p_yes_true,
        game_keys=game_keys,
        ts=ts,
    )


def _snapshot(
    *,
    state: str = "pre",
    start_offset_sec: float = 3600.0,
    fetched_at: float = NOW,
    keys: tuple[str, ...] = (GK,),
) -> GameStateSnapshot:
    games = {
        k: GameState(
            game_key=k,
            event_id=f"ev-{i}",
            start_ts=NOW + start_offset_sec,
            state=state,
            detail="STATUS_SCHEDULED",
            home="DET",
            away="CHI",
        )
        for i, k in enumerate(keys)
    }
    return GameStateSnapshot(games=games, fetched_at=fetched_at, source="test")


def _combo_msg(
    rfq_id: str = "r1",
    *,
    tickers: tuple[str, ...] = ("KXNFLRECYDS-26NOV26CHIDET-T1", "KXNFLRECYDS-26NOV26CHIDET-T2"),
    target_cost_dollars: str = "25.00",
    contracts_fp: str = "8.00",
    creator_id: str = "creator-1",
) -> dict:
    return {
        "id": rfq_id,
        "market_ticker": f"KXMVE-{rfq_id}",
        "target_cost_dollars": target_cost_dollars,
        "contracts_fp": contracts_fp,
        "creator_id": creator_id,
        "mve_collection_ticker": "KXMVECOLL",
        "mve_selected_legs": [
            {"event_ticker": "EV1", "market_ticker": t, "side": "yes"} for t in tickers
        ],
    }


def _canned_decision(
    rfq_id: str = "r1",
    *,
    action: str = "QUOTE",
    reason: str = "PLAN",
    no_bid: float = 0.60,
    p_yes_true: float = 0.20,
    contracts: float = 7.5,
    collateral: float = 4.50,
    tickers: tuple[str, ...] = ("KXNFLRECYDS-26NOV26CHIDET-T1", "KXNFLRECYDS-26NOV26CHIDET-T2"),
    game_key: str = GK,
    age_sec: float = 100.0,
    n_unpriced: int = 0,
) -> QuoteDecision:
    legs = tuple(
        {
            "ticker": t,
            "game_key": game_key,
            "player_norm": f"player_{i}",
            "side": "yes",
            "p_leg": 0.5,
            "p_true_yes": 0.5,
            "n_books": 5,
            "sport": "nfl",
            "market_class": "player_prop",
            "stat": "rec_yds",
            "strike": 50.5,
            "team": "CHI",
            "disp": 0.01,
            "p_max": 0.52,
            "p_cons_yes": 0.5,
            "p_model_yes": None,
            "pooled": False,
            "is_hot_scratch": False,
            "protected": False,
            "fv_updated_ts": NOW - age_sec,
        }
        for i, t in enumerate(tickers)
    )
    legs_status = [{"ticker": t, "status": "OK"} for t in tickers] + [
        {"ticker": f"UNPRICED-{i}", "status": "REJ_UNMAPPED_TICKER"} for i in range(n_unpriced)
    ]
    return QuoteDecision(
        action=action,
        reason=reason,
        rfq_id=rfq_id,
        kind="combo",
        yes_bid=0.0,
        no_bid=no_bid,
        p_yes_true=p_yes_true,
        p_yes_cons=p_yes_true + 0.02,
        edge=0.04,
        ev_per_contract=0.20,
        roi_on_collateral=0.30,
        contracts=contracts,
        collateral=collateral,
        legs=legs,
        notes={"n_unpriced": n_unpriced, "legs_status": legs_status},
    )


def _make_quoter(
    tmp_path: Path,
    *,
    client=None,
    flag_source=None,
    balance_fn=None,
    snapshot_fn=None,
    pricer=None,
    decisions: dict[str, QuoteDecision] | None = None,
    env=None,
    cfg: FillTestConfig = FillTestConfig(),
    clock=lambda: NOW,
) -> tuple[FillTestQuoter, CountingClient, Path]:
    client = client or CountingClient()
    flag_source = flag_source or MemoryFlagSource(enabled=True, stopped=False, readable=True)
    balance_fn = balance_fn or (lambda: 21.00)
    snapshot_fn = snapshot_fn or (lambda: _snapshot())
    book = InMemoryFairBook(clock=clock)
    book.replace_all([
        LegFairValue(
            ticker="KXNFLRECYDS-26NOV26CHIDET-T1",
            sport="nfl",
            game_key=GK,
            market_class="player_prop",
            player_norm="player_0",
            stat="rec_yds",
            strike=50.5,
            p_cons_yes=0.50,
            n_books=5,
            disp=0.01,
            p_model_yes=None,
            pooled=False,
            updated_ts=NOW - 100.0,
        ),
        LegFairValue(
            ticker="KXNFLRECYDS-26NOV26CHIDET-T2",
            sport="nfl",
            game_key=GK,
            market_class="player_prop",
            player_norm="player_1",
            stat="rec_yds",
            strike=50.5,
            p_cons_yes=0.50,
            n_books=5,
            disp=0.01,
            p_model_yes=None,
            pooled=False,
            updated_ts=NOW - 100.0,
        ),
    ])
    if pricer is None:
        pricer = StubPricer(book, decisions)
        pricer.default_decision = _canned_decision()
    ledger = RFQLedger(":memory:")
    risk_gate = RFQRiskGate(RFQConfig(), ledger, "live")
    maker = RFQMaker(pricer, risk_gate, ledger, client, mode="live", clock=clock, env={"KALSHI_RFQ_LIVE": "1"})
    log_path = tmp_path / "decisions.jsonl"
    decision_log = DecisionLog(path=log_path, clock=clock)
    quoter = FillTestQuoter(
        pricer=pricer,
        client=client,
        ledger=ledger,
        maker=maker,
        gate=TipoffGate(buffer_sec=cfg.tipoff_buffer_sec, max_snapshot_age_sec=cfg.snapshot_max_age_sec),
        snapshot_provider=snapshot_fn,
        flag_source=flag_source,
        balance_provider=balance_fn,
        decision_log=decision_log,
        cfg=cfg,
        clock=clock,
        env={ENABLE_ENV: "1"} if env is None else env,
    )
    quoter.start()
    return quoter, client, log_path


# ----------------------------------------------------------------------- 1. per-quote cap
def test_per_quote_cap():
    cfg = FillTestConfig()
    # Large balance so Kelly ceiling never binds before the $5.00 dollar cap.
    ev_over = evaluate_fire(
        _view(collateral=5.01, contracts=5.01 / 0.80),
        cfg=cfg,
        balance=500.0,
        committed=0.0,
        gate_ok=True,
        gate_reason="",
    )
    assert ev_over.outcome == "SKIPPED" and ev_over.reason == "PER_QUOTE_CAP"

    ev_exact = evaluate_fire(
        _view(collateral=5.00, contracts=5.00 / 0.80),
        cfg=cfg,
        balance=500.0,
        committed=0.0,
        gate_ok=True,
        gate_reason="",
    )
    assert ev_exact.outcome == "QUOTED" and ev_exact.committed_after == pytest.approx(5.00)

    ev_under = evaluate_fire(
        _view(collateral=4.00, contracts=4.00 / 0.80),
        cfg=cfg,
        balance=500.0,
        committed=0.0,
        gate_ok=True,
        gate_reason="",
    )
    assert ev_under.outcome == "QUOTED" and ev_under.committed_after == pytest.approx(4.00)


# ----------------------------------------------------------------------- 2. aggregate cap + balance drop
def test_aggregate_cap_and_mid_session_balance_drop(tmp_path):
    cfg = FillTestConfig()
    # 5, 5, 5, 5 quoted when Kelly ceiling allows it: use p_yes_true=0.02, no_bid=0.60 -> kf = 0.95 -> ceiling ~ 21*0.25*0.95 = 4.9875
    # With no_bid=0.50, p_yes_true=0.01 -> kf = 0.49/0.50 = 0.98 -> ceiling = 21*0.25*0.98 = 5.145 >= 5.00
    committed = 0.0
    for i in range(4):
        ev = evaluate_fire(
            _view(f"r-{i}", collateral=5.00, contracts=10.0, no_bid=0.50, p_yes_true=0.01),
            cfg=cfg,
            balance=21.0,
            committed=committed,
            gate_ok=True,
            gate_reason="",
        )
        assert ev.outcome == "QUOTED"
        committed = ev.committed_after
    assert committed == pytest.approx(20.00)

    ev_fifth = evaluate_fire(
        _view("r-5", collateral=5.00, contracts=10.0, no_bid=0.50, p_yes_true=0.01),
        cfg=cfg,
        balance=21.0,
        committed=committed,
        gate_ok=True,
        gate_reason="",
    )
    assert ev_fifth.outcome == "SKIPPED" and ev_fifth.reason == "AGGREGATE_CAP"

    # Mid-session balance drop through FillTestQuoter
    bal = {"v": 21.00}
    decisions = {
        f"r{i}": _canned_decision(f"r{i}", no_bid=0.50, p_yes_true=0.01, contracts=10.0, collateral=5.00)
        for i in range(1, 5)
    }
    book = InMemoryFairBook(clock=lambda: NOW)
    pricer = StubPricer(book, decisions)
    quoter, client, _ = _make_quoter(tmp_path, balance_fn=lambda: bal["v"], pricer=pricer)

    ev1 = quoter.on_rfq_created(_combo_msg("r1"))
    assert ev1.outcome == "QUOTED"
    assert quoter.exposure.committed() == pytest.approx(5.00)
    assert len(client.create_calls) == 1

    # Simulate fill + balance drop to 9.00 -> headroom = 9.00 - 5.00 = 4.00
    bal["v"] = 9.00
    res = quoter.on_quote_executed({"quote_id": "q-1"})
    assert res == "FILLED"
    assert quoter.balance == pytest.approx(9.00)
    assert quoter.exposure.headroom(quoter.balance, cfg) == pytest.approx(4.00)

    # A 5.00 fire that fit before now hits KELLY_CEILING or AGGREGATE_CAP; test explicit AGGREGATE_CAP via evaluate_fire
    ev_after = evaluate_fire(
        _view("r-after", collateral=4.50, contracts=9.0, no_bid=0.50, p_yes_true=0.01),
        cfg=cfg,
        balance=quoter.balance,
        committed=quoter.exposure.committed(),
        gate_ok=True,
        gate_reason="",
    )
    # balance=9.0, ceiling = 9.0*0.25*0.98 = 2.205 -> Kelly ceiling binds first at low balance unless balance*0.25*kf > 4.50
    # Wait: test headroom directly with a smaller committed=5.00 on a 4.50 fire where balance=9.00 -> if f0=1.0 or test evaluate_fire with exposure.mark_filled:
    assert ev_after.headroom == pytest.approx(4.00)

    book_exp = ExposureBook()
    book_exp.add_open("q-1", 5.00)
    book_exp.mark_filled("q-1")
    assert book_exp.headroom(9.00, cfg) == pytest.approx(4.00)
    ev_agg = evaluate_fire(
        _view("r-drop", collateral=4.50, contracts=9.0, no_bid=0.50, p_yes_true=0.01),
        cfg=FillTestConfig(f0=1.0),
        balance=9.00,
        committed=book_exp.committed(),
        gate_ok=True,
        gate_reason="",
    )
    assert ev_agg.outcome == "SKIPPED" and ev_agg.reason == "AGGREGATE_CAP"


# ----------------------------------------------------------------------- 3. admission exclusions
def test_admission_exclusions_unit_and_quoter(tmp_path):
    cfg = FillTestConfig()
    cases = [
        (_view(primary_class="EX_PREVIEW_10", disposition="EX_PREVIEW_10"), "NOT_DENOMINATOR"),
        (_view(disposition="FIRE_BOUNDED_OVER_CAP"), "DISPOSITION_FIRE_BOUNDED_OVER_CAP"),
        (_view(disposition="FIRE_STALE_ZERO_SIZE"), "DISPOSITION_FIRE_STALE_ZERO_SIZE"),
        (_view(flag_names=("NON_SPORTS",)), "NON_SPORTS"),
        (_view(disposition="DENOM_NOT_FIRED"), "DISPOSITION_DENOM_NOT_FIRED"),
    ]
    for v, expected_reason in cases:
        ev = evaluate_fire(v, cfg=cfg, balance=100.0, committed=0.0, gate_ok=True, gate_reason="")
        assert ev.outcome == "SKIPPED" and ev.reason == expected_reason

    # Through FillTestQuoter (at least two: EX_PREVIEW_10 and NON_SPORTS, plus FIRE_BOUNDED_OVER_CAP)
    decisions = {
        "r-prev": _canned_decision("r-prev", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00),
        "r-crypto": _canned_decision(
            "r-crypto",
            no_bid=0.50,
            p_yes_true=0.01,
            contracts=8.0,
            collateral=4.00,
            tickers=("KXBTCD-1", "KXBTCD-2"),
        ),
        "r-overcap": _canned_decision(
            "r-overcap",
            no_bid=0.50,
            p_yes_true=0.01,
            contracts=8.0,
            collateral=4.00,
            n_unpriced=4,
        ),
    }
    book = InMemoryFairBook(clock=lambda: NOW)
    pricer = StubPricer(book, decisions)
    quoter, client, _ = _make_quoter(tmp_path, pricer=pricer, balance_fn=lambda: 100.0)

    ev_prev = quoter.on_rfq_created(_combo_msg("r-prev", target_cost_dollars="10.00"))
    assert ev_prev.outcome == "SKIPPED" and ev_prev.reason == "NOT_DENOMINATOR"

    ev_crypto = quoter.on_rfq_created(_combo_msg("r-crypto", tickers=("KXBTCD-1", "KXBTCD-2")))
    assert ev_crypto.outcome == "SKIPPED" and ev_crypto.reason == "NON_SPORTS"

    ev_cap = quoter.on_rfq_created(_combo_msg("r-overcap"))
    assert ev_cap.outcome == "SKIPPED" and ev_cap.reason == "DISPOSITION_FIRE_BOUNDED_OVER_CAP"
    assert len(client.create_calls) == 0


# ----------------------------------------------------------------------- 4. taper ceiling
def test_taper_ceiling():
    cfg = FillTestConfig()
    # balance=100, f0=0.25, tier="A", no_bid=0.60, p_yes_true=0.20 (q=0.80) -> kf = 0.50
    # taper=1.0 -> ceiling=12.50; taper=0.25 -> ceiling=3.125; collateral=4.50 sits cleanly between them.
    base = dict(collateral=4.50, contracts=7.5, no_bid=0.60, p_yes_true=0.20, fire_tier="A")

    ev_full = evaluate_fire(_view(**base, taper_weight=1.0), cfg=cfg, balance=100.0, committed=0.0, gate_ok=True, gate_reason="")
    assert ev_full.outcome == "QUOTED"
    assert ev_full.kelly_ceiling == pytest.approx(12.50)

    ev_tapered = evaluate_fire(_view(**base, taper_weight=0.25), cfg=cfg, balance=100.0, committed=0.0, gate_ok=True, gate_reason="")
    assert ev_tapered.outcome == "SKIPPED" and ev_tapered.reason == "KELLY_CEILING"
    assert ev_tapered.kelly_ceiling == pytest.approx(3.125)

    ev_none = evaluate_fire(_view(**base, taper_weight=None), cfg=cfg, balance=100.0, committed=0.0, gate_ok=True, gate_reason="")
    assert ev_none.outcome == "SKIPPED" and ev_none.reason == "TAPER_UNKNOWN"

    ev_zero = evaluate_fire(_view(**base, taper_weight=0.0), cfg=cfg, balance=100.0, committed=0.0, gate_ok=True, gate_reason="")
    assert ev_zero.outcome == "SKIPPED" and ev_zero.reason == "STALE_ZERO"


# ----------------------------------------------------------------------- 5. tip-off gate
def test_tipoff_gate_unit_and_quoter(tmp_path):
    gate = TipoffGate(buffer_sec=600.0, max_snapshot_age_sec=180.0)

    assert gate.check((GK,), NOW, _snapshot(state="in")) == (False, "BLOCKED_LIVE_GAME")
    assert gate.check((GK,), NOW, _snapshot(state="post")) == (False, "GATE_STARTED")
    assert gate.check((GK,), NOW, _snapshot(state="pre", start_offset_sec=599.0)) == (False, "GATE_INSIDE_BUFFER")
    assert gate.check(("nfl:2026-11-26:NOPE@NONE",), NOW, _snapshot(state="pre")) == (False, "GATE_UNKNOWN_GAME")
    assert gate.check((GK,), NOW, _snapshot(state="pre", fetched_at=NOW - 181.0)) == (False, "GATE_SNAPSHOT_STALE")
    assert gate.check((GK,), NOW, None) == (False, "GATE_SNAPSHOT_STALE")
    assert gate.check((GK,), NOW, _snapshot(state="pre", start_offset_sec=601.0)) == (True, "")

    # Quoter-path check
    pricer = StubPricer(InMemoryFairBook(), {"r-buf": _canned_decision("r-buf", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00)})
    quoter, client, _ = _make_quoter(
        tmp_path,
        pricer=pricer,
        balance_fn=lambda: 100.0,
        snapshot_fn=lambda: _snapshot(state="pre", start_offset_sec=599.0),
    )
    ev = quoter.on_rfq_created(_combo_msg("r-buf"))
    assert ev.outcome == "SKIPPED" and ev.reason == "GATE_INSIDE_BUFFER"
    assert len(client.create_calls) == 0


# ------------------------------------------------- 5b. live-game hard blocker (Luc, Nov 26)
def test_blocked_live_game(tmp_path):
    gate = TipoffGate(buffer_sec=600.0, max_snapshot_age_sec=180.0)
    # in play -> BLOCKED_LIVE_GAME, distinct from final -> GATE_STARTED
    assert gate.check((GK,), NOW, _snapshot(state="in")) == (False, "BLOCKED_LIVE_GAME")
    # the blocker wins over an unresolvable second leg so the class stays countable
    mixed = _snapshot(state="in")
    assert gate.check((GK, "nfl:2026-11-26:NOPE@NONE"), NOW, mixed) == (False, "BLOCKED_LIVE_GAME")

    # quoter path: an admitted fire with an in-play leg is skipped under the blocker
    # reason and no quote attempt is made, even with caps wide open
    pricer = StubPricer(InMemoryFairBook(), {"r-live": _canned_decision("r-live", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00)})
    quoter, client, _ = _make_quoter(
        tmp_path,
        pricer=pricer,
        balance_fn=lambda: 100.0,
        snapshot_fn=lambda: _snapshot(state="in"),
    )
    ev = quoter.on_rfq_created(_combo_msg("r-live"))
    assert ev.outcome == "SKIPPED" and ev.reason == "BLOCKED_LIVE_GAME"
    assert len(client.create_calls) == 0


# ----------------------------------------------------------------------- 6. kill switch
def test_kill_switch_file_flag_source(tmp_path):
    flag_dir = tmp_path / "flags"
    flag_dir.mkdir()
    (flag_dir / "ENABLE").write_text("1", encoding="ascii")
    fs = FileFlagSource(flag_dir)

    pricer = StubPricer(InMemoryFairBook(), {
        "r1": _canned_decision("r1", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00),
        "r2": _canned_decision("r2", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00),
        "r3": _canned_decision("r3", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00),
    })
    quoter, client, _ = _make_quoter(tmp_path, flag_source=fs, pricer=pricer, balance_fn=lambda: 100.0)

    ev1 = quoter.on_rfq_created(_combo_msg("r1"))
    assert ev1.outcome == "QUOTED"
    assert len(client.create_calls) == 1

    # Trip STOP
    (flag_dir / "STOP").write_text("1", encoding="ascii")
    ev2 = quoter.on_rfq_created(_combo_msg("r2"))
    assert ev2.outcome == "SKIPPED" and ev2.reason == "STOPPED"
    assert len(client.create_calls) == 1

    # Confirm is withheld when STOPPED
    assert quoter.on_quote_accepted({"quote_id": "q-1", "rfq_id": "r1"}) == "CONFIRM_WITHHELD_STOPPED"
    assert len(client.confirm_calls) == 0

    # Clear STOP and remove ENABLE -> DISABLED
    (flag_dir / "STOP").unlink()
    (flag_dir / "ENABLE").unlink()
    ev3 = quoter.on_rfq_created(_combo_msg("r3"))
    assert ev3.outcome == "SKIPPED" and ev3.reason == "DISABLED"
    assert len(client.create_calls) == 1


# ----------------------------------------------------------------------- 7. enablement-off boot default & prod guards
def test_enablement_off_boot_default_and_prod_guards(tmp_path, pem, monkeypatch):
    empty_flag_dir = tmp_path / "empty_flags"
    empty_flag_dir.mkdir()
    fs = FileFlagSource(empty_flag_dir)

    pricer = StubPricer(InMemoryFairBook(), {
        f"r{i}": _canned_decision(f"r{i}", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00)
        for i in range(1, 4)
    })
    quoter, client, log_path = _make_quoter(tmp_path, flag_source=fs, pricer=pricer, env={})
    for i in range(1, 4):
        ev = quoter.on_rfq_created(_combo_msg(f"r{i}"))
        assert ev.outcome == "SKIPPED" and ev.reason == "DISABLED"
    assert len(client.create_calls) == 0

    rows = [json.loads(line) for line in log_path.read_text(encoding="ascii").splitlines()]
    skipped = [r for r in rows if r.get("outcome") == "SKIPPED" and r.get("reason") == "DISABLED"]
    assert len(skipped) == 3

    # Production guards
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    monkeypatch.delenv(PROD_QUOTES_ARM_ENV, raising=False)
    with pytest.raises(ProductionNotEnabled):
        build_prod_client("kid", pem, env={})
    with pytest.raises(ProductionNotEnabled, match=PROD_QUOTES_ARM_ENV):
        KalshiClient(PROD_QUOTES_ENV, "kid", pem)
    with pytest.raises(ProductionNotEnabled):
        RFQProdQuoteClient("kid", pem)

    # Armed env constructs production quotes client + stream cleanly (no network calls made)
    monkeypatch.setenv(PROD_QUOTES_ARM_ENV, "1")
    monkeypatch.setenv(ENABLE_ENV, "1")
    prod_client = build_prod_client("kid", pem)
    assert isinstance(prod_client, RFQProdQuoteClient)
    assert prod_client.env == PROD_QUOTES_ENV
    assert prod_client.base_url == PROD_QUOTES_URL
    assert WS_URLS[PROD_QUOTES_ENV].startswith("wss://external-api-ws.kalshi.com")
    stream = KalshiRFQStream(PROD_QUOTES_ENV, "kid", prod_client._key, lambda *a: None)
    assert stream.url == WS_URLS[PROD_QUOTES_ENV]

    # Missing flag directory fails closed as unreadable/stopped
    missing_fs = FileFlagSource(tmp_path / "does_not_exist")
    assert missing_fs.flags() == (False, True, False)
    gcs_err = GcsFlagSource(exists_fn=lambda k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gcs_err.flags() == (False, True, False)


# ----------------------------------------------------------------------- 8. ESPN parsing + poller
def test_espn_parsing_and_poller():
    payload = {
        "events": [
            {
                "id": "401",
                "date": "2026-11-26T18:00:00Z",
                "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "DET"}},
                            {"homeAway": "away", "team": {"abbreviation": "CHI"}},
                        ]
                    }
                ],
            },
            {
                "id": "402",
                "date": "2026-11-27T01:20:00Z",  # 2026-11-26 20:20 EST -> ET date 2026-11-26
                "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "BUF"}},
                            {"homeAway": "away", "team": {"abbreviation": "KC"}},
                        ]
                    }
                ],
            },
            {
                "id": "403",
                "date": "2026-11-26T21:30:00Z",
                "status": {"type": {"state": "in", "name": "STATUS_IN_PROGRESS"}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                            {"homeAway": "away", "team": {"abbreviation": "DAL"}},
                        ]
                    }
                ],
            },
        ]
    }
    games = parse_scoreboard(payload, "nfl")
    assert "nfl:2026-11-26:CHI@DET" in games
    assert "nfl:2026-11-26:KC@BUF" in games
    assert "nfl:2026-11-27:KC@BUF" in games
    assert "nfl:2026-11-26:DAL@WSH" in games
    assert "nfl:2026-11-26:DAL@WAS" in games
    expected_ts = datetime(2026, 11, 27, 1, 20, 0, tzinfo=timezone.utc).timestamp()
    assert games["nfl:2026-11-26:KC@BUF"].start_ts == pytest.approx(expected_ts)
    assert games["nfl:2026-11-26:KC@BUF"].game_key == "nfl:2026-11-26:KC@BUF"

    # Poller keeps previous snapshot on failure
    calls = {"fail": False}

    def fake_fetcher(url: str) -> dict:
        if calls["fail"]:
            raise RuntimeError("espn down")
        return payload

    poller = EspnGameStatePoller(fetcher=fake_fetcher, leagues=("nfl",), clock=lambda: NOW)
    snap1 = poller.poll_once(NOW)
    assert snap1.fetched_at == NOW and "nfl:2026-11-26:CHI@DET" in snap1.games
    calls["fail"] = True
    snap2 = poller.poll_once(NOW + 60.0)
    assert snap2 is snap1

    # Fresh poller failure returns empty snapshot with fetched_at=0.0
    poller_bad = EspnGameStatePoller(fetcher=fake_fetcher, leagues=("nfl",), clock=lambda: NOW)
    snap_bad = poller_bad.poll_once(NOW)
    assert snap_bad.fetched_at == 0.0 and snap_bad.games == {}


# ----------------------------------------------------------------------- 9. decision log
def test_decision_log_jsonl_flow(tmp_path):
    decisions = {
        "r-q": _canned_decision("r-q", no_bid=0.50, p_yes_true=0.01, contracts=8.0, collateral=4.00),
        "r-skip": _canned_decision("r-skip", no_bid=0.50, p_yes_true=0.01, contracts=12.0, collateral=6.00),
    }
    quoter, _, log_path = _make_quoter(tmp_path, decisions=decisions, balance_fn=lambda: 100.0)

    ev_q = quoter.on_rfq_created(_combo_msg("r-q"))
    assert ev_q.outcome == "QUOTED"
    ev_s = quoter.on_rfq_created(_combo_msg("r-skip"))
    assert ev_s.outcome == "SKIPPED"
    assert quoter.on_quote_accepted({"quote_id": "q-1", "rfq_id": "r-q"}) == "CONFIRMED"
    assert quoter.on_quote_executed({"quote_id": "q-1", "rfq_id": "r-q"}) == "FILLED"
    quoter.settle("q-1", won=True)

    rows = [json.loads(line) for line in log_path.read_text(encoding="ascii").splitlines()]
    assert len(rows) >= 4
    for row in rows:
        assert row["kind"] == "fill_test_decision"
        assert "ts" in row
        assert "rfq_id" in row
        assert "outcome" in row
        assert "reason" in row

    outcomes = [r["outcome"] for r in rows]
    assert "QUOTED" in outcomes
    assert "SKIPPED" in outcomes
    assert "SETTLED" in outcomes

    # DryRunClient smoke check
    dry = DryRunClient()
    assert dry.create_quote("r1", "0", "0.5000") == {"id": "dryrun-1"}
    assert dry.get_balance() == {"balance_dollars": "21.00"}


# ----------------------------------------------------------------------- 10. classification parity
def test_classification_parity_against_rfq_tape(tmp_path):
    t1, t2 = "KXNFLRECYDS-26NOV26CHIDET-T1", "KXNFLRECYDS-26NOV26CHIDET-T2"
    book = InMemoryFairBook(clock=lambda: NOW)
    book.replace_all([
        LegFairValue(
            ticker=t1,
            sport="nfl",
            game_key=GK,
            market_class="player_prop",
            player_norm="player_a",
            stat="rec_yds",
            strike=50.5,
            p_cons_yes=0.25,
            n_books=5,
            disp=0.01,
            p_model_yes=None,
            pooled=False,
            updated_ts=NOW - 120.0,
            team="CHI",
        ),
        LegFairValue(
            ticker=t2,
            sport="nfl",
            game_key=GK,
            market_class="player_prop",
            player_norm="player_b",
            stat="receptions",
            strike=4.5,
            p_cons_yes=0.30,
            n_books=5,
            disp=0.01,
            p_model_yes=None,
            pooled=False,
            updated_ts=NOW - 1200.0,
            team="DET",
        ),
    ])
    pricer = KalshiRFQPricer(book, PricerConfig(allow_same_game_combos=True, max_unpriced_legs=3))

    msgs = [
        _combo_msg("r-tier-a", tickers=(t1, t2), target_cost_dollars="25.00"),
        _combo_msg("r-tier-b", tickers=(t1, t2, "KXNFLPASSYDS-26NOV26CHIDET-U1"), target_cost_dollars="25.00"),
        _combo_msg("r-preview", tickers=(t1, t2), target_cost_dollars="10.00"),
    ]

    tape = RFQTape(str(tmp_path / "tape"), clock=lambda: NOW)
    filter_state = RFQFilterState()
    filter_cfg = FilterConfig()

    for msg in msgs:
        decision = pricer.price(msg, NOW)
        tape.record_rfq(msg, decision)
        rec = build_classification_record(msg, decision, NOW)
        stamp = classify_rfq_record(rec, filter_state, filter_cfg)

        tape_lines = [json.loads(line) for line in Path(tape.path()).read_text(encoding="ascii").splitlines()]
        tape_rec = tape_lines[-1]
        for field in ("primary_class", "disposition", "fire_tier", "taper_weight"):
            assert stamp[field] == tape_rec[field], f"mismatch on {field} for {msg['id']}"


def test_runner_dry_run_and_live_refusal(tmp_path, monkeypatch):
    from scripts.kalshi_rfq_fill_test import main as runner_main

    tape_dir = tmp_path / "tape"
    tape_dir.mkdir()
    espn_path = tmp_path / "espn.json"
    out_path = tmp_path / "summary.json"

    start_future = "2026-11-26T22:00:00Z"
    start_ts_future = datetime(2026, 11, 26, 22, 0, 0, tzinfo=timezone.utc).timestamp()
    espn_path.write_text(json.dumps({
        "leagues": [{"slug": "nfl"}],
        "events": [{
            "id": "401",
            "date": start_future,
            "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "DET"}},
                {"homeAway": "away", "team": {"abbreviation": "CHI"}},
            ]}],
        }],
    }), encoding="utf-8")

    t0 = start_ts_future - 3600.0
    recs = [
        # Pre-segment record (ignored by --from-ts)
        {"kind": "rfq", "ts": t0 - 10.0, "rfq_id": "old", "primary_class": "DENOMINATOR", "disposition": "ADMITTED_FIRE"},
        # 4 admitted fires of $5.00 each + 1 of $1.00 = $21.00 cap reached
        *[
            {
                "kind": "rfq", "ts": t0 + i, "rfq_id": f"admit-{i}", "primary_class": "DENOMINATOR",
                "disposition": "ADMITTED_FIRE", "fire_tier": "A", "taper_weight": 1.0, "flag_names": [],
                "no_bid": 0.50, "p_yes_true": 0.01, "contracts_fp_num": 10.0,
                "legs": [{"game_key": GK}],
            }
            for i in range(4)
        ],
        {
            "kind": "rfq", "ts": t0 + 10, "rfq_id": "admit-cap", "primary_class": "DENOMINATOR",
            "disposition": "ADMITTED_FIRE", "fire_tier": "A", "taper_weight": 1.0, "flag_names": [],
            "no_bid": 0.50, "p_yes_true": 0.01, "contracts_fp_num": 2.0,
            "legs": [{"game_key": GK}],
        },
        # Next admitted fire hits AGGREGATE_CAP
        {
            "kind": "rfq", "ts": t0 + 11, "rfq_id": "admit-over", "primary_class": "DENOMINATOR",
            "disposition": "ADMITTED_FIRE", "fire_tier": "A", "taper_weight": 1.0, "flag_names": [],
            "no_bid": 0.50, "p_yes_true": 0.01, "contracts_fp_num": 6.0,
            "legs": [{"game_key": GK}],
        },
        # Non-denominator preview_10
        {
            "kind": "rfq", "ts": t0 + 12, "rfq_id": "prev-1", "primary_class": "EX_PREVIEW_10",
            "disposition": "EX_PREVIEW_10", "preview_10": True, "flag_names": ["PREVIEW_10"],
            "no_bid": 0.50, "p_yes_true": 0.01, "contracts_fp_num": 4.0,
            "legs": [{"game_key": GK}],
        },
        # Tipoff-gated admitted fire (after game start)
        {
            "kind": "rfq", "ts": start_ts_future + 60.0, "rfq_id": "late-1", "primary_class": "DENOMINATOR",
            "disposition": "ADMITTED_FIRE", "fire_tier": "A", "taper_weight": 1.0, "flag_names": [],
            "no_bid": 0.50, "p_yes_true": 0.01, "contracts_fp_num": 2.0,
            "legs": [{"game_key": GK}],
        },
    ]
    (tape_dir / "chunk_0001.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    rc = runner_main([
        "dry-run", "--tape-dir", str(tape_dir), "--from-ts", str(t0),
        "--balance", "21.00", "--espn-json", str(espn_path), "--out", str(out_path),
    ])
    assert rc == 0
    summary = json.loads(out_path.read_text(encoding="utf-8"))
    assert summary["quoted_count"] == 5
    assert summary["total_collateral_quoted"] == pytest.approx(21.00)
    assert summary["cap_reached_at"]["rfq_id"] == "admit-cap"
    assert summary["zero_quote_proofs"]["non_denominator_quoted"] == 0
    assert summary["zero_quote_proofs"]["preview_quoted"] == 0
    assert summary["zero_quote_proofs"]["tipoff_gated_admitted_fires"] == 1
    assert summary["zero_quote_proofs"]["tipoff_gated_quoted"] == 0

    # Live subcommand refuses loudly with exit code 2 when flags/env are absent
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    monkeypatch.delenv("KALSHI_RFQ_LIVE", raising=False)
    assert runner_main(["live"]) == 2


def test_decision_log_storage_flush_retry(tmp_path):
    attempts = []

    def flaky_writer(data: bytes, key: str):
        attempts.append(key)
        if len(attempts) == 1:
            raise OSError("transient")

    dlog = DecisionLog(storage_prefix="Kalshi/fill_test", writer=flaky_writer, clock=lambda: NOW)
    dlog.record({"rfq_id": "r1", "outcome": "QUOTED", "reason": ""})
    assert dlog.flush() == 0
    assert dlog.flush() == 1
    assert len(attempts) == 2 and attempts[0].startswith("Kalshi/fill_test/")

