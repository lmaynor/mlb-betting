"""Tests for MOD-04 Cluster (e) D3 serving-path gate: gate remains shut."""
from ev import daily_projections as dp


def _sample_nfl_row(stat: str, engine: str) -> dict:
    return {
        "sport": "nfl",
        "stat": stat,
        "player": "Test Receiver",
        "team": "KC",
        "opp": "BUF",
        "game_key": "nfl:2026-11-29:BUF@KC",
        "has_line": True,
        "line": 5.5,
        "engine": engine,
        "p_over_model": 0.56,
        "p_over_blend": 0.50,
    }


def test_d3_gate_still_drops_hierarchical_nfl_and_keeps_legacy_ewm():
    hier_row = _sample_nfl_row("receptions", "hierarchical_v1")
    combo_row = _sample_nfl_row("rec_yds", "empirical_ewm_v2+hierarchical_v1")
    legacy_row = _sample_nfl_row("pass_yds", "empirical_ewm_v2")

    assert dp.is_hierarchical_nfl_row(hier_row) is True
    assert dp.is_hierarchical_nfl_row(combo_row) is True
    assert dp.is_hierarchical_nfl_row(legacy_row) is False

    served = dp.live_served_rows([hier_row, combo_row, legacy_row])
    assert served == [legacy_row]
