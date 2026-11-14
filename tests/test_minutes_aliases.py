"""nba.minutes_restriction.aliases (MIN-06): the minutes program's alias resolution layer.

Every name pair below is REAL, mined 2026-11-13 from the program's own data: the hoopR box-score
mirror (NBA identity: athlete_id + display name + team history, ~/workspace/minutes-run-data/hoopr),
the banked official injury reports (~/workspace/minutes-run-data/reports), the NBC and RotoWire
player maps, and the banked prose corpora (~/workspace/arm-t-smoke-data). Nothing is synthetic:
where a player is absent from the roster (Thomas Sorber, Graham Ike, Nikola Djurisic -- listed by
the official report / NBC but with no hoopR box rows), the pinned behaviour is UNKNOWN, forever.
"""
import pandas as pd
import pytest

from nba.minutes_restriction.aliases import AliasResolver, RosterIndex, canonical_key

# (player_id, ESPN display name, team history) -- real rows from the hoopR mirror, 2026-11-13.
ROSTER_ROWS = [
    {"player_id": 4067017, "full_name": "A.J. Lawson", "teams": ["DAL", "MIN", "TOR"]},
    {"player_id": 4397475, "full_name": "AJ Green", "teams": ["MIL"]},
    {"player_id": 3945274, "full_name": "Luka Doncic", "teams": ["DAL", "LAL"]},
    {"player_id": 3112335, "full_name": "Nikola Jokic", "teams": ["DEN"]},
    {"player_id": 3102530, "full_name": "Jusuf Nurkic", "teams": ["CHA", "PHX", "POR", "UTAH"]},
    {"player_id": 6478, "full_name": "Nikola Vucevic", "teams": ["BOS", "CHI"]},
    {"player_id": 5217746, "full_name": "Yang Hansen", "teams": ["POR"]},
    {"player_id": 4397450, "full_name": "Darius Brown II", "teams": ["CLE"]},
    {"player_id": 4278067, "full_name": "Nic Claxton", "teams": ["BKN"]},
    {"player_id": 3138196, "full_name": "Cameron Johnson", "teams": ["BKN", "DEN", "PHX"]},
    {"player_id": 3934620, "full_name": "Trevon Scott", "teams": ["BKN"]},
    {"player_id": 4711005, "full_name": "Trevon Scott", "teams": ["CLE"]},
    {"player_id": 4593803, "full_name": "Jalen Williams", "teams": ["OKC"]},
    {"player_id": 4432823, "full_name": "Jaylin Williams", "teams": ["OKC"]},
]

# The real player maps (player, <source>_id) as banked by the pullers.
NBC_MAP = {"11229": "A.J. Lawson", "00000188-9cf2-da6b-abd8-fcff197301b4": "AJ Green",
           "10213": "Aaron Gordon"}
ROTOWIRE_MAP = {"5398": "A.J. Lawson", "3577": "Aaron Gordon"}


@pytest.fixture(scope="module")
def resolver():
    return AliasResolver(RosterIndex.from_rows(ROSTER_ROWS),
                         source_maps={"nbc": NBC_MAP, "rotowire": ROTOWIRE_MAP})


# --------------------------------------------------------------------------------------
# canonical_key: the folding the corpus prose needs (cases._norm_name punches diacritics out)
@pytest.mark.parametrize("prose, roster", [
    ("Luka Dončić", "Luka Doncic"),          # 61 hits in the banked prose
    ("Nikola Jokić", "Nikola Jokic"),        # 17 hits
    ("Nikola Vučević", "Nikola Vucevic"),    # 7 hits
    ("Jusuf Nurkić", "Jusuf Nurkic"),        # 3 hits
])
def test_canonical_key_folds_diacritics(prose, roster):
    assert canonical_key(prose) == canonical_key(roster)


def test_canonical_key_drops_suffixes_and_punctuation():
    assert canonical_key("Darius Brown II") == canonical_key("Darius Brown")
    assert canonical_key("A.J. Lawson") == canonical_key("AJ Lawson")


# --------------------------------------------------------------------------------------
# exact resolutions across sources
@pytest.mark.parametrize("name, pid", [
    ("A.J. Lawson", 4067017), ("AJ Green", 4397475),
    ("Luka Dončić", 3945274), ("Nikola Jokić", 3112335), ("Jusuf Nurkić", 3102530),
    ("Darius Brown", 4397450),               # official report omits the II ESPN carries
])
def test_exact_key_resolutions(resolver, name, pid):
    r = resolver.resolve(name)
    assert r.status == "resolved" and r.player_id == pid


def test_official_token_swap_resolves_uniquely(resolver):
    # the official report prints "Hansen Yang"; ESPN's identity is "Yang Hansen" (POR)
    r = resolver.resolve("Hansen Yang", team="POR")
    assert (r.status, r.method, r.player_id) == ("resolved", "token_swap", 5217746)


@pytest.mark.parametrize("name, pid", [
    ("Nicolas Claxton", 4278067),            # official long form vs ESPN "Nic Claxton"
    ("Cam Johnson", 3138196),                # prose short form vs ESPN "Cameron Johnson"
])
def test_first_name_variants(resolver, name, pid):
    r = resolver.resolve(name)
    assert (r.status, r.method, r.player_id) == ("resolved", "first_name_variant", pid)


# --------------------------------------------------------------------------------------
# fail closed: collisions, near names, and the genuinely absent
def test_same_name_two_players_is_ambiguous_without_a_team(resolver):
    r = resolver.resolve("Trevon Scott")
    assert r.status == "ambiguous"
    assert {c.player_id for c in r.candidates} == {3934620, 4711005}


@pytest.mark.parametrize("team, pid", [("CLE", 4711005), ("BKN", 3934620)])
def test_same_name_resolves_on_team_history(resolver, team, pid):
    r = resolver.resolve("Trevon Scott", team=team)
    assert (r.status, r.method, r.player_id) == ("resolved", "exact_key_team", pid)


def test_same_name_with_a_team_neither_played_for_stays_ambiguous(resolver):
    assert resolver.resolve("Trevon Scott", team="LAL").status == "ambiguous"


def test_near_names_never_merge(resolver):
    # Jalen and Jaylin Williams are different people on the same team; neither may absorb
    # the other, and a bare "Williams" initialism must not pick one.
    assert resolver.resolve("Jalen Williams").player_id == 4593803
    assert resolver.resolve("Jaylin Williams").player_id == 4432823


@pytest.mark.parametrize("name", [
    "Thomas Sorber",        # official report lists him; no hoopR box rows exist
    "Graham Ike",           # an NBC subject with no roster identity
    "Nikola Djurisic",      # official report; NOT Nikola Jokic despite the shared first name
    "Alex Toohey",          # official report; no roster identity
])
def test_listed_but_absent_players_stay_unknown(resolver, name):
    assert resolver.resolve(name).status == "unknown"


# --------------------------------------------------------------------------------------
# source ids: string-normalized, and map-vs-name disagreements are reported, not picked
def test_source_id_resolves_through_the_map(resolver):
    r = resolver.resolve_source_id("nbc", "11229")
    assert (r.status, r.method, r.player_id) == ("resolved", "source_id", 4067017)


def test_source_id_typing_is_unstable_in_the_bank_so_ids_compare_as_strings(resolver):
    assert resolver.resolve_source_id("nbc", 11229).player_id == 4067017
    assert resolver.resolve_source_id("nbc", "00000188-9cf2-da6b-abd8-fcff197301b4").player_id \
        == 4397475


def test_source_id_and_record_name_agreeing_is_resolved(resolver):
    r = resolver.resolve_source_id("rotowire", "5398", name="A.J. Lawson")
    assert (r.status, r.player_id) == ("resolved", 4067017)


def test_source_id_conflicting_with_the_record_name_is_reported_not_merged(resolver):
    r = resolver.resolve_source_id("nbc", "11229", name="Nikola Jokic")
    assert (r.status, r.method) == ("ambiguous", "source_id_conflict")


def test_unknown_source_id_is_unknown(resolver):
    assert resolver.resolve_source_id("nbc", "99999999").status == "unknown"


# --------------------------------------------------------------------------------------
# audit: the whole-source report (counts + the human work queue)
def test_audit_reports_counts_and_queues():
    res = AliasResolver(RosterIndex.from_rows(ROSTER_ROWS))
    out = res.audit({"official": ["Hansen Yang", "Darius Brown", "Thomas Sorber", "Trevon Scott"],
                     "nbc": ["Luka Dončić", "Graham Ike"]})
    assert out["sources"]["official"] == {"n_names": 4, "resolved": 2, "ambiguous": 1,
                                          "unknown": 1, "via_exact_key": 1, "via_token_swap": 1}
    assert out["sources"]["nbc"]["resolved"] == 1 and out["sources"]["nbc"]["unknown"] == 1
    assert {u["name"] for u in out["unknown"]} == {"Thomas Sorber", "Graham Ike"}
    assert out["ambiguous"][0]["name"] == "Trevon Scott"
    assert {c["player_id"] for c in out["ambiguous"][0]["candidates"]} == {3934620, 4711005}


def test_roster_index_from_box_uses_latest_display_and_full_team_history():
    box = pd.DataFrame([
        {"athlete_id": 1, "athlete_display_name": "Test Player", "team_abbreviation": "AAA",
         "game_date": "2025-01-01"},
        {"athlete_id": 1, "athlete_display_name": "Test Player", "team_abbreviation": "BBB",
         "game_date": "2026-01-01"},
        {"athlete_id": 2, "athlete_display_name": "Test Player", "team_abbreviation": "CCC",
         "game_date": "2026-01-01"},
    ])
    idx = RosterIndex.from_box(box)
    assert idx.identities[1].teams == frozenset({"AAA", "BBB"})
    r = AliasResolver(idx).resolve("Test Player", team="AAA")
    assert (r.status, r.player_id) == ("resolved", 1)      # history, not just the latest team
