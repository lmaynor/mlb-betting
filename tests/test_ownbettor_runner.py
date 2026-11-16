"""ev.ownbettor runner: the full path, end to end in dry-run, audit trail complete.

Signals are AUT-01 shadow-ledger rows (status FILLED = upstream gate-passed).  The
venue is the paper adapter against a recorded book; the resolver is a recorded table.
"""
import pytest

from mlb.execution.kalshi.book import Book

from ev.ownbettor.audit import AuditLog
from ev.ownbettor.config import BettorConfig
from ev.ownbettor.resolver import RecordedTableResolver
from ev.ownbettor.runner import Bettor
from ev.ownbettor.signals import from_autopilot_row, signals_from_rows
from ev.ownbettor.venues.kalshi import KalshiPaperAdapter, StaticBookSource

DAY = "2026-11-16"
TICKER = "KXTEST"


def row(signal_id="sig-1", **kw) -> dict:
    base = {
        "signal_id": signal_id, "system": "NHL_SOG", "league": "nhl",
        "matchup": "A @ B", "market_ticker": "ev1:sog", "selection": "Some Player Over 2.5",
        "player": "Some Player", "player_norm": "some player", "stat": "sog",
        "event_id": "ev1", "line": 2.5, "side": "yes",
        "gates_snapshot": {"p_hat": 0.545, "net_ev_pct": 9.0, "ev_kalshi_form_pct": 9.0,
                           "n_books": 5, "disp_mad": 0.01},
        "signal_price": 0.50, "limit_price_ceiling": 0.51,
        "venue": "novig", "venue_order_id": None, "status": "FILLED", "simulated": True,
        "game_day": DAY, "commence": "2026-11-17T00:00:00+00:00",
    }
    base.update(kw)
    return base


def book(yes_ask=0.50, size=100.0) -> Book:
    return Book(TICKER, yes_bids=[(0.60, 50.0)], no_bids=[(round(1.0 - yes_ask, 4), size)],
                fetched_at=0.0)


def make_bettor(tmp_path, *, bk=None, mapping=None, **cfgkw) -> Bettor:
    cfg = BettorConfig(audit_dir=str(tmp_path), **cfgkw)
    adapter = KalshiPaperAdapter(StaticBookSource({TICKER: bk or book()}), cfg)
    resolver = RecordedTableResolver(
        mapping if mapping is not None else {"sig-1": {"ticker": TICKER}})
    return Bettor(cfg, adapter, resolver)


def events(bettor: Bettor, kind: str) -> list:
    return [e for e in bettor.audit.events(DAY) if e["event"] == kind]


# ------------------------------------------------------------------ signal intake
def test_only_upstream_filled_rows_are_signals():
    assert from_autopilot_row(row()) is not None
    assert from_autopilot_row(row(status="REJ_EV_LOW")) is None
    assert from_autopilot_row(row(status="SKIPPED_STALE")) is None
    assert len(signals_from_rows([row(), row(status="REJ_CONS_THIN")])) == 1


# ------------------------------------------------------------------ happy path
def test_end_to_end_dry_run_fill_with_full_audit_trail(tmp_path):
    bettor = make_bettor(tmp_path)
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "FILLED"
    # stake: 1000 * 0.15 * (0.545-0.50)/(1-0.50) = $13.50, recorded at receipt
    received = events(bettor, "signal_received")
    assert len(received) == 1 and received[0]["stake_usd"] == pytest.approx(13.5)
    kinds = [e["event"] for e in bettor.audit.events(DAY)]
    assert kinds == ["signal_received", "rule_decision", "risk_decision",
                     "order_intended", "order_result"]
    intended = events(bettor, "order_intended")[0]
    assert intended["contracts"] == 26 and intended["limit_price"] == 0.51
    result = events(bettor, "order_result")[0]
    assert result["status"] == "FILLED" and result["simulated"] is True
    assert result["filled_cost"] == pytest.approx(13.455)
    assert bettor.audit.orders_placed(DAY) == 1


# ------------------------------------------------------------------ gate rejects keep their stake
def test_ev_reject_still_records_the_computed_stake(tmp_path):
    bettor = make_bettor(tmp_path)
    r = row(gates_snapshot={"p_hat": 0.50, "net_ev_pct": 1.01, "ev_kalshi_form_pct": 1.01},
            signal_price=0.495, limit_price_ceiling=0.505)
    out = bettor.process_signal(from_autopilot_row(r))
    assert out["outcome"] == "REJ_EV_LOW"
    received = events(bettor, "signal_received")[0]
    assert received["stake_usd"] == pytest.approx(1.4851, abs=1e-3)   # the stake is on record
    decision = events(bettor, "rule_decision")[0]
    assert decision["allowed"] is False and decision["reason"] == "REJ_EV_LOW"
    assert decision["stake_usd"] == received["stake_usd"]
    assert events(bettor, "order_intended") == []


def test_price_band_reject(tmp_path):
    bettor = make_bettor(tmp_path)
    r = row(gates_snapshot={"p_hat": 0.30}, signal_price=0.20, limit_price_ceiling=0.21)
    out = bettor.process_signal(from_autopilot_row(r))
    assert out["outcome"] == "REJ_PRICE_BAND"
    assert events(bettor, "signal_received")[0]["stake_usd"] == pytest.approx(18.75)


# ------------------------------------------------------------------ pipeline guards
def test_duplicate_signal_never_intends_twice(tmp_path):
    bettor = make_bettor(tmp_path)
    sig = from_autopilot_row(row())
    assert bettor.process_signal(sig)["outcome"] == "FILLED"
    assert bettor.process_signal(sig)["outcome"] == "REJ_DUP_INTENT"
    assert len(events(bettor, "order_result")) == 1


def test_killed_switch_blocks_at_the_risk_gate(tmp_path):
    bettor = make_bettor(tmp_path)
    bettor.risk.trip("manual test")
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "REJ_KILLED"
    assert events(bettor, "order_intended") == []


def test_daily_bet_cap_blocks_the_second_order(tmp_path):
    bettor = make_bettor(tmp_path, daily_bet_cap=1,
                         mapping={"sig-1": {"ticker": TICKER}, "sig-2": {"ticker": TICKER}})
    assert bettor.process_signal(from_autopilot_row(row()))["outcome"] == "FILLED"
    out = bettor.process_signal(from_autopilot_row(row(signal_id="sig-2")))
    assert out["outcome"] == "REJ_DAILY_BET_CAP"


def test_unmapped_signal_is_an_audited_skip(tmp_path):
    bettor = make_bettor(tmp_path, mapping={})
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "REJ_UNMAPPED"
    assert events(bettor, "order_intended") == []


def test_price_moved_against_us_skips(tmp_path):
    bettor = make_bettor(tmp_path, bk=book(yes_ask=0.56))     # executable EV now negative
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "SKIPPED_PRICE_MOVED"


def test_thin_book_partially_fills_at_the_limit(tmp_path):
    bettor = make_bettor(tmp_path, bk=book(yes_ask=0.50, size=5.0))
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "PARTIAL"                        # 26 intended, only 5 on the book
    result = events(bettor, "order_result")[0]
    assert result["fill_count"] == 5
    intended = events(bettor, "order_intended")[0]
    assert intended["limit_price"] == 0.51                    # the ceiling is the limit, always


def test_settlement_loss_stop_halts_following_signals(tmp_path):
    bettor = make_bettor(tmp_path)
    bettor.record_settlement("sig-0", -60.0, DAY)             # past the $50 stop
    assert bettor.risk.killed
    out = bettor.process_signal(from_autopilot_row(row()))
    assert out["outcome"] == "REJ_KILLED"


def test_process_rows_summary_counts_outcomes(tmp_path):
    bettor = make_bettor(tmp_path)
    summary = bettor.process_rows([row(), row(status="REJ_EV_LOW"),
                                   row(signal_id="sig-9")])
    assert summary["signals"] == 2                            # upstream reject is not a signal
    assert summary["outcomes"] == {"FILLED": 1, "REJ_UNMAPPED": 1}
