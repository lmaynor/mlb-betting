"""PlayerResolver / team tables: the cross-book name problems found in live NHL/NBA data (2026-10-14)."""
import pytest

from mlb_core.data.player_names import PlayerResolver, norm_name, split_team_tail
from nba import teams as nba_teams

NHL_ROWS = [
    {"player_id": 8471214, "full_name": "Alex Ovechkin", "team": "WSH"},
    {"player_id": 8481559, "full_name": "Jack Hughes", "team": "NJD"},
    {"player_id": 8480800, "full_name": "Jack Hughes", "team": "VAN"},          # SYNTHETIC namesake (not a real player)
    {"player_id": 8478402, "full_name": "Connor McDavid", "team": "EDM"},
    {"player_id": 8477934, "full_name": "Alexandre Texier", "team": "MTL"},
    {"player_id": 1, "full_name": "Sebastian Aho", "team": "CAR"},
    {"player_id": 2, "full_name": "Sebastian Aho", "team": "NYI"},
]


@pytest.fixture
def r():
    return PlayerResolver(NHL_ROWS)


def test_norm_and_tail():
    assert norm_name("Kelly Oubre Jr.") == "kelly oubre" and norm_name("Ja'Marr Chase") == "jamarr chase"
    assert split_team_tail("Alex Ovechkin (WSH)") == ("Alex Ovechkin", "WSH")
    assert split_team_tail("Alex Ovechkin") == ("Alex Ovechkin", None)


def test_team_suffix_and_plain_name_resolve_to_same_player(r):
    assert r.resolve("Alex Ovechkin (WSH)")[0] == r.resolve("Alex Ovechkin")[0] == 8471214


def test_nickname_variant_resolves_by_compat_first_name(r):
    pid, how = r.resolve("Alexander Ovechkin")             # live: one book spelled it out
    assert (pid, how) == (8471214, "compat")
    assert r.resolve("Alex Texier")[0] == 8477934          # prefix-compatible with Alexandre


def test_same_name_two_players_fails_closed_without_a_hint(r):
    assert r.resolve("Jack Hughes") == (None, "ambiguous")


def test_same_name_resolved_by_team_hint_or_event_teams(r):
    assert r.resolve("Jack Hughes", "VAN") == (8480800, "team")
    assert r.resolve("Jack Hughes (NJD)")[0] == 8481559
    assert r.resolve("Jack Hughes", within=["VAN", "CGY"])[0] == 8480800     # only one of them is playing
    assert r.resolve("Sebastian Aho", within=["CAR", "TBL"])[0] == 1


def test_within_never_invents_a_candidate(r):
    assert r.resolve("Jack Hughes", within=["BOS", "TOR"]) == (None, "ambiguous")   # neither plays -> still ambiguous
    assert r.resolve("Alex Ovechkin", within=["BOS", "TOR"])[0] == 8471214           # unique name: not blocked


def test_unknown_and_single_token(r):
    assert r.resolve("Nobody Here") == (None, "unknown") and r.resolve("Ovechkin") == (None, "unknown")


def test_different_player_same_last_name_is_not_merged(r):
    assert r.resolve("Connor Aho") == (None, "unknown")


# ----------------------------------------------------------------------------- NBA teams
def test_nba_team_table_is_complete_and_unique():
    assert len(nba_teams.TEAMS) == 30
    assert len({t[0] for t in nba_teams.TEAMS}) == len({t[2] for t in nba_teams.TEAMS}) == 30


@pytest.mark.parametrize("raw,code", [
    ("L.A. Clippers", "LAC"), ("LA Clippers", "LAC"), ("Los Angeles Lakers", "LAL"), ("GS", "GSW"),
    ("Golden State Warriors", "GSW"), ("NY", "NYK"), ("UTAH", "UTA"), ("WSH", "WAS"), ("Philadelphia 76ers", "PHI"),
    ("san antonio spurs", "SAS"), ("NO", "NOP")])
def test_nba_team_code_aliases(raw, code):
    assert nba_teams.team_code(raw) == code


def test_nba_codes_match_the_kalshi_adapter():
    from ev.sports.nba import CODES
    assert {t[0] for t in nba_teams.TEAMS} == set(CODES)       # one vocabulary, two modules
