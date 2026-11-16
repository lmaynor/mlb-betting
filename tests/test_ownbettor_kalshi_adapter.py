"""ev.ownbettor Kalshi adapter: paper fills against recorded books; the live gauntlet."""
import pytest

from mlb.execution.kalshi.book import Book

from ev.ownbettor.config import BettorConfig
from ev.ownbettor.keys import Credentials
from ev.ownbettor.venues.base import BettorIntent
from ev.ownbettor.venues import kalshi as kalshi_mod
from ev.ownbettor.venues.kalshi import (KalshiLiveAdapter, KalshiPaperAdapter,
                                        LiveNotProvisioned, LiveRefused, StaticBookSource)

TICKER = "KXTEST"


def book() -> Book:
    # YES ask 0.50 x100 (mirror of a 0.50 NO bid); NO ask 0.40 x50 (mirror of 0.60 YES bid)
    return Book(TICKER, yes_bids=[(0.60, 50.0)], no_bids=[(0.50, 100.0)], fetched_at=0.0)


def intent(**kw) -> BettorIntent:
    base = dict(intent_id="ownbet-x", signal_id="s1", ticker=TICKER, side="yes",
                contracts=10, limit_price=0.51, planned_stake_usd=5.0)
    base.update(kw)
    return BettorIntent(**base)


def paper_adapter() -> KalshiPaperAdapter:
    return KalshiPaperAdapter(StaticBookSource({TICKER: book()}), BettorConfig())


def test_paper_fill_at_the_ask_with_fee():
    ack = paper_adapter().submit(intent())
    assert ack.status == "FILLED" and ack.simulated is True
    assert ack.fill_count == 10
    assert ack.filled_cost == pytest.approx(10 * 0.50 + 0.175)   # taker fee 0.07*C*P*(1-P)


def test_paper_no_fill_when_the_limit_is_below_the_ask():
    ack = paper_adapter().submit(intent(limit_price=0.45))
    assert ack.status == "NO_FILL" and ack.fill_count == 0


def test_paper_best_ask_reads_the_mirrored_ladder():
    a = paper_adapter()
    assert a.best_ask(TICKER, "yes") == 0.50
    assert a.best_ask(TICKER, "no") == 0.40
    assert a.best_ask("KXUNKNOWN", "yes") is None


def test_paper_partial_fill_when_the_book_is_thin():
    ack = paper_adapter().submit(intent(contracts=150, limit_price=0.51))
    assert ack.status == "PARTIAL" and ack.fill_count == 100


# ------------------------------------------------------------------ the live gauntlet
def test_live_refused_when_the_switch_is_off():
    with pytest.raises(LiveRefused):
        KalshiLiveAdapter(BettorConfig())                        # live_armed=False ships


def test_live_refused_on_demo_env():
    cfg = BettorConfig(live_armed=True, kalshi_env="demo")
    with pytest.raises(LiveRefused):
        KalshiLiveAdapter(cfg)


def test_live_not_provisioned_without_credentials(monkeypatch):
    monkeypatch.setattr(kalshi_mod, "load_kalshi_credentials", lambda env=None: None)
    cfg = BettorConfig(live_armed=True, kalshi_env="production")
    with pytest.raises(LiveNotProvisioned):
        KalshiLiveAdapter(cfg)


def test_live_not_provisioned_while_the_client_refuses_production(monkeypatch):
    fake = Credentials("fake-key-id", b"not-a-real-pem", "env")
    monkeypatch.setattr(kalshi_mod, "load_kalshi_credentials", lambda env=None: fake)
    cfg = BettorConfig(live_armed=True, kalshi_env="production")
    with pytest.raises(LiveNotProvisioned):   # mlb.execution.kalshi.client refuses production
        KalshiLiveAdapter(cfg)
