"""
Unit tests for mlb_core.odds.bestbook.
Tests pure transforms, odds conversions, state management, and mocked network handlers.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mlb_core.odds import bestbook as bb


def test_parse_game_key():
    # NFL key (unprefixed)
    res = bb.parse_game_key("2026-11-02-CHI-SEA")
    assert res == {
        "sport": "nfl",
        "date": "2026-11-02",
        "away": "CHI",
        "home": "SEA",
    }

    # NHL key (nhl: prefix)
    res = bb.parse_game_key("nhl:2026-10-30-NJD-VGK")
    assert res == {
        "sport": "nhl",
        "date": "2026-10-30",
        "away": "NJD",
        "home": "VGK",
    }

    # NBA key (nba: prefix)
    res = bb.parse_game_key("nba:2026-10-30-CHA-WAS")
    assert res == {
        "sport": "nba",
        "date": "2026-10-30",
        "away": "CHA",
        "home": "WAS",
    }

    # Invalid / unsupported keys
    assert bb.parse_game_key("") is None
    assert bb.parse_game_key("invalid-string") is None
    assert bb.parse_game_key(None) is None
    assert bb.parse_game_key("mlb:2026-10-30-NYY-LAD") is None


def test_parse_slate():
    status = {
        "feeds": {"dk": "ok"},
        "slate": {
            "games": [
                {
                    "key": "2026-11-02-CHI-SEA",
                    "away": "CHI",
                    "home": "SEA",
                    "start": "2026-11-02T01:15:00.000Z",
                    "venues": ["dk", "fd"],
                }
            ]
        },
        "slate:nhl": {
            "games": [
                {
                    "key": "nhl:2026-10-30-NJD-VGK",
                    "away": "NJD",
                    "home": "VGK",
                    "start": "2026-10-30T22:10:00.000Z",
                    "venues": ["dk", "mgm"],
                }
            ]
        },
        "slate:nba": {
            "games": [
                {
                    "key": "nba:2026-10-30-CHA-WAS",
                    "away": "CHA",
                    "home": "WAS",
                    "start": "2026-10-30T23:10:00.000Z",
                    "venues": ["dk", "fan"],
                }
            ]
        },
        "pp:2026-11-02-CHI-SEA": {"status": "ok"},
    }

    slate = bb.parse_slate(status)
    assert len(slate) == 3
    keys = {g["key"] for g in slate}
    assert keys == {
        "2026-11-02-CHI-SEA",
        "nhl:2026-10-30-NJD-VGK",
        "nba:2026-10-30-CHA-WAS",
    }

    nfl = next(g for g in slate if g["key"] == "2026-11-02-CHI-SEA")
    assert nfl["sport"] == "nfl"
    assert nfl["date"] == "2026-11-02"
    assert nfl["away"] == "CHI"
    assert nfl["home"] == "SEA"
    assert nfl["venues"] == ["dk", "fd"]


def test_pct_to_prob():
    assert bb.pct_to_prob(54.5454) == pytest.approx(0.545454)
    assert bb.pct_to_prob(50.0) == pytest.approx(0.5)
    assert bb.pct_to_prob("50.0") == pytest.approx(0.5)
    # Out of range / degenerate
    assert bb.pct_to_prob(0) is None
    assert bb.pct_to_prob(100) is None
    assert bb.pct_to_prob(-10) is None
    assert bb.pct_to_prob(120) is None
    assert bb.pct_to_prob(None) is None
    assert bb.pct_to_prob("bad") is None


def test_prob_to_american():
    # 50% -> -100
    assert bb.prob_to_american(0.5) == -100
    # ~54.5454% -> -120
    assert bb.prob_to_american(0.545454) == -120
    # 20% -> +400
    assert bb.prob_to_american(0.20) == 400
    # 80% -> -400
    assert bb.prob_to_american(0.80) == -400
    # Degenerate cases
    assert bb.prob_to_american(None) is None
    assert bb.prob_to_american(0) is None
    assert bb.prob_to_american(1.0) is None
    assert bb.prob_to_american(-0.5) is None


def test_pct_to_american():
    assert bb.pct_to_american(54.5454) == -120
    assert bb.pct_to_american(50.0) == -100
    assert bb.pct_to_american(20.0) == 400
    assert bb.pct_to_american(None) is None


def test_clean_native():
    assert bb.clean_native("-120") == "-120"
    assert bb.clean_native("\u2212120") == "-120"
    assert bb.clean_native("  +150  ") == "+150"
    assert bb.clean_native(None) is None
    assert bb.clean_native("") is None


def test_ms_to_iso():
    # 1730304000000 ms is 2024-10-30T16:00:00+00:00
    iso = bb._ms_to_iso(1730304000000)
    assert iso == "2024-10-30T16:00:00+00:00"
    assert bb._ms_to_iso(None) is None
    assert bb._ms_to_iso("invalid") is None


def test_venue_info():
    assert bb.venue_info("dk") == ("draftkings", "book")
    assert bb.venue_info("kalshi") == ("kalshi", "exchange")
    assert bb.venue_info("novig") == ("novig", "exchange")
    assert bb.venue_info("pp") == ("prizepicks", "dfs")
    assert bb.venue_info("ud") == ("underdog", "dfs")
    assert bb.venue_info("unknown_xyz") == ("unknown_xyz", "book")


def test_normalize_row_merged():
    row = {
        "id": "match:nba:2026-10-30-CHA-WAS|points|full|25.5",
        "game": "nba:2026-10-30-CHA-WAS",
        "gameTeams": ["CHA", "WAS"],
        "section": "props",
        "family": "points",
        "familyTitle": "Player Points",
        "rung": 25.5,
        "line": 25.5,
        "team": "CHA",
        "player": "LaMelo Ball",
        "bestYes": {"venue": "dk", "price": 52.0},
        "bestNo": {"venue": "fd", "price": 52.0},
        "edge": -0.04,
        "ts": 1730304000000,
        "quotes": [
            {
                "venue": "dk",
                "book": True,
                "yes": 52.381,
                "no": 52.381,
                "yesFee": 0,
                "noFee": 0,
                "yesSz": None,
                "noSz": None,
                "yesNative": "\u2212110",
                "noNative": "-110",
                "href": "https://sportsbook.draftkings.com/event/123",
                "yesSince": 1730304000000,
                "noSince": 1730304000000,
            },
            {
                "venue": "pp",
                "book": False,
                "yes": 50.0,
                "no": 50.0,
                "yesFee": 0,
                "noFee": 0,
                "yesSz": None,
                "noSz": None,
                "yesNative": None,
                "noNative": None,
                "href": None,
                "yesSince": 1730304000000,
                "noSince": 1730304000000,
            },
        ],
    }

    records = bb.normalize_row(row)
    assert len(records) == 2

    dk_rec = records[0]
    assert dk_rec["row_id"] == row["id"]
    assert dk_rec["sport"] == "nba"
    assert dk_rec["date"] == "2026-10-30"
    assert dk_rec["away"] == "CHA"
    assert dk_rec["home"] == "WAS"
    assert dk_rec["player"] == "LaMelo Ball"
    assert dk_rec["team"] == "CHA"
    assert dk_rec["line"] == 25.5
    assert dk_rec["venue"] == "dk"
    assert dk_rec["book"] == "draftkings"
    assert dk_rec["kind"] == "book"
    assert dk_rec["bettable"] is True
    assert dk_rec["yes_prob"] == pytest.approx(0.52381)
    assert dk_rec["yes_american"] == -110
    assert dk_rec["yes_native"] == "-110"
    assert dk_rec["no_native"] == "-110"
    assert dk_rec["href"] == "https://sportsbook.draftkings.com/event/123"
    assert dk_rec["best_yes_venue"] == "dk"
    assert dk_rec["edge"] == -0.04
    assert dk_rec["ts"] == "2024-10-30T16:00:00+00:00"

    pp_rec = records[1]
    assert pp_rec["venue"] == "pp"
    assert pp_rec["book"] == "prizepicks"
    assert pp_rec["kind"] == "dfs"
    assert pp_rec["bettable"] is False


def test_normalize_row_single_venue_synthesis():
    # Unmerged row from changes stream where quotes is None
    row = {
        "id": "match:nfl:2026-11-02-CHI-SEA|spread|full|-3",
        "venue": "fd",
        "game": "2026-11-02-CHI-SEA",
        "gameTeams": ["CHI", "SEA"],
        "section": "main",
        "family": "spread",
        "yes": 52.381,
        "no": 52.381,
        "yesNative": "-110",
        "noNative": "-110",
        "href": "https://fanduel.com",
    }
    records = bb.normalize_row(row)
    assert len(records) == 1
    rec = records[0]
    assert rec["venue"] == "fd"
    assert rec["book"] == "fanduel"
    assert rec["kind"] == "book"
    assert rec["bettable"] is True
    assert rec["yes_native"] == "-110"


def test_apply_changes():
    state: dict = {}

    # Snapshot message
    snap_msg = {
        "type": "snapshot",
        "rows": [
            {"id": "row1", "val": 1},
            {"id": "row2", "val": 2},
        ],
    }
    u, d = bb.apply_changes(state, snap_msg)
    assert u == 2
    assert d == 0
    assert len(state) == 2
    assert state["row1"]["val"] == 1

    # Changes message: upsert row2, insert row3, remove row1
    chg_msg = {
        "type": "changes",
        "upsert": [
            {"id": "row2", "val": 20},
            {"id": "row3", "val": 3},
        ],
        "remove": ["row1", "row_nonexistent"],
    }
    u, d = bb.apply_changes(state, chg_msg)
    assert u == 2
    assert d == 1
    assert "row1" not in state
    assert state["row2"]["val"] == 20
    assert state["row3"]["val"] == 3


def test_bootstrap_and_renew_token():
    with patch.object(bb._session, "get") as mock_get:
        # 1. Successful bootstrap
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        bb._session.cookies.set("bb", "dummy_token")
        bb.bootstrap()
        assert bb._session.cookies.get("bb") == "dummy_token"

        # 2. Renew token 204
        mock_resp.status_code = 204
        bb.renew_token()

        # 3. Renew token 403 raises TokenExpired
        mock_resp.status_code = 403
        with pytest.raises(bb.TokenExpired):
            bb.renew_token()


def test_ensure_token():
    with patch.object(bb, "renew_token") as mock_renew, patch.object(bb, "bootstrap") as mock_boot:
        mock_renew.side_effect = bb.TokenExpired("expired")
        bb._ensure_token()
        mock_boot.assert_called_once()


def test_fetch_prices_and_json():
    with patch.object(bb, "_ensure_token"), patch.object(bb._session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"kickoff": "2026-11-02", "prices": []}
        mock_get.return_value = mock_resp

        data = bb.fetch_prices("match:2026-11-02-CHI-SEA")
        assert data == {"kickoff": "2026-11-02", "prices": []}
