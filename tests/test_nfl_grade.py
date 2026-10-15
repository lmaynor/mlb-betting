"""nfl.props.grade -- every grade branch for the six market stats, ladder semantics, and the batch grader end to end on
synthetic frames shaped like the real nflverse tables (player_stats / schedules / snap_counts / rosters_weekly)."""
import math

import pandas as pd
import pytest

from nfl.props import grade as G
from nfl.props.grade import (LOSS, NOT_YET_GRADED, PUSH, UNGRADABLE, VOID, WIN, Actuals, grade, grade_detail,
                             grade_frame, ladder_point)
from nfl.props.resolve import PlayerResolver


def _row(**kw):
    base = {c: 0 for c in G.STAT_ROW_COLUMNS}
    base.update(kw)
    return base


# ------------------------------------------------------------------------------------------- stat formulas
@pytest.mark.parametrize("stat,col", [("pass_yds", "passing_yards"), ("rush_yds", "rushing_yards"),
                                      ("rec_yds", "receiving_yards"), ("receptions", "receptions"),
                                      ("pass_tds", "passing_tds")])
def test_each_stat_reads_its_own_column_only(stat, col):
    row = _row(**{col: 60})
    others = {c: 999 for c in G.STAT_ROW_COLUMNS if c != col}
    assert G.actual_value(stat, row) == 60
    assert G.actual_value(stat, {**others, col: 60}) == 60            # no other column leaks in


def test_anytime_td_is_rush_plus_rec_and_excludes_passing_and_other_scores():
    row = _row(rushing_tds=1, receiving_tds=1, passing_tds=5, special_teams_tds=1, def_tds=1, fumble_recovery_tds=1)
    assert G.actual_value("anytime_td", row) == 2
    assert G.actual_value("anytime_td", row, td_scope="broad") == 5
    assert grade("anytime_td", 0.5, "over", _row(passing_tds=4), game_final=True) == LOSS     # a QB's passing TDs do not count
    assert grade("anytime_td", 0.5, "over", _row(special_teams_tds=1), game_final=True) == LOSS
    assert grade("anytime_td", 0.5, "over", _row(special_teams_tds=1), game_final=True, td_scope="broad") == WIN


def test_actual_value_missing_or_nan_is_none():
    assert G.actual_value("rec_yds", {"receptions": 3}) is None
    assert G.actual_value("rec_yds", _row(receiving_yards=float("nan"))) is None
    assert G.actual_value("anytime_td", _row(rushing_tds=float("nan"))) is None
    assert grade("rec_yds", 49.5, "over", _row(receiving_yards=float("nan")), game_final=True) == UNGRADABLE
    with pytest.raises(ValueError):
        G.actual_value("sacks", _row())
    with pytest.raises(ValueError):
        G.actual_value("rec_yds", _row(), td_scope="weird")


def test_works_on_pandas_series_rows():
    s = pd.Series(_row(receiving_yards=71))
    assert grade("rec_yds", 70.5, "over", s, game_final=True) == WIN


# ------------------------------------------------------------------------------------------- win / loss / push
@pytest.mark.parametrize("stat,col", [("pass_yds", "passing_yards"), ("rush_yds", "rushing_yards"),
                                      ("rec_yds", "receiving_yards"), ("receptions", "receptions"),
                                      ("pass_tds", "passing_tds")])
def test_win_loss_push_branches_for_every_counting_stat(stat, col):
    line = 4
    at, hi, lo = _row(**{col: line}), _row(**{col: line + 1}), _row(**{col: line - 1})
    assert grade(stat, line, "over", hi, game_final=True) == WIN
    assert grade(stat, line, "over", lo, game_final=True) == LOSS
    assert grade(stat, line, "under", lo, game_final=True) == WIN
    assert grade(stat, line, "under", hi, game_final=True) == LOSS
    assert grade(stat, line, "over", at, game_final=True) == PUSH                      # integer line, exact equality
    assert grade(stat, line, "under", at, game_final=True) == PUSH
    assert grade(stat, line, "over", at, game_final=False) == NOT_YET_GRADED
    assert grade(stat, line, "over", None, game_final=True) == VOID                    # DNP
    assert grade(stat, line, "over", None, game_final=False) == NOT_YET_GRADED         # not-in-mirror is never void


def test_half_point_lines_never_push():
    for actual in range(0, 12):
        for side in ("over", "under"):
            assert grade("receptions", 4.5, side, _row(receptions=actual), game_final=True) in (WIN, LOSS)
    assert grade("rec_yds", 49.5, "over", _row(receiving_yards=50), game_final=True) == WIN
    assert grade("rec_yds", 49.5, "over", _row(receiving_yards=49), game_final=True) == LOSS
    assert grade("rec_yds", 49.5, "under", _row(receiving_yards=49), game_final=True) == WIN


def test_negative_yardage_and_zero():
    assert grade("rush_yds", 0.5, "under", _row(rushing_yards=-3), game_final=True) == WIN
    assert grade("rush_yds", 0, "over", _row(rushing_yards=0), game_final=True) == PUSH


def test_anytime_td_branches_and_default_point():
    td, no = _row(receiving_tds=1), _row(rushing_yards=100)
    assert grade("anytime_td", 0.5, "over", td, game_final=True) == WIN
    assert grade("anytime_td", None, "yes", td, game_final=True) == WIN                # point defaults to 0.5
    assert grade("anytime_td", float("nan"), "yes", no, game_final=True) == LOSS
    assert grade("anytime_td", None, "no", no, game_final=True) == WIN                 # 'No' == under 0.5
    assert grade("anytime_td", 0.5, "over", None, game_final=True) == VOID
    assert grade("anytime_td", 0.5, "over", td, game_final=False) == NOT_YET_GRADED
    assert grade("anytime_td", 1.5, "over", _row(rushing_tds=2), game_final=True) == WIN   # a '2+ TD' ladder
    assert grade("anytime_td", 1, "over", _row(rushing_tds=1), game_final=True) == PUSH


# ------------------------------------------------------------------------------------------- ladder / side semantics
def test_ladder_semantics_n_plus_equals_over_n_minus_half():
    assert ladder_point(3) == 2.5 and ladder_point(250) == 249.5
    for bad in (0, -1, 2.5, float("nan"), "x"):
        with pytest.raises((ValueError, TypeError)):
            ladder_point(bad)
    # '3+ receptions' (Yes) wins iff receptions >= 3; 'No' is the complement; neither can push
    for n in range(0, 7):
        yes = grade("receptions", ladder_point(3), "yes", _row(receptions=n), game_final=True)
        no = grade("receptions", ladder_point(3), "no", _row(receptions=n), game_final=True)
        assert yes == (WIN if n >= 3 else LOSS) and no == (LOSS if n >= 3 else WIN)


@pytest.mark.parametrize("a,b", [("Over", "over"), ("OVER", "over"), ("yes", "over"), ("Y", "over"),
                                 ("Under", "under"), ("UNDER", "under"), ("no", "under"), ("n", "under")])
def test_side_spellings(a, b):
    assert G.canon_side(a) == b


def test_bad_inputs_raise_instead_of_grading():
    row = _row(receptions=3)
    for kw in (dict(stat="sacks", point=1.5, side="over"), dict(stat="receptions", point=None, side="over"),
               dict(stat="receptions", point=-1, side="over"), dict(stat="receptions", point=float("inf"), side="over"),
               dict(stat="receptions", point=2.5, side="push"), dict(stat="receptions", point=2.5, side=None)):
        with pytest.raises(ValueError):
            grade(kw["stat"], kw["point"], kw["side"], row, game_final=True)


# ------------------------------------------------------------------------------------------- DNP vs zero-stat play
def test_no_row_is_void_unless_snaps_show_the_player_played():
    assert grade("rec_yds", 19.5, "over", None, game_final=True) == VOID
    assert grade("rec_yds", 19.5, "over", None, game_final=True, played=False) == VOID
    assert grade("rec_yds", 19.5, "over", None, game_final=True, played=True) == LOSS     # on the field, no catches
    assert grade("rec_yds", 19.5, "under", None, game_final=True, played=True) == WIN
    assert grade("anytime_td", 0.5, "over", None, game_final=True, played=True) == LOSS
    d = grade_detail("rec_yds", 19.5, "over", None, game_final=True, played=True)
    assert d.actual == 0 and "played_no_stat_row" in d.reason
    assert grade_detail("rec_yds", 19.5, "over", None, game_final=True).reason == "no_stat_row_assumed_dnp"
    assert grade_detail("rec_yds", 19.5, "over", None, game_final=True, played=False).reason == "dnp_no_stat_row"
    assert grade("rec_yds", 19.5, "over", None, game_final=False, played=True) == NOT_YET_GRADED


# ================================================================================================ batch grader
def _stats(rows):
    recs = []
    for pid, gid, team, kw in rows:
        recs.append({"player_id": pid, "game_id": gid, "team": team, **_row(**kw)})
    return pd.DataFrame(recs)


@pytest.fixture
def env():
    sched = pd.DataFrame([
        dict(game_id="2026_05_ATL_NO", season=2026, week=5, gameday="2026-10-04", away_team="ATL", home_team="NO",
             home_score=20.0, away_score=17.0),
        dict(game_id="2026_05_JAX_LA", season=2026, week=5, gameday="2026-10-04", away_team="JAX", home_team="LA",
             home_score=24.0, away_score=10.0),                                    # final but NOT yet in player_stats
        dict(game_id="2026_06_NO_LA", season=2026, week=6, gameday="2026-10-11", away_team="NO", home_team="LA",
             home_score=float("nan"), away_score=float("nan")),                    # not played yet
    ])
    ros = pd.DataFrame([
        # two Lamar-style namesakes on the two teams of ONE game; plus the real targets
        dict(gsis_id="00-PENIX", full_name="Michael Penix Jr.", first_name="Michael", last_name="Penix", team="ATL",
             position="QB", status="ACT", season=2026, week=5, football_name="Michael"),
        dict(gsis_id="00-KAM", full_name="Alvin Kamara", first_name="Alvin", last_name="Kamara", team="NO",
             position="RB", status="ACT", season=2026, week=5, football_name="Alvin"),
        dict(gsis_id="00-WR1", full_name="Drake London", first_name="Drake", last_name="London", team="ATL",
             position="WR", status="ACT", season=2026, week=5, football_name="Drake"),
        dict(gsis_id="00-WR2", full_name="Dnp Guy", first_name="Dnp", last_name="Guy", team="NO",
             position="WR", status="INA", season=2026, week=5, football_name="Dnp"),
        dict(gsis_id="00-JAX", full_name="Trevor Jax", first_name="Trevor", last_name="Jax", team="JAX",
             position="QB", status="ACT", season=2026, week=5, football_name="Trevor"),
        dict(gsis_id="00-A", full_name="Sam Same", first_name="Sam", last_name="Same", team="ATL",
             position="WR", status="ACT", season=2026, week=5, football_name="Sam"),
        dict(gsis_id="00-B", full_name="Sam Same", first_name="Sam", last_name="Same", team="NO",
             position="WR", status="ACT", season=2026, week=5, football_name="Sam"),
        dict(gsis_id="00-KAM", full_name="Alvin Kamara", first_name="Alvin", last_name="Kamara", team="NO",
             position="RB", status="ACT", season=2026, week=6, football_name="Alvin"),
    ])
    stats = _stats([
        ("00-PENIX", "2026_05_ATL_NO", "ATL", dict(passing_yards=224, passing_tds=2)),
        ("00-KAM", "2026_05_ATL_NO", "NO", dict(rushing_yards=39, rushing_tds=1, receptions=4, receiving_yards=30)),
        ("00-A", "2026_05_ATL_NO", "ATL", dict(receptions=2, receiving_yards=18)),
        ("00-B", "2026_05_ATL_NO", "NO", dict(receptions=7, receiving_yards=90)),
    ])
    snaps = pd.DataFrame([
        dict(game_id="2026_05_ATL_NO", gsis_id="00-WR1", offense_snaps=40, defense_snaps=0, st_snaps=2),   # played, no stats
        dict(game_id="2026_05_ATL_NO", gsis_id="00-WR2", offense_snaps=0, defense_snaps=0, st_snaps=0),    # DNP
        dict(game_id="2026_05_ATL_NO", gsis_id="00-PENIX", offense_snaps=60, defense_snaps=0, st_snaps=0),
    ])
    res = PlayerResolver(ros, schedules=sched)
    return res, Actuals(stats, sched, snaps), Actuals(stats, sched, None)


def _q(**kw):
    base = dict(player="Michael Penix Jr.", team="ATL", season=2026, week=5, stat="pass_yds", point=224.5, side="over")
    base.update(kw)
    return base


def _one(env, **kw):
    res, act, _ = env
    return grade_frame(pd.DataFrame([_q(**kw)]), resolver=res, actuals=act).iloc[0]


def test_frame_basic_grades_and_audit_columns(env):
    r = _one(env)
    assert r.result == LOSS and r.actual == 224 and r.gsis_id == "00-PENIX" and r.game_id == "2026_05_ATL_NO"
    assert r.resolve_status == "resolved"
    assert _one(env, point=223.5).result == WIN
    assert _one(env, point=224).result == PUSH
    assert _one(env, stat="pass_tds", point=1.5).result == WIN
    assert _one(env, player="Alvin Kamara", team="NO", stat="rec_yds", point=29.5).result == WIN
    assert _one(env, player="Alvin Kamara", team="NO", stat="anytime_td", point=None, side="yes").result == WIN


def test_frame_game_identification_routes(env):
    assert _one(env, game_id="2026_05_ATL_NO", team=None, season=None, week=None).result == LOSS
    assert _one(env, game_key="nfl:2026-10-04:ATL@NO", season=None, week=None).result == LOSS
    assert _one(env, season=None, week=None, game_date="2026-10-04").result == LOSS
    r = _one(env, season=None, week=None)
    assert r.result == UNGRADABLE and "no_game_identifier" in r.reason
    r = _one(env, game_key="nfl:2026-10-04:ATL@LAR")
    assert r.result == UNGRADABLE and "game_key_matches_0_games" in r.reason
    r = _one(env, team="DAL")
    assert r.result == UNGRADABLE and r.reason == "season_week_team_matches_0_games"      # DAL has no game in wk 5


def test_frame_team_must_be_in_the_game(env):
    r = _one(env, game_id="2026_05_ATL_NO", team="JAX")
    assert r.result == UNGRADABLE and "team_JAC_not_in_game" in r.reason


def test_frame_not_yet_graded_is_distinct_from_void(env):
    # scored in schedules but the mirror has no player_stats rows for it yet
    r = _one(env, player="Trevor Jax", team="JAX", stat="pass_yds", point=200.5)
    assert r.result == NOT_YET_GRADED and r.reason == "scored_but_player_stats_not_mirrored_yet"
    # not played at all (no score)
    ros = env[0]
    r2 = grade_frame(pd.DataFrame([_q(week=6, season=2026, team="NO", player="Alvin Kamara", stat="rush_yds", point=30.5)]),
                     resolver=ros, actuals=env[1]).iloc[0]
    assert r2.result == NOT_YET_GRADED and r2.reason == "game_not_played_yet_or_unscored"


def test_frame_dnp_void_vs_played_zero_line_uses_snaps(env):
    played = _one(env, player="Drake London", stat="rec_yds", point=19.5)
    assert played.result == LOSS and played.actual == 0 and "played_no_stat_row" in played.reason
    assert _one(env, player="Drake London", stat="rec_yds", point=19.5, side="under").result == WIN
    dnp = _one(env, player="Dnp Guy", team="NO", stat="rec_yds", point=19.5)
    assert dnp.result == VOID and "snaps=0" in dnp.reason
    # a player absent from a game that HAS snap data is also a DNP
    ghost = _one(env, player="Dnp Guy", team="NO", stat="receptions", point=0.5, side="under")
    assert ghost.result == VOID


def test_frame_without_snap_data_voids_but_says_it_assumed(env):
    res, _, act_nosnaps = env
    r = grade_frame(pd.DataFrame([_q(player="Drake London", stat="rec_yds", point=19.5)]), resolver=res,
                    actuals=act_nosnaps).iloc[0]
    assert r.result == VOID and "assumed_dnp" in r.reason and "no_snap_data_for_game" in r.reason


def test_frame_unresolved_players_are_ungradable_never_a_loss(env):
    r = _one(env, player="Nobody Real")
    assert r.result == UNGRADABLE and r.resolve_status == "unknown" and r.gsis_id is None
    r = _one(env, player="Sam Same", team=None, game_id="2026_05_ATL_NO")   # one Sam Same per team -> ambiguous
    assert r.result == UNGRADABLE and r.resolve_status == "ambiguous"
    assert "player_ambiguous" in r.reason


def test_frame_namesakes_grade_against_the_right_players_row(env):
    a = _one(env, player="Sam Same", team="ATL", stat="rec_yds", point=50.5)
    b = _one(env, player="Sam Same", team="NO", stat="rec_yds", point=50.5)
    assert (a.gsis_id, a.result, a.actual) == ("00-A", LOSS, 18)
    assert (b.gsis_id, b.result, b.actual) == ("00-B", WIN, 90)
    # 'Sam Same (NO)' tail routes the same way
    c = _one(env, player="Sam Same (NO)", team=None, game_key="nfl:2026-10-04:ATL@NO", season=None, week=None,
             stat="rec_yds", point=50.5)
    assert c.gsis_id == "00-B" and c.result == WIN


def test_frame_player_on_a_team_outside_the_game_is_ungradable(env):
    # resolves cleanly (Trevor Jax is on the roster), but he is on JAX and the game is ATL@NO
    r = _one(env, player="Trevor Jax", team=None, game_id="2026_05_ATL_NO", season=None, week=None)
    assert r.result == UNGRADABLE and "not_in_game" in r.reason


def test_frame_side_column_absent_grades_both_sides(env):
    res, act, _ = env
    df = pd.DataFrame([{k: v for k, v in _q(point=200.5).items() if k != "side"}])
    out = grade_frame(df, resolver=res, actuals=act)
    assert sorted(out.side) == ["over", "under"] and set(out.result) == {WIN, LOSS}
    assert out.set_index("side").loc["over", "result"] == WIN


def test_frame_blank_side_and_bad_inputs_are_ungradable(env):
    assert _one(env, side=None).result == UNGRADABLE
    assert _one(env, side="push").result == UNGRADABLE
    assert _one(env, stat="sacks").result == UNGRADABLE and "unknown_stat" in _one(env, stat="sacks").reason
    assert _one(env, point=-3).result == UNGRADABLE
    assert _one(env, point=None).result == UNGRADABLE            # only anytime_td may omit the point


def test_frame_kalshi_ladder_strike_and_n_plus(env):
    # strike 3.5 == '4+' ; n_plus=4 gives the same line; Kamara had 4 receptions
    assert _one(env, player="Alvin Kamara", team="NO", stat="receptions", point=3.5, side="yes").result == WIN
    r = _one(env, player="Alvin Kamara", team="NO", stat="receptions", point=None, n_plus=4, side="yes")
    assert r.result == WIN
    r = _one(env, player="Alvin Kamara", team="NO", stat="receptions", point=None, n_plus=5, side="yes")
    assert r.result == LOSS
    assert _one(env, player="Alvin Kamara", team="NO", stat="receptions", strike=4.5, point=None, side="no").result == WIN


def test_frame_td_scope_sensitivity_flag(env):
    res, _, _ = env
    sched = pd.DataFrame([dict(game_id="2026_05_ATL_NO", season=2026, week=5, gameday="2026-10-04", away_team="ATL",
                               home_team="NO", home_score=20.0, away_score=17.0)])
    stats = _stats([("00-KAM", "2026_05_ATL_NO", "NO", dict(special_teams_tds=1, rushing_yards=5)),
                    ("00-PENIX", "2026_05_ATL_NO", "ATL", dict(rushing_tds=1))])
    act = Actuals(stats, sched, None)
    df = pd.DataFrame([_q(player="Alvin Kamara", team="NO", stat="anytime_td", point=None, side="yes"),
                       _q(player="Michael Penix Jr.", team="ATL", stat="anytime_td", point=None, side="yes")])
    out = grade_frame(df, resolver=res, actuals=act)
    assert list(out.result) == [LOSS, WIN]                       # default scope: a return TD is not a TD scorer
    assert list(out.result_broad_td) == [WIN, WIN]
    assert list(out.td_scope_sensitive) == [True, False]


def test_frame_stat_row_team_vs_hint_mismatch_is_ungradable(env):
    res, _, _ = env
    sched = pd.DataFrame([dict(game_id="2026_05_ATL_NO", season=2026, week=5, gameday="2026-10-04", away_team="ATL",
                               home_team="NO", home_score=20.0, away_score=17.0)])
    stats = _stats([("00-KAM", "2026_05_ATL_NO", "ATL", dict(rushing_yards=50))])          # row says ATL, roster says NO
    r = grade_frame(pd.DataFrame([_q(player="Alvin Kamara", team="NO", stat="rush_yds", point=10.5)]),
                    resolver=res, actuals=Actuals(stats, sched, None)).iloc[0]
    assert r.result == UNGRADABLE and "stat_row_team" in r.reason


def test_frame_passes_extra_columns_through_and_does_not_mutate_input(env):
    res, act, _ = env
    df = pd.DataFrame([_q(ticker="KX-1", p_over=0.4)])
    before = df.copy()
    out = grade_frame(df, resolver=res, actuals=act)
    pd.testing.assert_frame_equal(df, before)
    assert out.iloc[0].ticker == "KX-1" and out.iloc[0].p_over == 0.4


def test_summarize_counts(env):
    res, act, _ = env
    df = pd.DataFrame([_q(point=100.5), _q(point=300.5), _q(player="Nobody"),
                       _q(player="Dnp Guy", team="NO", stat="rec_yds", point=1.5)])
    s = G.summarize(grade_frame(df, resolver=res, actuals=act))
    assert s["counts"] == {LOSS: 1, WIN: 1, UNGRADABLE: 1, VOID: 1}
    assert math.isclose(s["win_rate_ex_push_void"], 0.5)


def test_actuals_requires_columns_and_snap_ids(env):
    with pytest.raises(ValueError):
        Actuals(pd.DataFrame({"player_id": ["x"], "game_id": ["g"]}), pd.DataFrame())
    ok_stats = _stats([("00-KAM", "2026_05_ATL_NO", "NO", {})])
    sched = pd.DataFrame([dict(game_id="2026_05_ATL_NO", season=2026, week=5, gameday="2026-10-04", away_team="ATL",
                               home_team="NO", home_score=1.0, away_score=0.0)])
    with pytest.raises(ValueError):
        Actuals(ok_stats, sched, pd.DataFrame({"game_id": ["g"], "pfr_player_id": ["x"], "offense_snaps": [1]}))


def test_attach_gsis_maps_pfr_ids():
    snaps = pd.DataFrame({"game_id": ["g"] * 2, "pfr_player_id": ["AbcdEf00", "Nope00"], "offense_snaps": [3, 4]})
    players = pd.DataFrame({"gsis_id": ["00-1", "00-2"], "pfr_id": ["AbcdEf00", None]})
    out = G.attach_gsis(snaps, players)
    assert out.gsis_id.tolist()[0] == "00-1" and pd.isna(out.gsis_id.tolist()[1])


# ---- volume-count stats (pass_attempts / completions / rush_attempts) ------------------------------------------
def test_volume_stats_grade_from_nflverse_columns_and_keep_the_dnp_rule():
    from nfl.props import grade as G
    row = {"attempts": 35, "completions": 22, "carries": 4}
    assert G.grade("pass_attempts", 34.5, "over", row, game_final=True) == G.WIN
    assert G.grade("completions", 22, "over", row, game_final=True) == G.PUSH
    assert G.grade("rush_attempts", 4.5, "over", row, game_final=True) == G.LOSS
    # missing player_stats row is NOT a DNP: snaps decide (played -> zeros, not played -> void)
    assert G.grade("rush_attempts", 0.5, "over", None, game_final=True, played=True) == G.LOSS
    assert G.grade("pass_attempts", 0.5, "over", None, game_final=True, played=False) == G.VOID
    # a stats row that lacks the column is ungradable, never a loss
    assert G.grade("pass_attempts", 20.5, "under", {"carries": 1}, game_final=True) == G.UNGRADABLE
    # older stat frames (no attempts/completions/carries) must still construct an Actuals
    assert not {"attempts", "completions", "carries"} & set(G.STAT_ROW_COLUMNS)
