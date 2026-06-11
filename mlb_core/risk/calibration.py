"""
mlb_core.risk.calibration -- prediction calibration applied PRE-edge.

The /edge-analysis diagnostic (2026-06-11) showed every system is systematically
overconfident, and the overconfidence scales with the gap to the line: the
largest apparent edges are dominated by model error (winner's curse), with
realized_cal_err reaching -0.31 in the >=20% edge bucket (model says ~0.77,
wins ~0.46). Calibrating model_prob against REALIZED OUTCOMES (not the market)
before computing edge collapses those fake gaps below min_edge while leaving
honest small/moderate gaps intact.

This module loads per-system isotonic calibrators fit by
training/fit_prediction_calibrators.py and applies them inside each runner,
before edge = calibrated_prob - fair_prob.

SAFE ROLLOUT: apply() returns (prob, was_calibrated). When no calibrator exists
for a system (or any I/O/parse error), it returns the prob UNCHANGED and
was_calibrated=False -- so a system behaves exactly as before until its
calibrator is fit. Runners apply the interim EDGE_CAP only when was_calibrated
is True, so the cap never acts on a raw (uncalibrated) edge.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Interim adverse-selection guard: skip a bet whose POST-calibration edge still
# exceeds this. The >=20% bucket is where adverse selection concentrates; a gap
# that survives calibration that large is almost always residual overconfidence.
EDGE_CAP = float(os.getenv("EDGE_CAP", "0.20"))

CALIBRATION_PREFIX = "Calibration"

# Process-local cache: {system: (calibrator_or_None)}. Reset per process; runners
# are short-lived jobs so staleness is not a concern.
_CACHE: dict = {}


def _calibrator_key(system: str) -> str:
    return f"{CALIBRATION_PREFIX}/{system}_prediction_calibrator.pkl"


def _load(system: str):
    """Load + cache the isotonic calibrator for a system. None if absent/error."""
    if system in _CACHE:
        return _CACHE[system]
    calibrator = None
    try:
        import pickle
        from mlb_core.storage import read_bytes, exists
        key = _calibrator_key(system)
        if exists(key):
            calibrator = pickle.loads(read_bytes(key))
            logger.info("calibration: loaded calibrator for %s", system)
    except Exception as exc:
        logger.warning("calibration: load failed for %s -- passthrough: %s", system, exc)
        calibrator = None
    _CACHE[system] = calibrator
    return calibrator


def apply(system: str, prob: float) -> tuple[float, bool]:
    """Return (calibrated_prob, was_calibrated).

    Fail-open: no calibrator or any error -> (prob, False), unchanged.
    Calibrators are fit with out_of_bounds='clip' so any input in [0,1] is safe.
    """
    if prob is None:
        return prob, False
    calibrator = _load(system)
    if calibrator is None:
        return float(prob), False
    try:
        p = float(calibrator.predict([float(prob)])[0])
        # Guard against degenerate 0/1 saturation inflating downstream edge.
        p = min(max(p, 0.01), 0.99)
        return p, True
    except Exception as exc:
        logger.warning("calibration: predict failed for %s -- passthrough: %s", system, exc)
        return float(prob), False


def reset_cache() -> None:
    """Clear the process-local calibrator cache (tests)."""
    _CACHE.clear()
