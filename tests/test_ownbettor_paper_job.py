"""ev.ownbettor.paper_job (AUT-10): the scheduled paper operation.

Covers the pure helpers (dedupe set, unsettled-fill reconstruction, settlement
math), the two refusal guards (disarmed / live-armed), and the full firing
loop against a DirStore: restore audit -> settle decided fills -> process new
signals -> bank audit + run summary, with the second firing proving both the
settlement booking and the never-reprocess dedupe.
"""
import gzip
import json

import pytest

from mlb.execution.kalshi.book import Book

from ev.novig.shadow_bank import DirStore
from ev.ownbettor import paper_job
from ev.ownbettor.config import BettorConfig
from ev.ownbettor.venues.kalshi import StaticBookSource

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


class FakeResults:
    def __init__(self, result=None):
        self._result = result

    def result(self, ticker):
        return self._result


def make_store(tmp_path, rows) -> DirStore:
    store = DirStore(str(tmp_path / "store"))
    payload = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
    store.put(paper_job.LEDGER_KEY, gzip.compress(payload))
    return store


def make_cfg(tmp_path) -> BettorConfig:
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"sig-1": {"ticker": TICKER}}), encoding="utf-8")
    return BettorConfig(audit_dir=str(tmp_path / "audit"), mapping_path=str(mapping))


# ------------------------------------------------------------------ pure helpers
def test_processed_signal_ids_reads_signal_received_only():
    events = [{"event": "signal_received", "signal_id": "a"},
              {"event": "order_intended", "signal_id": "b"},
              {"event": "signal_received", "signal_id": "c"}]
    assert paper_job.processed_signal_ids(events) == {"a", "c"}


def _fill_events(settled=False, status="FILLED", simulated=True):
    events = [
        {"event": "order_intended", "signal_id": "s1", "ticker": TICKER, "side": "yes", "day": DAY},
        {"event": "order_result", "signal_id": "s1", "status": status, "simulated": simulated,
         "fill_count": 10, "filled_cost": 5.20, "day": DAY},
    ]
    if settled:
        events.append({"event": "settlement", "signal_id": "s1", "pnl_usd": 4.8, "day": DAY})
    return events


def test_unsettled_fills_reconstruction_and_filters():
    fills = paper_job.unsettled_fills(_fill_events())
    assert len(fills) == 1 and fills[0]["ticker"] == TICKER and fills[0]["fill_count"] == 10
    assert paper_job.unsettled_fills(_fill_events(settled=True)) == []
    assert paper_job.unsettled_fills(_fill_events(status="NO_FILL")) == []
    assert paper_job.unsettled_fills(_fill_events(simulated=False)) == []


def test_settlement_pnl_win_and_loss():
    fill = {"side": "yes", "fill_count": 26, "filled_cost": 13.455}
    assert paper_job.settlement_pnl(fill, "yes") == pytest.approx(12.545)
    assert paper_job.settlement_pnl(fill, "no") == pytest.approx(-13.455)


# ------------------------------------------------------------------ refusal guards
def test_main_refuses_when_disarmed(monkeypatch):
    monkeypatch.delenv(paper_job.ARM_ENV, raising=False)
    assert paper_job.main() == 3


def test_main_refuses_when_live_armed(monkeypatch):
    monkeypatch.setenv(paper_job.ARM_ENV, "1")
    monkeypatch.setenv("OWNBETTOR_LIVE_ARMED", "1")
    assert paper_job.main() == 2


# ------------------------------------------------------------------ the firing loop
def test_firing_loop_fill_then_settle_then_quiet(tmp_path):
    store = make_store(tmp_path, [row(), row(signal_id="sig-x", status="REJ_EV_LOW")])
    cfg = make_cfg(tmp_path)
    results = FakeResults()
    books = StaticBookSource({TICKER: book()})

    first = paper_job.run_job(store, results, books, cfg)
    assert first["ledger_rows"] == 2 and first["actionable_in_ledger"] == 1
    assert first["signals_new"] == 1 and first["outcomes"] == {"FILLED": 1}
    assert first["settlements_booked"] == []
    # the audit was banked back to the store, and a run summary exists
    assert f"audit-{DAY}.jsonl" in first["audit_files_banked"]
    assert any(k.startswith(paper_job.RUNS_PREFIX) for k in store.list(paper_job.RUNS_PREFIX))

    results._result = "yes"                      # the market decides before the next firing
    second = paper_job.run_job(store, results, books, cfg)
    assert second["signals_new"] == 0            # dedupe: never re-processed
    assert len(second["settlements_booked"]) == 1
    assert second["settlements_booked"][0]["pnl_usd"] == pytest.approx(12.545)
    assert second["realized_pnl_total"] == pytest.approx(12.545)

    third = paper_job.run_job(store, results, books, cfg)
    assert third["settlements_booked"] == []     # settled fills stay settled
    assert third["signals_new"] == 0
