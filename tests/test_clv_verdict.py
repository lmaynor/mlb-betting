"""Tests for the CLV promotion-scorecard verdict (A4).

Pure logic -- no GCS / DB. Defaults: promote bar = +2.0% mean, t>2.0, n>=100.
"""
from mlb_core.risk.clv import clv_verdict


def test_insufficient_below_min_n():
    v = clv_verdict(mean_clv=5.0, clv_tstat=4.0, clv_n=80)
    assert v["clv_status"] == "insufficient"
    assert v["clv_promote_ready"] is False


def test_insufficient_none_mean():
    v = clv_verdict(mean_clv=None, clv_tstat=None, clv_n=150)
    assert v["clv_status"] == "insufficient"
    assert v["clv_promote_ready"] is False


def test_ready_clears_full_bar():
    v = clv_verdict(mean_clv=2.5, clv_tstat=3.1, clv_n=150)
    assert v["clv_status"] == "ready"
    assert v["clv_promote_ready"] is True


def test_promising_mean_ok_but_tstat_fails():
    v = clv_verdict(mean_clv=2.5, clv_tstat=1.5, clv_n=150)
    assert v["clv_status"] == "promising"
    assert v["clv_promote_ready"] is False


def test_promising_positive_but_below_mean_bar():
    v = clv_verdict(mean_clv=1.0, clv_tstat=3.0, clv_n=150)
    assert v["clv_status"] == "promising"
    assert v["clv_promote_ready"] is False


def test_negative_significant():
    v = clv_verdict(mean_clv=-1.5, clv_tstat=-3.0, clv_n=150)
    assert v["clv_status"] == "negative"
    assert v["clv_promote_ready"] is False


def test_negative_not_significant_is_flat():
    v = clv_verdict(mean_clv=-0.2, clv_tstat=-0.5, clv_n=150)
    assert v["clv_status"] == "flat"


def test_flat_near_zero():
    v = clv_verdict(mean_clv=0.0, clv_tstat=0.0, clv_n=150)
    assert v["clv_status"] == "flat"
    assert v["clv_promote_ready"] is False
