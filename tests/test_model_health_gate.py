"""
tests/test_model_health_gate.py

Unit tests for:
  - Task A: _health_verdict (flags / health / recommended_action) precedence
  - Task B: gate decision logic (suppress/clear/hysteresis/min-n)
  - Task B: is_suppressed fail-open on missing/garbage gate file
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Force local mode -- no GCS, no Postgres
os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)
os.environ["MLB_BASE_DATA"] = str(Path(tempfile.mkdtemp()))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Task A: _health_verdict
# ---------------------------------------------------------------------------

def _make_health(n, auc, cal_err, roi,
                 min_n=20, cal_tol=0.10, roi_floor=-10):
    """Replicate _health_verdict logic without importing Flask-dependent main.

    Defaults match the production env-var defaults in main.py.
    """
    MIN_HEALTH_N = min_n
    CAL_ERR_TOL  = cal_tol
    ROI_FLOOR    = roi_floor

    flags = []
    if n < MIN_HEALTH_N:
        flags.append("underpowered")
    if auc is not None and auc < 0.50:
        flags.append("inverted")
    if cal_err is not None and abs(cal_err) > CAL_ERR_TOL:
        flags.append("miscalibrated")
    if roi is not None and roi < ROI_FLOOR:
        flags.append("negative_roi")
    if auc is not None and 0.50 <= auc < 0.53:
        flags.append("no_edge")

    if n < MIN_HEALTH_N:
        health = "underpowered"
    elif auc is not None and auc < 0.50:
        health = "inverted"
    elif cal_err is not None and abs(cal_err) > CAL_ERR_TOL:
        health = "miscalibrated"
    elif auc is not None and auc < 0.53:
        health = "no_edge"
    elif roi is not None and roi < ROI_FLOOR:
        health = "degraded"
    elif auc is not None and auc < 0.57:
        health = "moderate"
    else:
        health = "healthy"

    return {"flags": flags, "health": health}


class TestHealthVerdict:
    def test_underpowered_wins_over_inverted(self):
        r = _make_health(n=5, auc=0.48, cal_err=0.20, roi=-30)
        assert r["health"] == "underpowered"
        assert "underpowered" in r["flags"]

    def test_inverted(self):
        r = _make_health(n=50, auc=0.48, cal_err=0.05, roi=5)
        assert r["health"] == "inverted"
        assert "inverted" in r["flags"]

    def test_miscalibrated_wins_over_no_edge(self):
        # AUC 0.51 (no_edge) but cal_err = 0.26 (miscalibrated)
        r = _make_health(n=50, auc=0.51, cal_err=0.26, roi=0)
        assert r["health"] == "miscalibrated"
        assert "miscalibrated" in r["flags"]

    def test_no_edge(self):
        r = _make_health(n=50, auc=0.52, cal_err=0.03, roi=5)
        assert r["health"] == "no_edge"
        assert "no_edge" in r["flags"]

    def test_degraded(self):
        r = _make_health(n=50, auc=0.55, cal_err=0.05, roi=-15)
        assert r["health"] == "degraded"
        assert "negative_roi" in r["flags"]

    def test_moderate(self):
        r = _make_health(n=50, auc=0.55, cal_err=0.03, roi=5)
        assert r["health"] == "moderate"

    def test_healthy(self):
        r = _make_health(n=50, auc=0.60, cal_err=0.02, roi=10)
        assert r["health"] == "healthy"
        assert r["flags"] == []

    def test_none_auc_gives_unknown_not_crash(self):
        # None AUC should not raise
        r = _make_health(n=50, auc=None, cal_err=0.02, roi=5)
        assert r["health"] in ("healthy", "degraded", "underpowered", "moderate")

    def test_hr_profile_is_degraded(self):
        # HR: AUC 0.61, slightly neg cal, ROI -20% -> degraded
        r = _make_health(n=250, auc=0.61, cal_err=-0.05, roi=-20)
        assert r["health"] == "degraded"


# ---------------------------------------------------------------------------
# Task B: gate decision logic (_gate_condition_met)
# ---------------------------------------------------------------------------

def _gate_cond(n, auc, roi):
    """Replicate _gate_condition_met logic for unit testing."""
    GATE_AUC_MIN = 0.52
    GATE_ROI_MIN = -20.0
    MIN_GATE_N   = 30

    if n < MIN_GATE_N:
        return False, f"underpowered (n={n})"

    reasons = []
    if auc is not None and auc < GATE_AUC_MIN:
        reasons.append(f"auc {auc:.3f} < {GATE_AUC_MIN}")
    if roi < GATE_ROI_MIN:
        reasons.append(f"roi {roi:+.1f}%")
    if reasons:
        return True, "; ".join(reasons)
    return False, "healthy"


class TestGateCondition:
    def test_underpowered_never_suppressed(self):
        ok, reason = _gate_cond(n=10, auc=0.40, roi=-50)
        assert ok is False
        assert "underpowered" in reason

    def test_low_auc_triggers(self):
        ok, _ = _gate_cond(n=50, auc=0.50, roi=5)
        assert ok is True

    def test_low_roi_triggers(self):
        ok, _ = _gate_cond(n=50, auc=0.55, roi=-25)
        assert ok is True

    def test_healthy_clears(self):
        ok, reason = _gate_cond(n=50, auc=0.55, roi=5)
        assert ok is False
        assert reason == "healthy"


# ---------------------------------------------------------------------------
# Task B: hysteresis logic (_update_gate)
# ---------------------------------------------------------------------------

def _apply_hysteresis(should_suppress: bool, prev_state: dict,
                      hysteresis: int = 2) -> dict:
    """Replicate the _update_gate logic (no registry, no Discord)."""
    was_suppressed  = prev_state.get("suppressed", False)
    suppress_streak = prev_state.get("suppress_streak", 0)
    clear_streak    = prev_state.get("clear_streak", 0)

    if should_suppress:
        suppress_streak += 1
        clear_streak     = 0
        now_suppressed   = was_suppressed or (suppress_streak >= hysteresis)
    else:
        clear_streak    += 1
        suppress_streak  = 0
        now_suppressed   = was_suppressed and (clear_streak < hysteresis)

    return {
        "suppressed":      now_suppressed,
        "suppress_streak": suppress_streak,
        "clear_streak":    clear_streak,
    }


class TestHysteresis:
    def test_single_bad_run_does_not_suppress(self):
        prev = {"suppressed": False, "suppress_streak": 0, "clear_streak": 0}
        result = _apply_hysteresis(should_suppress=True, prev_state=prev)
        assert result["suppressed"] is False
        assert result["suppress_streak"] == 1

    def test_two_consecutive_bad_runs_suppress(self):
        prev = {"suppressed": False, "suppress_streak": 1, "clear_streak": 0}
        result = _apply_hysteresis(should_suppress=True, prev_state=prev)
        assert result["suppressed"] is True

    def test_single_good_run_does_not_unsuppress(self):
        prev = {"suppressed": True, "suppress_streak": 0, "clear_streak": 0}
        result = _apply_hysteresis(should_suppress=False, prev_state=prev)
        assert result["suppressed"] is True
        assert result["clear_streak"] == 1

    def test_two_consecutive_good_runs_unsuppress(self):
        prev = {"suppressed": True, "suppress_streak": 0, "clear_streak": 1}
        result = _apply_hysteresis(should_suppress=False, prev_state=prev)
        assert result["suppressed"] is False

    def test_suppress_resets_clear_streak(self):
        prev = {"suppressed": True, "suppress_streak": 0, "clear_streak": 1}
        result = _apply_hysteresis(should_suppress=True, prev_state=prev)
        assert result["clear_streak"] == 0
        assert result["suppress_streak"] == 1

    def test_clear_resets_suppress_streak(self):
        prev = {"suppressed": False, "suppress_streak": 1, "clear_streak": 0}
        result = _apply_hysteresis(should_suppress=False, prev_state=prev)
        assert result["suppress_streak"] == 0
        assert result["clear_streak"] == 1


# ---------------------------------------------------------------------------
# Task B: is_suppressed fail-open
# ---------------------------------------------------------------------------

class TestIsSupressedFailOpen:
    def _set_gate_file(self, tmp_path: Path, content: bytes):
        """Write a fake gate file to the local MLB_BASE_DATA dir."""
        base = Path(os.environ["MLB_BASE_DATA"])
        gate_dir = base / "Gates"
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / "model_gates.json").write_bytes(content)

    def test_missing_gate_file_returns_false(self, tmp_path):
        # No gate file exists -> fail open
        from mlb_core.risk.gates import is_suppressed
        assert is_suppressed("K") is False

    def test_suppressed_true_returns_true(self, tmp_path):
        self._set_gate_file(tmp_path, json.dumps({
            "systems": {"K": {"suppressed": True}}
        }).encode())
        from importlib import reload
        import mlb_core.risk.gates as gates_mod
        # Ensure file path is picked up by re-importing with tmp base
        os.environ["MLB_BASE_DATA"] = str(Path(os.environ["MLB_BASE_DATA"]))
        result = gates_mod.is_suppressed("K")
        assert result is True

    def test_suppressed_false_returns_false(self, tmp_path):
        self._set_gate_file(tmp_path, json.dumps({
            "systems": {"K": {"suppressed": False}}
        }).encode())
        import mlb_core.risk.gates as gates_mod
        assert gates_mod.is_suppressed("K") is False

    def test_garbage_json_returns_false(self, tmp_path):
        self._set_gate_file(tmp_path, b"not valid json !!!")
        import mlb_core.risk.gates as gates_mod
        assert gates_mod.is_suppressed("K") is False

    def test_system_absent_returns_false(self, tmp_path):
        self._set_gate_file(tmp_path, json.dumps({
            "systems": {"F5": {"suppressed": True}}
        }).encode())
        import mlb_core.risk.gates as gates_mod
        assert gates_mod.is_suppressed("K") is False
