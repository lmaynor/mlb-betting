"""Tests for the bettable-slate / edge-bucket gating in monitor_performance.

Locks in the NRFI-misdiagnosis correction (handoff 2026-06-29):
  - per-edge-bucket ROI is computed over settled placed bets,
  - the validated dead mid-edge band [5%,12%) is aggregated and flagged,
  - placed-bet AUC NEVER alerts on its own (only alongside negative ROI),
  - suppression stays ROI-only (unchanged).
"""
import pandas as pd

from mlb.runners.monitor_performance import (
    _edge_bucket_stats,
    _check_alerts,
    AUC_ALERT_MIN_N,
)


def _bets(spec):
    """spec: list of (edge, win, count). Even-ish odds (decimal 1.9)."""
    rows = []
    for edge, win, count in spec:
        for _ in range(count):
            rows.append({"result": "win" if win else "loss",
                         "stake": 1.0, "profit": 0.9 if win else -1.0,
                         "edge": edge})
    return pd.DataFrame(rows)


class TestEdgeBucketStats:
    def test_buckets_and_dead_band(self):
        df = _bets([(0.03, True, 30), (0.03, False, 15),   # 0-5%
                    (0.07, False, 35), (0.07, True, 15),    # dead band
                    (0.25, True, 22), (0.25, False, 6)])    # 20%+
        eb = _edge_bucket_stats(df)
        assert eb["0-5%"]["n"] == 45 and eb["0-5%"]["roi"] > 0
        assert eb["20%+"]["roi"] > 0
        assert eb["_dead_band"]["n"] == 50
        assert eb["_dead_band"]["roi"] < 0
        assert eb["_dead_band"]["range"] == "[5%,12%)"

    def test_empty_without_edge_or_results(self):
        assert _edge_bucket_stats(pd.DataFrame()) == {}
        assert _edge_bucket_stats(pd.DataFrame({"result": [None]})) == {}


class TestDeadBandAlert:
    def test_band_bleed_flagged_when_season_positive(self):
        df = _bets([(0.07, False, 40), (0.07, True, 10)])  # band -ROI, n=50
        season = {"roi": 6.0, "edge_buckets": _edge_bucket_stats(df)}
        rolling = {"n": 30, "roi": 6.0, "hit_rate": 0.6, "auc": 0.51}
        alerts = _check_alerts("1IOU", rolling, season)
        assert any("Mid-edge band" in a for a in alerts)

    def test_band_not_flagged_when_too_few_bets(self):
        df = _bets([(0.07, False, 10)])  # n=10 < BAND_FLAG_MIN_N
        season = {"roi": 6.0, "edge_buckets": _edge_bucket_stats(df)}
        rolling = {"n": 30, "roi": 6.0, "hit_rate": 0.6}
        assert not any("Mid-edge band" in a for a in _check_alerts("1IOU", rolling, season))


class TestAucDoesNotCryWolf:
    def test_low_auc_with_positive_roi_does_not_alert(self):
        rolling = {"n": AUC_ALERT_MIN_N + 10, "roi": 8.0, "hit_rate": 0.57, "auc": 0.49}
        assert not any("rank-ordering" in a for a in _check_alerts("1IOU", rolling))

    def test_low_auc_only_alerts_with_negative_roi_and_large_n(self):
        rolling = {"n": AUC_ALERT_MIN_N + 10, "roi": -25.0, "hit_rate": 0.40, "auc": 0.45}
        assert any("rank-ordering" in a for a in _check_alerts("1IOU", rolling))

    def test_low_auc_negative_roi_but_small_n_does_not_alert(self):
        rolling = {"n": 30, "roi": -25.0, "hit_rate": 0.40, "auc": 0.45}
        assert not any("rank-ordering" in a for a in _check_alerts("1IOU", rolling))
