"""Tests for mlb_core.data.id_resolver (offline; caches primed, no network)."""

from mlb_core.data import id_resolver as R


def test_build_roster_norm_names():
    box = {"teams": {
        "home": {"players": {"ID1": {"person": {"id": 545361, "fullName": "Mike Trout"}}}},
        "away": {"players": {"ID2": {"person": {"id": 592450, "fullName": "Aaron Judge"}}}}}}
    assert R._build_roster(box) == {"mike trout": 545361, "aaron judge": 592450}


def test_boxscore_fallback_resolves_season_miss(monkeypatch):
    # season index empty -> miss; boxscore roster (via game_pk) resolves
    monkeypatch.setitem(R._player_cache, "2024", ({}, {}))
    monkeypatch.setitem(R._roster_cache, 745101, {"mike trout": 545361})
    assert R.resolve_player_id("Mike Trout", "LAA", "2024-05-01") is None          # no game_pk
    assert R.resolve_player_id("Mike Trout", "LAA", "2024-05-01", game_pk=745101) == 545361
    assert R.resolve_player_id("Nobody Here", "LAA", "2024-05-01", game_pk=745101) is None


def test_season_index_still_wins_first(monkeypatch):
    # when season index has it, boxscore is not needed
    monkeypatch.setitem(R._player_cache, "2024",
                        ({"jose ramirez": {608070}}, {("jose ramirez", "CLE"): 608070}))
    monkeypatch.setitem(R._roster_cache, 1, {})   # empty roster -> would miss
    assert R.resolve_player_id("Jose Ramirez", "CLE", "2024-05-01", game_pk=1) == 608070


def test_norm_suffix_and_punct():
    assert R._norm("Lourdes Gurriel Jr.") == R._norm("Lourdes Gurriel") == "lourdes gurriel"
    assert R._norm("T.J. Rumfield") == "tj rumfield"
    assert R._norm("Ronald Acuna Jr.") == "ronald acuna"
    assert R._norm("Jose O'Neill") == "jose oneill"


def test_is_player_name_filters_junk():
    assert R.is_player_name("Mike Trout")
    assert not R.is_player_name("{optionTypeAbbr}{value} HR")
    assert not R.is_player_name("Los Angeles Angels @ Seattle Mariners")
    assert not R.is_player_name("")
