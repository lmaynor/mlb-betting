"""
tests/test_clv.py -- price-based CLV (Task: CLV bug fix).

The old probability-relative CLV was sign-inverted and blew up to +-35-68%
when the closing fair prob was small/mismatched. These tests pin the new
price-based definition: bounded, correctly signed, computed on raw prices.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlb_core.odds.utils import american_to_decimal, clv_pct_from_prices


class TestAmericanToDecimal:
    def test_plus_money(self):
        assert abs(american_to_decimal(150) - 2.5) < 1e-9

    def test_minus_money(self):
        assert abs(american_to_decimal(-200) - 1.5) < 1e-9

    def test_even(self):
        assert abs(american_to_decimal(100) - 2.0) < 1e-9


class TestClvSign:
    def test_beat_the_close_is_positive(self):
        # Bet +150, closed +120 (line shortened toward us) -> POSITIVE CLV
        clv = clv_pct_from_prices(entry_odds=150, closing_odds=120)
        assert clv > 0

    def test_worse_than_close_is_negative(self):
        # Bet +120, closed +150 (line drifted away) -> NEGATIVE CLV
        clv = clv_pct_from_prices(entry_odds=120, closing_odds=150)
        assert clv < 0

    def test_no_move_is_zero(self):
        assert clv_pct_from_prices(-110, -110) == 0.0


class TestClvMagnitude:
    def test_bounded_and_sane(self):
        # A normal line move produces a single-digit / low-double-digit CLV,
        # never the +35-68% the old formula produced.
        clv = clv_pct_from_prices(entry_odds=150, closing_odds=120)
        assert 0 < clv < 30

    def test_does_not_divide_by_small_prob(self):
        # Heavy favorite both sides: old formula divided by a small closing
        # fair prob complement and blew up; price-based stays bounded.
        clv = clv_pct_from_prices(entry_odds=-300, closing_odds=-320)
        assert abs(clv) < 10

    def test_invalid_inputs_return_nan(self):
        import pandas as pd
        assert pd.isna(clv_pct_from_prices(None, -110))
        assert pd.isna(clv_pct_from_prices(-110, None))
