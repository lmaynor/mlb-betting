"""Regression tests for the production boxscore-master schema.

The pre-existing fixtures all use the clean contract schema (`team`, numeric
minutes, short stat names). The production master at
~/workspace/mod04d-data/player_boxscores_master.csv instead carries
`team_abbr`, 'MM:SS' string minutes, and long stat names
(`field_goals_attempted`, ...). On 2026-11-25 that mismatch silently zeroed
every minutes/usage feature and the daily bank shipped one constant
(mu, alpha) for the whole slate. These tests pin the master-schema behavior
plus the banking guards that now refuse such a bank.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mod04d.distributions import negbin_pmf
from mod04d.features import (
    NBA_FEATURE_COLS,
    build_pregame_features,
    build_rolling_training_frame,
    validate_feature_frame,
)
from mod04d.shadow import build_shadow_rows


def _mmss(minutes: float) -> str:
    m = int(minutes)
    s = int(round((minutes - m) * 60))
    return f"{m}:{s:02d}"


def _master_boxscores() -> pd.DataFrame:
    """Production-master schema: team_abbr, 'MM:SS' minutes, long stat names."""
    # Player 10 minutes by date; 2025-11-04 is a DNP ('0:00', played False).
    minutes_by_date = {
        "2025-11-01": 30.0,
        "2025-11-02": 32.0,
        "2025-11-03": 34.0,
        "2025-11-04": 0.0,
        "2025-11-05": 36.0,
        "2025-11-06": 28.0,
        "2025-11-07": 30.0,
        "2025-11-08": 32.0,
    }
    rows = []
    for i, (d, mins) in enumerate(minutes_by_date.items()):
        played = mins > 0
        fga = int(mins * 0.5) if played else 0
        fg3a = fga // 3
        fg2a = fga - fg3a
        fta = 4 if played else 0
        rows.append(
            {
                "game_id": f"g{i}",
                "season_year": 2025,
                "season_type": "Regular Season",
                "date": d,
                "team_id": "t-bos",
                "team_abbr": "BOS",
                "is_home": True,
                "player_id": "p10",
                "name": "Player Ten",
                "position": "G",
                "starter": True,
                "played": played,
                "points": (2 * (fg2a // 2) + 3 * (fg3a // 3) + fta // 2) if played else 0,
                "minutes": _mmss(mins),
                "plus_minus": 0.0,
                "field_goals_made": fg2a // 2 + fg3a // 3,
                "field_goals_attempted": fga,
                "two_points_made": fg2a // 2,
                "two_points_attempted": fg2a,
                "three_points_made": fg3a // 3,
                "three_points_attempted": fg3a,
                "free_throws_made": fta // 2,
                "free_throws_attempted": fta,
                "rebounds": 5 if played else 0,
                "rebounds_offensive": 1 if played else 0,
                "rebounds_defensive": 4 if played else 0,
                "assists": 3 if played else 0,
                "steals": 1 if played else 0,
                "blocks": 0,
                "turnovers": 2 if played else 0,
                "fouls": 2 if played else 0,
            }
        )
    return pd.DataFrame(rows)


def test_pregame_features_parse_mmss_minutes_and_team_abbr() -> None:
    box = _master_boxscores()
    feat = build_pregame_features("2025-11-09", "2025-11-09T23:00:00Z", box)
    assert len(feat) == 1
    row = feat.iloc[0]
    # Played games only: 30,32,34,36,28,30,32 (the '0:00' DNP is excluded).
    assert row["n_prior_games"] == 7.0
    # Last 5 played: 34,36,28,30,32 -> 32.0
    assert row["min_l5"] == pytest.approx(32.0)
    assert row["min_l15"] == pytest.approx((30 + 32 + 34 + 36 + 28 + 30 + 32) / 7)
    assert np.isfinite(row["fga_per_min_l5"])
    assert np.isfinite(row["fg3a_per_min_ewm"])
    # team comes from team_abbr, so pace is a real team value, not absent.
    assert row["team"] == "BOS"
    assert np.isfinite(row["pace_proxy"])


def test_rolling_frame_window_restricts_dates_not_history() -> None:
    box = _master_boxscores()
    frame = build_rolling_training_frame(
        box, min_prior_games=3, start_date="2025-11-07", end_date="2025-11-08"
    )
    assert sorted(frame["date"].unique()) == ["2025-11-07", "2025-11-08"]
    # History before the window still counts toward prior games / features.
    assert frame["min_l5"].notna().all()
    assert (frame["n_prior_games"] >= 3).all()


def test_validate_feature_frame_rejects_all_nan_key_columns() -> None:
    bad = pd.DataFrame({c: [np.nan, np.nan] for c in NBA_FEATURE_COLS})
    with pytest.raises(ValueError, match="all-NaN"):
        validate_feature_frame(bad)
    good = pd.DataFrame({c: [1.0, 2.0] for c in NBA_FEATURE_COLS})
    validate_feature_frame(good)  # must not raise


class _VaryingModel:
    meta = {"engine": "stub_varying"}

    def predict_mu_alpha(self, X: pd.DataFrame):
        mu = 5.0 + np.arange(len(X), dtype=float)
        return mu, np.full(len(X), 0.1) + 0.01 * np.arange(len(X))

    def pmf(self, X: pd.DataFrame, k_max: int | None = None):
        mu, alpha = self.predict_mu_alpha(X)
        return negbin_pmf(mu, alpha, k_max=k_max or 80)


class _ConstantModel:
    meta = {"engine": "stub_constant"}

    def predict_mu_alpha(self, X: pd.DataFrame):
        return np.full(len(X), 6.587318), np.full(len(X), 1.101488)

    def pmf(self, X: pd.DataFrame, k_max: int | None = None):
        mu, alpha = self.predict_mu_alpha(X)
        return negbin_pmf(mu, alpha, k_max=k_max or 80)


def _slate(n: int = 4, nan_features: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"date": ["2026-11-25"] * n, "player_id": [f"p{i}" for i in range(n)]})
    for c in NBA_FEATURE_COLS:
        df[c] = np.nan if nan_features else rng.uniform(0.5, 30.0, size=n)
    df["team"] = ["BOS"] * n
    return df


def test_shadow_bank_rejects_all_nan_slate_features() -> None:
    with pytest.raises(ValueError, match="all-NaN"):
        build_shadow_rows(
            family="nba_points",
            slate_features=_slate(nan_features=True),
            model=_VaryingModel(),
            lines=[10.5] * 4,
            date="2026-11-25",
        )


def test_shadow_bank_rejects_constant_parameter_slate() -> None:
    with pytest.raises(ValueError, match="constant"):
        build_shadow_rows(
            family="nba_points",
            slate_features=_slate(n=12),
            model=_ConstantModel(),
            lines=[10.5] * 12,
            date="2026-11-25",
        )


def test_shadow_bank_accepts_varying_model() -> None:
    rows = build_shadow_rows(
        family="nba_points",
        slate_features=_slate(),
        model=_VaryingModel(),
        lines=[10.5] * 4,
        date="2026-11-25",
    )
    assert len(rows) == 4
    assert len({r["mu"] for r in rows}) == 4
