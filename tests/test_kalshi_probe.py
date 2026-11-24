"""Tests for the Kalshi liquidity probe (ev/kalshi_probe).

Covers: orderbook parsing (YES ask derived from NO bids), the four-way
classification incl. the $25 ask-size bar, the T-4h..T-15m window, the
hard stop, the no-op path (no orderbook calls when nothing is in window),
snapshot mode, and create-only banking layout.
"""
from datetime import datetime, timedelta, timezone

import pytest

from ev.kalshi_probe.classifier import classify, parse_orderbook
from ev.kalshi_probe import sampler
from ev.kalshi_probe.sampler import (
    HARD_STOP_UTC, in_window, is_hard_stopped, run_probe)

NOW = datetime(2026, 11, 22, 20, 0, 0, tzinfo=timezone.utc)


def book(yes, no):
    return {"orderbook_fp": {"yes_dollars": yes, "no_dollars": no}}


# ---------------- classifier / parser ----------------

def test_parse_derives_ask_from_no_bids():
    p = parse_orderbook(book([["0.48", "100"], ["0.52", "50"]],
                             [["0.43", "200"], ["0.47", "80"]]))
    assert p["best_bid"] == 0.52 and p["bid_size_contracts"] == 50
    assert p["best_ask"] == 0.53  # 1 - 0.47
    assert p["ask_size_contracts"] == 80
    assert p["ask_dollar_size"] == pytest.approx(0.53 * 80)
    assert p["spread"] == pytest.approx(0.01)


def test_classify_has_ask_25():
    p = parse_orderbook(book([["0.50", "10"]], [["0.40", "100"]]))
    assert p["ask_dollar_size"] == 60.0
    assert classify(p) == "HAS_ASK_25"


def test_classify_has_ask_small():
    p = parse_orderbook(book([["0.50", "10"]], [["0.40", "10"]]))
    assert p["ask_dollar_size"] == 6.0
    assert classify(p) == "HAS_ASK_SMALL"


def test_classify_exactly_25_is_has_ask_25():
    p = parse_orderbook(book([], [["0.50", "50"]]))  # ask 0.50 x 50 = $25
    assert classify(p) == "HAS_ASK_25"


def test_classify_bid_only():
    p = parse_orderbook(book([["0.50", "10"]], []))
    assert classify(p) == "BID_ONLY"


def test_classify_no_book():
    assert classify(parse_orderbook(book([], []))) == "NO_BOOK"
    assert classify(parse_orderbook({})) == "NO_BOOK"
    assert classify(parse_orderbook(None)) == "NO_BOOK"


# ---------------- window + hard stop ----------------

def test_window_bounds():
    start = NOW + timedelta(hours=2)
    assert in_window(start, NOW)
    assert in_window(NOW + timedelta(hours=4), NOW)          # T-4h edge
    assert in_window(NOW + timedelta(minutes=15), NOW)       # T-15m edge
    assert not in_window(NOW + timedelta(hours=4, seconds=1), NOW)
    assert not in_window(NOW + timedelta(minutes=14), NOW)
    assert not in_window(NOW - timedelta(minutes=5), NOW)    # already tipped
    assert not in_window(None, NOW)


def test_hard_stop_constant_and_gate():
    assert HARD_STOP_UTC == datetime(2026, 11, 24, 8, 0, 0,
                                     tzinfo=timezone.utc)
    assert not is_hard_stopped(HARD_STOP_UTC - timedelta(seconds=1))
    assert is_hard_stopped(HARD_STOP_UTC)
    assert is_hard_stopped(HARD_STOP_UTC + timedelta(days=1))


# ---------------- sampler behaviour with a fake client ----------------

class FakeClient:
    def __init__(self, events_by_series, books):
        self.events_by_series = events_by_series
        self.books = books
        self.book_calls = []

    def list_events(self, series):
        return self.events_by_series.get(series, [])

    def get_orderbook(self, ticker):
        self.book_calls.append(ticker)
        return self.books[ticker]


def _market(ticker, start_iso):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "title": ticker, "occurrence_datetime": start_iso,
            "fee_multiplier": None}


def _events(series, markets):
    ev = {"event_ticker": markets[0]["event_ticker"], "title": "Game",
          "series_ticker": series, "markets": markets}
    return [ev]


def test_no_op_makes_no_orderbook_calls():
    start = (NOW + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    m = _market("KXNBAPTS-26NOV22AAA-1", start)
    client = FakeClient({"KXNBAPTS": _events("KXNBAPTS", [m])},
                        {m["ticker"]: book([], [])})
    out = run_probe(now=NOW, client=client, do_bank=False)
    assert out["status"] == "no_op" and out["rows"] == 0
    assert client.book_calls == []


def test_window_run_samples_and_classifies():
    start = (NOW + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    m = _market("KXNBAPTS-26NOV22AAA-1", start)
    client = FakeClient({"KXNBAPTS": _events("KXNBAPTS", [m])},
                        {m["ticker"]: book([["0.50", "10"]],
                                            [["0.40", "100"]])})
    rows, mode = sampler.collect_rows(client, NOW)
    assert mode == "window" and len(rows) == 1
    r = rows[0]
    assert r["classification"] == "HAS_ASK_25"
    assert r["source"] == "BOOK" and r["in_window"] is True
    assert r["family"] == "NBA_POINTS" and r["series"] == "KXNBAPTS"
    assert r["best_ask"] == 0.60 and r["ask_dollar_size"] == 60.0


def test_snapshot_mode_marks_out_of_window():
    start = (NOW + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    m = _market("KXNBAGAME-26NOV22AAA-AAA", start)
    client = FakeClient({"KXNBAGAME": _events("KXNBAGAME", [m])},
                        {m["ticker"]: book([], [])})
    rows, mode = sampler.collect_rows(client, NOW, snapshot_if_empty=True)
    assert mode == "snapshot" and len(rows) == 1
    assert rows[0]["in_window"] is False
    assert rows[0]["classification"] == "NO_BOOK"


def test_hard_stop_short_circuits_before_any_client_use():
    class Exploding:
        def list_events(self, series):
            raise AssertionError("must not be called after hard stop")
    out = run_probe(now=HARD_STOP_UTC + timedelta(hours=1),
                    client=Exploding(), do_bank=False)
    assert out["status"] == "hard_stop" and out["rows"] == 0


def test_bank_layout_is_create_only_chunk(monkeypatch):
    captured = {}

    class FakeBlob:
        def upload_from_string(self, payload, content_type=None,
                               if_generation_match=None):
            captured["payload"] = payload
            captured["if_generation_match"] = if_generation_match

    class FakeBucket:
        def blob(self, name):
            captured["name"] = name
            return FakeBlob()

    class FakeStorage:
        def bucket(self, name):
            captured["bucket"] = name
            return FakeBucket()

    rows = [{"ticker": "X", "classification": "NO_BOOK"}]
    uri = sampler.bank_rows(rows, NOW, bucket="bkt",
                            storage_client=FakeStorage(), run_id="abc123")
    assert uri.startswith("gs://bkt/Kalshi/probe/dt=2026-11-22/run-")
    assert uri.endswith("-abc123.jsonl")
    assert captured["if_generation_match"] == 0
    assert '"ticker": "X"' in captured["payload"]


def test_series_set_excludes_nhl_and_includes_game_lines():
    assert set(sampler.TARGET_SERIES) == {
        "KXNBAPTS", "KXNBAREB", "KXNBA3PT",
        "KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL"}
    assert sampler.SERIES_FAMILIES["KXNBAGAME"] == "NBA_GAME_MONEYLINE"
    assert sampler.SERIES_FAMILIES["KXNBASPREAD"] == "NBA_GAME_SPREAD"
    assert sampler.SERIES_FAMILIES["KXNBATOTAL"] == "NBA_GAME_TOTAL"
    assert sampler.USER_AGENT == "Beezy Research (beezy.fyi; research@beezy.fyi)"
