"""nfl.props.resolve -- fail-closed player identity. Offline: synthetic frames shaped like the real nflverse mirror
(rosters_weekly / players / schedules); the namesake cases are modelled on pairs that really exist in the 2024-26
data (Lamar Jackson QB BAL / CB ATL, Josh Allen QB BUF / LB JAX, DeVonta Smith WR PHI / Devonta Smith CB CAR,
Michael Carter RB TEN / Michael Carter II CB PHI, Frank Gore / Frank Gore Jr., Marvin Harrison Sr. / Jr.)."""
import pandas as pd
import pytest

from nfl.props import resolve as R
from nfl.props.resolve import AMBIGUOUS, RESOLVED, UNKNOWN, PlayerResolver


def _roster(rows):
    """rows: (gsis, full_name, team, position, status, season, week[, first, last, football])"""
    recs = []
    for r in rows:
        gid, full, team, pos, status, season, week = r[:7]
        first, last, football = (r[7:10] + (None,) * 3)[:3] if len(r) > 7 else (None, None, None)
        if first is None:
            parts = full.split()
            first, last = parts[0], parts[-1]
        recs.append(dict(gsis_id=gid, full_name=full, team=team, position=pos, status=status, season=season,
                         week=week, first_name=first, last_name=last, football_name=football))
    return pd.DataFrame(recs)


def _weeks(gid, full, team, pos, season, weeks, status="ACT", **kw):
    return [(gid, full, team, pos, status, season, w) + tuple(kw.get(k) for k in ("first", "last", "football"))
            for w in weeks]


@pytest.fixture
def world():
    rows = []
    rows += _weeks("00-QB", "Lamar Jackson", "BAL", "QB", 2024, range(1, 4))
    rows += _weeks("00-CB", "Lamar Jackson", "ATL", "CB", 2024, range(1, 4))
    rows += _weeks("00-QB", "Lamar Jackson", "BAL", "QB", 2026, range(1, 4))              # CB gone by 2026
    rows += _weeks("00-BUFQ", "Josh Allen", "BUF", "QB", 2024, range(1, 4))
    rows += _weeks("00-JAXL", "Josh Allen", "JAX", "LB", 2024, range(1, 4))               # roster spells JAX
    rows += _weeks("00-WRSM", "DeVonta Smith", "PHI", "WR", 2026, range(1, 4))
    rows += _weeks("00-CBSM", "Devonta Smith", "CAR", "CB", 2026, range(1, 4))
    rows += _weeks("00-RBMC", "Michael Carter", "TEN", "RB", 2026, range(1, 4))
    rows += _weeks("00-CBMC", "Michael Carter II", "PHI", "CB", 2026, range(1, 4))
    rows += _weeks("00-GORE", "Frank Gore", "NYJ", "RB", 2018, range(1, 4))
    rows += _weeks("00-GOREJ", "Frank Gore Jr.", "BUF", "RB", 2026, range(1, 4))
    rows += _weeks("00-GORE", "Frank Gore", "NYJ", "RB", 2026, range(1, 4), status="CUT")  # stale CUT namesake
    rows += _weeks("00-HARS", "Marvin Harrison", "ARI", "WR", 2026, range(1, 4), status="DEV")
    rows += _weeks("00-HARJ", "Marvin Harrison Jr.", "ARI", "WR", 2026, range(1, 4))
    # a player traded between weeks 2 and 3
    rows += _weeks("00-TRAD", "Tyler Trade", "DAL", "WR", 2026, [1, 2])
    rows += _weeks("00-TRAD", "Tyler Trade", "NYG", "WR", 2026, [3])
    rows += _weeks("00-TRAD2", "Tyler Trade", "DAL", "WR", 2026, [3])                      # a DAL namesake from wk 3
    # nickname carried only by nflverse fields
    rows += _weeks("00-WARD", "Cameron Ward", "TEN", "QB", 2026, range(1, 4), first="Cameron", last="Ward",
                   football="Cameron")
    rows += _weeks("00-OKON", "Chig Okonkwo", "WAS", "TE", 2026, range(1, 4), first="Chigoziem", last="Okonkwo")
    rows += _weeks("00-JAX1", "Trevor Jax", "JAX", "QB", 2026, range(1, 4))
    rows += _weeks("00-LAR1", "Rams Guy", "LA", "WR", 2024, range(1, 4))                   # old spelling of the Rams
    rows += _weeks("00-PS1", "Sam Squad", "DEN", "WR", 2026, range(1, 4), status="DEV")
    rows += _weeks("00-ACT1", "Sam Squad", "KC", "WR", 2026, range(1, 4))
    ros = _roster(rows)
    ros = pd.concat([ros, _roster([("", "No Id", "KC", "WR", "DEV", 2026, 1)])], ignore_index=True)   # blank gsis row
    players = pd.DataFrame([
        dict(gsis_id="00-WARD", display_name="Cam Ward", first_name="Cameron", last_name="Ward",
             common_first_name="Cam", football_name="Cameron"),
        dict(gsis_id="00-OKON", display_name="Chig Okonkwo", first_name="Chigoziem", last_name="Okonkwo",
             common_first_name="Chig", football_name="Chigoziem"),
    ])
    sched = pd.DataFrame([
        dict(game_id="2026_02_DAL_NYG", season=2026, week=2, gameday="2026-09-20", away_team="DAL", home_team="NYG"),
        dict(game_id="2026_03_TEN_PHI", season=2026, week=3, gameday="2026-09-27", away_team="TEN", home_team="PHI"),
        dict(game_id="2026_03_JAX_KC", season=2026, week=3, gameday="2026-09-27", away_team="JAX", home_team="KC"),
    ])
    return PlayerResolver(ros, players=players, schedules=sched)


# ------------------------------------------------------------------------------------------- the basics
def test_unique_name_resolves_and_normalises(world):
    r = world.resolve("Marvin Harrison Jr.", season=2026, week=2)
    assert r.ok and r.gsis_id == "00-HARJ"                       # suffix stripped, DEV Sr. namesake dropped by 'active'
    assert world.resolve("CHIG OKONKWO", season=2026, week=1).gsis_id == "00-OKON"
    assert world.resolve("Chig  Okonkwo", season=2026).gsis_id == "00-OKON"


def test_unknown_name_never_fuzzy_matches(world):
    for bad in ("Lamar Jacksn", "L. Jackson", "Jackson", "", "   ", "Lamar", "Nobody Real"):
        r = world.resolve(bad, season=2024, week=1)
        assert r.status == UNKNOWN and r.gsis_id is None


def test_nickname_and_formal_name_via_nflverse_fields(world):
    # 'Cam Ward' (players.display_name / common_first_name) and 'Cameron Ward' (roster full_name / first_name)
    assert world.resolve("Cam Ward", season=2026, week=1).gsis_id == "00-WARD"
    assert world.resolve("Cameron Ward", season=2026, week=1).gsis_id == "00-WARD"
    assert world.resolve("Chigoziem Okonkwo", season=2026, week=1).gsis_id == "00-OKON"


def test_blank_gsis_rows_are_ignored(world):
    assert world.resolve("No Id", season=2026, week=1).status == UNKNOWN


# ------------------------------------------------------------------------------------------- namesakes
def test_namesakes_without_team_are_ambiguous_and_list_candidates(world):
    r = world.resolve("Lamar Jackson", season=2024, week=1)
    assert r.status == AMBIGUOUS and r.gsis_id is None
    assert set(r.candidates) == {"00-QB", "00-CB"}


def test_namesake_disambiguated_by_team_hint_and_tail(world):
    assert world.resolve("Lamar Jackson", season=2024, week=1, team="BAL").gsis_id == "00-QB"
    assert world.resolve("Lamar Jackson", season=2024, week=1, team="ATL").gsis_id == "00-CB"
    assert world.resolve("Lamar Jackson (ATL)", season=2024, week=1).gsis_id == "00-CB"


def test_namesake_disambiguated_by_roster_season(world):
    # the CB is not on a 2026 roster at all, so the bare name is safe THERE (and only there)
    assert world.resolve("Lamar Jackson", season=2026, week=1).gsis_id == "00-QB"
    assert world.resolve("Lamar Jackson", season=2024, week=1).status == AMBIGUOUS


def test_namesake_disambiguated_by_week(world):
    # Tyler Trade (DAL wk1-2 -> NYG wk3) and a DAL namesake who only appears wk3.
    assert world.resolve("Tyler Trade", season=2026, week=1).gsis_id == "00-TRAD"
    assert world.resolve("Tyler Trade", season=2026, week=3).status == AMBIGUOUS
    assert world.resolve("Tyler Trade", season=2026, week=3, team="NYG").gsis_id == "00-TRAD"
    assert world.resolve("Tyler Trade", season=2026, week=3, team="DAL").gsis_id == "00-TRAD2"


def test_team_hint_contradicting_the_only_candidate_fails_closed(world):
    # the only 2026 Josh Allen is... none (2024 only) -> use the QB: hint says JAX, he is BUF that week
    r = world.resolve("Josh Allen (JAX)", season=2024, week=1)
    assert r.ok and r.gsis_id == "00-JAXL"
    r = world.resolve("Tyler Trade", season=2026, week=1, team="NYG")   # he was DAL in week 1
    assert r.status == UNKNOWN and r.how.endswith("team_hint_mismatch")
    r = world.resolve("Cam Ward", season=2026, week=1, team="PHI")      # unique name, wrong team: not him
    assert r.status == UNKNOWN


def test_team_codes_translated_with_repo_keys(world):
    assert world.resolve("Trevor Jax", season=2026, week=1, team="JAC").gsis_id == "00-JAX1"   # ev code
    assert world.resolve("Trevor Jax", season=2026, week=1, team="JAX").gsis_id == "00-JAX1"   # nflverse code
    assert world.resolve("Rams Guy", season=2024, week=1, team="LAR").gsis_id == "00-LAR1"     # roster says LA
    assert world.resolve("Cam Ward (TEN)", season=2026, week=1).ok


def test_book_three_letter_team_variants(world):
    assert R.team_code("NOR") == "NO"           # seen on real ParlayAPI payloads
    assert R.team_code("jax") == "JAC"
    assert R.team_code("XYZ") is None and R.team_code(None) is None and R.team_code("") is None
    r = world.resolve("Cam Ward", season=2026, week=1, team="XYZ")
    assert r.status == UNKNOWN and r.how == "bad_team_hint"


def test_conflicting_team_hints_are_ambiguous(world):
    r = world.resolve("Lamar Jackson (BAL)", season=2024, week=1, team="ATL")
    assert r.status == AMBIGUOUS and r.how == "team_hint_conflict"
    assert world.resolve("Lamar Jackson (BAL)", season=2024, week=1, team="BAL").ok     # agreeing hints are fine


def test_generational_namesakes_fail_closed_until_context_separates_them(world):
    # norm_name strips Jr./II so Frank Gore / Frank Gore Jr. share a key.
    r = world.resolve("Frank Gore Jr.", season=2026, week=1)
    assert r.ok and r.gsis_id == "00-GOREJ"                    # CUT Sr. is dropped by the active tie-break
    r = world.resolve("Frank Gore Jr.", season=2026, week=1, team="NYJ")
    assert r.ok and r.gsis_id == "00-GORE"                     # a hint is honoured even if the suffix said otherwise
    assert world.resolve("Frank Gore", season=2018, week=1).gsis_id == "00-GORE"


def test_both_active_namesakes_never_collapse_on_status(world):
    r = world.resolve("Michael Carter", season=2026, week=1)
    assert r.status == AMBIGUOUS and set(r.candidates) == {"00-RBMC", "00-CBMC"}
    assert world.resolve("Michael Carter", season=2026, week=1, team="TEN").gsis_id == "00-RBMC"
    assert world.resolve("Michael Carter II", season=2026, week=1, team="PHI").gsis_id == "00-CBMC"
    r = world.resolve("DeVonta Smith", season=2026, week=1)
    assert r.status == AMBIGUOUS                                # case-folded names collide, by design


def test_position_hint_breaks_ties_and_contradictions_fail_closed(world):
    assert world.resolve("Michael Carter", season=2026, week=1, position="RB").gsis_id == "00-RBMC"
    assert world.resolve("Michael Carter", season=2026, week=1, position="HB").gsis_id == "00-RBMC"
    assert world.resolve("DeVonta Smith", season=2026, week=1, position="cb").gsis_id == "00-CBSM"
    r = world.resolve("Michael Carter", season=2026, week=1, position="QB")
    assert r.status == UNKNOWN and r.how.endswith("position_hint_mismatch")


def test_active_status_is_only_a_last_tiebreak(world):
    r = world.resolve("Sam Squad", season=2026, week=1)            # DEN practice squad vs KC active
    assert r.ok and r.gsis_id == "00-ACT1" and "active" in r.how
    assert world.resolve("Sam Squad", season=2026, week=1, team="DEN").gsis_id == "00-PS1"   # hint beats status


# ------------------------------------------------------------------------------------------- context handling
def test_no_context_keeps_namesakes_ambiguous(world):
    assert world.resolve("Lamar Jackson").status == AMBIGUOUS
    assert world.resolve("Cam Ward").gsis_id == "00-WARD"
    # a team hint with no season is tested against every loaded roster
    assert world.resolve("Lamar Jackson", team="ATL").gsis_id == "00-CB"


def test_name_known_but_not_rostered_is_unknown_not_guessed(world):
    r = world.resolve("Frank Gore", season=2022, week=1)            # no 2022 rosters in this world
    assert r.status == UNKNOWN and r.how.endswith("no_roster_data")
    r = world.resolve("Josh Allen", season=2026, week=1)            # name exists but not on a 2026 roster
    assert r.status == UNKNOWN and r.how.endswith("not_on_roster") and set(r.candidates) == {"00-BUFQ", "00-JAXL"}


def test_week_missing_from_data_falls_back_to_season_and_says_so(world):
    r = world.resolve("Cam Ward", season=2026, week=17)
    assert r.ok and "season_fallback" in r.how
    assert world.resolve("Tyler Trade", season=2026, week=17).status == AMBIGUOUS    # fallback never over-resolves


def test_game_date_context_via_schedules(world):
    assert world.resolve("Tyler Trade", game_date="2026-09-20", team="DAL").gsis_id == "00-TRAD"   # week 2: DAL
    r = world.resolve("Tyler Trade", game_date="2026-09-20", team="PHI")                           # no PHI game that day
    assert r.status == UNKNOWN and r.how == "no_game_for_date"
    r = world.resolve("Tyler Trade", game_date="2026-09-27")           # two games, same (season, week) -> fine
    assert r.status == AMBIGUOUS                                       # wk3: two namesakes, no team
    assert world.resolve("Tyler Trade", week=3).how == "week_without_season"


def test_resolve_id_helper(world):
    assert world.resolve_id("Cam Ward", season=2026) == "00-WARD"
    assert world.resolve_id("Lamar Jackson", season=2024) is None
    assert world.resolve_id("Nobody", season=2024) is None


def test_teams_of(world):
    assert world.teams_of("00-TRAD", 2026, 1) == frozenset({"DAL"})
    assert world.teams_of("00-TRAD", 2026, 3) == frozenset({"NYG"})
    assert world.teams_of("00-LAR1", 2024, 1) == frozenset({"LAR"})
    assert world.teams_of("nope", 2026, 1) == frozenset()


# ------------------------------------------------------------------------------------------- alias table integrity
def _aliased(world_roster, aliases):
    return PlayerResolver(world_roster, aliases=aliases)


def test_alias_resolves_only_when_no_exact_key_and_goes_through_context():
    ros = _roster(_weeks("00-A", "Marquise Brown", "KC", "WR", 2026, [1]) +
                  _weeks("00-B", "Marquise Brown", "ARI", "WR", 2026, [1]))
    res = _aliased(ros, [("Hollywood Brown", "Marquise Brown", "evidence: book spelling in test")])
    assert res.alias_problems() == []
    r = res.resolve("Hollywood Brown", season=2026, week=1)
    assert r.status == AMBIGUOUS and r.how.startswith("alias:")             # alias does NOT bypass namesake logic
    r = res.resolve("Hollywood Brown", season=2026, week=1, team="KC")
    assert r.ok and r.gsis_id == "00-A" and r.how.startswith("alias:")


def test_alias_never_shadows_an_exact_match():
    ros = _roster(_weeks("00-A", "Josh Allen", "BUF", "QB", 2026, [1]) +
                  _weeks("00-B", "Joshua Allen", "JAX", "LB", 2026, [1]))
    res = _aliased(ros, [("Josh Allen", "Joshua Allen", "bad alias")])
    probs = res.alias_problems()
    assert any("shadows an exact" in p for p in probs)
    # and it is behaviourally inert: the exact key still wins
    assert res.resolve("Josh Allen", season=2026, week=1).gsis_id == "00-A"


def test_alias_cannot_map_to_two_targets():
    ros = _roster(_weeks("00-A", "Marquise Brown", "KC", "WR", 2026, [1]) +
                  _weeks("00-B", "Mark Brown", "ARI", "WR", 2026, [1]))
    res = _aliased(ros, [("Hollywood Brown", "Marquise Brown", "x"), ("hollywood  brown", "Mark Brown", "y")])
    assert any("two targets" in p for p in res.alias_problems())
    assert res.resolve("Hollywood Brown", season=2026, week=1).gsis_id == "00-A"      # first mapping kept, never merged


def test_dead_and_degenerate_aliases_are_reported():
    ros = _roster(_weeks("00-A", "Marquise Brown", "KC", "WR", 2026, [1]))
    res = _aliased(ros, [("Hollywood Brown", "Nobody Atall", "x"), ("", "Marquise Brown", "y"),
                         ("marquise brown", "Marquise Brown", "z")])
    probs = " | ".join(res.alias_problems())
    assert "dead alias" in probs and "empty after normalisation" in probs and "equals its own target" in probs
    assert res.resolve("Hollywood Brown", season=2026, week=1).status == UNKNOWN


def test_module_alias_table_is_internally_consistent():
    seen = {}
    for alias, canonical, evidence in R.ALIASES:
        a, c = R.norm_name(alias), R.norm_name(canonical)
        assert a and c and a != c
        assert evidence, f"alias {alias!r} has no documented evidence"
        assert seen.setdefault(a, c) == c, f"alias {a!r} maps to two ids"
    for a, c in list(seen.items()):
        assert c not in seen, f"alias chain {a!r}->{c!r}->{seen[c]!r}"       # aliases must not chain


# ------------------------------------------------------------------------------------------- coverage report
def test_coverage_levels_and_zero_wrong(world):
    truth = pd.DataFrame([
        dict(gsis_id="00-QB", season=2024, week=1, team="BAL"),
        dict(gsis_id="00-CB", season=2024, week=1, team="ATL"),
        dict(gsis_id="00-WARD", season=2026, week=1, team="TEN"),
        dict(gsis_id="00-RBMC", season=2026, week=1, team="TEN"),
        dict(gsis_id="00-CBMC", season=2026, week=1, team="PHI"),
    ])
    cov = world.coverage(truth)
    assert cov["week_team"]["correct"] == 5 and cov["week_team"]["wrong"] == 0
    assert cov["week"]["correct"] == 1 and cov["week"]["ambiguous"] == 4          # Lamar x2 (2024 wk1) + Carter x2
    assert cov["name"]["wrong"] == 0 and cov["week"]["wrong"] == 0
    names = {n for n, _ in cov["ambiguous_names"]["week"]}
    assert {"Lamar Jackson", "Michael Carter"} <= names
    assert cov["week"]["players"] == 5 and cov["week"]["players_all_correct"] == 1
