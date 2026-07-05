"""Tests for mlb.analysis.book_vig and mlb.analysis.quote_survival (synthetic frames)."""

import numpy as np
import pandas as pd
import pytest

from mlb.analysis import book_vig as bv
from mlb.analysis import quote_survival as qs


def _quote(book, sel, implied, ts="2026-07-01 15:00:00", line=0.5, gpk=1, pid=100,
           fair=None, dec=None):
    return {
        "game_pk": gpk, "player_id": pid, "line": line, "selection": sel,
        "book": book, "snapshot_ts": ts, "game_date": "2026-07-01",
        "implied_prob": implied, "decimal": dec or round(1.0 / implied, 4),
        "fair_prob": fair if fair is not None else implied,
        "source": "bettingpros",
    }


class TestPairHolds:
    def test_two_sided_hold(self):
        df = pd.DataFrame([
            _quote("draftkings", "OVER", 0.55),
            _quote("draftkings", "UNDER", 0.52),
        ])
        holds = bv.pair_holds(df)
        assert len(holds) == 1
        assert holds["hold"].iloc[0] == pytest.approx(0.07, abs=1e-9)
        assert holds["book"].iloc[0] == "draftkings"

    def test_unmatched_side_dropped(self):
        df = pd.DataFrame([_quote("fanduel", "OVER", 0.55)])
        assert len(bv.pair_holds(df)) == 0

    def test_cross_book_pairs_not_mixed(self):
        df = pd.DataFrame([
            _quote("draftkings", "OVER", 0.55),
            _quote("fanduel", "UNDER", 0.52),
        ])
        assert len(bv.pair_holds(df)) == 0  # different books never pair

    def test_absurd_hold_filtered(self):
        df = pd.DataFrame([
            _quote("draftkings", "OVER", 0.90),
            _quote("draftkings", "UNDER", 0.60),  # hold 0.50 -> bad ingest
        ])
        assert len(bv.pair_holds(df)) == 0


class TestGetVig:
    def test_lookup_and_fallbacks(self, monkeypatch):
        monkeypatch.setattr(bv, "_VIG_CACHE", {
            "k_ou": {"draftkings": {"vig": 0.055, "n": 100},
                     "thin_book": {"vig": 0.02, "n": 5},
                     "_market": {"vig": 0.06, "n": 500}},
        })
        assert bv.get_vig("k_ou", "draftkings") == pytest.approx(0.055)
        # thin sample -> market median
        assert bv.get_vig("k_ou", "thin_book") == pytest.approx(0.06)
        # unknown book -> market median
        assert bv.get_vig("k_ou", "novig") == pytest.approx(0.06)
        # unknown market -> default
        assert bv.get_vig("nope_ou", "draftkings") == pytest.approx(0.07)


def _snapshot(ts, lag_impl, cons_impl=0.50, lag_book="fanduel"):
    """4 books quoting one selection; `lag_book` priced at lag_impl."""
    rows = []
    for book in ("draftkings", "betmgm", "caesars"):
        rows.append(_quote(book, "OVER", cons_impl, ts=ts, fair=cons_impl))
    rows.append(_quote(lag_book, "OVER", lag_impl, ts=ts, fair=lag_impl))
    return rows


class TestQuoteSurvival:
    def _run(self, frames, monkeypatch, **kw):
        df = pd.DataFrame([r for f in frames for r in f])
        monkeypatch.setattr(qs.oh, "read_history", lambda *a, **k: df)
        monkeypatch.setattr(qs.oh, "dedupe_by_source", lambda d, **k: d)
        return qs.events_for_market("k_ou", min_ev=0.03, min_books=4, **kw)

    def test_event_opens_and_corrects_book_moved(self, monkeypatch):
        # fanduel lags at 0.44 impl vs 0.50 consensus (ev ~ 0.5/0.44-1 = +13.6%),
        # then corrects to consensus one hour later.
        ev = self._run([
            _snapshot("2026-07-01 15:00:00", lag_impl=0.44),
            _snapshot("2026-07-01 16:00:00", lag_impl=0.50),
        ], monkeypatch)
        assert len(ev) == 1
        e = ev.iloc[0]
        assert e["book"] == "fanduel"
        assert not e["censored"]
        assert e["survival_min"] == pytest.approx(60.0)
        assert e["mover"] == "book_moved"

    def test_censored_when_never_corrects(self, monkeypatch):
        ev = self._run([
            _snapshot("2026-07-01 15:00:00", lag_impl=0.44),
            _snapshot("2026-07-01 22:00:00", lag_impl=0.44),
        ], monkeypatch)
        assert len(ev) == 1
        assert bool(ev.iloc[0]["censored"])
        assert ev.iloc[0]["mover"] == "held"

    def test_consensus_moved_attribution(self, monkeypatch):
        # consensus comes DOWN to the "lagging" book -> book was right, EV fake
        ev = self._run([
            _snapshot("2026-07-01 15:00:00", lag_impl=0.44, cons_impl=0.50),
            _snapshot("2026-07-01 16:00:00", lag_impl=0.44, cons_impl=0.445),
        ], monkeypatch)
        assert len(ev) == 1
        assert ev.iloc[0]["mover"] == "consensus_moved"

    def test_no_event_below_threshold(self, monkeypatch):
        ev = self._run([
            _snapshot("2026-07-01 15:00:00", lag_impl=0.495),
            _snapshot("2026-07-01 16:00:00", lag_impl=0.50),
        ], monkeypatch)
        assert len(ev) == 0

    def test_summarize(self, monkeypatch):
        ev = self._run([
            _snapshot("2026-07-01 15:00:00", lag_impl=0.44),
            _snapshot("2026-07-01 16:00:00", lag_impl=0.50),
        ], monkeypatch)
        stats = qs.summarize(ev)
        assert len(stats) == 1
        r = stats.iloc[0]
        assert r["n_events"] == 1
        assert r["median_surv_min"] == pytest.approx(60.0)
        assert r["pct_book_moved"] == pytest.approx(1.0)
