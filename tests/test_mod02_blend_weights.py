"""MOD-02 Phase 2 recalibration blend-weight tests for ev.daily_projections.blend_c()."""
from ev.daily_projections import blend_c


def test_mod02_phase2_ship_set_weights():
    assert blend_c("nhl", "sog") == 0.29
    assert blend_c("nba", "blocks") == 0.03
    assert blend_c("nhl", "points", trained=True) == 0.43


def test_mod02_phase2_unchanged_neighbors():
    assert blend_c("nhl", "assists", trained=True) == 0.34
    assert blend_c("nba", "points") == 0.06
    assert blend_c("nba", "pra") == 0.06
    assert blend_c("nba", "assists") == 0.00
    assert blend_c("nhl", "saves", trained=True) == 0.35
