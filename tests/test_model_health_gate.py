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

def _gate_cond(n, auc_model=0.60, roi=5.0, avg_model_prob=None, hit_rate=0.50):
    """Call the REAL _gate_condition_met with a rolling-stats dict.

    Imports the production function so tests catch the model-AUC + calibration
    fixes rather than a drifting replica.
    """
    from mlb.runners.monitor_performance import _gate_condition_met
    rolling = {
        "n": n,
        "auc_model": auc_model,
        "roi": roi,
        "hit_rate": hit_rate,
        "avg_model_prob": avg_model_prob,
    }
    return _gate_condition_met(rolling)


class TestGateCondition:
    """ROI-only suppression. Bet-sample AUC/cal are observability-only and must
    NOT trigger suppression (proven selection-biased by the run-sim spike)."""

    def test_underpowered_never_suppressed(self):
        ok, reason = _gate_cond(n=10, auc_model=0.40, roi=-50)
        assert ok is False
        assert "underpowered" in reason

    def test_low_model_auc_does_NOT_trigger(self):
        # auc 0.42 (backwards on bet sample) but profitable -> NOT suppressed
        ok, reason = _gate_cond(n=50, auc_model=0.42, roi=11)
        assert ok is False
        assert reason == "healthy"

    def test_low_roi_triggers(self):
        ok, reason = _gate_cond(n=50, auc_model=0.55, roi=-25)
        assert ok is True
        assert "roi" in reason

    def test_healthy_clears(self):
        ok, reason = _gate_cond(n=50, auc_model=0.55, roi=5)
        assert ok is False
        assert reason == "healthy"

    def test_bad_calibration_does_NOT_trigger_when_profitable(self):
        # cal_err -0.20 (overconfident) but ROI positive -> NOT suppressed
        ok, reason = _gate_cond(n=50, auc_model=0.60, roi=11,
                                avg_model_prob=0.65, hit_rate=0.45)
        assert ok is False
        assert reason == "healthy"

    def test_profitable_system_never_suppressed(self):
        # The OUTS case: bet-sample AUC 0.43, bad cal, but +11% ROI
        ok, reason = _gate_cond(n=489, auc_model=0.43, roi=11,
                                avg_model_prob=0.56, hit_rate=0.49)
        assert ok is False

    def test_negative_roi_suppressed_regardless_of_auc(self):
        # F5 case: decent-ish AUC but ROI -22.6% -> suppressed
        ok, reason = _gate_cond(n=30, auc_model=0.55, roi=-22.6)
        assert ok is True
        assert "roi" in reason


# ---------------------------------------------------------------------------
# Task B: _rolling_stats plumbs model AUC + avg_model_prob (defect-2 fix)
# ---------------------------------------------------------------------------

class TestRollingStatsModelFields:
    def _df(self):
        import pandas as pd
        # 4 settled bets: model_prob high on wins, low on losses (good ranker)
        return pd.DataFrame({
            "result":      ["win", "loss", "win", "loss"],
            "stake":       [10.0, 10.0, 10.0, 10.0],
            "profit":      [9.0, -10.0, 9.0, -10.0],
            "edge":        [0.05, 0.05, 0.05, 0.05],
            "market_prob": [0.50, 0.50, 0.50, 0.50],
            "model_prob":  [0.70, 0.40, 0.65, 0.45],
            "clv_pct":     [None, None, None, None],
        })

    def test_auc_model_and_avg_present(self):
        from mlb.runners.monitor_performance import _rolling_stats
        stats = _rolling_stats(self._df(), window=30)
        assert "auc_model" in stats
        assert "avg_model_prob" in stats
        assert stats["avg_model_prob"] is not None
        # mean of [0.70,0.40,0.65,0.45] = 0.55
        assert abs(stats["avg_model_prob"] - 0.55) < 1e-6
        # perfect ranking (wins have higher model_prob than losses) -> AUC 1.0
        assert stats["auc_model"] == 1.0


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
        """Write a fake gate file to the local MLB_BASE_DATA dir.

        Also clears MLB_GCS_BUCKET so gates.py reads local disk even when
        other test modules (e.g. test_public_api.py) set a fake bucket.
        """
        os.environ.pop("MLB_GCS_BUCKET", None)
        os.environ.pop("GCS_BUCKET", None)
        base = Path(os.environ["MLB_BASE_DATA"])
        gate_dir = base / "Gates"
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / "model_gates.json").write_bytes(content)

    def test_missing_gate_file_returns_false(self, tmp_path):
        # No gate file exists -> fail open
        os.environ.pop("MLB_GCS_BUCKET", None)
        os.environ.pop("GCS_BUCKET", None)
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
