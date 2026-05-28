import sys
import types


sys.modules.setdefault("xgboost", types.SimpleNamespace())
sys.modules.setdefault("sklearn", types.SimpleNamespace())
sys.modules.setdefault(
    "sklearn.calibration",
    types.SimpleNamespace(IsotonicRegression=object),
)
sys.modules.setdefault(
    "sklearn.metrics",
    types.SimpleNamespace(brier_score_loss=lambda y_true, y_pred: 0.0),
)

from runners.run_k import _simulate_k
from runners.run_1i import _derive_3way_probs
from training.calibrate_nrfi_v18 import _build_game_level


def test_k_simulation_uses_calibrated_lambda_not_recent_rate_proxy():
    """Recent K/9 proxy should be diagnostic, not overwrite model lambda."""
    _simulate_k._nb_alpha = 0.0

    dist = _simulate_k(
        lambda_k=4.0,
        avg_ip_L5=6.0,
        k_per_9_L5=18.0,
        n_sims=50_000,
        cap=14,
        seed=123,
    )

    assert abs(dist["mean"] - 4.0) < 0.08
    assert dist["proxy_lambda_k"] == 12.0


def test_nrfi_calibrator_uses_full_first_inning_actual():
    """Game-level YRFI actual should be true if either half scores."""
    import numpy as np
    import pandas as pd

    df = pd.DataFrame(
        [
            {"game_pk": 1, "game_date": "2026-05-01", "pitcher_is_home": 1, "yrfi": 1},
            {"game_pk": 1, "game_date": "2026-05-01", "pitcher_is_home": 0, "yrfi": 0},
            {"game_pk": 2, "game_date": "2026-05-01", "pitcher_is_home": 1, "yrfi": 0},
            {"game_pk": 2, "game_date": "2026-05-01", "pitcher_is_home": 0, "yrfi": 1},
            {"game_pk": 3, "game_date": "2026-05-01", "pitcher_is_home": 1, "yrfi": 0},
            {"game_pk": 3, "game_date": "2026-05-01", "pitcher_is_home": 0, "yrfi": 0},
        ]
    )

    out = _build_game_level(df, np.array([0.2, 0.3, 0.2, 0.3, 0.2, 0.3]))

    actuals = dict(zip(out["game_pk"], out["yrfi"]))
    assert actuals == {1: 1, 2: 1, 3: 0}


def test_1i_probs_allocate_both_score_slice_to_true_3way_outcomes():
    """1I draw should include 1-1 style tied outcomes, not only NRFI."""
    out = _derive_3way_probs(
        p_away_score=[0.5],
        p_home_score=[0.5],
        p_nrfi_prob=[0.25],
        both_score_shares={"away": 0.2, "home": 0.3, "draw": 0.5},
    ).iloc[0]

    assert round(float(out["p_3way_away"]), 4) == 0.3
    assert round(float(out["p_3way_home"]), 4) == 0.325
    assert round(float(out["p_3way_draw"]), 4) == 0.375
    assert round(float(out.sum()), 4) == 1.0
