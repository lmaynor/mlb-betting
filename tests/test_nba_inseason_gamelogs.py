"""NBA game logs keep updating in-season: the SportsBlaze master (frozen at its last game, it has no 2026-27 season)
is topped up with the hoopR/ESPN player box for every game after the master's newest date.

Pure in-memory: mlb_core.storage.read_bytes is monkeypatched, nothing touches the network or GCS."""
import io

import numpy as np
import pandas as pd

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf

HOOPR_2026 = cf.NBA_HOOPR_KEY.format(season=2026)
HOOPR_2027 = cf.NBA_HOOPR_KEY.format(season=2027)


def _put(monkeypatch, files):
    def read_bytes(key):
        if key not in files:
            raise FileNotFoundError(key)
        v = files[key]
        buf = io.BytesIO()
        (v.to_parquet(buf, index=False) if key.endswith(".parquet") else v.to_csv(buf, index=False))
        return buf.getvalue()
    monkeypatch.setattr("mlb_core.storage.read_bytes", read_bytes)
    cf.clear_cache()


def _master(n=12, last="2026-06-14", name=" Test Guard"):
    """SportsBlaze-shaped master rows (season_year 2025, MM:SS minutes, a leading space on the name as in prod)."""
    days = pd.date_range(end=last, periods=n)
    return pd.DataFrame({
        "game_id": [f"sb{i}" for i in range(n)], "season_year": 2025, "season_type": "Regular Season",
        "date": [d.date().isoformat() for d in days], "team_abbr": "GSW", "name": name, "played": True,
        "minutes": "30:00", "points": 20, "rebounds": 5, "assists": 5, "three_points_made": 3, "steals": 1,
        "blocks": 0, "turnovers": 2})


def _hoopr(dates, name="Test Guard", team="GS", season=2027, points=30, season_type=2, dnp=False, minutes=34.0,
           athlete_id=77, game_prefix="e"):
    n = len(dates)
    return pd.DataFrame({
        "game_id": [f"{game_prefix}{i}" for i in range(n)], "athlete_id": float(athlete_id),
        "athlete_display_name": name, "team_abbreviation": team, "game_date": dates, "season": season,
        "season_type": season_type, "did_not_play": dnp, "minutes": minutes if not dnp else np.nan,
        "points": points, "rebounds": 8, "assists": 9, "steals": 2, "blocks": 1, "turnovers": 3, "fouls": 2,
        "three_point_field_goals_made": 4, "three_point_field_goals_attempted": 9, "field_goals_made": 10,
        "field_goals_attempted": 20, "free_throws_made": 6, "free_throws_attempted": 7})


def _rows(name="test guard"):
    return cf._cached("form:nba", cf._load_nba)["all"][name]


def test_new_season_games_after_the_master_cutoff_are_appended(monkeypatch):
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2027: _hoopr(["2026-10-20", "2026-10-22"])})
    rows = _rows()
    assert len(rows) == 14
    assert rows[-1]["_sort"] == "2026-10-22" and rows[-1]["points"] == 30 and rows[-1]["three_points_made"] == 4
    assert rows[-1]["season_year"] == 2026 and rows[-1]["team_abbr"] == "GSW"      # ESPN "GS" folded to the tricode
    # the engine sees the new games: they are the newest entries of the player's history
    hist = dp._hist(rows, cf.NBA_STATS["points"])
    assert [h[0] for h in hist[-2:]] == [30, 30] and hist[-1][2] == 2026
    assert dp.GameLogs("nba", {"all": {"test guard": rows}}).latest_date() == "2026-10-22"
    assert dp.GameLogs("nba", {"all": {"test guard": rows}}).team_of(rows) == "GSW"


def test_without_the_new_season_file_the_master_alone_is_served(monkeypatch):
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master()})
    rows = _rows()
    assert len(rows) == 12 and rows[-1]["_sort"] == "2026-06-14"


def test_unreadable_hoopr_file_degrades_to_the_master(monkeypatch):
    files = {cf.NBA_KEYS[1]: _master()}
    _put(monkeypatch, files)
    real = cf._st.read_bytes
    monkeypatch.setattr("mlb_core.storage.read_bytes",
                        lambda k: b"not a parquet file" if k == HOOPR_2027 else real(k))
    assert len(_rows()) == 12


def test_overlap_with_the_master_is_not_double_counted(monkeypatch):
    # hoopR 2026 (the 2025-26 season) overlaps the master; only games after the master's newest date are taken
    overlap = _hoopr(["2026-06-10", "2026-06-14", "2026-06-15"], season=2026, game_prefix="o")
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2026: overlap})
    rows = _rows()
    assert len(rows) == 13 and rows[-1]["_sort"] == "2026-06-15"


def test_dnp_preseason_and_missing_minutes_rows_are_dropped(monkeypatch):
    e = pd.concat([_hoopr(["2026-10-20"], game_prefix="a"),
                   _hoopr(["2026-10-21"], dnp=True, game_prefix="b"),
                   _hoopr(["2026-10-22"], season_type=1, game_prefix="c"),
                   _hoopr(["2026-10-23"], season_type=3, game_prefix="d")], ignore_index=True)
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2027: e})
    rows = _rows()
    assert [r["_sort"] for r in rows[-2:]] == ["2026-10-20", "2026-10-23"] and len(rows) == 14


def test_duplicate_espn_rows_and_unknown_player_names(monkeypatch):
    dup = pd.concat([_hoopr(["2026-10-20"]), _hoopr(["2026-10-20"]),
                     _hoopr(["2026-10-20"], name="Rookie McNew", athlete_id=99, game_prefix="r")], ignore_index=True)
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2027: dup})
    idx = cf._cached("form:nba", cf._load_nba)["all"]
    assert len(idx["test guard"]) == 13 and len(idx["rookie mcnew"]) == 1


def test_missing_columns_in_the_mirror_degrade_to_the_master(monkeypatch):
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2027: _hoopr(["2026-10-20"]).drop(columns=["minutes"])})
    assert len(_rows()) == 12


def test_recent_form_serves_the_new_games(monkeypatch):
    _put(monkeypatch, {cf.NBA_KEYS[1]: _master(), HOOPR_2027: _hoopr(["2026-10-20", "2026-10-22"])})
    f = cf.recent_form("nba", "points", 24.5, "Test Guard")
    assert f["n_games"] == 10 and [g["val"] for g in f["games"][-2:]] == [30, 30]
    assert f["games"][-1]["usage"] == 34.0 and f["games"][-1]["date"] == "2026-10-22"
