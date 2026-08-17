"""
Regression pin for the 2026-08-16 audit's HR same-game-leakage fix
(finding A14): five same-game (un-shifted) columns must stay denylisted
from _load_features()'s auto-discovered feature set, or they get silently
picked up as pre-game features on every retrain.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
from mlb.training.retrain_hr_v6 import _NON_FEATURE_COLS

LEAKY_SAME_GAME_COLS = {
    "gb_game", "pull_air_game", "whiff_pct", "chase_pct", "zone_contact_pct",
}


def test_same_game_leakage_columns_are_denylisted():
    missing = LEAKY_SAME_GAME_COLS - _NON_FEATURE_COLS
    assert not missing, (
        f"{missing} must be in _NON_FEATURE_COLS -- these are same-game "
        f"(un-shifted) aggregates that leak future-game information into "
        f"what should be a pre-game feature set"
    )
