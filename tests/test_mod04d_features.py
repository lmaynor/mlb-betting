from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mod04d.features import assert_no_leak_columns, build_pregame_features


def _sample_boxscores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_date": "2025-11-01",
                "game_id": 1,
                "player_id": 10,
                "team": "BOS",
                "minutes": 32.0,
                "fga": 16,
                "fgm": 8,
                "fg3a": 6,
                "fg3m": 3,
                "fta": 4,
                "ftm": 4,
                "points": 23,
                "oreb": 1,
                "tov": 2,
                "starter": 1,
            },
            {
                "game_date": "2025-11-03",
                "game_id": 2,
                "player_id": 10,
                "team": "BOS",
                "minutes": 34.0,
                "fga": 18,
                "fgm": 9,
                "fg3a": 8,
                "fg3m": 4,
                "fta": 5,
                "ftm": 4,
                "points": 26,
                "oreb": 0,
                "tov": 1,
                "starter": 1,
            },
            # On target_date itself (must NEVER be read for target_date = 2025-11-05)
            {
                "game_date": "2025-11-05",
                "game_id": 3,
                "player_id": 10,
                "team": "BOS",
                "minutes": 40.0,
                "fga": 30,
                "fgm": 20,
                "fg3a": 12,
                "fg3m": 9,
                "fta": 10,
                "ftm": 10,
                "points": 59,
                "oreb": 2,
                "tov": 0,
                "starter": 1,
            },
            # Future game after target_date
            {
                "game_date": "2025-11-07",
                "game_id": 4,
                "player_id": 10,
                "team": "BOS",
                "minutes": 12.0,
                "fga": 4,
                "fgm": 1,
                "fg3a": 2,
                "fg3m": 0,
                "fta": 0,
                "ftm": 0,
                "points": 2,
                "oreb": 0,
                "tov": 3,
                "starter": 0,
            },
        ]
    )


@pytest.mark.parametrize(
    "bad_col",
    [
        "points_post",
        "sv_l60_post",
        "actual_points",
        "outcome_over",
        "line",
        "odds",
        "p_market",
        "spread",
        "total",
        "implied_team_total",
    ],
)
def test_schema_gate_rejects_leak_columns(bad_col: str) -> None:
    df = pd.DataFrame({"player_id": [1], "min_l5": [30.0], bad_col: [1.0]})
    with pytest.raises(ValueError, match="Leakage"):
        assert_no_leak_columns(df)


def test_schema_gate_allows_env_columns() -> None:
    df = pd.DataFrame(
        {
            "player_id": [1],
            "min_l5": [30.0],
            "env_spread": [-4.5],
            "env_total": [224.5],
            "env_implied_team_total": [114.5],
        }
    )
    assert_no_leak_columns(df)


def test_time_travel_strictly_prior_and_snapshot_cutoff() -> None:
    full = _sample_boxscores()
    target_date = "2025-11-05"
    cutoff = "2025-11-05T22:00:00Z"

    snaps = pd.DataFrame(
        [
            {
                "game_date": "2025-11-05",
                "player_id": 10,
                "ts_utc": "2025-11-05T20:00:00Z",
                "env_spread": -3.5,
                "env_total": 225.0,
                "env_implied_team_total": 114.25,
            },
            # Post-cutoff snapshot MUST be ignored
            {
                "game_date": "2025-11-05",
                "player_id": 10,
                "ts_utc": "2025-11-05T23:30:00Z",
                "env_spread": -99.0,
                "env_total": 999.0,
                "env_implied_team_total": 500.0,
            },
        ]
    )

    feat_full = build_pregame_features(target_date, cutoff, full, snaps)
    only_prior = full[full["game_date"] < target_date].copy()
    feat_prior = build_pregame_features(target_date, cutoff, only_prior, snaps)

    pd.testing.assert_frame_equal(feat_full, feat_prior)

    # Adding another future game must not alter features
    extra_future = pd.concat(
        [
            full,
            pd.DataFrame(
                [
                    {
                        "game_date": "2025-11-12",
                        "game_id": 5,
                        "player_id": 10,
                        "team": "BOS",
                        "minutes": 5.0,
                        "fga": 2,
                        "fgm": 0,
                        "fg3a": 1,
                        "fg3m": 0,
                        "fta": 0,
                        "ftm": 0,
                        "points": 0,
                        "oreb": 0,
                        "tov": 0,
                        "starter": 0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    feat_extra = build_pregame_features(target_date, cutoff, extra_future, snaps)
    pd.testing.assert_frame_equal(feat_full, feat_extra)

    # Verify post-cutoff snapshot was ignored and pre-cutoff env values kept
    assert float(feat_full["env_spread"].iloc[0]) == -3.5
    assert float(feat_full["env_total"].iloc[0]) == 225.0
    assert float(feat_full["n_prior_games"].iloc[0]) == 2.0
    assert np.isclose(float(feat_full["min_l5"].iloc[0]), 33.0)
