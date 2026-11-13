"""Tests for nhl.data.shiftcharts_bank."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from mlb_core import storage
from nhl.data import shiftcharts_bank as B
from nhl.data.settings import raw_shiftchart_key


def test_trailing_window_ids():
    rows = []
    # Two teams (AAA, BBB) playing each other 12 times (all regular-season FINAL)
    for i in range(1, 13):
        rows.append({
            "game_id": 1000 + i,
            "season_year": 2024,
            "game_type": 2,
            "date": f"2024-10-{i:02d}",
            "state": "FINAL",
            "home_abbr": "AAA",
            "away_abbr": "BBB",
        })
    # Plus games vs CCC (regular-season FINAL)
    rows.append({
        "game_id": 1013,
        "season_year": 2024,
        "game_type": 2,
        "date": "2024-10-13",
        "state": "FINAL",
        "home_abbr": "AAA",
        "away_abbr": "CCC",
    })
    rows.append({
        "game_id": 1014,
        "season_year": 2024,
        "game_type": 2,
        "date": "2024-10-14",
        "state": "FINAL",
        "home_abbr": "BBB",
        "away_abbr": "CCC",
    })
    # One game_type 3 (playoff) game with latest date
    rows.append({
        "game_id": 2001,
        "season_year": 2024,
        "game_type": 3,
        "date": "2024-10-20",
        "state": "FINAL",
        "home_abbr": "AAA",
        "away_abbr": "BBB",
    })
    # One state "LIVE" game with latest date
    rows.append({
        "game_id": 2002,
        "season_year": 2024,
        "game_type": 2,
        "date": "2024-10-21",
        "state": "LIVE",
        "home_abbr": "AAA",
        "away_abbr": "CCC",
    })

    games_df = pd.DataFrame(rows)

    # For window=10:
    # AAA eligible: 1001..1013 (13 games). Last 10: 1004..1013.
    # BBB eligible: 1001..1012, 1014 (13 games). Last 10: 1004..1012, 1014.
    # CCC eligible: 1013, 1014 (2 games). Last 10: 1013, 1014.
    # Union is exactly {1004..1014}.
    expected_ids = list(range(1004, 1015))

    actual_ids = B.trailing_window_ids(games_df, window=10)

    assert actual_ids == expected_ids
    assert all(isinstance(gid, int) for gid in actual_ids)
    assert 2001 not in actual_ids  # Playoff excluded
    assert 2002 not in actual_ids  # LIVE excluded
    assert 1001 not in actual_ids  # Excluded by window
    assert 1002 not in actual_ids  # Excluded by window
    assert 1003 not in actual_ids  # Excluded by window

    # Test as_of filter
    as_of_ids = B.trailing_window_ids(games_df, window=10, as_of="2024-10-12")
    # Only games up to 2024-10-12: games 1001..1012. Last 10 for AAA/BBB are 1003..1012.
    assert as_of_ids == list(range(1003, 1013))


def test_bank_game_skips_existing_and_writes_new(monkeypatch):
    existing_keys: set[str] = set()
    writes: dict[str, bytes] = {}

    monkeypatch.setattr(storage, "exists", lambda key: key in existing_keys)
    monkeypatch.setattr(storage, "write_bytes", lambda data, key: writes.__setitem__(key, data))

    class FakeClient:
        def __init__(self, payload):
            self.payload = payload
            self.calls: list[int] = []

        def shiftcharts(self, game_id: int):
            self.calls.append(game_id)
            return self.payload

    # 1. Key exists and force=False -> "already" without fetch or write
    gid_existing = 2024020001
    existing_keys.add(raw_shiftchart_key(gid_existing))
    client_existing = FakeClient({"data": [{"typeCode": 517}]})

    res_already = B.bank_game(gid_existing, client=client_existing)
    assert res_already == "already"
    assert client_existing.calls == []
    assert raw_shiftchart_key(gid_existing) not in writes

    # 2. Key does not exist -> "banked" plus one gzip write
    gid_new = 2024020002
    expected_payload = {"data": [{"typeCode": 517, "playerId": 8478402}]}
    client_new = FakeClient(expected_payload)

    res_banked = B.bank_game(gid_new, client=client_new)
    assert res_banked == "banked"
    assert client_new.calls == [gid_new]
    target_key = raw_shiftchart_key(gid_new)
    assert target_key in writes
    decompressed = json.loads(gzip.decompress(writes[target_key]))
    assert decompressed == expected_payload

    # 3. Payload has no data -> "missing" plus no write
    gid_missing = 2024020003
    client_missing = FakeClient({"data": []})

    res_missing = B.bank_game(gid_missing, client=client_missing)
    assert res_missing == "missing"
    assert client_missing.calls == [gid_missing]
    assert raw_shiftchart_key(gid_missing) not in writes


def test_run_counts_statuses_and_handles_failures(monkeypatch):
    existing_keys: set[str] = {raw_shiftchart_key(1)}
    writes: dict[str, bytes] = {}

    monkeypatch.setattr(storage, "exists", lambda key: key in existing_keys)
    monkeypatch.setattr(storage, "write_bytes", lambda data, key: writes.__setitem__(key, data))

    class MultiFakeClient:
        def shiftcharts(self, game_id: int):
            if game_id == 2:
                return {"data": [{"typeCode": 517}]}
            if game_id == 3:
                return {"data": []}
            if game_id == 4:
                raise RuntimeError("API network glitch")
            return None

    summary = B.run(game_ids=[1, 2, 3, 4], delay=0.0, client=MultiFakeClient())
    assert summary == {
        "requested": 4,
        "already": 1,
        "banked": 1,
        "missing": 1,
        "failed": 1,
    }


def test_main_cli(monkeypatch):
    monkeypatch.setattr(
        B,
        "run",
        lambda **kwargs: {"requested": 2, "already": 1, "banked": 1, "missing": 0, "failed": 0},
    )
    assert B.main(["--game-ids", "1,2", "--delay", "0.0"]) == 0

    monkeypatch.setattr(
        B,
        "run",
        lambda **kwargs: {"requested": 2, "already": 0, "banked": 0, "missing": 0, "failed": 2},
    )
    assert B.main(["--game-ids", "1,2"]) == 1


def test_output_is_ascii_only():
    root = Path(__file__).resolve().parent.parent
    for p in ("nhl/data/shiftcharts_bank.py", "tests/test_nhl_shiftcharts_bank.py"):
        txt = (root / p).read_bytes()
        assert all(b < 128 for b in txt), f"Non-ASCII found in {p}"
