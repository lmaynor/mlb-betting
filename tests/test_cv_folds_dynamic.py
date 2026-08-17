"""
Regression test for the 2026-08-16 audit's frozen-CV_FOLDS fix (finding
C3.3): five retrain scripts (K, OUTS, BATTER_HITS, BATTER_TB, GAME) had
`CV_FOLDS = [2023, 2024, 2025]` as a hardcoded module-level literal --
walk-forward CV, the OOS train/test split, and the leakage check all
silently stopped covering the current season the moment year 2026 games
started landing, with zero error or warning. Each was replaced with a
`_cv_folds(df, n=3)` helper that derives the most recent `n` years actually
present in the data.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd
import pytest

from mlb.training.retrain_k_v1 import _cv_folds as _cv_folds_k
from mlb.training.retrain_outs_v1 import _cv_folds as _cv_folds_outs
from mlb.training.retrain_batter_hits_v1 import _cv_folds as _cv_folds_batter_hits
from mlb.training.retrain_batter_tb_v1 import _cv_folds as _cv_folds_batter_tb
from mlb.training.retrain_game_v1 import _cv_folds as _cv_folds_game

_ALL_CV_FOLDS_FNS = {
    "K":           _cv_folds_k,
    "OUTS":        _cv_folds_outs,
    "BATTER_HITS": _cv_folds_batter_hits,
    "BATTER_TB":   _cv_folds_batter_tb,
    "GAME":        _cv_folds_game,
}


def _df_with_years(years: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"year": years})


@pytest.mark.parametrize("system,fn", _ALL_CV_FOLDS_FNS.items(), ids=_ALL_CV_FOLDS_FNS.keys())
def test_picks_up_current_season_not_frozen_at_2025(system, fn):
    """The whole point of the fix: a year the old hardcoded literal
    ([2023, 2024, 2025]) would have silently never seen must show up once
    it's actually present in the data."""
    df = _df_with_years([2022, 2023, 2024, 2025, 2026])
    folds = fn(df)
    assert 2026 in folds, (
        f"{system}'s _cv_folds dropped the most recent season (2026) -- "
        f"regression back to a frozen fold list. Got {folds}"
    )
    assert folds == [2024, 2025, 2026], f"{system}: expected last 3 years, got {folds}"


@pytest.mark.parametrize("system,fn", _ALL_CV_FOLDS_FNS.items(), ids=_ALL_CV_FOLDS_FNS.keys())
def test_handles_fewer_than_n_years_gracefully(system, fn):
    """Early in a system's life (or in a small test fixture) there may be
    fewer than 3 distinct years on record -- must not crash or silently pad
    with a stale literal."""
    df = _df_with_years([2025, 2025, 2026])
    folds = fn(df)
    assert folds == [2025, 2026], f"{system}: expected both available years, got {folds}"


@pytest.mark.parametrize("system,fn", _ALL_CV_FOLDS_FNS.items(), ids=_ALL_CV_FOLDS_FNS.keys())
def test_ignores_nan_years(system, fn):
    df = pd.DataFrame({"year": [2023, 2024, 2025, float("nan")]})
    folds = fn(df)
    assert all(isinstance(y, int) for y in folds)
    assert folds == [2023, 2024, 2025]
