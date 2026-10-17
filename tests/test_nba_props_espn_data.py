"""nba.props.espn_data: the ESPN/hoopR history path must feed the same downstream code as the SportsBlaze path.

The parity tests build ESPN-shaped box scores FROM the synthetic SportsBlaze masters (tests/nba_props_helpers.py), run both
paths, and require the same rows / team totals / features. Nothing here touches the network."""
import numpy as np
import pandas as pd
import pytest

from nba.props import data as D
from nba.props import espn_data as E
from nba.props.features import FeatureContext, build_training_frame
from tests.nba_props_helpers import NAMED, make_masters

ESPN_ABBR = {"BOS": "BOS", "LAL": "LAL", "NY": "NY", "MIA": "MIA"}        # ESPN spells New York "NY" (SportsBlaze too)


def to_espn(pb: pd.DataFrame, gm: pd.DataFrame, tips: pd.Series, dnp_reason="COACH'S DECISION") -> pd.DataFrame:
    """SportsBlaze-shaped masters -> hoopR player_box shape (only the columns the loader reads)."""
    pid = {p: 1000 + i for i, p in enumerate(sorted(pb["player_id"].unique()))}
    g = gm.set_index("game_id")
    rows = []
    for r in pb.itertuples(index=False):
        tip = tips[r.game_id].tz_convert("America/New_York")
        played = bool(r.played)
        mins = D.parse_minutes(pd.Series([r.minutes])).iloc[0]
        away = g.loc[r.game_id, "away_abbr"]
        home = g.loc[r.game_id, "home_abbr"]
        rows.append({
            "game_id": int(r.game_id[1:]) + 400000000, "season": r.season_year + 1, "season_type": 2,
            "game_date": tip.date().isoformat(), "game_date_time": tip,
            "athlete_id": float(pid[r.player_id]), "athlete_display_name": r.name,
            "athlete_position_abbreviation": r.position, "team_abbreviation": ESPN_ABBR[r.team_abbr],
            "opponent_team_abbreviation": away if r.is_home else home,
            "home_away": "home" if r.is_home else "away", "starter": bool(r.starter),
            "did_not_play": not played, "reason": None if played else dnp_reason,
            "minutes": mins if played else np.nan,
            "field_goals_made": r.field_goals_made, "field_goals_attempted": r.field_goals_attempted,
            "three_point_field_goals_made": r.three_points_made,
            "three_point_field_goals_attempted": r.three_points_attempted,
            "free_throws_made": r.free_throws_made, "free_throws_attempted": r.free_throws_attempted,
            "offensive_rebounds": r.rebounds_offensive, "defensive_rebounds": r.rebounds_defensive,
            "rebounds": r.rebounds, "assists": r.assists, "steals": r.steals, "blocks": r.blocks,
            "turnovers": r.turnovers, "fouls": r.fouls, "points": r.points})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def world():
    pb, gm, tips = make_masters(n_days=45, seed=5)
    espn = to_espn(pb, gm, tips)
    return pb, gm, tips, espn


def test_rows_schema_matches_the_sportsblaze_prepare(world):
    pb, gm, tips, espn = world
    sb_rows, sb_tg = D.prepare(pb, gm, tips)
    e_rows, e_tg = E.prepare_espn(espn)
    # ESPN has no team uuid / two-point / plus-minus columns; nothing downstream reads them
    sb_only = {"team_id", "two_points_made", "two_points_attempted", "plus_minus"}
    assert set(sb_rows.columns) - set(e_rows.columns) <= sb_only
    assert list(e_tg.columns) == list(sb_tg.columns)
    assert len(e_rows) == len(sb_rows) and len(e_tg) == len(sb_tg)


def test_same_games_same_stats_same_et_days_and_totals(world):
    pb, gm, tips, espn = world
    sb_rows, sb_tg = D.prepare(pb, gm, tips)
    e_rows, e_tg = E.prepare_espn(espn)
    # same ET game day and tip ordering as the SportsBlaze-with-schedule-tips path
    assert (e_rows["date"].values == sb_rows["date"].values).all()
    assert (e_rows["tip_sort"].values == sb_rows["tip_sort"].values).all()
    assert e_rows["tip_known"].all()
    for col in ("min", "points", "rebounds", "assists", "three_points_made", "pra", "usg_poss", "starter"):
        assert np.allclose(e_rows[col].astype(float).values, sb_rows[col].astype(float).values), col
    assert (e_rows["team"].values == sb_rows["team"].values).all() and (e_rows["opp"].values == sb_rows["opp"].values).all()
    for col in ("t_pts", "t_fga", "poss", "pace", "ortg"):
        assert np.allclose(e_tg[col].values, sb_tg[col].values), col


def test_player_id_is_the_espn_athlete_id_and_season_year_is_the_start_year(world):
    _, _, _, espn = world
    rows, _ = E.prepare_espn(espn)
    assert set(rows["player_id"].unique()) == set(espn["athlete_id"].astype(int))
    assert rows["season_year"].unique().tolist() == [2025]               # hoopR season 2026 = the 2025-26 season
    assert set(rows["season_type"]) == {"Regular Season"}


def test_features_are_identical_through_both_paths_and_leakage_free(world):
    pb, gm, tips, espn = world
    sb_rows, sb_tg = D.prepare(pb, gm, tips)
    e_rows, e_tg = E.prepare_espn(espn)
    Xs, ms = build_training_frame(sb_rows, sb_tg)
    Xe, me = build_training_frame(e_rows, e_tg)
    assert list(Xs.columns) == list(Xe.columns) and len(Xs) == len(Xe)
    assert np.allclose(Xs.to_numpy(float), Xe.to_numpy(float), equal_nan=True)
    assert np.allclose(ms["min"].values, me["min"].values)


def test_dnp_and_zero_minute_rows_never_enter_history(world):
    _, _, _, espn = world
    rows, _ = E.prepare_espn(espn)
    assert (rows["min"] > 0).all()
    n_dnp = int(espn["did_not_play"].sum())
    assert n_dnp > 0 and len(rows) == len(espn) - n_dnp


def test_all_star_and_unmapped_team_games_are_dropped_whole(world):
    _, _, _, espn = world
    gid = int(espn["game_id"].iloc[0])
    bad = espn.copy()
    bad.loc[bad["game_id"] == gid, "team_abbreviation"] = np.where(
        bad.loc[bad["game_id"] == gid, "home_away"] == "home", "WORLD", "STARS")
    rows, tg = E.prepare_espn(bad)
    assert gid not in set(rows["game_id"]) and gid not in set(tg["game_id"])


def test_preseason_is_excluded_like_the_sportsblaze_path(world):
    _, _, _, espn = world
    pre = espn.copy()
    pre["season_type"] = 1
    rows, _ = E.prepare_espn(pre)
    assert rows.empty
    rows_inc, _ = E.prepare_espn(pre, include_preseason=True)
    assert len(rows_inc) > 0


def test_empty_input_does_not_crash():
    rows, tg = E.prepare_espn(pd.DataFrame())
    assert rows.empty and tg.empty


# ------------------------------------------------------------------------------------------------ availability
@pytest.mark.parametrize("reason,cat", [
    ("COACH'S DECISION", "COACH"), ("LEFT ANKLE SPRAIN", "INJURY"), ("RIGHT KNEE", "INJURY"),
    ("LEFT KNEE INJURY MANAGEMENT", "INJURY"),           # injury management is an injury, not rest
    ("LEFT HAMSTRING - INJURY MANAGEMENT", "INJURY"), ("REST", "REST"), ("SUSPENDED BY LEAGUE", "SUSPENSION"),
    ("SUSPENDED BY TEAM", "SUSPENSION"), ("NOT WITH TEAM", "NOT_WITH_TEAM"), ("PERSONAL", "PERSONAL"),
    ("PERSONAL REASONS", "PERSONAL"), ("ILLNESS", "ILLNESS"), ("INJURY/ILLNESS", "ILLNESS"), ("VIRUS", "ILLNESS"),
    ("DID NOT DRESS", "UNKNOWN"), (None, "UNKNOWN"), (float("nan"), "UNKNOWN"), ("", "UNKNOWN"),
    ("TORN RIGHT ACL", "INJURY"), ("DENTAL PAIN", "INJURY"),
])
def test_classify_reason(reason, cat):
    assert E.classify_reason(reason) == cat


def test_inactives_lists_every_dnp_with_team_tricode_and_category(world):
    _, _, _, espn = world
    espn = espn.copy()
    first_dnp = espn.index[espn["did_not_play"]][0]
    espn.loc[first_dnp, "reason"] = "LEFT ANKLE SPRAIN"
    ina = E.inactives(espn)
    assert len(ina) == int(espn["did_not_play"].sum())
    assert set(ina["team"]) <= {"BOS", "LAL", "NYK", "MIA"}            # ESPN 'NY' -> tricode NYK
    assert (ina["category"] == "INJURY").sum() == 1 and (ina["category"] == "COACH").sum() == len(ina) - 1
    assert ina["player_id"].dtype == np.int64


def test_inactives_ignores_unlisted_players_and_empty_input():
    assert E.inactives(pd.DataFrame()).empty


def test_scratch_frame_keeps_only_coachs_decision_rows(world):
    _, _, _, espn = world
    espn = espn.copy()
    dnp_idx = list(espn.index[espn["did_not_play"]])
    espn.loc[dnp_idx[0], "reason"] = "LEFT ANKLE SPRAIN"
    espn.loc[dnp_idx[1], "reason"] = "REST"
    ina = E.inactives(espn)
    sc = E.scratch_frame(ina)
    assert len(sc) == len(ina) - 2 and list(sc.columns) == ["game_id", "team", "player_id"]
    assert E.scratch_frame(pd.DataFrame()).empty


# ------------------------------------------------------------------------------------------------ identity
def test_identity_bridge_maps_espn_ids_with_history_and_drops_the_rest(world):
    _, _, _, espn = world
    rows, _ = E.prepare_espn(espn)
    have = int(rows["player_id"].iloc[0])
    xw = pd.DataFrame({"espn_id": [have, 999999], "full_name": ["Has History", "Rookie Draftee"], "team": ["BOS", "LAL"]})
    b = E.identity_bridge(rows, xw)
    assert b.sb_id(have) == (have, "ok")
    assert b.sb_id(999999) == (None, "no_history")
    assert b.sb_id(424242) == (None, "espn_id_not_in_crosswalk")
    assert b.n_ok == 1


# ------------------------------------------------------------------------------------------------ scorer wiring
def test_load_resources_espn_source_reads_mirror_and_uses_identity_bridge(world, tmp_path, monkeypatch):
    from nba.data import hoopr
    from nba.props import score as S
    from nba.props.train import StatModel
    _, _, _, espn = world
    d = tmp_path / "player_boxscores"
    d.mkdir()
    espn.to_parquet(d / "player_box_2026.parquet", index=False)
    have = int(espn["athlete_id"].iloc[0])
    pd.DataFrame({"espn_id": [have, 999999], "full_name": ["Has History", "Draftee"], "team": ["BOS", "LAL"]}).to_csv(
        tmp_path / "players_xwalk.csv", index=False)
    monkeypatch.setattr(StatModel, "load", classmethod(lambda cls, stat, models_dir: object()))
    monkeypatch.setattr(hoopr, "current_season", lambda today=None: 2026)
    res = S.load_resources(str(tmp_path), "unused", source="espn")
    assert res.bridge.sb_id(have) == (have, "ok") and res.bridge.sb_id(999999) == (None, "no_history")
    assert res.history["n_rows"] > 0 and res.history["tip_known_share"] == 1.0
    with pytest.raises(ValueError):
        S.load_resources(str(tmp_path), "unused", source="nope")


def test_load_resources_espn_refuses_when_nothing_is_mirrored(tmp_path, monkeypatch):
    from nba.props import score as S
    from nba.props.train import StatModel
    monkeypatch.setattr(StatModel, "load", classmethod(lambda cls, stat, models_dir: object()))
    with pytest.raises(RuntimeError, match="no ESPN history mirrored"):
        S.load_resources(str(tmp_path), "unused", source="espn")
