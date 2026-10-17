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


# ---------------------------------------------------------------------------------------------------------------------
# Edge shrinkage and market-anchored blend (2026-10-16 quant audit).
# ---------------------------------------------------------------------------------------------------------------------
SHRINK_KNEE = 0.06
SHRINK_TAIL_SLOPE = 0.35
SHRINK_MAX_EFF_EDGE = 0.10


def shrink_edge(edge: float, knee: float = SHRINK_KNEE, tail_slope: float = SHRINK_TAIL_SLOPE,
                max_eff_edge: float = SHRINK_MAX_EFF_EDGE) -> float:
    """Concave shrinkage of a CLAIMED edge (fraction) before Kelly sizing: identity up to `knee`, `tail_slope` per unit
    above it, capped at `max_eff_edge`. Odd in `edge` (a negative edge is shrunk symmetrically, never amplified).

    Evidence: graded +EV alerts in the 12%+ edge bucket claimed 61.6% but hit 49.9% (n=2,474, ui_consistency_sweep
    handoff item 10), i.e. the biggest gaps are partly adverse selection. This only SIZES smaller; it does not drop bets.
    Mirror of beezy-vip/lib/ev-math.ts shrinkEdge()."""
    if edge is None or edge != edge:
        return 0.0
    mag = abs(float(edge))
    eff = mag if mag <= knee else knee + tail_slope * (mag - knee)
    eff = min(eff, max_eff_edge)
    return eff if edge >= 0 else -eff


# Blend weight c on (logit(p_model) - logit(p_market)), from the 2026-10-15 BettingPros walk-forward study
# (docs/analysis/bettingpros_model_vs_market_2026-10-15.md). c is a fit on the same test rows it describes (one parameter,
# date-clustered t in brackets) and is NOT a tradability result: NHL SOG priced at the best Under was ROI -0.08%.
# Anything not listed (every NFL stat, every unlisted stat) gets c=0, i.e. the market itself.
BLEND_C = {
    ("nba", "points"): 0.07,      # t=+2.99
    ("nba", "rebounds"): 0.19,    # t=+7.71
    ("nba", "assists"): 0.18,     # t=+7.65
    ("nba", "threes"): 0.28,      # t=+8.32
    ("nba", "pra"): 0.05,         # t=+2.52
    ("nhl", "sog"): 0.30,         # t=+4.9
}
BLEND_MAX_SHIFT = 0.08


def _logit(p: float) -> float:
    import math
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def market_blend_prob(p_mkt: float, p_model: float, sport: str, stat: str, a: float = 0.0, b: float = 1.0,
                      c: float | None = None, max_shift: float = BLEND_MAX_SHIFT) -> float:
    """Posterior P(over): logit(p) = a + b*logit(p_mkt) + c*(logit(p_model) - logit(p_mkt)), clamped to
    p_mkt +/- max_shift. c defaults to BLEND_C[(sport, stat)], else 0 (returns the market). Missing or non-finite model
    or market probability returns the market (or NaN if that is missing too) -- never a model-only price."""
    import math
    if p_mkt is None or not math.isfinite(float(p_mkt)):
        return float("nan")
    if p_model is None or not math.isfinite(float(p_model)):
        return float(p_mkt)
    if c is None:
        c = BLEND_C.get((str(sport).lower(), str(stat).lower()), 0.0)
    z = a + b * _logit(p_mkt) + c * (_logit(p_model) - _logit(p_mkt))
    p = 1.0 / (1.0 + math.exp(-z))
    return min(max(p, float(p_mkt) - max_shift), float(p_mkt) + max_shift)


def reset_cache() -> None:
    """Clear the process-local calibrator cache (tests)."""
    _CACHE.clear()
