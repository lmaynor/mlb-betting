"""Tests for nba.odds.extract -- prop/game-line flattening + best-book selection.

Fixtures mirror The Odds API v4 response shapes (single-event /odds object for
props; /odds slate list for game lines)."""
import pytest

from nba.odds import extract

PROPS_EVENT = {
    "id": "evt1",
    "home_team": "Boston Celtics",
    "away_team": "New York Knicks",
    "commence_time": "2026-11-05T00:10:00Z",
    "bookmakers": [
        {"key": "draftkings", "markets": [
            {"key": "player_points", "outcomes": [
                {"description": "Jayson Tatum", "name": "Over", "price": -110, "point": 27.5},
                {"description": "Jayson Tatum", "name": "Under", "price": -110, "point": 27.5},
                {"description": "Jalen Brunson", "name": "Over", "price": 100, "point": 24.5},
                {"description": "Jalen Brunson", "name": "Under", "price": -120, "point": 24.5},
            ]},
        ]},
        {"key": "fanduel", "markets": [
            {"key": "player_points", "outcomes": [
                {"description": "Jayson Tatum", "name": "Over", "price": -105, "point": 27.5},
                {"description": "Jayson Tatum", "name": "Under", "price": -115, "point": 27.5},
            ]},
            {"key": "player_assists", "outcomes": [  # missing Under -> dropped
                {"description": "Jayson Tatum", "name": "Over", "price": -130, "point": 4.5},
            ]},
            {"key": "h2h", "outcomes": []},  # non-prop market -> ignored
        ]},
    ],
}


def test_flatten_player_props_basic():
    rows = extract.flatten_player_props(PROPS_EVENT)
    # DK: Tatum pts + Brunson pts (2) ; FD: Tatum pts (1). assists dropped (no Under).
    assert len(rows) == 3
    tatum_dk = [r for r in rows if r["player"] == "Jayson Tatum" and r["book"] == "draftkings"][0]
    assert tatum_dk["market"] == "points"
    assert tatum_dk["line"] == 27.5
    assert tatum_dk["over_odds"] == -110 and tatum_dk["under_odds"] == -110
    assert tatum_dk["event_date"] == "2026-11-05"
    assert all(r["market"] != "assists" for r in rows)  # incomplete side dropped


def test_best_book_props_picks_highest_each_side():
    rows = extract.flatten_player_props(PROPS_EVENT)
    best = extract.best_book_props(rows)
    tatum = [b for b in best if b["player"] == "Jayson Tatum"][0]
    # Over: DK -110 vs FD -105 -> -105 (FD) is the better (higher) price
    assert tatum["best_over"] == -105 and tatum["best_over_book"] == "fanduel"
    # Under: DK -110 vs FD -115 -> -110 (DK)
    assert tatum["best_under"] == -110 and tatum["best_under_book"] == "draftkings"
    assert tatum["n_books"] == 2
    # Brunson only on DK
    brunson = [b for b in best if b["player"] == "Jalen Brunson"][0]
    assert brunson["best_over"] == 100 and brunson["n_books"] == 1


def test_flatten_player_props_empty():
    assert extract.flatten_player_props(None) == []
    assert extract.flatten_player_props({}) == []


def test_flatten_game_lines():
    slate = [{
        "id": "evt1", "home_team": "Boston Celtics", "away_team": "New York Knicks",
        "commence_time": "2026-11-05T00:10:00Z",
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Boston Celtics", "price": -180},
                {"name": "New York Knicks", "price": 150},
            ]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 224.5},
                {"name": "Under", "price": -110, "point": 224.5},
            ]},
        ]}],
    }]
    rows = extract.flatten_game_lines(slate)
    assert len(rows) == 4
    h2h = [r for r in rows if r["market"] == "h2h" and r["outcome"] == "Boston Celtics"][0]
    assert h2h["price"] == -180 and h2h["point"] is None
    tot = [r for r in rows if r["market"] == "totals" and r["outcome"] == "Over"][0]
    assert tot["point"] == 224.5
    assert extract.flatten_game_lines([]) == []
