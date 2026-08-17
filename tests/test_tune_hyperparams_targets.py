"""
Regression test for the 2026-08-16 audit's F5 tune-target fix (finding
C3.5): tune_hyperparams.py's SYSTEM_CONFIG and registry.py's SystemConfig
both said F5's target column was "home_win" -- it's actually
"home_wins_f5" (retrain_f5_v5.py's own TARGET constant). A same-file diff
between tune_hyperparams.py and registry.py alone would never catch this
since both were wrong in the same way; this test checks both against the
actual retrain script's target instead.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
from mlb.training.tune_hyperparams import SYSTEM_CONFIG
from mlb_core.registry import SYSTEMS
from mlb.training.retrain_f5_v5 import TARGET as F5_REAL_TARGET
from mlb.training.retrain_game_v1 import TARGET as GAME_REAL_TARGET


def test_f5_tune_target_matches_real_column():
    assert SYSTEM_CONFIG["F5"]["target"] == F5_REAL_TARGET == "home_wins_f5"
    assert SYSTEMS["F5"].tune_target == F5_REAL_TARGET


def test_game_tune_target_matches_real_column():
    """GAME's target genuinely is home_win -- confirm the F5 fix didn't
    accidentally also change GAME's (correct) entry."""
    assert SYSTEM_CONFIG["GAME"]["target"] == GAME_REAL_TARGET == "home_win"
    assert SYSTEMS["GAME"].tune_target == GAME_REAL_TARGET
