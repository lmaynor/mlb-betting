"""tests/test_futures_basis_monitor.py -- PR-C extension coverage (RUL-20261119):
FuturesFairProvider/Null provider with the NO_BET_NO_PSIM token, the shadow
cross-venue basis monitor (SHADOW_BASIS_ONLY, fee-adjusted executable asks,
honest unmatched counts), and the guarded canonical-futures consumption."""

from __future__ import annotations

import pandas as pd
import pytest

from ev.futures_board_feed import (
    NullFuturesFairProvider,
    basis_monitor,
    canonical_futures_snapshots,
    kalshi_fee,
    shadow_signal_log,
)
from ev.futures_sleeve import FuturesBoardRow


def _row(venue="kalshi", market="KXNBA", outcome="Boston Celtics", q=0.2, fair=0.21):
    return FuturesBoardRow(venue=venue, market=market, outcome=outcome,
                           q_market=q, p_fair_power=fair)


def test_null_provider_means_no_bet_no_psim():
    recs = shadow_signal_log([_row()])
    sig = [r for r in recs if r["kind"] == "signal"][0]
    assert sig["p_sim"] is None
    assert sig["entry_fired"] is False
    assert sig["reason"] == "NO_BET_NO_PSIM"
    assert NullFuturesFairProvider().p_sim("kalshi", "KXNBA", "X", "2026-11-19") is None


def test_kalshi_fee_form():
    assert kalshi_fee(0.5) == pytest.approx(0.0175)
    assert kalshi_fee(0.0) == 0.0


def _bp_snap(book, outcome, q, market="nba:nba_championship_winner"):
    return {"venue": book, "market": market, "outcome": outcome, "q": q, "ts": None, "sport": "nba"}


def _k_snap(outcome, bid, ask, league="nba", market="NBA Championship Winner"):
    return {"venue": "kalshi", "market": market, "outcome": outcome, "q": (bid + ask) / 2,
            "ts": None, "bid": bid, "ask": ask, "league": league}


def test_basis_monitor_matches_and_fee_adjusts():
    # Two books, two outcomes each; raw q sums to 1.10 per book (10% overround).
    bp = [
        _bp_snap("draftkings", "Boston Celtics", 0.60), _bp_snap("draftkings", "Denver Nuggets", 0.50),
        _bp_snap("fanduel", "Boston Celtics", 0.62), _bp_snap("fanduel", "Denver Nuggets", 0.48),
    ]
    k = [_k_snap("Boston Celtics", 0.55, 0.57), _k_snap("Phoenix Suns", 0.10, 0.12)]
    recs = basis_monitor(k, bp, ts="2026-11-19T12:00:00Z")
    basis = [r for r in recs if r["kind"] == "basis"]
    summary = [r for r in recs if r["kind"] == "basis_summary"][0]
    assert len(basis) == 1  # only Boston is on both venues
    b = basis[0]
    assert b["status"] == "SHADOW_BASIS_ONLY"
    assert b["bp_n_books"] == 2
    assert b["kalshi_ask_yes"] == 0.57
    assert b["kalshi_exec_yes_fee_adj"] == pytest.approx(0.57 + kalshi_fee(0.57), abs=1e-6)
    assert b["basis_yes"] == pytest.approx(b["bp_consensus_fair"] - b["kalshi_exec_yes_fee_adj"], abs=1e-6)
    # NO side: executable no ask = 1 - bid = 0.45, fee-adjusted.
    assert b["kalshi_ask_no"] == pytest.approx(0.45)
    assert summary["matched_outcomes"] == 1
    assert summary["bp_outcomes_total"] == 2
    assert summary["bp_outcomes_unmatched"] == 1  # Denver has no Kalshi quote
    assert summary["kalshi_snapshots_total"] == 2


def test_basis_monitor_no_overlap_is_all_unmatched():
    bp = [_bp_snap("draftkings", "Boston Celtics", 0.60), _bp_snap("draftkings", "Denver Nuggets", 0.50)]
    k = [_k_snap("Phoenix Suns", 0.10, 0.12)]
    recs = basis_monitor(k, bp, ts="2026-11-19T12:00:00Z")
    assert [r for r in recs if r["kind"] == "basis"] == []
    summary = [r for r in recs if r["kind"] == "basis_summary"][0]
    assert summary["matched_outcomes"] == 0 and summary["bp_outcomes_unmatched"] == 2


def test_canonical_futures_consumption_falls_back_when_layer_absent():
    # This tree predates PR-B: the canonical layer is not importable, so the
    # guarded consumer returns None and callers use the direct bank adapters.
    assert canonical_futures_snapshots("2026-11-19") is None
