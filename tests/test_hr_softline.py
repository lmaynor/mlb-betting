"""
tests/test_hr_softline.py -- mlb.analysis.hr_softline pure helpers.

No existing test file covered this module. Scoped to the one regression this
was added for; score_softline/scan/validate (GCS + real-outcome I/O) are left
to manual/live validation, same convention as kalshi_vs_books.py's own tests.
"""
import pandas as pd

from mlb.analysis import hr_softline as hs


def test_tstat_none_when_zero_variance():
    """2026-09-18: float64 rounding on near-identical values (e.g. three 0.1s)
    can leave scipy's sem a tiny non-zero epsilon instead of exactly 0.0, which
    a bare `sem > 0` guard would let through -- producing a meaningless ~1e16
    t-stat instead of the correct None for a genuinely flat/zero-variance
    sample. _tstat was promoted from a local closure inside validate() to a
    module-level function as part of this fix (same behavior, now testable in
    isolation -- backtest_market.py's own _tstat already mirrors this one)."""
    assert hs._tstat(pd.Series([0.1, 0.1, 0.1])) is None


def test_tstat_positive_for_consistently_positive_roi():
    t = hs._tstat(pd.Series([0.1, 0.12, 0.09, 0.11, 0.10]))
    assert t is not None and t > 0


def test_tstat_none_with_fewer_than_two_values():
    assert hs._tstat(pd.Series([0.5])) is None
