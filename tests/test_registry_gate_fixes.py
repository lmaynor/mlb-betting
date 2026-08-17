"""
tests/test_registry_gate_fixes.py -- Regression pins for the 2026-08-16
audit's registry/gate fixes (findings A8, A9, A10). These are cheap
tripwires against silent re-regression, not full functional tests.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
from mlb_core.registry import SYSTEMS, CANONICAL_ORDER, active_systems
from mlb_core.risk.gates import is_suppressed


def test_outs_has_its_own_model_artifact_not_ks():
    """Finding A8: OUTS previously pointed at K's model artifact, so no
    health check ever verified xgb_outs_v1.json exists."""
    assert SYSTEMS["OUTS"].model_artifact == "OUTS_Pro_System/models/xgb_outs_v1.json"
    assert SYSTEMS["OUTS"].model_artifact != SYSTEMS["K"].model_artifact


def test_pitcher_er_and_f1h_have_registry_entries():
    """Finding A9: PITCHER_ER and F1H previously had no registry entry at
    all, so is_suppressed() could never reflect real state for either."""
    assert "PITCHER_ER" in SYSTEMS
    assert "F1H" in SYSTEMS
    assert "PITCHER_ER" in CANONICAL_ORDER
    assert "F1H" in CANONICAL_ORDER
    assert "PITCHER_ER" in active_systems()
    assert "F1H" in active_systems()


def test_f1h_force_suppressed_matching_its_parent_f5():
    """F1H is a scalar proxy off F5, which is itself force_gate='on' for a
    documented no-live-edge finding -- F1H should inherit that, not be left
    to a dynamic gate that was never actually reachable before this fix."""
    assert is_suppressed("F1H") is True
    assert is_suppressed("F5") is True


def test_pitcher_er_log_only_flag_defaults_true():
    """Finding A9: run_k.py had zero hardcoded safety net for PITCHER_ER
    (unlike F1H's LOG_ONLY_SYSTEMS in run_f5.py) despite CONTEXT.md
    documenting it as log-only pending ~100-settled-bet validation."""
    from mlb.runners.run_k import PITCHER_ER_LOG_ONLY
    assert PITCHER_ER_LOG_ONLY is True


def test_f1h_log_only_set_matches_registry_gate():
    from mlb.runners.run_f5 import LOG_ONLY_SYSTEMS
    assert "F1H" in LOG_ONLY_SYSTEMS
