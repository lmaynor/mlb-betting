"""Tests for nba.data.flatten against a real (trimmed) SportsBlaze fixture."""
import json
from pathlib import Path

import pytest

from nba.config import STAT_FIELDS
from nba.data.flatten import flatten_boxscores, flatten_event

FIXTURE = Path(__file__).parent / "fixtures" / "nba_boxscore_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text())


def test_flatten_shapes(raw):
    games, team_box, player_box = flatten_boxscores(raw)
    assert len(games) == 1
    assert len(team_box) == 2                     # away + home
    assert len(player_box) == 6                   # 3 per side in the trimmed fixture


def test_game_row_fields(raw):
    ev = raw["events"][0]
    game, _, _ = flatten_event(ev)
    assert game["game_id"] == ev["id"]
    assert game["season_year"] == 2025
    assert game["season_type"] == "Regular Season"
    assert game["date"] == "2026-03-15"
    assert game["away_abbr"] == "MIN"
    assert game["home_abbr"] == "OKC"
    assert game["away_points"] == 103
    assert game["home_points"] == 116
    # quarter scores present for q1..q4, OT absent (regulation game)
    for i in range(1, 5):
        assert game[f"away_q{i}"] is not None
        assert game[f"home_q{i}"] is not None
    assert game["away_ot"] is None
    assert game["home_ot"] is None


def test_team_rows_have_all_stats(raw):
    _, team_box, _ = flatten_boxscores(raw)
    home = [t for t in team_box if t["is_home"]][0]
    assert home["team_abbr"] == "OKC"
    assert home["opp_abbr"] == "MIN"
    assert home["points"] == 116
    for f in STAT_FIELDS:
        assert f in home


def test_player_rows_have_all_stats_and_meta(raw):
    _, _, player_box = flatten_boxscores(raw)
    edwards = [p for p in player_box if p["name"] == "Anthony Edwards"][0]
    assert edwards["starter"] is True
    assert edwards["played"] is True
    assert edwards["position"] == "SG"
    assert edwards["points"] == 19
    assert edwards["team_abbr"] == "MIN"
    assert edwards["is_home"] is False
    for f in STAT_FIELDS:
        assert f in edwards


def test_empty_events_yield_nothing():
    games, team_box, player_box = flatten_boxscores({"events": []})
    assert games == [] and team_box == [] and player_box == []
    # tolerate a None / missing-key payload too
    assert flatten_boxscores(None) == ([], [], [])
    assert flatten_boxscores({}) == ([], [], [])


def test_overtime_aggregation():
    """A synthetic game with a 5th period should populate *_ot."""
    ev = {
        "id": "ot-game", "season": {"year": 2025, "type": "Regular Season"},
        "date": "2026-01-02T00:00:00.000Z", "status": "Final", "live": False,
        "teams": {"away": {"id": "a", "name": "A", "abbreviation": "AAA"},
                  "home": {"id": "h", "name": "H", "abbreviation": "HHH"}},
        "scores": {"total": {"away": 120, "home": 118},
                   "periods": {"1": {"away": 30, "home": 28},
                               "2": {"away": 28, "home": 30},
                               "3": {"away": 27, "home": 29},
                               "4": {"away": 23, "home": 21},
                               "5": {"away": 12, "home": 10}}},
        "statistics": {}, "players": {},
    }
    game, team_box, player_box = flatten_event(ev)
    assert game["away_ot"] == 12
    assert game["home_ot"] == 10
    assert team_box == [] and player_box == []   # no stats blocks -> no rows
