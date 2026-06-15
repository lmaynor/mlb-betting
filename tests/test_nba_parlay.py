"""Tests for nba.odds.parlay_extract + accumulator flow (ParlayAPI shape).

ParlayAPI prop outcomes: name='Over <player>' / 'Under <player>', description=player,
american price (oddsFormat=american), point=line. Game-line shape matches The Odds
API so extract.flatten_game_lines / best_book_props are reused.
"""
import pytest

from nba.odds import extract, parlay_extract

# Real ParlayAPI single-event prop shape (trimmed, oddsFormat=american)
EVENT = {
    "id": "e1", "sport_key": "basketball_nba",
    "home_team": "Boston Celtics", "away_team": "New York Knicks",
    "commence_time": "2026-11-05T00:10:00Z",
    "bookmakers": [
        {"key": "pinnacle", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over Jayson Tatum", "description": "Jayson Tatum", "price": -112, "point": 27.5},
                {"name": "Under Jayson Tatum", "description": "Jayson Tatum", "price": -108, "point": 27.5},
            ]},
        ]},
        {"key": "draftkings", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over Jayson Tatum", "description": "Jayson Tatum", "price": -120, "point": 27.5},
                {"name": "Under Jayson Tatum", "description": "Jayson Tatum", "price": 100, "point": 27.5},
            ]},
            {"key": "player_assists", "outcomes": [  # missing Under -> dropped
                {"name": "Over Jayson Tatum", "description": "Jayson Tatum", "price": -130, "point": 4.5},
            ]},
            {"key": "h2h", "outcomes": []},  # non-prop -> ignored
        ]},
    ],
}


def test_flatten_parlay_props():
    rows = parlay_extract.flatten_parlay_props(EVENT, "basketball_nba")
    assert len(rows) == 2  # Tatum points on pinnacle + dk; assists dropped (no Under)
    r = [x for x in rows if x["book"] == "pinnacle"][0]
    assert r["market"] == "points"
    assert r["player"] == "Jayson Tatum"
    assert r["line"] == 27.5
    assert r["over_odds"] == -112 and r["under_odds"] == -108
    assert r["event_date"] == "2026-11-05"
    assert all(x["market"] != "assists" for x in rows)


def test_best_book_reused_on_parlay_rows():
    rows = parlay_extract.flatten_parlay_props(EVENT, "basketball_nba")
    best = extract.best_book_props(rows)          # reuse the shared collapser
    t = [b for b in best if b["player"] == "Jayson Tatum"][0]
    # Over: pinnacle -112 vs dk -120 -> -112 (pinnacle) is the better price
    assert t["best_over"] == -112 and t["best_over_book"] == "pinnacle"
    # Under: pinnacle -108 vs dk +100 -> +100 (dk)
    assert t["best_under"] == 100 and t["best_under_book"] == "draftkings"
    assert t["n_books"] == 2


def test_flatten_parlay_props_empty():
    assert parlay_extract.flatten_parlay_props(None) == []
    assert parlay_extract.flatten_parlay_props({}) == []


def test_accumulate_props_flow(monkeypatch):
    """End-to-end accumulate_props with a fake client + stubbed storage."""
    from nba.odds import accumulator

    class FakeClient:
        credits_remaining = "950"
        def get_slate(self, sport, markets=None):
            return [{"id": "e1"}]
        def get_event_props(self, sport, event_id, markets):
            return EVENT

    written = {}
    monkeypatch.setattr(accumulator.storage, "write_bytes",
                        lambda data, key: written.__setitem__(key, data))
    monkeypatch.setattr(accumulator.storage, "write_csv",
                        lambda df, key: written.__setitem__(key, df))

    res = accumulator.accumulate_props("basketball_nba", client=FakeClient())
    assert res["events_priced"] == 1
    assert res["best_book_rows"] == 1
    assert res["prop_rows"] == 2
    # raw + csv + latest all written
    assert any(k.endswith("latest.json") for k in written)
    assert any("/raw/" in k and k.endswith(".json") for k in written)
    assert any(k.endswith(".csv") for k in written)
