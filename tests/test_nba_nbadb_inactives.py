"""nba.data.nbadb_inactives + availability="history_inactive". Fixture tables follow nbadb's documented schema; the loader has
never seen the real dataset (no Kaggle credentials), so these tests pin the contract, not real-data behaviour."""
import numpy as np
import pandas as pd
import pytest

from nba.data import nbadb_inactives as N
from nba.props import config as C
from nba.props import features as F
from nba.teams import NBA_ID
from tests.nba_props_helpers import make_masters, prepared

AV = list(C.AVAILABILITY_FEATURES)


def write_tables(tmp_path, inactive, games, fmt="parquet"):
    for name, df in ((N.INACTIVE_TABLE, inactive), (N.GAME_TABLE, games)):
        (df.to_parquet if fmt == "parquet" else df.to_csv)(tmp_path / f"{name}.{fmt}", index=False)


def tables():
    games = pd.DataFrame({"game_id": ["0022500001", "0022500002"], "game_date": ["2025-11-01", "2025-11-02"],
                          "home_team_id": [NBA_ID["BOS"], NBA_ID["LAL"]], "visitor_team_id": [NBA_ID["NYK"], NBA_ID["MIA"]]})
    ina = pd.DataFrame({"game_id": ["0022500001", "0022500001", "0022500002", "0022500002"],
                        "team_id": [NBA_ID["BOS"], NBA_ID["NYK"], NBA_ID["LAL"], 999],
                        "person_id": [1, 2, 3, 4], "first_name": ["Jayson", "Jalen", "Austin", "X"],
                        "family_name": ["Tatum", "Brunson", "Reaves", "Y"], "jersey_num": ["0", "11", "15", "1"]})
    return ina, games


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_load_maps_team_ids_names_and_dates(tmp_path, fmt):
    write_tables(tmp_path, *tables(), fmt=fmt)
    out = N.load_inactives(str(tmp_path))
    assert list(out.columns) == ["game_id", "game_date", "team", "nba_person_id", "name"]
    assert out["team"].tolist() == ["BOS", "NYK", "LAL"] and out.attrs["n_unknown_team"] == 1     # team 999 dropped, counted
    assert out["name"].tolist()[:2] == ["Jayson Tatum", "Jalen Brunson"]
    assert out["game_date"].iloc[0] == pd.Timestamp("2025-11-01")


def test_missing_table_or_column_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        N.load_inactives(str(tmp_path))
    ina, games = tables()
    write_tables(tmp_path, ina.drop(columns=["first_name"]), games)
    with pytest.raises(ValueError, match="first_name"):
        N.load_inactives(str(tmp_path))


def test_to_espn_resolves_by_name_team_and_date_and_fails_closed():
    pb, gm, tips = make_masters(n_days=12, seed=2)
    rows, tg = prepared(pb, gm, tips)
    bos = tg[tg["team"] == "BOS"].sort_values("tip_sort").iloc[0]
    inactives = pd.DataFrame({
        "game_id": ["a", "b", "c", "d"], "game_date": [bos["date"]] * 3 + [bos["date"] - pd.Timedelta(days=40)],
        "team": ["BOS", "BOS", "BOS", "BOS"], "nba_person_id": [1, 2, 3, 4],
        "name": ["Jayson Tatum", "Nobody Known", "Jaylen Brown", "Jayson Tatum"]})
    out, st = N.to_espn(inactives, rows, tg)
    assert st == {"in": 4, "out": 2, "unmatched_game": 1, "unresolved_name": 1}      # unknown name + a date with no game dropped
    assert set(out["player_id"]) == {"BOS-0", "BOS-1"} and set(out["game_id"]) == {bos["game_id"]}


def test_history_inactive_mode_counts_only_listed_rotation_absentees():
    pb, gm, tips = make_masters(skip={"BOS-0": set(range(14, 30))})
    rows, tg = prepared(pb, gm, tips)
    miss = pb[(pb["player_id"] == "BOS-0") & (~pb["played"].astype(bool))]
    ina = pd.DataFrame({"game_id": miss["game_id"].values, "team": "BOS", "player_id": "BOS-0"})
    ctx = F.FeatureContext(rows, tg, inactives=ina)
    Xh = ctx.features(rows[F.ID_COLS], availability="history")
    Xi = ctx.features(rows[F.ID_COLS], availability="history_inactive")
    assert (Xi["team_missing_n_rotation"].fillna(0) <= Xh["team_missing_n_rotation"].fillna(0) + 1e-9).all()
    assert (Xi["team_missing_n_rotation"].fillna(0) < Xh["team_missing_n_rotation"].fillna(0) - 1e-9).any()
    both = rows[(rows["team"] == "BOS") & (rows["player_id"] != "BOS-0")].index
    assert Xi.loc[both, "team_missing_n_rotation"].max() >= 1.0                    # BOS-0's listed stretch is seen
    other = rows[rows["team"] != "BOS"].index
    assert Xi.loc[other, "team_missing_n_rotation"].dropna().eq(0).all()           # nobody else is on the list


def test_history_inactive_requires_the_frame():
    pb, gm, tips = make_masters(n_days=12)
    rows, tg = prepared(pb, gm, tips)
    with pytest.raises(ValueError, match="inactives"):
        F.FeatureContext(rows, tg).features(rows[F.ID_COLS].head(2), availability="history_inactive")
