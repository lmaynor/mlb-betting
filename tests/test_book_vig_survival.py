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


class TestOutlierAnchor:
    def _hist(self, pinn_fair):
        rows = []
        for book, fair in [("draftkings", 0.50), ("betmgm", 0.50),
                           ("caesars", 0.50), ("fanduel", 0.42)]:
            rows.append(_quote(book, "OVER", fair, fair=fair))
        if pinn_fair is not None:
            rows.append(_quote("pinnacle", "OVER", pinn_fair, fair=pinn_fair))
        return pd.DataFrame(rows)

    def test_pinnacle_anchor_overrides_median(self, monkeypatch):
        from mlb.analysis import outlier_scan as osc
        # soft median says 0.50 -> fanduel @0.42 impl looks +EV; pinnacle at 0.43
        # says the market IS ~0.43 -> anchored consensus kills the fake alert
        monkeypatch.setattr(osc.oh, "read_history", lambda *a, **k: self._hist(0.43))
        monkeypatch.setattr(osc.oh, "dedupe_by_source", lambda d, **k: d)
        hits = osc.scan("k_ou", min_ev=0.03, min_books=4, anchor_book="pinnacle")
        assert len(hits) == 0

    def test_no_anchor_falls_back_to_median(self, monkeypatch):
        from mlb.analysis import outlier_scan as osc
        monkeypatch.setattr(osc.oh, "read_history", lambda *a, **k: self._hist(None))
        monkeypatch.setattr(osc.oh, "dedupe_by_source", lambda d, **k: d)
        hits = osc.scan("k_ou", min_ev=0.03, min_books=4, anchor_book="pinnacle")
        assert len(hits) == 1 and hits.iloc[0]["book"] == "fanduel"
        assert not hits.iloc[0]["anchored"]

    def test_anchor_confirms_real_lag(self, monkeypatch):
        from mlb.analysis import outlier_scan as osc
        # pinnacle agrees with the soft median (0.50) -> fanduel truly lagging
        monkeypatch.setattr(osc.oh, "read_history", lambda *a, **k: self._hist(0.50))
        monkeypatch.setattr(osc.oh, "dedupe_by_source", lambda d, **k: d)
        hits = osc.scan("k_ou", min_ev=0.03, min_books=4, anchor_book="pinnacle")
        assert len(hits) == 1 and bool(hits.iloc[0]["anchored"])


class TestAltLineScan:
    def _quotes(self):
        rows = []
        # main line 5.5 quoted by 3 books; alt 7.5 by one lazy book
        for book, line, sel, dec in [("draftkings", 5.5, "OVER", 1.87),
                                     ("fanduel", 5.5, "OVER", 1.91),
                                     ("betmgm", 5.5, "OVER", 1.87),
                                     ("hardrock", 7.5, "OVER", 5.10)]:
            q = _quote(book, sel, 1.0 / dec, line=line, gpk=7, pid=500)
            q["american"] = 100
            rows.append(q)
        return pd.DataFrame(rows)

    def test_alt_flag_and_model_ev(self, monkeypatch):
        from mlb.analysis import alt_line_scan as als
        monkeypatch.setattr(als.oh, "read_history", lambda *a, **k: self._quotes())
        monkeypatch.setattr(als.oh, "dedupe_by_source", lambda d, **k: d)
        preds = pd.DataFrame({"player_id": [500], "mu": [7.4], "nb_alpha": [0.05],
                              "game_pk": [7], "game_date": ["2026-07-06"]})
        monkeypatch.setattr(als.gp, "gen_preds", lambda *a, **k: preds)
        out = als.scan_system("K", "2026-07-06", min_ev=0.02)
        assert len(out) >= 1
        # with mu=7.4, over 5.5 at ~1.9 and over 7.5 at 5.1 should both be +EV,
        # and the 7.5 quote must be flagged ALT (modal line is 5.5)
        alt = out[out["line"] == 7.5]
        assert len(alt) == 1 and bool(alt.iloc[0]["is_alt"])
        main = out[out["line"] == 5.5]
        assert (~main["is_alt"]).all()

    def test_no_preds_returns_empty(self, monkeypatch):
        from mlb.analysis import alt_line_scan as als
        monkeypatch.setattr(als.gp, "gen_preds",
                            lambda *a, **k: pd.DataFrame({"mu": [], "player_id": [],
                                                          "nb_alpha": [], "game_pk": [],
                                                          "game_date": []}))
        out = als.scan_system("K", "2026-07-06", min_ev=0.02)
        assert len(out) == 0
