"""Tests for nhl.data.lines."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from nhl.data import lines


def make_shift(pid: int, tid: int, abbr: str, period: int, start: str, end: str) -> dict:
    return {
        "playerId": pid,
        "teamId": tid,
        "teamAbbrev": abbr,
        "period": period,
        "startTime": start,
        "endTime": end,
        "typeCode": 517,
    }


def make_fake_game(
    home_id: int = 1,
    away_id: int = 2,
    home_abbr: str = "AAA",
    away_abbr: str = "BBB",
    game_date: str = "2024-10-15",
) -> tuple[dict, dict]:
    """Build a fake PBP dict + shift chart dict for one game.

    Two teams (ids 1, 2; abbrevs AAA, BBB), each with 1 goalie (pos G), 3 defensemen, 6 forwards.
    Shift rows are typeCode 517 dicts.
    Home forwards F1 F2 F3 share period 1 (00:00-20:00) together;
    Home forwards F4 F5 F6 share period 2 (00:00-20:00) together;
    F1..F3 have NO shifts in period 2 and F4..F6 none in period 1.
    Defensemen D1 D2 play both periods.
    Away team static all game: 1 goalie, 2 defensemen, 3 forwards play both periods.
    Every second has exactly 5 skaters + goalie per team for 5v5 mask across periods 1 and 2.
    """
    rs = []
    # Home roster
    rs.append({"playerId": 901, "teamId": home_id, "positionCode": "G", "firstName": {"default": "Goalie"}, "lastName": {"default": "Home"}})
    rs.append({"playerId": 101, "teamId": home_id, "positionCode": "D", "firstName": {"default": "Dan"}, "lastName": {"default": "One"}})
    rs.append({"playerId": 102, "teamId": home_id, "positionCode": "D", "firstName": {"default": "Dan"}, "lastName": {"default": "Two"}})
    rs.append({"playerId": 103, "teamId": home_id, "positionCode": "D", "firstName": {"default": "Dan"}, "lastName": {"default": "Three"}})
    f_names = ["Fred One", "Fred Two", "Fred Three", "Frank Four", "Frank Five", "Frank Six"]
    f_positions = ["C", "L", "R", "C", "L", "R"]
    for i, pid in enumerate([111, 112, 113, 114, 115, 116]):
        fn, ln = f_names[i].split()
        rs.append({"playerId": pid, "teamId": home_id, "positionCode": f_positions[i], "firstName": {"default": fn}, "lastName": {"default": ln}})

    # Away roster
    rs.append({"playerId": 902, "teamId": away_id, "positionCode": "G", "firstName": {"default": "Goalie"}, "lastName": {"default": "Away"}})
    rs.append({"playerId": 201, "teamId": away_id, "positionCode": "D", "firstName": {"default": "Art"}, "lastName": {"default": "One"}})
    rs.append({"playerId": 202, "teamId": away_id, "positionCode": "D", "firstName": {"default": "Art"}, "lastName": {"default": "Two"}})
    rs.append({"playerId": 203, "teamId": away_id, "positionCode": "D", "firstName": {"default": "Art"}, "lastName": {"default": "Three"}})
    for i, pid in enumerate([211, 212, 213, 214, 215, 216]):
        rs.append({"playerId": pid, "teamId": away_id, "positionCode": f_positions[i], "firstName": {"default": f"AwayF{i+1}"}, "lastName": {"default": "Player"}})

    pbp = {
        "homeTeam": {"id": home_id, "abbrev": home_abbr},
        "awayTeam": {"id": away_id, "abbrev": away_abbr},
        "rosterSpots": rs,
        "plays": [],
        "gameType": 2,
        "gameDate": game_date,
    }

    shifts = []
    # Home: G1 (901), D1 (101), D2 (102) play both period 1 and 2
    for p in (1, 2):
        shifts.append(make_shift(901, home_id, home_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(101, home_id, home_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(102, home_id, home_abbr, p, "00:00", "20:00"))

    # Home forwards: F1, F2, F3 play period 1 (00:00-20:00)
    for pid in (111, 112, 113):
        shifts.append(make_shift(pid, home_id, home_abbr, 1, "00:00", "20:00"))

    # Home forwards: F4, F5, F6 play period 2 (00:00-20:00)
    for pid in (114, 115, 116):
        shifts.append(make_shift(pid, home_id, home_abbr, 2, "00:00", "20:00"))

    # Away: static all game (G2, D21, D22, FA1, FA2, FA3)
    for p in (1, 2):
        shifts.append(make_shift(902, away_id, away_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(201, away_id, away_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(202, away_id, away_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(211, away_id, away_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(212, away_id, away_abbr, p, "00:00", "20:00"))
        shifts.append(make_shift(213, away_id, away_abbr, p, "00:00", "20:00"))

    chart = {"data": shifts}
    return chart, pbp


def test_game_pair_seconds():
    chart, pbp = make_fake_game()
    res = lines.game_pair_seconds(chart, pbp)
    assert res is not None
    assert "AAA" in res and "BBB" in res

    home_pairs = res["AAA"]["pairs"]
    # F1-F2 shared seconds == 1200 (period 1 at 5v5)
    assert home_pairs[(111, 112)] == 1200
    assert home_pairs[(111, 113)] == 1200
    assert home_pairs[(112, 113)] == 1200

    # F1-F4 == 0 (or absent)
    assert home_pairs.get((111, 114), 0) == 0
    assert (111, 114) not in home_pairs or home_pairs[(111, 114)] == 0

    # D1-D2 == 2400 (both periods at 5v5)
    assert home_pairs[(101, 102)] == 2400

    # D3 (103) had no shifts -> not in skaters
    assert 103 not in res["AAA"]["players"]
    # Goalie 901 not in skaters
    assert 901 not in res["AAA"]["players"]

    # Away team static pairs
    away_pairs = res["BBB"]["pairs"]
    assert away_pairs[(201, 202)] == 2400
    assert away_pairs[(211, 212)] == 2400


def test_game_pair_seconds_guards():
    chart, pbp = make_fake_game()
    assert lines.game_pair_seconds(None, pbp) is None
    assert lines.game_pair_seconds({}, pbp) is None
    assert lines.game_pair_seconds({"data": []}, pbp) is None
    assert lines.game_pair_seconds(chart, None) is None
    assert lines.game_pair_seconds(chart, {}) is None
    assert lines.game_pair_seconds(chart, {"rosterSpots": []}) is None

    bad_pbp = dict(pbp)
    bad_pbp["homeTeam"] = {}
    assert lines.game_pair_seconds(chart, bad_pbp) is None


def test_derive_lines():
    F1, F2, F3, F4, F5, F6 = 111, 112, 113, 114, 115, 116
    D1, D2 = 101, 102

    pair_totals = {
        (F1, F2): 5000,
        (F1, F3): 4800,
        (F2, F3): 4900,
        (F4, F5): 3000,
        (F1, F4): 100,
        (F1, F5): 100,
        (F1, F6): 100,
        (F2, F4): 100,
        (F2, F5): 100,
        (F2, F6): 100,
        (F3, F4): 100,
        (F3, F5): 100,
        (F3, F6): 100,
        (F4, F6): 100,
        (F5, F6): 100,
        (D1, D2): 4000,
    }

    players = {
        F1: {"name": "Forward One", "pos": "C", "player_norm": "forward one"},
        F2: {"name": "Forward Two", "pos": "L", "player_norm": "forward two"},
        F3: {"name": "Forward Three", "pos": "R", "player_norm": "forward three"},
        F4: {"name": "Forward Four", "pos": "C", "player_norm": "forward four"},
        F5: {"name": "Forward Five", "pos": "L", "player_norm": "forward five"},
        F6: {"name": "Forward Six", "pos": "R", "player_norm": "forward six"},
        D1: {"name": "Defense One", "pos": "D", "player_norm": "defense one"},
        D2: {"name": "Defense Two", "pos": "D", "player_norm": "defense two"},
    }

    derived = lines.derive_lines("AAA", pair_totals, players)

    f_lines = [l for l in derived if l["kind"] == "F"]
    d_lines = [l for l in derived if l["kind"] == "D"]

    assert len(f_lines) >= 1
    # First F line is exactly {F1, F2, F3}
    first_f_pids = {p["player_id"] for p in f_lines[0]["players"]}
    assert first_f_pids == {F1, F2, F3}
    assert f_lines[0]["line_id"] == "AAA-F1"

    # No line mixes F1 with F4/F5
    for l in f_lines:
        pids = {p["player_id"] for p in l["players"]}
        if F1 in pids:
            assert F4 not in pids and F5 not in pids

    # Second F line is the leftover pair {F4, F5}
    if len(f_lines) > 1:
        second_f_pids = {p["player_id"] for p in f_lines[1]["players"]}
        assert second_f_pids == {F4, F5}
        assert f_lines[1]["line_id"] == "AAA-F2"

    # A D line {D1, D2} exists
    assert len(d_lines) == 1
    assert {p["player_id"] for p in d_lines[0]["players"]} == {D1, D2}
    assert d_lines[0]["line_id"] == "AAA-D1"
    assert d_lines[0]["pair_seconds"] == {f"{D1}|{D2}": 4000}


def test_player_index_and_thresholds():
    F1, F2 = 111, 112
    D1, D2 = 101, 102

    # Forward pair 800 < SEED_PAIR_MIN_S (900), D pair 1000 < DEF_PAIR_MIN_S (1200)
    weak_pairs = {(F1, F2): 800, (D1, D2): 1000}
    players = {
        F1: {"name": "Forward One", "pos": "C"},
        F2: {"name": "Forward Two", "pos": "L"},
        D1: {"name": "Defense One", "pos": "D"},
        D2: {"name": "Defense Two", "pos": "D"},
    }

    derived = lines.derive_lines("AAA", weak_pairs, players)
    assert derived == []


def test_build_artifact_and_publish(monkeypatch):
    chart, pbp = make_fake_game(game_date="2024-11-20")
    chart_gz = gzip.compress(json.dumps(chart).encode("utf-8"))
    pbp_gz = gzip.compress(json.dumps(pbp).encode("utf-8"))

    storage_data = {
        lines.raw_shiftchart_key(1001): chart_gz,
        lines.raw_pbp_key(1001): pbp_gz,
    }

    games_df = pd.DataFrame([{
        "game_id": 1001,
        "date": "2024-11-20",
        "game_type": 2,
        "state": "FINAL",
        "home_abbr": "AAA",
        "away_abbr": "BBB",
    }])

    monkeypatch.setattr(lines.storage, "read_csv", lambda key, **kw: games_df)
    monkeypatch.setattr(lines.storage, "read_bytes", lambda key: storage_data[key])

    written_data = {}
    monkeypatch.setattr(lines.storage, "write_bytes", lambda data, key: written_data.__setitem__(key, data))

    artifact, stats = lines.build_artifact(window=10, game_ids=[1001])

    assert artifact["version"] == 1
    assert artifact["as_of"] == "2024-11-20"
    assert artifact["n_games"] == 1
    assert "AAA" in artifact["teams"]
    assert stats["games_requested"] == 1
    assert stats["games_used"] == 1
    assert stats["no_chart"] == 0
    assert stats["no_pbp"] == 0

    # Every player in player_index must be in a derived line
    derived_pids = set()
    for team_data in artifact["teams"].values():
        for line_data in team_data["lines"]:
            for pl in line_data["players"]:
                derived_pids.add(pl["player_id"])

    indexed_pids = {entry["player_id"] for entries in artifact["player_index"].values() for entry in entries}
    assert indexed_pids == derived_pids

    # Publish
    keys = lines.publish(artifact)
    assert lines.LINES_LATEST_KEY in keys
    assert "NHL/lines/lines_2024-11-20.json" in keys
    assert len(written_data) == 2


def test_build_artifact_missing_chart_and_pbp(monkeypatch):
    storage_data = {}
    monkeypatch.setattr(lines.storage, "read_bytes", lambda key: storage_data.get(key))
    monkeypatch.setattr(lines.storage, "read_csv", lambda key, **kw: pd.DataFrame())

    artifact, stats = lines.build_artifact(window=5, game_ids=[2001, 2002])
    assert stats["games_requested"] == 2
    assert stats["games_used"] == 0
    assert stats["no_chart"] == 2


def test_cli_main(monkeypatch, tmp_path):
    chart, pbp = make_fake_game(game_date="2024-11-20")
    chart_gz = gzip.compress(json.dumps(chart).encode("utf-8"))
    pbp_gz = gzip.compress(json.dumps(pbp).encode("utf-8"))
    storage_data = {
        lines.raw_shiftchart_key(1001): chart_gz,
        lines.raw_pbp_key(1001): pbp_gz,
    }

    monkeypatch.setattr(lines.storage, "read_csv", lambda key, **kw: pd.DataFrame())
    monkeypatch.setattr(lines.storage, "read_bytes", lambda key: storage_data[key])
    monkeypatch.setattr(lines.storage, "write_bytes", lambda data, key: None)

    out_file = tmp_path / "test_lines.json"
    code = lines.main(["--game-ids", "1001", "--out", str(out_file), "--no-write"])
    assert code == 0
    assert out_file.exists()
    saved = json.loads(out_file.read_text())
    assert saved["version"] == 1
