"""
mlb_core.risk.gates -- Dynamic model-performance suppression gate.

Reads Gates/model_gates.json from GCS (or local in dev mode) and returns
whether a given system is currently suppressed by the rolling-performance gate.

The gate file is written by runners/monitor_performance.py after every
monitor run. It is NOT the static registry.log_only flag -- a system is
effectively log-only when EITHER registry.log_only OR is_suppressed() is True.

Fail-open contract: if the file is missing, unreadable, or the system is
absent, is_suppressed() returns False so betting is never accidentally halted
by a gate I/O error.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

GATE_FILE_KEY = "Gates/model_gates.json"


def is_suppressed(system: str) -> bool:
    """Return True if the system's rolling-performance gate is currently active.

    Checks registry force_gate first (human override always wins), then reads
    Gates/model_gates.json. Fails open on any read or parse error.
    """
    # Human override: registry.force_gate takes precedence over everything.
    try:
        from mlb_core.registry import SYSTEMS
        cfg = SYSTEMS.get(system)
        if cfg is not None and cfg.force_gate is not None:
            forced = cfg.force_gate.lower().strip()
            if forced == "on":
                logger.info("gates: %s force-suppressed via registry.force_gate", system)
                return True
            if forced == "off":
                logger.info("gates: %s force-enabled via registry.force_gate", system)
                return False
    except Exception as exc:
        logger.warning("gates: registry lookup failed: %s", exc)

    # Dynamic gate file from GCS.
    try:
        from mlb_core.storage import read_bytes, exists
        if not exists(GATE_FILE_KEY):
            return False  # first deploy or gate not yet written
        raw = read_bytes(GATE_FILE_KEY)
        data = json.loads(raw)
        sys_data = data.get("systems", {}).get(system, {})
        return bool(sys_data.get("suppressed", False))
    except Exception as exc:
        logger.warning("gates: failed to read %s -- failing open: %s", GATE_FILE_KEY, exc)
        return False


# ==============================================================================
# Module 9: 28-Day Rolling Live Decay Circuit Breaker (Pure Functions)
# ==============================================================================

import math
from typing import Any, Sequence
import numpy as np


class DecayBreakerDecision:
    """Decision from the 28-day rolling decay circuit breaker."""
    def __init__(self, action: str, effective_c_stat: float, reason: str = ""):
        self.action = action  # "ok" | "center" | "clamp"
        self.effective_c_stat = effective_c_stat
        self.reason = reason

    @property
    def needs_centering(self) -> bool:
        return self.action == "center"

    @property
    def is_clamped(self) -> bool:
        return self.action == "clamp"

    def __repr__(self) -> str:
        return f"DecayBreakerDecision(action={self.action!r}, effective_c_stat={self.effective_c_stat}, reason={self.reason!r})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.action == other
        if isinstance(other, DecayBreakerDecision):
            return self.action == other.action and math.isclose(self.effective_c_stat, other.effective_c_stat, abs_tol=1e-7)
        return False

    def __iter__(self):
        return iter((self.action, self.effective_c_stat))


def rolling_decay_status(
    ece_28d: float,
    delta_ll_28d: float,
    c_stat: float,
    *,
    ece_limit: float = 0.015,
    ll_limit: float = 0.002,
    centered: bool = False,
    **kwargs: Any,
) -> DecayBreakerDecision:
    """Evaluate 28-day live calibration decay status.

    Rule:
      1. If Delta LL > +0.002 persists (e.g. after centering has been applied or evaluated),
         clamp c_stat to 0.0 (action='clamp').
      2. If ECE > 0.015, apply 28-day rolling log-odds intercept centering (action='center').
      3. Otherwise healthy (action='ok').
    """
    is_centered = centered or kwargs.get("centered_applied", False) or kwargs.get("after_centering", False)

    # If centering was already applied and Delta LL > ll_limit persists -> clamp to 0.0
    if is_centered and delta_ll_28d > ll_limit:
        return DecayBreakerDecision(
            action="clamp",
            effective_c_stat=0.0,
            reason=f"Delta LL {delta_ll_28d:.4f} > {ll_limit:.4f} persists after centering; clamping c_stat to 0.0",
        )

    # If ECE > ece_limit -> center
    if ece_28d > ece_limit:
        return DecayBreakerDecision(
            action="center",
            effective_c_stat=c_stat,
            reason=f"ECE {ece_28d:.4f} > {ece_limit:.4f}; apply 28-day logit intercept centering",
        )

    # If Delta LL > ll_limit without prior centering but ECE is <= limit, persistent positive Delta LL still clamps
    if delta_ll_28d > ll_limit:
        return DecayBreakerDecision(
            action="clamp",
            effective_c_stat=0.0,
            reason=f"Delta LL {delta_ll_28d:.4f} > {ll_limit:.4f} indicates out-of-sample log-loss harm; clamping c_stat to 0.0",
        )

    return DecayBreakerDecision(
        action="ok",
        effective_c_stat=c_stat,
        reason="Model metrics within healthy bounds",
    )


def logit_intercept_centering(pairs: Sequence[tuple[float, float]]) -> float:
    """Compute the 28-day rolling log-odds intercept shift from (p_pred, y) pairs.

    Solves for alpha such that sum(sigmoid(logit(p_i) + alpha)) == sum(y_i).
    """
    if not pairs:
        return 0.0

    y_vals = [float(y) for _, y in pairs]
    p_vals = [min(max(float(p), 1e-6), 1.0 - 1e-6) for p, _ in pairs]

    target_sum = sum(y_vals)
    n = len(pairs)
    if target_sum <= 0:
        return -5.0
    if target_sum >= n:
        return 5.0

    logits = [math.log(p / (1.0 - p)) for p in p_vals]

    def f(alpha: float) -> float:
        return sum(1.0 / (1.0 + math.exp(-(l + alpha))) for l in logits) - target_sum

    lo, hi = -10.0, 10.0
    if f(lo) > 0:
        return lo
    if f(hi) < 0:
        return hi
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)


def apply_centering(p: float | np.ndarray | Sequence[float], shift: float) -> Any:
    """Apply log-odds intercept shift to probability p: sigmoid(logit(p) + shift)."""
    if isinstance(p, (int, float)):
        p_clamped = min(max(float(p), 1e-9), 1.0 - 1e-9)
        logit_p = math.log(p_clamped / (1.0 - p_clamped))
        return 1.0 / (1.0 + math.exp(-(logit_p + shift)))

    arr = np.asarray(p, dtype=float)
    arr_clamped = np.clip(arr, 1e-9, 1.0 - 1e-9)
    logits = np.log(arr_clamped / (1.0 - arr_clamped))
    res = 1.0 / (1.0 + np.exp(-(logits + shift)))
    if isinstance(p, list):
        return res.tolist()
    return res
