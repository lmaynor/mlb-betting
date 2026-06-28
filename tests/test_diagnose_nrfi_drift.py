"""Tests for the NRFI concept-drift attribution pure functions.

Exercises _safe_auc / auc_over_windows / classify_submodels / recommend with
synthetic data -- no xgboost / GCS (those imports are function-local in run()).
"""
import numpy as np
import pandas as pd

from mlb.runners import diagnose_nrfi_drift as d


def _synth(n, sep, seed=0):
    """n rows, balanced target, score separated by `sep` (0 => pure noise)."""
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n).astype(float)
    p = rng.normal(0.5, 0.1, n) + sep * (y - 0.5)
    return y, np.clip(p, 0.001, 0.999)


def test_safe_auc_signal_vs_noise():
    y, p = _synth(1000, sep=0.6)
    assert d._safe_auc(y, p) > 0.6
    yn, pn = _synth(1000, sep=0.0)
    assert 0.45 < d._safe_auc(yn, pn) < 0.55  # noise ~ 0.5


def test_safe_auc_insufficient_n_is_none():
    y, p = _synth(50, sep=0.6)  # below MIN_WINDOW_N (200)
    assert d._safe_auc(y, p) is None


def test_safe_auc_single_class_is_none():
    p = np.linspace(0, 1, 500)
    assert d._safe_auc(np.ones(500), p) is None


def test_auc_over_windows_buckets_by_month():
    y1, p1 = _synth(400, sep=0.7, seed=1)   # strong month
    y2, p2 = _synth(400, sep=0.0, seed=2)   # noise month
    df = pd.DataFrame({
        "game_date": list(pd.date_range("2026-04-01", periods=400, freq="h"))
                     + list(pd.date_range("2026-06-01", periods=400, freq="h")),
        "yrfi": np.concatenate([y1, y2]),
        "_stacked": np.concatenate([p1, p2]),
    })
    out = d.auc_over_windows(df, ["_stacked"])
    periods = list(out)
    assert periods == sorted(periods)  # chronological
    assert out["2026-04"]["_stacked"] > 0.6
    assert 0.45 < out["2026-06"]["_stacked"] < 0.55


def test_classify_submodels_statuses():
    live = {"pitcher": 0.51, "lineup": 0.59, "context": 0.535, "missing": None}
    oos  = {"pitcher": 0.55, "lineup": 0.589, "context": 0.52, "missing": 0.54}
    v = d.classify_submodels(live, oos)
    assert v["pitcher"]["status"] == "dead"          # <= 0.52
    assert v["lineup"]["status"] == "ok"             # > 0.55
    assert v["context"]["status"] == "weak"          # (0.52, 0.55]
    assert v["missing"]["status"] == "insufficient_data"
    assert v["lineup"]["gap"] == round(0.589 - 0.59, 4)


def test_recommend_lineup_only_path():
    verdict = {
        "pitcher": {"status": "dead"},
        "lineup":  {"status": "ok"},
        "context": {"status": "dead"},
    }
    msg = d.recommend(verdict, stacked_live=0.53)
    assert "lineup" in msg.lower()


def test_recommend_unsalvageable_path():
    verdict = {"pitcher": {"status": "dead"}, "lineup": {"status": "dead"}}
    msg = d.recommend(verdict, stacked_live=0.50)
    assert "not salvageable" in msg.lower()
