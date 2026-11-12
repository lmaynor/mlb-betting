"""Tests for first-scorer outcomes derivation (NBA first basket, both variants; NFL first TD).

Offline: synthetic play-by-play frames in the confirmed hoopR / nflverse column shapes. The
disabled-by-default gate of scripts/derive_first_scorer_outcomes.py is tripwired: with either
gate missing, the run functions may not be called at all.
"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from nba.data import first_basket_outcomes as NBA
from nfl.data import first_td_outcomes as NFL

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "derive_first_scorer_outcomes", ROOT / "scripts" / "derive_first_scorer_outcomes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def nba_play(**kw):
    base = {"game_id": "401", "season": 2026, "season_type": 2, "game_date": "2025-11-01",
            "period_number": 1, "sequence_number": 1, "scoring_play": False, "shooting_play": False,
            "type_text": "", "text": "", "athlete_id_1": None, "athlete_name_1": None,
            "team_id": 18.0, "home_team_id": 18, "home_team_abbrev": "NY", "away_team_abbrev": "BOS",
            "score_value": 0}
    base.update(kw)
    return base


def nba_frame_ft_first():
    """Game opens on a made free throw (away), then a home dunk, then more scoring."""
    return pd.DataFrame([
        nba_play(sequence_number=1, text="jump ball"),
        nba_play(sequence_number=2, scoring_play=True, type_text="Free Throw", score_value=1,
                 text="Jayson Tatum makes free throw 1 of 2", athlete_id_1=100.0,
                 athlete_name_1="Jayson Tatum", team_id=2.0),
        nba_play(sequence_number=3, scoring_play=True, shooting_play=True, type_text="Dunk", score_value=2,
                 text="Jalen Brunson makes dunk", athlete_id_1=200.0, athlete_name_1="Jalen Brunson"),
        nba_play(sequence_number=4, scoring_play=True, shooting_play=True, type_text="Jump Shot", score_value=2,
                 text="Jayson Tatum makes jump shot", athlete_id_1=100.0,
                 athlete_name_1="Jayson Tatum", team_id=2.0),
    ])


def test_both_settlement_variants_are_stored_and_differ_on_an_ft_opener():
    out = NBA.derive_first_basket(nba_frame_ft_first())
    assert len(out) == 1
    r = out.iloc[0]
    # first POINT (FT-inclusive variant) is the free throw; first FIELD GOAL is the dunk
    assert r["first_point_player"] == "Jayson Tatum" and r["first_point_method"] == "free_throw"
    assert r["first_point_team"] == "BOS"
    assert r["first_fg_player"] == "Jalen Brunson" and r["first_fg_method"] == "dunk"
    assert r["first_fg_team"] == "NY"
    # per-team first scorers for player_first_team_basket
    assert r["first_team_point_home_player"] == "Jalen Brunson"
    assert r["first_team_point_away_player"] == "Jayson Tatum"
    assert r["first_team_fg_home_player"] == "Jalen Brunson"
    assert r["first_team_fg_away_player"] == "Jayson Tatum"


def test_variants_agree_when_the_opener_is_a_field_goal():
    df = pd.DataFrame([
        nba_play(sequence_number=1, scoring_play=True, shooting_play=True, type_text="Layup", score_value=2,
                 text="Jalen Brunson makes layup", athlete_id_1=200.0, athlete_name_1="Jalen Brunson"),
    ])
    r = NBA.derive_first_basket(df).iloc[0]
    assert r["first_point_player"] == r["first_fg_player"] == "Jalen Brunson"
    assert r["first_fg_method"] == "layup"


def test_method_classification():
    assert NBA.classify_method("X makes three point jumper") == "three_pointer"
    assert NBA.classify_method("X makes free throw 1 of 1") == "free_throw"
    assert NBA.classify_method("X makes hook shot") == "hook_shot"
    assert NBA.classify_method("X makes tip shot") == "tip_in"
    assert NBA.classify_method("X makes something unlisted") == "other"


def test_ordering_uses_sequence_not_file_order_and_unplayed_games_are_omitted():
    df = nba_frame_ft_first().iloc[::-1]                       # reversed file order
    df = pd.concat([df, pd.DataFrame([nba_play(game_id="999", text="tip only")])], ignore_index=True)
    out = NBA.derive_first_basket(df)
    assert set(out["game_id"]) == {"401"}                      # game 999 has no scoring play: omitted
    assert out.iloc[0]["first_point_player"] == "Jayson Tatum"


def test_empty_and_missing_column_frames_fail_safe():
    assert NBA.derive_first_basket(pd.DataFrame()).empty
    assert NBA.derive_first_basket(pd.DataFrame({"game_id": [1]})).empty
    assert list(NBA.derive_first_basket(pd.DataFrame()).columns) == NBA.OUTCOME_COLUMNS


def test_nba_outcome_key_layout():
    assert NBA.outcome_key(2026) == "FirstScorer/outcomes/basketball_nba/first_basket_2026.parquet"


# -------------------------------------------------------------------------------- NFL
def nfl_play(**kw):
    base = {"game_id": "2025_01_KC_BAL", "season": 2025, "week": 1, "season_type": "REG",
            "game_date": "2025-09-05", "home_team": "BAL", "away_team": "KC", "posteam": "KC",
            "play_id": 1, "touchdown": 0, "td_player_id": None, "td_player_name": None,
            "td_team": None, "pass": 0, "rush": 0, "pass_touchdown": 0, "rush_touchdown": 0,
            "return_touchdown": 0, "qtr": 1, "yards_gained": 0, "desc": ""}
    base.update(kw)
    return base


def test_first_td_is_the_earliest_touchdown_play():
    df = pd.DataFrame([
        nfl_play(play_id=30, desc="field goal good"),
        nfl_play(play_id=55, touchdown=1, pass_touchdown=1, **{"pass": 1}, td_player_id="00-003",
                 td_player_name="Xavier Worthy", td_team="KC", yards_gained=21,
                 desc="Mahomes pass to Worthy for 21 yards, touchdown"),
        nfl_play(play_id=90, touchdown=1, rush=1, rush_touchdown=1, posteam="BAL",
                 td_player_id="00-004", td_player_name="Derrick Henry", td_team="BAL",
                 desc="Henry rush for 5 yards, touchdown"),
    ])
    out = NFL.derive_first_td(df)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["first_td_player"] == "Xavier Worthy" and r["first_td_method"] == "pass"
    assert r["first_td_team"] == "KC" and r["first_td_play_id"] == 55


def test_first_td_rush_and_no_td_game_omitted():
    df = pd.DataFrame([
        nfl_play(play_id=10, touchdown=1, rush=1, rush_touchdown=1, td_player_id="00-004",
                 td_player_name="Derrick Henry", td_team="BAL", desc="Henry rush touchdown"),
        nfl_play(game_id="2025_01_DEN_NE", play_id=5, desc="punt"),
    ])
    out = NFL.derive_first_td(df)
    assert len(out) == 1 and out.iloc[0]["first_td_method"] == "rush"
    assert NFL.derive_first_td(pd.DataFrame()).empty
    assert NFL.outcome_key(2025) == "FirstScorer/outcomes/americanfootball_nfl/first_td_2025.parquet"


# -------------------------------------------------------------------------------- the two-key gate
def test_runner_gate_and_plan_mode_never_run(monkeypatch):
    mod = _load_script()
    assert mod.execution_allowed(False, {})[0] is False
    assert mod.execution_allowed(True, {})[0] is False
    assert mod.execution_allowed(True, {"FIRST_SCORER_OUTCOMES_ENABLED": "1"})[0] is True
    calls = []
    monkeypatch.setattr(mod, "run_nba", lambda *a, **k: calls.append("nba"))
    monkeypatch.setattr(mod, "run_nfl", lambda *a, **k: calls.append("nfl"))
    assert mod.main(["--sport", "basketball_nba"], environ={}) == 0
    assert mod.main(["--sport", "americanfootball_nfl", "--execute"], environ={}) == 0
    assert mod.main(["--sport", "basketball_nba"], environ={"FIRST_SCORER_OUTCOMES_ENABLED": "1"}) == 0
    assert calls == []


def test_runner_season_parsing():
    mod = _load_script()
    assert mod._parse_seasons(["2019-2021", "2023"]) == [2019, 2020, 2021, 2023]
    plan = mod.build_plan("basketball_nba", [2025, 2026])
    assert "hoopr" in plan["source"] and plan["target_prefix"].endswith("basketball_nba/")
