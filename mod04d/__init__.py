"""MOD-04 Cluster (d) SCORING — shadow-only distributional prop candidates.

SHADOW-ONLY: Nothing in this package alters production serving weights,
`ev/daily_projections.py` blend tables, `nhl/props/trained.py`, or `nba/props/train.py`.

Rebind note: NBA feature assembly in `mod04d.features` is self-contained for Cluster (d)
and is designed to rebind cleanly to Cluster (a)'s shared NBA pregame backbone on merge.
"""
from __future__ import annotations

from mod04d.distributions import (
    betabinom_negbin_pmf,
    blend_probs,
    brier_score,
    calibration_params,
    calibration_slope,
    convolve_pmfs,
    ece,
    fit_blend_c,
    log_loss,
    negbin_logpmf,
    negbin_pmf,
    pmf_log_loss,
    pmf_mean,
    pmf_p_over,
    scale_pmf,
    skewnorm_params_from_moments,
    skewnorm_pmf,
)
from mod04d.features import (
    ALLOWED_ENV_COLUMNS,
    FORBIDDEN_PATTERNS,
    assert_no_leak_columns,
    build_pregame_features,
    build_rolling_training_frame,
)

__all__ = [
    "ALLOWED_ENV_COLUMNS",
    "FORBIDDEN_PATTERNS",
    "assert_no_leak_columns",
    "betabinom_negbin_pmf",
    "blend_probs",
    "brier_score",
    "build_pregame_features",
    "build_rolling_training_frame",
    "calibration_params",
    "calibration_slope",
    "convolve_pmfs",
    "ece",
    "fit_blend_c",
    "log_loss",
    "negbin_logpmf",
    "negbin_pmf",
    "pmf_log_loss",
    "pmf_mean",
    "pmf_p_over",
    "scale_pmf",
    "skewnorm_params_from_moments",
    "skewnorm_pmf",
]
