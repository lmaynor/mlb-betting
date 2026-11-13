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


# ---------------------------------------------------------------------------------------------------------------------
# Pre-blend calibration + winner's-curse concave shrinkage (Argon 2026-11-10: model-improvement D2 and
# data-methods D4). D4: "Fit isotonic / temperature scaling PRE-BLEND to ECE <= 0.015" and the concave
# damping below; D2: "Per-stat isotonic calibration ECE <= 0.015, concave damping above 8%".
# ---------------------------------------------------------------------------------------------------------------------
ECE_TARGET = 0.015


def expected_calibration_error(y, p, n_bins: int = 10) -> float:
    """Expected Calibration Error with equal-count (quantile) bins: sum over bins of
    (bin share) * |mean outcome - mean predicted|. Same estimator as mlb_core.odds.props_residual.ece.
    NaN when there are too few rows to bin honestly (< 5 per bin)."""
    import numpy as np
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) < n_bins * 5:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    return float(sum(len(i) / len(y) * abs(y[i].mean() - p[i].mean()) for i in np.array_split(order, n_bins)))


class IsotonicCalibrator:
    """Isotonic (monotone) probability calibrator fit by the pool-adjacent-violators algorithm.

    Duck-types the calibrator contract of apply() above (predict(list) -> list) so a fitted instance
    can serve either path. Pure numpy; the artifact is two small arrays (thresholds, values)."""

    def __init__(self):
        self.x_: object = None      # sorted training probabilities (bin edges)
        self.y_: object = None      # fitted (monotone) values at x_

    def fit(self, p, y) -> "IsotonicCalibrator":
        import numpy as np
        p = np.asarray(p, dtype=float)
        y = np.asarray(y, dtype=float)
        # Aggregate to UNIQUE x values first (weighted mean outcome per distinct probability):
        # without this, rows sharing one x form separate PAVA blocks and prediction at that x
        # returns an arbitrary block's mean instead of the pooled rate at that probability.
        xs, inv, counts = np.unique(p, return_inverse=True, return_counts=True)
        sums = np.bincount(inv, weights=y, minlength=len(xs))
        ys = sums / counts
        wts = counts.astype(float)
        # Weighted PAVA: pool adjacent blocks whose means violate monotonicity.
        stack_x, stack_v, stack_w = [], [], []
        for idx in range(len(xs)):
            cx, cv, cw = xs[idx], ys[idx], wts[idx]
            while stack_v and stack_v[-1] > cv:
                _px, pv, pw = stack_x.pop(), stack_v.pop(), stack_w.pop()
                cv = (cv * cw + pv * pw) / (cw + pw)
                cw = cw + pw
                # the merged block's right edge stays cx: x are processed in increasing
                # order, so the current segment's x is the merged block's largest x.
            stack_x.append(cx)
            stack_v.append(cv)
            stack_w.append(cw)
        self.x_ = np.asarray(stack_x, dtype=float)
        self.y_ = np.asarray(stack_v, dtype=float)
        return self

    def predict(self, probs):
        import numpy as np
        if self.x_ is None:
            raise RuntimeError("IsotonicCalibrator.predict before fit")
        p = np.asarray(probs, dtype=float)
        idx = np.searchsorted(self.x_, p, side="left")
        idx = np.clip(idx, 0, len(self.y_) - 1)
        out = self.y_[idx]
        return out.tolist() if np.ndim(probs) else float(out)


def fit_pre_blend_calibrator(y_cal, p_cal) -> IsotonicCalibrator:
    """Fit the pre-blend isotonic calibrator on a CALIBRATION slice (never the rows being scored)."""
    return IsotonicCalibrator().fit(p_cal, y_cal)


def calibration_report(y_val, p_val, calibrator=None) -> dict:
    """ECE before / after calibration on a VALIDATION slice, against ECE_TARGET (0.015).

    Honest reporting contract: `meets_target` is computed from the measured validation ECE, never
    assumed; when the slice is too small to bin, ece is NaN and meets_target is False."""
    import numpy as np
    p_val = np.asarray(p_val, dtype=float)
    ece_before = expected_calibration_error(y_val, p_val)
    out = {"n": int(len(p_val)), "ece_before": ece_before, "ece_after": ece_before,
           "ece_target": ECE_TARGET, "meets_target": bool(ece_before == ece_before and ece_before <= ECE_TARGET)}
    if calibrator is not None:
        p_cal = np.asarray(calibrator.predict(list(p_val)), dtype=float)
        ece_after = expected_calibration_error(y_val, p_cal)
        out.update(ece_after=ece_after,
                   meets_target=bool(ece_after == ece_after and ece_after <= ECE_TARGET))
    return out


# Per-(sport, stat) pre-blend calibrators, registered by the training / refit job that fit them.
# EMPTY by default: with nothing registered, pre_blend_calibrate() is a passthrough and every serving
# path behaves exactly as before (the same fail-open contract as apply() above).
_PRE_BLEND: dict = {}


def register_pre_blend_calibrator(sport: str, stat: str, calibrator) -> None:
    _PRE_BLEND[(str(sport).lower(), str(stat).lower())] = calibrator


def clear_pre_blend_calibrators() -> None:
    _PRE_BLEND.clear()


def pre_blend_calibrate(prob, sport: str | None, stat: str | None) -> tuple:
    """(calibrated_prob, was_calibrated): apply the registered pre-blend calibrator for (sport, stat)
    to a MODEL probability BEFORE it enters the market blend. Passthrough (prob, False) when no
    calibrator is registered or on any error -- fail-open, like apply()."""
    if prob is None or sport is None or stat is None:
        return prob, False
    calibrator = _PRE_BLEND.get((str(sport).lower(), str(stat).lower()))
    if calibrator is None:
        return float(prob), False
    try:
        p = float(calibrator.predict([float(prob)])[0])
        return min(max(p, 0.01), 0.99), True
    except Exception as exc:
        logger.warning("calibration: pre-blend predict failed for %s/%s -- passthrough: %s", sport, stat, exc)
        return float(prob), False


# Winner's-curse concave shrinkage (Argon data-methods D4, exact form):
#     edge_shrunk = edge_blend * exp(-max(0, |gap_raw| - 0.08) / 0.04)
# gap_raw is the RAW model-vs-market gap (probability points) before blending. At |gap| <= 0.08 the
# factor is exactly 1 (no shrinkage); above it the claimed blended edge decays exponentially with a
# 0.04 scale. Distinct from shrink_edge() above (the older linear knee for Kelly sizing): this one is
# the D4 formula, applied to the blended edge in the picks path.
WINNERS_CURSE_KNEE = 0.08
WINNERS_CURSE_TAU = 0.04


def concave_shrink_edge(edge_blend: float, gap_raw: float, knee: float = WINNERS_CURSE_KNEE,
                        tau: float = WINNERS_CURSE_TAU) -> float:
    """edge_blend * exp(-max(0, |gap_raw| - knee) / tau), exactly the Argon D4 form."""
    import math
    if edge_blend is None or edge_blend != edge_blend:
        return 0.0
    if gap_raw is None or gap_raw != gap_raw:
        return float(edge_blend)
    excess = max(0.0, abs(float(gap_raw)) - float(knee))
    return float(edge_blend) * math.exp(-excess / float(tau))


def reset_cache() -> None:
    """Clear the process-local calibrator cache (tests)."""
    _CACHE.clear()
