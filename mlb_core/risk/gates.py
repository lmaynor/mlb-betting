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
