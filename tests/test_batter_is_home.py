"""
Regression test for the 2026-08-16 audit's is_home constant-zero fix
(finding C2.3): build_batter_hits_rolling/build_batter_tb_rolling must
derive is_home from real historical inning_topbot, not leave it dead-coded
to a batter_team_side field that's only ever populated at live-scoring
time (never in the historical/training frame these functions build).

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd

from mlb.runners.build_batter_hits_features import build_batter_hits_rolling
from mlb.runners.build_batter_tb_features import build_batter_tb_rolling


def _pa_row(batter, game_pk, inning_topbot, game_date="2024-05-01", events="single"):
    return {
        "batter": batter,
        "game_pk": game_pk,
        "game_date": game_date,
        "inning_topbot": inning_topbot,
        "events": events,
        "launch_speed": 90.0,
        "launch_angle": 15.0,
        "home_team": "CLE",
        "away_team": "LAA",
        "pitcher": 500,
        "total_bases": 1,
    }


def test_batter_hits_is_home_has_real_variance_not_all_zero():
    """Home batter (Bot half) and away batter (Top half) in the same game
    must get DIFFERENT is_home values, not both 0."""
    sc = pd.DataFrame([
        _pa_row(100, 999, "Bot"),   # home team (CLE) batter
        _pa_row(200, 999, "Top"),   # away team (LAA) batter
    ])
    out = build_batter_hits_rolling(sc, pd.DataFrame(), lookback_days=60, run_date="2024-05-02")
    got = dict(zip(out["batter"], out["is_home"]))
    assert got == {100: 1, 200: 0}, (
        f"expected batter 100 (Bot half, home) is_home=1 and batter 200 "
        f"(Top half, away) is_home=0, got {got} -- if both are 0, the "
        f"zero-variance regression is back"
    )


def test_batter_tb_is_home_has_real_variance_not_all_zero():
    sc = pd.DataFrame([
        _pa_row(100, 999, "Bot"),
        _pa_row(200, 999, "Top"),
    ])
    out = build_batter_tb_rolling(sc, pd.DataFrame(), lookback_days=60, run_date="2024-05-02")
    got = dict(zip(out["batter"], out["is_home"]))
    assert got == {100: 1, 200: 0}, (
        f"expected batter 100 (Bot half, home) is_home=1 and batter 200 "
        f"(Top half, away) is_home=0, got {got} -- if both are 0, the "
        f"zero-variance regression is back"
    )
