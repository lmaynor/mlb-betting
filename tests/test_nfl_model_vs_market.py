"""Pure-function tests for scripts/nfl_model_vs_market.py (no GCS, no models)."""
import importlib.util
import os

import numpy as np
import pandas as pd

_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "nfl_model_vs_market.py")
_spec = importlib.util.spec_from_file_location("nfl_mvm", _P)
mvm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mvm)


def test_main_line_keeps_most_books_then_closest_to_even():
    t = pd.DataFrame({
        "game_id": ["g"] * 4, "gsis_id": ["p"] * 4, "stat": ["rec_yds"] * 4,
        "point": [30.5, 40.5, 50.5, 60.5], "close_n_books": [1, 3, 3, 2],
        "close_p_over": [0.5, 0.9, 0.52, 0.5], "y": [1, 1, 0, 0], "p_model": [.5] * 4,
    })
    m = mvm.main_line_only(t)
    assert len(m) == 1 and m.iloc[0]["point"] == 50.5


def test_alt_ladders_collapse_per_player_game_stat():
    t = pd.DataFrame({
        "game_id": ["g", "g", "g"], "gsis_id": ["a", "a", "b"], "stat": ["pass_yds"] * 3,
        "point": [200.5, 220.5, 210.5], "close_n_books": [1, 1, 1],
        "close_p_over": [0.5, 0.5, 0.5], "y": [0, 0, 1], "p_model": [.5] * 3,
    })
    assert len(mvm.main_line_only(t)) == 2


def test_paired_diff_sign_and_se():
    d, se = mvm._paired([0.2, 0.2, 0.2, 0.3], [0.3, 0.3, 0.3, 0.3])
    assert d < 0 and se > 0
    assert np.isnan(mvm._paired([0.1], [0.2])[1])


def test_scores_zero_for_perfect_and_clipped():
    assert mvm._brier(np.array([1.0]), np.array([1.0]))[0] == 0
    assert np.isfinite(mvm._logloss(np.array([0.0]), np.array([1.0]))[0])
