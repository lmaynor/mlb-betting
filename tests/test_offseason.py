"""
tests/test_offseason.py -- verifies MLB off-season gating logic.

MLB off-season runs from post-World Series (Oct 29) through March 19.
Resumes March 20 (4 days before Opening Day).
MLB_FORCE_RUN=1 overrides off-season gating.
"""
from datetime import date
import os
from unittest.mock import patch

from mlb_core.config import is_mlb_offseason
from main import _off_season


def test_is_mlb_offseason_dates():
    # In-season dates (March 20 through October 28)
    assert not is_mlb_offseason(date(2026, 3, 20))
    assert not is_mlb_offseason(date(2026, 4, 1))
    assert not is_mlb_offseason(date(2026, 7, 4))
    assert not is_mlb_offseason(date(2026, 10, 28))  # World Series final day

    # Off-season dates (October 29 through March 19)
    assert is_mlb_offseason(date(2026, 10, 29))  # Off-season begins
    assert is_mlb_offseason(date(2026, 10, 31))
    assert is_mlb_offseason(date(2026, 11, 15))
    assert is_mlb_offseason(date(2026, 12, 25))
    assert is_mlb_offseason(date(2027, 1, 1))
    assert is_mlb_offseason(date(2027, 2, 14))
    assert is_mlb_offseason(date(2027, 2, 28))
    assert is_mlb_offseason(date(2027, 3, 1))
    assert is_mlb_offseason(date(2027, 3, 19))  # Last off-season day


def test_is_mlb_offseason_force_override():
    with patch.dict(os.environ, {"MLB_FORCE_RUN": "1"}):
        assert not is_mlb_offseason(date(2026, 12, 25))
        assert not is_mlb_offseason(date(2026, 10, 29))


def test_main_off_season_helper():
    with patch.dict(os.environ, {"MLB_FORCE_RUN": ""}):
        # Mocking datetime.now in main._CT
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value = date(2026, 10, 29)
            assert _off_season()

            mock_dt.now.return_value.date.return_value = date(2027, 3, 19)
            assert _off_season()

            mock_dt.now.return_value.date.return_value = date(2027, 3, 20)
            assert not _off_season()

            mock_dt.now.return_value.date.return_value = date(2026, 10, 28)
            assert not _off_season()
