"""
tests/test_kalshi_to_history.py -- mlb.analysis.kalshi_to_history.

Covers the pure selection-shaping logic (_selections) and the row-building
pipeline (build_rows) with kalshi.fetch_active_markets + id_resolver
monkeypatched (no network, no GCS -- follows the test_parlay_adapter.py
_prime() pattern).

Also locks in the SERIES_MAP <-> kalshi_vs_books naming reconciliation that
was flagged as a KNOWN ISSUE when this module first shipped (2026-07-23):
kalshi_to_history WRITES canonical market names that kalshi_vs_books must
READ under the same names, or the two modules silently stop talking to each
other. This regression test fails loudly if that drifts again.
"""
import pandas as pd
import pytest

from mlb_core.data import id_resolver as R
from mlb_core.odds import kalshi as K
from mlb.analysis import kalshi_to_history as H
from mlb.analysis import kalshi_vs_books as V


DATE = "2026-07-23"


def _prime(monkeypatch):
    monkeypatch.setitem(R._schedule_cache, DATE, {("TB", "TOR"): [745999]})
    # id_resolver._norm() drops the "Jr." suffix + the period -- index under
    # the SAME normalized form the lookup will produce, not the raw title text.
    monkeypatch.setitem(R._player_cache, DATE[:4], (
        {"vladimir guerrero": {665489}},
        {("vladimir guerrero", "TOR"): 665489},
    ))


# --------------------------------------------------------------------------- #
# reconciliation regression: SERIES_MAP names must line up with kalshi_vs_books
# --------------------------------------------------------------------------- #

def test_liquid_markets_all_known_to_kalshi_to_history():
    """Every market kalshi_vs_books.LIQUID trusts as a firm Kalshi mid must be
    a name kalshi_to_history actually writes -- otherwise the "trustworthy"
    label is scanning a market that never gets populated. Guards against the
    total_ou/runline vs game_total/game_rl mismatch from the original
    2026-07-23 commit (both modules must agree on the name)."""
    written_markets = {canon for canon, _system, _kind in H.SERIES_MAP.values()}
    assert V.LIQUID <= written_markets, (
        f"kalshi_vs_books trusts markets kalshi_to_history never writes: "
        f"{V.LIQUID - written_markets}"
    )


def test_game_total_and_runline_specifically_reconciled():
    # The two markets literally named in the original KNOWN ISSUE.
    written = {canon for canon, _s, _k in H.SERIES_MAP.values()}
    assert "game_total" in written
    assert "game_rl" in written
    assert "total_ou" not in written   # the old, wrong name
    assert "runline" not in written    # the old, wrong name


# --------------------------------------------------------------------------- #
# _selections() -- pure, per-market-kind row shaping
# --------------------------------------------------------------------------- #

class TestSelections:
    def test_rfi_kind_returns_yrfi_and_nrfi(self):
        p = {"yes_ask": 0.55, "yes_mid": 0.52, "no_ask": 0.48, "no_mid": 0.48}
        sels = H._selections("rfi", {}, p)
        assert sels == [("YRFI", 0.55, 0.52), ("NRFI", 0.48, 0.48)]

    def test_over_kind_returns_over_and_under(self):
        p = {"yes_ask": 0.6, "yes_mid": 0.58, "no_ask": 0.44, "no_mid": 0.42}
        sels = H._selections("over", {}, p)
        assert sels == [("OVER", 0.6, 0.58), ("UNDER", 0.44, 0.42)]

    def test_ml_kind_returns_single_outcome_from_ticker(self):
        mk = {"ticker": "KXMLBGAME-26JUL23TBTOR-TOR"}
        p = {"yes_ask": 0.53, "yes_mid": 0.5}
        sels = H._selections("ml", mk, p)
        assert sels == [("TOR", 0.53, 0.5)]

    def test_spread_kind_strips_trailing_digit_via_market_outcome(self):
        mk = {"ticker": "KXMLBSPREAD-26JUL23TBTOR-TOR2"}
        p = {"yes_ask": 0.47, "yes_mid": 0.45}
        sels = H._selections("spread", mk, p)
        assert sels == [("TOR", 0.47, 0.45)]


# --------------------------------------------------------------------------- #
# build_rows() -- end-to-end row construction, network mocked out
# --------------------------------------------------------------------------- #

def _market(ticker, event_ticker, yes_bid, yes_ask, no_bid, no_ask, **extra):
    return {"ticker": ticker, "event_ticker": event_ticker, "status": "active",
            "yes_bid_dollars": yes_bid, "yes_ask_dollars": yes_ask,
            "no_bid_dollars": no_bid, "no_ask_dollars": no_ask, **extra}


def test_build_rows_ml_market_maps_home_away(monkeypatch):
    _prime(monkeypatch)

    def fake_fetch(series):
        if series == "KXMLBGAME":
            return [_market("KXMLBGAME-26JUL231507TBTOR-TOR",
                             "KXMLBGAME-26JUL231507TBTOR",
                             "0.50", "0.54", "0.46", "0.50")]
        return []

    monkeypatch.setattr(K, "fetch_active_markets", fake_fetch)
    hist, raw, stats = H.build_rows({"KXMLBGAME"}, "2026-07-23 15:55:00", "2026-07-23T15:55:00Z", False)

    assert len(hist) == 1
    row = hist[0]
    assert row["market"] == "game_ml"
    assert row["selection"] == "HOME"   # TOR is home per the _prime schedule
    assert row["game_pk"] == 745999
    assert row["book"] == "kalshi"
    assert row["source"] == "kalshi"
    assert stats["series"]["KXMLBGAME"]["rows"] == 1


def test_build_rows_prop_market_resolves_player_id(monkeypatch):
    _prime(monkeypatch)

    def fake_fetch(series):
        if series == "KXMLBHR":
            return [_market("KXMLBHR-26JUL231507TBTOR-VGJR",
                             "KXMLBHR-26JUL231507TBTOR",
                             "0.20", "0.24", "0.76", "0.80",
                             yes_sub_title="Vladimir Guerrero Jr.: 1+ home runs?")]
        return []

    monkeypatch.setattr(K, "fetch_active_markets", fake_fetch)
    hist, raw, stats = H.build_rows({"KXMLBHR"}, "2026-07-23 15:55:00", "2026-07-23T15:55:00Z", False)

    assert len(hist) == 2  # OVER + UNDER
    assert {r["selection"] for r in hist} == {"OVER", "UNDER"}
    assert all(r["player_id"] == 665489 for r in hist)
    assert all(r["market"] == "hr_yn" for r in hist)
    assert stats["no_player"] == 0


def test_build_rows_unresolved_player_counted_not_dropped_from_raw(monkeypatch):
    monkeypatch.setitem(R._schedule_cache, DATE, {("TB", "TOR"): [745999]})
    monkeypatch.setitem(R._player_cache, DATE[:4], ({}, {}))  # nobody resolves
    # Season index is empty, so resolve_player_id falls through to the
    # boxscore-roster fallback -- prime it empty too so we never touch the
    # network for the game_pk it wasn't given.
    monkeypatch.setitem(R._roster_cache, 745999, {})

    def fake_fetch(series):
        if series == "KXMLBHR":
            return [_market("KXMLBHR-26JUL231507TBTOR-NOBODY",
                             "KXMLBHR-26JUL231507TBTOR",
                             "0.20", "0.24", "0.76", "0.80",
                             yes_sub_title="Some Rookie: 1+ home runs?")]
        return []

    monkeypatch.setattr(K, "fetch_active_markets", fake_fetch)
    hist, raw, stats = H.build_rows({"KXMLBHR"}, "2026-07-23 15:55:00", "2026-07-23T15:55:00Z", False)

    assert stats["no_player"] == 1
    assert len(raw) == 1              # still banked to the raw depth snapshot
    assert raw[0]["player_id"] is None
    assert all(r["player_id"] is None for r in hist)  # history rows still written


def test_build_rows_bad_event_ticker_skips_market(monkeypatch):
    def fake_fetch(series):
        if series == "KXMLBGAME":
            return [_market("KXMLBGAME-garbage", "KXMLBGAME-garbage",
                             "0.50", "0.54", "0.46", "0.50")]
        return []

    monkeypatch.setattr(K, "fetch_active_markets", fake_fetch)
    hist, raw, stats = H.build_rows({"KXMLBGAME"}, "2026-07-23 15:55:00", "2026-07-23T15:55:00Z", False)

    assert hist == []
    assert raw == []
    assert stats["no_date"] == 1


def test_build_rows_unpriceable_side_is_dropped(monkeypatch):
    _prime(monkeypatch)

    def fake_fetch(series):
        if series == "KXMLBGAME":
            # no_ask/no_bid both 0 -> no_mid falls back to 1-yes_mid, still
            # priceable; drive the drop via a zero yes_ask (ask<=0 filter).
            return [_market("KXMLBGAME-26JUL231507TBTOR-TOR",
                             "KXMLBGAME-26JUL231507TBTOR",
                             "0.50", "0.0", "0.46", "0.50")]
        return []

    monkeypatch.setattr(K, "fetch_active_markets", fake_fetch)
    hist, raw, stats = H.build_rows({"KXMLBGAME"}, "2026-07-23 15:55:00", "2026-07-23T15:55:00Z", False)

    assert hist == []                  # ask<=0 -> row dropped from history
    assert len(raw) == 1               # raw depth snapshot still banked
