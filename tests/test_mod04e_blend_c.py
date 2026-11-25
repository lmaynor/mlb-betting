"""Tests for MOD-04 Cluster (e) E0(iii): NFL default blend weight c = 0.00."""
import pytest

from ev import daily_projections as dp


def test_nfl_blend_c_is_zero_for_all_nfl_stats_and_unknown():
    nfl_stats = list(dp.PROJECT_STATS["nfl"].keys()) + ["unknown_stat", None, ""]
    for stat in nfl_stats:
        assert dp.blend_c("nfl", stat) == 0.00, f"expected 0.00 for nfl/{stat!r}"
        assert dp.blend_c("nfl", stat, trained=True) == 0.00


def test_non_nfl_blend_c_unchanged():
    assert dp.BLEND_C == 0.10
    assert dp.blend_c("nba", "threes") == 0.07
    assert dp.blend_c("nba", "blocks") == 0.03
    assert dp.blend_c("nhl", "sog") == 0.29
    assert dp.blend_c("mlb", "k") == dp.BLEND_C


def test_nfl_blend_p_over_returns_market_prob_exactly():
    for stat in ("receptions", "rec_yds", "rush_attempts", "rush_yds", "anytime_td", "unknown_stat"):
        for p_mkt in (0.35, 0.50, 0.62):
            out = dp.blend_p_over(0.80, p_mkt, sport="nfl", stat=stat)
            assert out == pytest.approx(p_mkt, abs=1e-12), (stat, p_mkt, out)
