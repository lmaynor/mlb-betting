"""Tests for nhl.data.flatten against a real (trimmed) 2026-10-13 NJD@DET fixture (OT, 5-4)."""
import copy
import json
from pathlib import Path

import pytest

from nhl.data.flatten import (_situation, flatten_game, shot_geometry, toi_seconds)

FIX = Path(__file__).parent / "fixtures"
AWAY, HOME = 1, 17            # NJD, DET


@pytest.fixture
def box():
    return json.loads((FIX / "nhl_boxscore_sample.json").read_text())


@pytest.fixture
def pbp():
    return json.loads((FIX / "nhl_pbp_sample.json").read_text())


def test_toi_seconds():
    assert toi_seconds("12:14") == 734
    assert toi_seconds("61:05") == 3665       # goalie who played OT
    assert toi_seconds("00:00") == 0
    assert toi_seconds(None) is None
    assert toi_seconds("bad") is None


def test_game_row(box, pbp):
    g = flatten_game(box, pbp)["game"]
    assert g["game_id"] == 2026020090
    assert g["season_year"] == 2026 and g["game_type"] == 2
    assert g["date"] == "2026-10-13"
    assert (g["away_abbr"], g["home_abbr"]) == ("NJD", "DET")
    assert g["away_name"] == "New Jersey Devils" and g["home_name"] == "Detroit Red Wings"
    assert (g["away_score"], g["home_score"]) == (5, 4)
    assert g["last_period_type"] == "OT" and g["went_ot"] is True and g["went_so"] is False
    assert g["n_periods"] == 4


def test_goals_ex_so_matches_final_score_when_no_shootout(box, pbp):
    g = flatten_game(box, pbp)["game"]
    assert (g["away_goals_ex_so"], g["home_goals_ex_so"]) == (5, 4)
    assert g["away_p1"] + g["away_p2"] + g["away_p3"] + g["away_ot"] == 5
    assert g["home_p1"] + g["home_p2"] + g["home_p3"] + g["home_ot"] == 4
    assert g["away_ot"] + g["home_ot"] == 1             # an OT game has exactly one OT goal


def test_shootout_goal_excluded_from_ex_so(box, pbp):
    """The official score credits the shootout winner +1; ex_so must not."""
    box = copy.deepcopy(box)
    pbp = copy.deepcopy(pbp)
    box["gameOutcome"] = {"lastPeriodType": "SO"}
    box["awayTeam"]["score"] = 5 + 1                      # shootout winner gets +1 in the official score
    so_goal = copy.deepcopy([p for p in pbp["plays"] if p["typeDescKey"] == "goal"][0])
    so_goal["periodDescriptor"] = {"number": 5, "periodType": "SO", "maxRegulationPeriods": 3}
    so_goal["details"]["eventOwnerTeamId"] = AWAY
    pbp["plays"].append(so_goal)
    g = flatten_game(box, pbp)["game"]
    assert g["went_so"] is True and g["went_ot"] is True
    assert g["away_score"] == 6
    assert g["away_goals_ex_so"] == 5                     # shootout goal not counted


def test_no_pbp_keeps_schema_but_nulls_pbp_columns(box):
    out = flatten_game(box, None)
    g = out["game"]
    assert out["shots"] == []
    assert g["away_goals_ex_so"] is None and g["home_p1"] is None      # None, NOT 0 (0 would claim a 0-0)
    assert set(flatten_game(box, {"plays": [], "rosterSpots": []})["game"]) == set(g)   # stable columns


def test_full_names_from_roster_spots_disambiguate_duplicates(box, pbp):
    sk = flatten_game(box, pbp)["skaters"]
    det_johansson = [s for s in sk if s["team_abbr"] == "DET" and "Johansson" in s["name"]]
    assert sorted(s["name"] for s in det_johansson) == ["Albert Johansson", "Anton Johansson"]
    assert len({s["player_id"] for s in det_johansson}) == 2
    assert not any(s["name"][1:3] == ". " for s in sk)                  # no abbreviated names left


def test_names_fall_back_to_abbreviated_without_pbp(box):
    sk = flatten_game(box, None)["skaters"]
    assert any(s["name"] == "S. Dries" for s in sk)


def test_skater_rows(box, pbp):
    sk = {s["player_id"]: s for s in flatten_game(box, pbp)["skaters"]}
    deb = sk[8479337]                                   # Alex DeBrincat: 2G 1A, 6 SOG
    assert (deb["goals"], deb["assists"], deb["points"], deb["sog"]) == (2, 1, 3, 6)
    assert deb["team_abbr"] == "DET" and deb["is_home"] is True
    assert deb["toi_sec"] == 21 * 60 + 25 and deb["played"] is True
    assert deb["position"] == "R"


def test_goalie_rows(box, pbp):
    gl = {g["player_id"]: g for g in flatten_game(box, pbp)["goalies"]}
    gibson, allen, tarasov = gl[8476434], gl[8474596], gl[8480193]
    assert (gibson["saves"], gibson["shots_against"], gibson["goals_against"]) == (32, 37, 5)
    assert (gibson["es_saves"], gibson["es_shots"], gibson["pp_saves"], gibson["pp_shots"]) == (31, 35, 1, 2)
    assert gibson["decision"] == "O" and gibson["starter"] is True and gibson["toi_sec"] == 3665
    assert allen["decision"] == "W"
    assert tarasov["played"] is False and tarasov["starter"] is False       # backup who never entered
    assert gibson["name"] == "John Gibson"


def test_team_rows_tie_back_to_box_and_players(box, pbp):
    out = flatten_game(box, pbp)
    teams = {t["team_abbr"]: t for t in out["teams"]}
    assert len(teams) == 2
    assert (teams["NJD"]["goals"], teams["NJD"]["goals_against"]) == (5, 4)
    assert teams["DET"]["is_home"] is True and teams["DET"]["opp_abbr"] == "NJD"
    for abbr, t in teams.items():
        assert t["goals"] == sum(s["goals"] for s in out["skaters"] if s["team_abbr"] == abbr)
    # shots faced by NJD's goalies == DET's team SOG
    assert teams["NJD"]["shots_against"] == teams["DET"]["sog"]


def test_shot_rows(box, pbp):
    shots = flatten_game(box, pbp)["shots"]
    by = {}
    for s in shots:
        by.setdefault(s["event"], []).append(s)
    assert {k: len(v) for k, v in by.items()} == {"goal": 9, "sog": 4, "miss": 4, "block": 4}
    goal = next(s for s in by["goal"] if s["event_id"] == 131)
    assert goal["shooter_id"] == 8478542 and goal["assist1_id"] == 8476474
    assert goal["team_id"] == AWAY and goal["is_home"] is False
    assert goal["time_sec"] == 6 * 60 + 7 and goal["period"] == 1
    assert goal["dist_ft"] == pytest.approx(22.56, abs=0.01)              # hypot(89-67, -5)
    assert goal["skaters_for"] == 5 and goal["skaters_against"] == 5 and goal["empty_net"] is False
    blk = by["block"][0]
    assert blk["blocking_player_id"] is not None and blk["shooter_id"] is not None


def test_shot_geometry_attacks_the_opposite_goal():
    # home defends RIGHT (x=+89): AWAY attacks right, HOME attacks left
    away = shot_geometry(67, -5, AWAY, HOME, "right")
    home = shot_geometry(-67, -5, HOME, HOME, "right")
    assert away == home                                   # mirror images -> same distance/angle
    assert away[0] == pytest.approx(22.56, abs=0.01)
    # same coordinates but from the wrong end = a long shot at the goal that team ATTACKS
    far = shot_geometry(-67, -5, AWAY, HOME, "right")
    assert far[0] > 150
    # dead-centre slot shot: 0 degrees
    assert shot_geometry(79, 0, AWAY, HOME, "right") == (10.0, 0.0)
    # missing data
    assert shot_geometry(None, 0, AWAY, HOME, "right") == (None, None)
    assert shot_geometry(10, 0, AWAY, HOME, None) == (None, None)


def test_situation_code_decoding():
    # digits = away goalie, away skaters, home skaters, home goalie
    assert _situation("1551", AWAY, AWAY) == (5, 5, False)
    assert _situation("1451", HOME, AWAY) == (5, 4, False)        # away is a man down; home shooter on the PP
    assert _situation("1451", AWAY, AWAY) == (4, 5, False)
    assert _situation("0651", HOME, AWAY) == (5, 6, True)         # away goalie pulled -> home shoots at an empty net
    assert _situation("0651", AWAY, AWAY) == (6, 5, False)        # ... while the away team is the one with the extra man
    assert _situation(None, AWAY, AWAY) == (None, None, None)
    assert _situation("55", AWAY, AWAY) == (None, None, None)
