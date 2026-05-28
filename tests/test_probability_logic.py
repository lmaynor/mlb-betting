import sys
import types


sys.modules.setdefault("xgboost", types.SimpleNamespace())

from runners.run_k import _simulate_k


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
