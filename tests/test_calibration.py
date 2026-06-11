"""
tests/test_calibration.py -- prediction calibration helper (Task #3).

Covers fail-open behavior, passthrough when no calibrator, application of a
calibrator, and the (prob, was_calibrated) contract that gates the EDGE_CAP.
Uses a duck-typed stub calibrator so sklearn is not required to run the tests.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)
os.environ["MLB_BASE_DATA"] = str(Path(tempfile.mkdtemp()))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Stub:
    """Duck-typed calibrator: deflates by a fixed factor (overconfidence fix)."""
    def __init__(self, factor=0.6):
        self.factor = factor

    def predict(self, xs):
        return [x * self.factor for x in xs]


class _Boom:
    def predict(self, xs):
        raise RuntimeError("predict exploded")


@pytest.fixture(autouse=True)
def _clear_cache():
    from mlb_core.risk import calibration
    calibration.reset_cache()
    yield
    calibration.reset_cache()


class TestCalibrationApply:
    def test_no_calibrator_is_passthrough(self):
        from mlb_core.risk import calibration
        prob, was = calibration.apply("NOPE", 0.77)
        assert prob == 0.77
        assert was is False

    def test_applies_when_present(self):
        from mlb_core.risk import calibration
        calibration._CACHE["K"] = _Stub(factor=0.6)
        prob, was = calibration.apply("K", 0.80)
        assert was is True
        assert abs(prob - 0.48) < 1e-9   # 0.80 * 0.6

    def test_overconfidence_pulled_down(self):
        # The /edge-analysis case: model says 0.77, calibrator deflates it.
        from mlb_core.risk import calibration
        calibration._CACHE["F5"] = _Stub(factor=0.6)
        prob, was = calibration.apply("F5", 0.77)
        assert was is True
        assert prob < 0.77

    def test_clamps_to_unit_interval(self):
        from mlb_core.risk import calibration
        calibration._CACHE["X"] = _Stub(factor=10.0)  # would overshoot 1.0
        prob, was = calibration.apply("X", 0.5)
        assert was is True
        assert prob <= 0.99

    def test_none_prob_passthrough(self):
        from mlb_core.risk import calibration
        prob, was = calibration.apply("K", None)
        assert prob is None
        assert was is False

    def test_predict_error_fails_open(self):
        from mlb_core.risk import calibration
        calibration._CACHE["K"] = _Boom()
        prob, was = calibration.apply("K", 0.7)
        assert prob == 0.7
        assert was is False

    def test_edge_cap_default(self):
        from mlb_core.risk import calibration
        assert calibration.EDGE_CAP == 0.20


class TestEdgeCapInteraction:
    """The runner rule: cap only fires when was_calibrated is True."""

    def _capped(self, edge, was_calibrated, cap=0.20):
        return was_calibrated and edge > cap

    def test_uncalibrated_never_capped(self):
        # Big raw edge but no calibrator -> NOT capped (system behaves as before)
        assert self._capped(edge=0.35, was_calibrated=False) is False

    def test_calibrated_big_edge_capped(self):
        assert self._capped(edge=0.35, was_calibrated=True) is True

    def test_calibrated_small_edge_not_capped(self):
        assert self._capped(edge=0.08, was_calibrated=True) is False
