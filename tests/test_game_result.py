"""Regression tests for mlb_core.data.game_result.

test_empty_pitchers_list_does_not_crash covers a real bug found 2026-08-20:
it crashed the SB historical boxscore backfill mid-run on a real game whose
boxscore had "pitchers": [] (an empty list, not a missing key) for one side.
"""
from unittest.mock import patch

from mlb_core.data import game_result


def _fake_schedule():
    return {"dates": [{"games": [{"status": {"abstractGameState": "Final"}}]}]}


def _fake_linescore():
    return {"innings": [{"num": 1, "away": {"runs": 0}, "home": {"runs": 1}}]}


def _boxscore_with_empty_pitchers_list():
    """One side's boxscore has "pitchers": [] -- an empty list, not absent.
    team.get("pitchers", [None])[0] does NOT fall back for this case (the
    default only applies when the key itself is missing) -- [0] on an empty
    list raises IndexError. Confirmed on a real historical game_pk."""
    return {
        "teams": {
            "away": {
                "team": {"name": "New York Yankees"},
                "pitchers": [],   # <- empty list, the actual bug trigger
                "players": {},
            },
            "home": {
                "team": {"name": "Boston Red Sox"},
                "pitchers": [12345],
                "players": {
                    "ID12345": {
                        "person": {"fullName": "Some Pitcher", "id": 12345},
                        "stats": {"pitching": {"gamesPlayed": 1, "strikeOuts": 5, "outs": 15}},
                    },
                },
            },
        }
    }


def test_empty_pitchers_list_does_not_crash():
    responses = [_fake_schedule(), _fake_linescore(), _boxscore_with_empty_pitchers_list()]

    def fake_get(path, params=None):
        return responses.pop(0)

    with patch.object(game_result, "_get", side_effect=fake_get):
        result = game_result.fetch_game_result(123456)

    assert result is not None
    assert result["game_pk"] == 123456
    # The empty-pitchers side should simply have no pitcher entries -- not crash.
    assert "some pitcher" in result["pitchers"]


def test_team_and_caught_stealing_fields_present():
    """Regression coverage for the two fields added for the SB model
    (2026-08-20): team abbreviation and caught_stealing, both additive."""
    responses = [_fake_schedule(), _fake_linescore(), {
        "teams": {
            "away": {
                "team": {"name": "New York Yankees"},
                "pitchers": [1],
                "players": {
                    "ID1": {
                        "person": {"fullName": "Aaron Judge", "id": 2},
                        "battingOrder": "200",
                        "stats": {"batting": {
                            "gamesPlayed": 1, "atBats": 4, "hits": 1,
                            "stolenBases": 1, "caughtStealing": 1,
                        }},
                    },
                },
            },
            "home": {"team": {"name": "Boston Red Sox"}, "pitchers": [], "players": {}},
        },
    }]

    def fake_get(path, params=None):
        return responses.pop(0)

    with patch.object(game_result, "_get", side_effect=fake_get):
        result = game_result.fetch_game_result(1)

    judge = result["batters"]["aaron judge"]
    assert judge["team"] == "NYY"
    assert judge["stolen_bases"] == 1
    assert judge["caught_stealing"] == 1
