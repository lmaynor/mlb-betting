"""Tests for monitor_drift meta-flattening + PSI.

Covers the 2026-06-28 fix: the NRFI drift monitor must read the v18 ensemble meta,
whose feature_means/feature_stds are nested under sub_models (pitcher/lineup/
context). Before the fix it read top-level feature_stds -> {} -> checked nothing.

These exercise pure functions (numpy/pandas only) -- no GCS / xgboost needed.
"""
import numpy as np

from mlb.runners.monitor_drift import _flatten_meta_stats, _psi


def test_flatten_v18_ensemble_unions_submodels():
    meta = {
        "version": "v18",
        "sub_models": {
            "pitcher": {"feature_means": {"k_pct_L3": 0.22, "velo_mean_L3": 93.1},
                        "feature_stds":  {"k_pct_L3": 0.05, "velo_mean_L3": 1.8}},
            "lineup":  {"feature_means": {"platoon_edge": 0.01},
                        "feature_stds":  {"platoon_edge": 0.03}},
            "context": {"feature_means": {"temperature_f": 72.0},
                        "feature_stds":  {"temperature_f": 12.0}},
        },
    }
    means, stds, dists, is_ensemble = _flatten_meta_stats(meta)
    assert is_ensemble is True
    assert set(stds) == {"k_pct_L3", "velo_mean_L3", "platoon_edge", "temperature_f"}
    assert means["temperature_f"] == 72.0
    assert stds["platoon_edge"] == 0.03  # lineup features survive (the live signal)


def test_flatten_flat_meta_unchanged():
    meta = {"feature_means": {"a": 1.0, "b": 2.0}, "feature_stds": {"a": 0.5, "b": 0.7}}
    means, stds, dists, is_ensemble = _flatten_meta_stats(meta)
    assert is_ensemble is False
    assert stds == {"a": 0.5, "b": 0.7}
    assert means == {"a": 1.0, "b": 2.0}


def test_flatten_empty_meta():
    means, stds, dists, is_ensemble = _flatten_meta_stats({})
    assert (means, stds, dists, is_ensemble) == ({}, {}, {}, False)


def test_psi_identical_distribution_near_zero():
    rng = np.random.default_rng(0)
    x = rng.normal(0.0, 1.0, 5000)
    assert _psi(x, x) < 0.01


def test_psi_shifted_distribution_flags_significant():
    rng = np.random.default_rng(0)
    base    = rng.normal(0.0, 1.0, 5000)
    shifted = rng.normal(2.0, 1.0, 5000)
    assert _psi(base, shifted) > 0.25  # PSI_WARN_THRESHOLD


def test_psi_insufficient_data_is_nan():
    rng = np.random.default_rng(0)
    big = rng.normal(0.0, 1.0, 100)
    assert np.isnan(_psi(big, np.array([0.1, 0.2])))  # < 5 actual samples
