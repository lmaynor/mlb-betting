"""Tests for MOD-04 Cluster (e) U1 plumbing: pregame features, schema gate, and labels."""
import inspect
import pandas as pd
import pytest

from nfl.data import outcome_matching as om
from nfl.mod04e import features as F
from nfl.mod04e import labels as L


def _synthetic_frames():
    schedules = pd.DataFrame([
        {
            "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "gameday": "2024-09-05",
            "home_team": "KC", "away_team": "BAL", "spread_line": 3.0, "total_line": 46.5,
            "roof": "outdoors", "home_score": 27, "away_score": 20,
        },
        {
            "game_id": "2024_02_CIN_KC", "season": 2024, "week": 2, "gameday": "2024-09-15",
            "home_team": "KC", "away_team": "CIN", "spread_line": 6.5, "total_line": 48.0,
            "roof": "outdoors", "home_score": 26, "away_score": 25,
        },
        {
            "game_id": "2024_03_KC_ATL", "season": 2024, "week": 3, "gameday": "2024-09-22",
            "home_team": "ATL", "away_team": "KC", "spread_line": -3.5, "total_line": 47.0,
            "roof": "dome", "home_score": 17, "away_score": 22,
        },
    ])
    player_stats = pd.DataFrame([
        # Week 1 (2024-09-05)
        {
            "player_id": "P_WR1", "player_display_name": "Star WR", "position": "WR", "team": "KC",
            "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1,
            "targets": 9, "receptions": 7, "receiving_yards": 85, "receiving_tds": 1,
            "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
            "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        },
        {
            "player_id": "P_WR2", "player_display_name": "Second WR", "position": "WR", "team": "KC",
            "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1,
            "targets": 5, "receptions": 3, "receiving_yards": 40, "receiving_tds": 0,
            "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
            "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        },
        {
            "player_id": "P_QB1", "player_display_name": "Star QB", "position": "QB", "team": "KC",
            "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1,
            "targets": 0, "receptions": 0, "receiving_yards": 0, "receiving_tds": 0,
            "carries": 2, "rushing_yards": 12, "rushing_tds": 0,
            "attempts": 28, "completions": 20, "passing_yards": 291, "passing_tds": 1, "passing_interceptions": 1,
        },
        # Week 2 (2024-09-15) -- target date in time-travel test, plus same-day rookie P_SAME_DAY
        {
            "player_id": "P_WR1", "player_display_name": "Star WR", "position": "WR", "team": "KC",
            "game_id": "2024_02_CIN_KC", "season": 2024, "week": 2,
            "targets": 11, "receptions": 8, "receiving_yards": 110, "receiving_tds": 1,
            "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
            "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        },
        {
            "player_id": "P_SAME_DAY", "player_display_name": "Same Day Rookie", "position": "WR", "team": "KC",
            "game_id": "2024_02_CIN_KC", "season": 2024, "week": 2,
            "targets": 4, "receptions": 3, "receiving_yards": 33, "receiving_tds": 0,
            "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
            "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        },
        # Week 3 (2024-09-22) -- future relative to Week 2
        {
            "player_id": "P_WR1", "player_display_name": "Star WR", "position": "WR", "team": "KC",
            "game_id": "2024_03_KC_ATL", "season": 2024, "week": 3,
            "targets": 6, "receptions": 5, "receiving_yards": 60, "receiving_tds": 0,
            "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
            "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        },
    ])
    snap_counts = pd.DataFrame([
        {"pfr_player_id": "pfr_wr1", "player": "Star WR", "position": "WR", "team": "KC",
         "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "offense_snaps": 55, "offense_pct": 0.90},
        {"pfr_player_id": "pfr_wr2", "player": "Second WR", "position": "WR", "team": "KC",
         "game_id": "2024_01_KC_BAL", "season": 2024, "week": 1, "offense_snaps": 40, "offense_pct": 0.65},
        {"pfr_player_id": "pfr_same", "player": "Same Day Rookie", "position": "WR", "team": "KC",
         "game_id": "2024_02_CIN_KC", "season": 2024, "week": 2, "offense_snaps": 25, "offense_pct": 0.40},
    ])
    players = pd.DataFrame([
        {"gsis_id": "P_WR1", "pfr_id": "pfr_wr1", "display_name": "Star WR"},
        {"gsis_id": "P_WR2", "pfr_id": "pfr_wr2", "display_name": "Second WR"},
        {"gsis_id": "P_QB1", "pfr_id": "pfr_qb1", "display_name": "Star QB"},
        {"gsis_id": "P_SAME_DAY", "pfr_id": "pfr_same", "display_name": "Same Day Rookie"},
    ])
    depth_charts = pd.DataFrame([
        {"gsis_id": "P_WR1", "team": "KC", "season": 2024, "week": 2, "depth_team": 1, "dt": "2024-09-14T12:00:00Z"},
        {"gsis_id": "P_WR2", "team": "KC", "season": 2024, "week": 2, "depth_team": 2, "dt": "2024-09-14T12:00:00Z"},
        # Post-cutoff depth chart change must be ignored
        {"gsis_id": "P_WR1", "team": "KC", "season": 2024, "week": 2, "depth_team": 3, "dt": "2024-09-15T18:00:00Z"},
    ])
    injuries = pd.DataFrame([
        # WR2 listed OUT before cutoff -> vacates share for WR1
        {"gsis_id": "P_WR2", "team": "KC", "season": 2024, "week": 2, "report_status": "Out",
         "date_modified": "2024-09-14T15:00:00Z"},
        # WR1 listed OUT AFTER cutoff -> must be ignored at cutoff 2024-09-15T15:30:00Z
        {"gsis_id": "P_WR1", "team": "KC", "season": 2024, "week": 2, "report_status": "Out",
         "date_modified": "2024-09-15T16:30:00Z"},
    ])
    return {
        "schedules": schedules,
        "player_stats": player_stats,
        "snap_counts": snap_counts,
        "players": players,
        "depth_charts": depth_charts,
        "injuries": injuries,
    }


def test_time_travel_deleting_future_and_sameday_rows_is_bit_identical():
    frames_full = _synthetic_frames()
    target_date = "2024-09-15"
    cutoff = "2024-09-15T15:30:00Z"

    feat_full = F.build_pregame_features(target_date, cutoff, frames_full)

    # Delete all box-score rows with game_date >= target_date
    sched_dates = frames_full["schedules"].set_index("game_id")["gameday"].to_dict()
    frames_trimmed = dict(frames_full)
    ps = frames_full["player_stats"]
    frames_trimmed["player_stats"] = ps[ps["game_id"].map(sched_dates) < target_date].copy()
    sn = frames_full["snap_counts"]
    frames_trimmed["snap_counts"] = sn[sn["game_id"].map(sched_dates) < target_date].copy()

    feat_trimmed = F.build_pregame_features(target_date, cutoff, frames_trimmed)
    pd.testing.assert_frame_equal(feat_full, feat_trimmed)


def test_same_day_only_player_gets_no_row_and_cutoff_respected():
    frames = _synthetic_frames()
    feat = F.build_pregame_features("2024-09-15", "2024-09-15T15:30:00Z", frames)

    # P_SAME_DAY only played on 2024-09-15 -> must have NO row
    assert "P_SAME_DAY" not in set(feat["player_id"])
    assert set(feat["player_id"]) == {"P_WR1", "P_WR2", "P_QB1"}

    wr1 = feat[feat["player_id"] == "P_WR1"].iloc[0]
    # Post-cutoff depth chart demotion (3) and post-cutoff OUT report ignored
    assert wr1["depth_chart_role"] == 1.0
    assert wr1["injury_status"] == "ACTIVE"
    # Pre-cutoff OUT report on WR2 vacates WR2's share to WR1
    assert wr1["vacated_out_count"] == pytest.approx(1.0)
    assert wr1["vacated_target_share"] > 0.30
    # Market environment present
    assert wr1["total_line"] == pytest.approx(48.0)
    assert wr1["team_spread"] == pytest.approx(6.5)
    assert wr1["implied_team_total"] == pytest.approx(27.25)


def test_schema_gate_rejects_banned_columns():
    df = pd.DataFrame({"player_id": ["P1"], "targets_ewm": [5.0]})
    F.assert_feature_schema(df)  # clean passes

    for bad_col in ("targets_post", "actual_receptions", "outcome_status", "my_prop_line", "book_prop_odds"):
        poisoned = df.copy()
        poisoned[bad_col] = 1.0
        with pytest.raises(ValueError):
            F.assert_feature_schema(poisoned)


def test_labels_module_is_separate_and_joins_outcomes():
    assert "labels" not in inspect.getsource(F)
    frames = _synthetic_frames()
    feat = F.build_pregame_features("2024-09-15", "2024-09-15T15:30:00Z", frames)
    rosters_weekly = pd.DataFrame([
        {"season": 2024, "week": 2, "team": "KC", "gsis_id": "P_WR2", "status": "INA", "game_status": "INA"},
        {"season": 2024, "week": 2, "team": "KC", "gsis_id": "P_WR1", "status": "ACT", "game_status": "ACT"},
    ])
    lbl = L.build_labels(
        feat,
        player_stats=frames["player_stats"],
        schedules=frames["schedules"],
        players=frames["players"],
        snap_counts=frames["snap_counts"],
        rosters_weekly=rosters_weekly,
    )
    assert len(lbl) == len(feat)
    wr1_l = lbl[lbl["player_id"] == "P_WR1"].iloc[0]
    wr2_l = lbl[lbl["player_id"] == "P_WR2"].iloc[0]
    assert wr1_l["outcome_status"] == om.STATUS_STAT
    assert bool(wr1_l["gradeable"]) is True
    assert wr1_l["receptions"] == pytest.approx(8.0)
    assert wr1_l["rec_yds"] == pytest.approx(110.0)
    assert wr1_l["anytime_td"] == pytest.approx(1.0)

    assert wr2_l["outcome_status"] == om.STATUS_INACTIVE
    assert bool(wr2_l["gradeable"]) is False
    assert pd.isna(wr2_l["receptions"])
