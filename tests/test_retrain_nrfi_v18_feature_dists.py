"""
Regression test for the 2026-08-16 audit's missing-feature_dists fix
(finding C3.4): retrain_nrfi_v18.py's 3 sub-models computed feature_means
and feature_stds but never feature_dists (the empirical-percentile PSI
drift-monitor input every other system's retrain script produces) -- a
real gap given this file's own docstring cites live AUC drift
(2024=0.5985, 2025=0.5876, 2026=0.5394) as the reason the ensemble
architecture exists in the first place.

Runs the real run() end-to-end with GCS I/O and _load_features mocked out,
on a small synthetic dataset, and inspects the meta bytes actually handed
to write_bytes() -- not a re-implementation of the fix's logic.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import json

import numpy as np
import pandas as pd
import pytest

import mlb.training.retrain_nrfi_v18 as v18


def _synthetic_nrfi_df(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-04-01", periods=n, freq="h")
    return pd.DataFrame({
        "game_date":      dates,
        "yrfi":           rng.integers(0, 2, size=n),
        # One real feature per sub-model group so none of the 3 hits the
        # "no available features" early-return.
        "velo_mean_L3":   rng.normal(93, 2, size=n),      # pitcher
        "platoon_edge":   rng.normal(0, 0.05, size=n),     # lineup
        "temperature_f":  rng.normal(72, 10, size=n),      # context
    })


@pytest.fixture
def _captured_meta(monkeypatch):
    """Run retrain_nrfi_v18.run() end-to-end (real XGBoost, real stacker)
    against a synthetic df, with every I/O boundary mocked, and hand back
    whatever meta bytes it tried to write."""
    monkeypatch.setenv("NRFI_SKIP_CV", "1")  # unrelated to this fix; keeps the test fast
    monkeypatch.setattr(v18, "_load_features", lambda: (_synthetic_nrfi_df(), None))
    monkeypatch.setattr("mlb_core.config.GCS_BUCKET", "fake-bucket-for-test")

    written = {}

    def _fake_upload_model(local_path, gcs_key):
        written[gcs_key] = "booster"

    def _fake_write_bytes(data, gcs_key):
        written[gcs_key] = data

    monkeypatch.setattr("mlb_core.storage.upload_model", _fake_upload_model)
    monkeypatch.setattr("mlb_core.storage.write_bytes", _fake_write_bytes)

    result = v18.run()
    assert result.get("status") == "ok", f"run() did not succeed: {result}"

    meta_bytes = written.get(v18.GCS_META_LATEST)
    assert meta_bytes is not None, "run() never wrote model_meta -- check the fixture's mocks"
    return json.loads(meta_bytes)


def test_every_submodel_has_nonempty_feature_dists(_captured_meta):
    sub_models = _captured_meta["sub_models"]
    assert set(sub_models) == set(v18.SUB_MODEL_ORDER)
    for name, info in sub_models.items():
        assert "feature_dists" in info, f"sub-model '{name}' meta has no feature_dists key at all"
        assert info["feature_dists"], f"sub-model '{name}' feature_dists is empty"


def test_feature_dists_has_the_psi_percentile_shape(_captured_meta):
    sub_models = _captured_meta["sub_models"]
    expected_keys = {"p5", "p10", "p25", "p50", "p75", "p90", "p95", "prop_1"}
    for name, info in sub_models.items():
        for feature, dist in info["feature_dists"].items():
            assert set(dist) == expected_keys, (
                f"sub-model '{name}' feature '{feature}' feature_dists keys "
                f"{set(dist)} don't match the PSI monitor's expected shape {expected_keys}"
            )
