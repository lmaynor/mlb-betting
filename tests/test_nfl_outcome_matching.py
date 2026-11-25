"""Offline fixture-based tests for nfl.data.outcome_matching (no network or GCS)."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from nfl.data import outcome_matching as OM


def _make_tables():
    players = pd.DataFrame([
        {
            "gsis_id": "00-QB1",
            "display_name": "Michael Penix Jr.",
            "first_name": "Michael",
            "last_name": "Penix",
            "common_first_name": "Michael",
            "football_name": "Michael",
            "pfr_id": "PeniMi00",
            "latest_team": "ATL",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-WR1",
            "display_name": "Drake London",
            "first_name": "Drake",
            "last_name": "London",
            "common_first_name": "Drake",
            "football_name": "Drake",
            "pfr_id": "LondDr00",
            "latest_team": "ATL",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-WR2",
            "display_name": "Dnp Receiver",
            "first_name": "Dnp",
            "last_name": "Receiver",
            "common_first_name": "Dnp",
            "football_name": "Dnp",
            "pfr_id": "ReceDn00",
            "latest_team": "ATL",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-INACT",
            "display_name": "Absent Player",
            "first_name": "Absent",
            "last_name": "Player",
            "common_first_name": "Absent",
            "football_name": "Absent",
            "pfr_id": "PlayAb00",
            "latest_team": "ATL",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-RB1",
            "display_name": "Bijan Robinson",
            "first_name": "Bijan",
            "last_name": "Robinson",
            "common_first_name": "Bijan",
            "football_name": "Bijan",
            "pfr_id": "RobiBi00",
            "latest_team": "ATL",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-SAME1",
            "display_name": "Josh Allen",
            "first_name": "Josh",
            "last_name": "Allen",
            "common_first_name": "Josh",
            "football_name": "Josh",
            "pfr_id": "AlleJo02",
            "latest_team": "BUF",
            "last_season": 2026,
        },
        {
            "gsis_id": "00-SAME2",
            "display_name": "Josh Allen",
            "first_name": "Josh",
            "last_name": "Allen",
            "common_first_name": "Josh",
            "football_name": "Josh",
            "pfr_id": "AlleJo03",
            "latest_team": "JAX",
            "last_season": 2026,
        },
    ])

    schedules = pd.DataFrame([
        {
            "game_id": "2026_10_ATL_NO",
            "season": 2026,
            "week": 10,
            "gameday": "2026-11-15",
            "away_team": "ATL",
            "home_team": "NO",
        },
        {
            "game_id": "2026_10_BUF_JAX",
            "season": 2026,
            "week": 10,
            "gameday": "2026-11-15",
            "away_team": "BUF",
            "home_team": "JAX",
        },
    ])

    player_stats = pd.DataFrame([
        {
            "player_id": "00-QB1",
            "game_id": "2026_10_ATL_NO",
            "season": 2026,
            "week": 10,
            "team": "ATL",
            "passing_yards": 245.0,
            "passing_tds": 2.0,
            "attempts": 31.0,
            "completions": 20.0,
            "passing_interceptions": 1.0,
            "rushing_yards": 12.0,
            "carries": 3.0,
            "rushing_tds": 0.0,
            "receiving_yards": 0.0,
            "receptions": 0.0,
            "targets": 0.0,
            "receiving_tds": 0.0,
        },
        {
            "player_id": "00-RB1",
            "game_id": "2026_10_ATL_NO",
            "season": 2026,
            "week": 10,
            "team": "ATL",
            "passing_yards": 0.0,
            "passing_tds": 0.0,
            "attempts": 0.0,
            "completions": 0.0,
            "passing_interceptions": 0.0,
            "rushing_yards": 88.0,
            "carries": 18.0,
            "rushing_tds": 1.0,
            "receiving_yards": 32.0,
            "receptions": 4.0,
            "targets": 5.0,
            "receiving_tds": 1.0,
        },
        {
            "player_id": "00-SAME1",
            "game_id": "2026_10_BUF_JAX",
            "season": 2026,
            "week": 10,
            "team": "BUF",
            "passing_yards": 280.0,
            "passing_tds": 3.0,
            "attempts": 35.0,
            "completions": 24.0,
            "passing_interceptions": 0.0,
            "rushing_yards": 30.0,
            "carries": 6.0,
            "rushing_tds": 0.0,
            "receiving_yards": 0.0,
            "receptions": 0.0,
            "targets": 0.0,
            "receiving_tds": 0.0,
        },
    ])

    snap_counts = pd.DataFrame([
        {
            "game_id": "2026_10_ATL_NO",
            "season": 2026,
            "week": 10,
            "pfr_player_id": "LondDr00",
            "offense_snaps": 42.0,
            "defense_snaps": 0.0,
            "st_snaps": 0.0,
        },
        {
            "game_id": "2026_10_ATL_NO",
            "season": 2026,
            "week": 10,
            "pfr_player_id": "ReceDn00",
            "offense_snaps": 0.0,
            "defense_snaps": 0.0,
            "st_snaps": 0.0,
        },
    ])

    rosters_weekly = pd.DataFrame([
        {"season": 2026, "week": 10, "team": "ATL", "gsis_id": "00-QB1", "status": "ACT"},
        {"season": 2026, "week": 10, "team": "ATL", "gsis_id": "00-WR1", "status": "ACT"},
        {"season": 2026, "week": 10, "team": "ATL", "gsis_id": "00-WR2", "status": "ACT"},
        {"season": 2026, "week": 10, "team": "ATL", "gsis_id": "00-RB1", "status": "ACT"},
        {"season": 2026, "week": 10, "team": "BUF", "gsis_id": "00-SAME1", "status": "ACT"},
        {"season": 2026, "week": 10, "team": "JAX", "gsis_id": "00-SAME2", "status": "ACT"},
    ])

    return {
        "players": players,
        "schedules": schedules,
        "player_stats": player_stats,
        "snap_counts": snap_counts,
        "rosters_weekly": rosters_weekly,
    }


@pytest.fixture
def stub_load(monkeypatch):
    tables = _make_tables()

    def _fake_load(dataset, seasons=None, columns=None, strict=True):
        df = tables[dataset].copy()
        if columns is not None:
            df = df[columns]
        return df

    monkeypatch.setattr("nfl.data.read.load", _fake_load)
    return tables


def test_stat_row_resolves_stat_and_actual_value(stub_load):
    board = pd.DataFrame([
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "rush_yds", "line": 74.5},
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "receptions", "line": 3.5},
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "targets", "line": 4.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert list(out["status"]) == ["stat", "stat", "stat"]
    assert list(out["actual"]) == [88.0, 4.0, 5.0]
    assert list(out["gradeable"]) == [True, True, True]
    assert list(out["gsis_id"]) == ["00-RB1", "00-RB1", "00-RB1"]
    assert list(out["week"]) == [10, 10, 10]


def test_zero_stat_player_with_snaps_resolves_played_zero(stub_load):
    board = pd.DataFrame([
        {"player": "Drake London", "team": "ATL", "game_date": "2026-11-15", "stat": "receptions", "line": 4.5},
        {"player": "Drake London", "team": "ATL", "game_date": "2026-11-15", "stat": "anytime_td", "line": 0.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert list(out["status"]) == ["played_zero", "played_zero"]
    assert list(out["actual"]) == [0.0, 0.0]
    assert list(out["gradeable"]) == [True, True]


def test_zero_snap_player_resolves_dnp_not_gradeable(stub_load):
    board = pd.DataFrame([
        {"player": "Dnp Receiver", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 24.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    r = out.iloc[0]
    assert r["status"] == "dnp"
    assert math.isnan(r["actual"])
    assert bool(r["gradeable"]) is False


def test_absent_from_snaps_and_weekly_roster_resolves_inactive(stub_load):
    board = pd.DataFrame([
        {"player": "Absent Player", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 19.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    r = out.iloc[0]
    assert r["status"] == "inactive"
    assert math.isnan(r["actual"])
    assert bool(r["gradeable"]) is False


def test_inactive_roster_status_and_game_status_resolve_inactive(monkeypatch):
    tables = _make_tables()
    tables["rosters_weekly"] = pd.concat([
        tables["rosters_weekly"],
        pd.DataFrame([
            {"season": 2026, "week": 10, "team": "ATL", "gsis_id": "00-INACT", "status": "INA", "game_status": "OUT"},
        ]),
    ], ignore_index=True)
    monkeypatch.setattr("nfl.data.read.load", lambda ds, seasons=None, columns=None, strict=True: tables[ds].copy())

    board = pd.DataFrame([
        {"player": "Absent Player", "team": "ATL", "game_date": "2026-11-15", "stat": "receptions", "line": 2.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert out.iloc[0]["status"] == "inactive"
    assert bool(out.iloc[0]["gradeable"]) is False


def test_jr_suffix_and_punctuation_resolve_via_crosswalk(stub_load):
    board = pd.DataFrame([
        {"player": "Michael Penix Jr.", "team": "ATL", "game_date": "2026-11-15", "stat": "pass_yds", "line": 230.5},
        {"player": "Michael Penix", "team": "ATL", "game_date": "2026-11-15", "stat": "interceptions", "line": 0.5},
        {"player": "Michael Penix Jr", "team": "ATL", "game_date": "2026-11-15", "stat": "pass_att", "line": 29.5},
        {"player": "Michael Penix Jr.", "team": "ATL", "game_date": "2026-11-15", "stat": "pass_cmp", "line": 19.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert list(out["status"]) == ["stat", "stat", "stat", "stat"]
    assert list(out["actual"]) == [245.0, 1.0, 31.0, 20.0]
    assert all(gid == "00-QB1" for gid in out["gsis_id"])


def test_unknown_player_and_unknown_stat_are_unresolved(stub_load):
    board = pd.DataFrame([
        {"player": "Nobody Real", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 10.5},
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "sacks_made", "line": 0.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert list(out["status"]) == ["unresolved", "unresolved"]
    assert all(math.isnan(v) for v in out["actual"])
    assert list(out["gradeable"]) == [False, False]
    assert "unknown_stat" in out.iloc[1]["reason"]


def test_anytime_td_composes_rush_and_rec_tds_and_excludes_passing_tds(stub_load):
    board = pd.DataFrame([
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "anytime_td", "line": 0.5},
        {"player": "Michael Penix Jr.", "team": "ATL", "game_date": "2026-11-15", "stat": "anytime_td", "line": 0.5},
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "rush_tds", "line": 0.5},
        {"player": "Bijan Robinson", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_tds", "line": 0.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert list(out["status"]) == ["stat", "stat", "stat", "stat"]
    # Bijan had 1 rush TD + 1 rec TD => anytime_td = 1.0; Penix had 2 pass TDs and 0 rush/rec TDs => 0.0
    assert list(out["actual"]) == [1.0, 0.0, 1.0, 1.0]
    assert list(out["gradeable"]) == [True, True, True, True]


def test_team_disambiguates_same_name_players_and_summary(stub_load):
    board = pd.DataFrame([
        {"player": "Josh Allen", "team": "BUF", "game_date": "2026-11-15", "stat": "pass_yds", "line": 250.5},
        {"player": "Josh Allen", "team": "JAC", "game_date": "2026-11-15", "stat": "anytime_td", "line": 0.5},
        {"player": "Drake London", "team": "ATL", "game_date": "2026-11-15", "stat": "receptions", "line": 4.5},
        {"player": "Dnp Receiver", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 24.5},
        {"player": "Absent Player", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 19.5},
        {"player": "Nobody Real", "team": "ATL", "game_date": "2026-11-15", "stat": "rec_yds", "line": 10.5},
    ])
    out = OM.resolve_board_outcomes(board, 2026)
    assert out.iloc[0]["gsis_id"] == "00-SAME1"
    assert out.iloc[0]["status"] == "stat"
    assert out.iloc[1]["gsis_id"] == "00-SAME2"
    # On JAC roster (ACT) but no snap_counts / player_stats row -> unresolved
    assert out.iloc[1]["status"] == "unresolved"

    summary = OM.summarize_resolution(out)
    assert summary["total"] == 6
    assert summary["counts"] == {
        "stat": 1,
        "played_zero": 1,
        "dnp": 1,
        "inactive": 1,
        "unresolved": 2,
    }
    assert summary["clean_resolved"] == 4
    assert math.isclose(summary["clean_resolution_rate"], 4 / 6)
    assert summary["gradeable"] == 2
