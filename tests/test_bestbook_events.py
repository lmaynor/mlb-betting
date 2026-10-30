"""
Unit tests for ev.bestbook_events and BestBook backup filler integration in ev.daily_projections.
Tests pure transforms, stat mappings, event synthesis, and feed unioning offline without network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from ev import bestbook_events as bbe
from ev import daily_projections as dp

START = datetime(2026, 11, 2, 1, 15, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_stat_from_row():
    # NBA
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "nbapts", "family": "nba:2026-10-30-CHA-WAS|pts|lamelo ball"}) == "points"
    assert bbe.stat_from_row("nba", {"player": "Trae Young", "section": "nbareb", "family": "nba:2026-10-30-CHA-WAS|reb|trae young"}) == "rebounds"
    assert bbe.stat_from_row("nba", {"player": "Trae Young", "section": "nbaast", "family": "nba:2026-10-30-CHA-WAS|ast|trae young"}) == "assists"
    assert bbe.stat_from_row("nba", {"player": "Grayson Allen", "section": "nba3pm", "family": "nba:2026-10-30-CHA-WAS|3pm|grayson allen"}) == "threes"
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "nbapra", "family": "nba:2026-10-30-CHA-WAS|pra|lamelo ball"}) == "pra"
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "nbadd", "family": "nba:2026-10-30-CHA-WAS|dd|lamelo ball"}) == "double_double"

    # Period filters: quarter and half props must be dropped
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "nbapts", "family": "nba:2026-10-30-CHA-WAS|pts@1q|lamelo ball"}) is None
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "nbapts:1h", "family": "nba:2026-10-30-CHA-WAS|pts|lamelo ball"}) is None
    assert bbe.stat_from_row("nba", {"player": "LaMelo Ball", "section": "h2h", "family": "nba:2026-10-30-CHA-WAS|h2h|pts|lamelo ball~anthony davis"}) is None

    # NHL
    assert bbe.stat_from_row("nhl", {"player": "Mitch Marner", "section": "goals", "family": "nhl:2026-10-30-NJD-VGK|goals|mitch marner"}) == "goals"
    assert bbe.stat_from_row("nhl", {"player": "Jack Hughes", "section": "points", "family": "nhl:2026-10-30-NJD-VGK|pts|jack hughes"}) == "points"
    assert bbe.stat_from_row("nhl", {"player": "Jack Hughes", "section": "shots", "family": "nhl:2026-10-30-NJD-VGK|shots|jack hughes"}) == "sog"

    # NFL
    assert bbe.stat_from_row("nfl", {"player": "Caleb Williams", "section": "passing", "family": "2026-11-02-CHI-SEA|passyds|caleb williams"}) == "pass_yds"
    assert bbe.stat_from_row("nfl", {"player": "D'Andre Swift", "section": "rushyds", "family": "2026-11-02-CHI-SEA|rushyds|dandre swift"}) == "rush_yds"
    assert bbe.stat_from_row("nfl", {"player": "DJ Moore", "section": "recyds", "family": "2026-11-02-CHI-SEA|recyds|dj moore"}) == "rec_yds"
    assert bbe.stat_from_row("nfl", {"player": "DJ Moore", "section": "td", "family": "2026-11-02-CHI-SEA|td|dj moore"}) == "anytime_td"

    # Missing player -> None
    assert bbe.stat_from_row("nba", {"player": None, "section": "nbapts"}) is None


def test_point_from_row():
    # Milestone ladder: "11+" on line 11 -> 10.5
    assert bbe.point_from_row(11, "11+", "points") == 10.5
    assert bbe.point_from_row("11", "11+", "points") == 10.5
    assert bbe.point_from_row(3, "3+", "rebounds") == 2.5

    # Standard half point line
    assert bbe.point_from_row(25.5, "25.5", "points") == 25.5

    # Anytime TD
    assert bbe.point_from_row(1, "1+", "anytime_td") == 0.5
    assert bbe.point_from_row(0.5, "0.5", "anytime_td") == 0.5


def test_parse_american():
    assert bbe.parse_american("-110") == -110
    assert bbe.parse_american("\u2212120") == -120
    assert bbe.parse_american("+150") == 150
    assert bbe.parse_american("-115.0") == -115
    assert bbe.parse_american(None) is None
    assert bbe.parse_american("") is None


def test_build_events_from_snapshots():
    slate_games = [
        {
            "key": "2026-11-02-CHI-SEA",
            "sport": "nfl",
            "away": "CHI",
            "home": "SEA",
            "start": "2026-11-02T01:15:00Z",
            "venues": ["dk", "fd"],
        }
    ]

    snapshots = {
        "2026-11-02-CHI-SEA": {
            "rows": [
                # Main line (most two-sided quotes): Caleb Williams 235.5 Pass Yds
                {
                    "player": "Caleb Williams",
                    "team": "CHI",
                    "section": "passing",
                    "family": "2026-11-02-CHI-SEA|passyds|caleb williams",
                    "line": 235.5,
                    "rung": "235.5",
                    "quotes": [
                        {"venue": "dk", "yesNative": "-115", "noNative": "-115"},
                        {"venue": "fd", "yesNative": "-110", "noNative": "-120"},
                        {"venue": "pp", "yesNative": "-110", "noNative": "-110"},  # DFS dropped
                    ],
                },
                # Alt line: Caleb Williams 250+ Pass Yds (one-sided)
                {
                    "player": "Caleb Williams",
                    "team": "CHI",
                    "section": "passing",
                    "family": "2026-11-02-CHI-SEA|passyds|caleb williams",
                    "line": 250,
                    "rung": "250+",
                    "quotes": [
                        {"venue": "dk", "yesNative": "+140", "noNative": None},
                    ],
                },
            ]
        }
    }

    events, counts = bbe.build_events_from_snapshots("nfl", slate_games, snapshots, NOW)
    assert len(events) == 1
    assert counts["events_priced"] == 1
    assert counts["books"] == ["draftkings", "fanduel"]

    ev = events[0]
    assert ev["id"] == "bb_2026-11-02-CHI-SEA"
    assert ev["source"] == "bestbook"
    assert ev["away_team"] == "CHI"
    assert ev["home_team"] == "SEA"

    dk_b = next(b for b in ev["bookmakers"] if b["key"] == "draftkings")
    mkt_keys = {m["key"] for m in dk_b["markets"]}
    assert "player_pass_yds" in mkt_keys
    assert "player_pass_yds_alternate" in mkt_keys

    main_mkt = next(m for m in dk_b["markets"] if m["key"] == "player_pass_yds")
    assert len(main_mkt["outcomes"]) == 2
    over_out = next(o for o in main_mkt["outcomes"] if o["name"] == "Over")
    under_out = next(o for o in main_mkt["outcomes"] if o["name"] == "Under")
    assert over_out["point"] == 235.5
    assert over_out["price"] == -115
    assert under_out["point"] == 235.5
    assert under_out["price"] == -115

    alt_mkt = next(m for m in dk_b["markets"] if m["key"] == "player_pass_yds_alternate")
    assert len(alt_mkt["outcomes"]) == 1
    alt_out = alt_mkt["outcomes"][0]
    assert alt_out["name"] == "Over"
    assert alt_out["point"] == 249.5
    assert alt_out["price"] == 140


def test_publishable():
    assert bbe.publishable({"counts": {"events_priced": 1, "events_upcoming": 5}}) == (True, "priced")
    assert bbe.publishable({"counts": {"events_priced": 0, "events_upcoming": 0}}) == (True, "no upcoming games")
    assert bbe.publishable({"counts": {"events_priced": 0, "events_upcoming": 5}}) == (False, "games listed but no prices found")


def test_read_and_write_latest():
    payload = {
        "sport": "nfl",
        "fetched_at": NOW.isoformat(),
        "events": [{"id": "bb_1", "commence_time": START.isoformat()}],
        "counts": {"quotes": 10, "books": ["draftkings"]},
    }
    with patch("mlb_core.storage.write_bytes") as mock_write, patch("mlb_core.storage.read_bytes") as mock_read:
        bbe.write_latest(payload)
        mock_write.assert_called_once()

        mock_read.return_value = json.dumps(payload).encode()
        events, meta = bbe.read_latest("nfl", NOW + timedelta(minutes=10), max_age_min=60.0)
        assert len(events) == 1
        assert meta["age_min"] == 10.0
        assert meta["books"] == ["draftkings"]

        # Expired max age
        events, meta = bbe.read_latest("nfl", NOW + timedelta(minutes=120), max_age_min=60.0)
        assert events is None
        assert "old" in meta["reason"]


def test_union_events_with_bestbook_fallback_and_merge():
    # 1. BestBook serves as sole fallback when ParlayAPI and BettingPros are missing
    bb_ev = {
        "id": "bb_game1",
        "source": "bestbook",
        "commence_time": "2026-11-02T01:15:00Z",
        "away_team": "CHI",
        "home_team": "SEA",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Caleb Williams (CHI)", "price": -115, "point": 235.5},
                            {"name": "Under", "description": "Caleb Williams (CHI)", "price": -115, "point": 235.5},
                        ],
                    }
                ],
            }
        ],
    }

    parlay_empty = (None, {"reason": "no ParlayAPI lines"})
    bp_empty = (None, {"reason": "no BettingPros lines"})
    bb_feed = ([bb_ev], {"age_min": 5.0})

    events, meta = dp.union_events("nfl", parlay_empty, bp_empty, bb_feed)
    assert len(events) == 1
    assert events[0]["id"] == "bb_game1"
    assert meta["lines_source"] == ["bestbook"]
    assert meta["age_min"] == 5.0

    # 2. BestBook merges into ParlayAPI and BettingPros
    parlay_ev = {
        "id": "p1",
        "source": "parlayapi",
        "commence_time": "2026-11-02T01:15:00Z",
        "away_team": "Chicago Bears",
        "home_team": "Seattle Seahawks",
        "bookmakers": [
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "player_rush_yds",
                        "outcomes": [
                            {"name": "Over", "description": "D'Andre Swift (CHI)", "price": -110, "point": 65.5},
                        ],
                    }
                ],
            }
        ],
    }
    parlay_feed = ([parlay_ev], {"age_min": 45.0})
    bp_feed = ([], {"age_min": 30.0})

    events, meta = dp.union_events("nfl", parlay_feed, bp_feed, bb_feed)
    assert len(events) == 1
    ev = events[0]
    # Game was matched on (CHI, SEA, date)
    assert len(ev["bookmakers"]) == 2
    b_keys = {b["key"] for b in ev["bookmakers"]}
    assert b_keys == {"fanduel", "draftkings"}
    assert "bestbook" in meta["lines_source"]
