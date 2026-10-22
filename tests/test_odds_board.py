"""ev.odds_board -- the per-book price matrix behind /ev/odds, its hook in run_sport, and the public API. Offline."""
from datetime import datetime, timedelta, timezone

import pytest

import mlb.runners.public_api as pa
from ev import daily_projections as dp
from ev import odds_board as ob
from tests.test_daily_projections import FAIR, NOW, START, book, event, nba_logs


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])
    ob._CACHE.clear()
    yield
    ob._CACHE.clear()


@pytest.fixture
def mem_storage(monkeypatch):
    import mlb_core.storage as st
    store: dict = {}
    monkeypatch.setattr(st, "write_bytes", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(st, "read_bytes", lambda key: store[key])
    return store


def test_every_book_price_is_kept_with_no_fair_value():
    b = ob.build_board("nba", [event([*FAIR, book("pinnacle", -105, -115)])], NOW, {"age_min": 4.0, "lines_source": ["parlayapi"]})
    assert b["count"] == 1 and b["status"] == "live" and b["validated"] is False
    row = b["rows"][0]
    assert row["player"] == "Jo Doe" and row["stat"] == "points" and row["line"] == 24.5
    assert {q["book"]: (q["over"], q["under"]) for q in row["books"]} == {
        "betmgm": (-110, -110), "caesars": (-110, -110), "draftkings": (-110, -110), "fanduel": (-110, -110),
        "pinnacle": (-105, -115)}
    assert "p_fair" not in row and "fair" not in row["books"][0]
    assert b["offshore_books"] == ["pinnacle"] and b["lines_age_minutes"] == 4.0 and b["lines_source"] == ["parlayapi"]
    assert b["games"][0]["away"] and b["games"][0]["game_key"] == row["game_key"]


def test_dfs_alt_started_and_single_book_lines_are_dropped():
    alt = book("draftkings", 300, -400, point=40.5, mkey="player_points_alternate")
    lone = book("draftkings", -110, -110, point=30.5, player="Solo Guy")
    started = event(FAIR, start=NOW - timedelta(minutes=5))
    b = ob.build_board("nba", [event([*FAIR, book("underdog", -112, -112), alt, lone]), started], NOW)
    assert b["count"] == 1 and b["summary"]["events_started"] == 1
    assert "underdog" not in {q["book"] for q in b["rows"][0]["books"]}
    assert all(r["player"] != "Solo Guy" for r in b["rows"])


def test_one_sided_yes_quote_keeps_under_null():
    yes = {"key": "bovada", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Yes", "description": "Jo Doe", "price": 120, "point": 24.5}]}]}
    b = ob.build_board("nba", [event([*FAIR, yes])], NOW)
    q = {x["book"]: x for x in b["rows"][0]["books"]}["bovada"]
    assert q["over"] == 120 and q["under"] is None


def test_rows_are_nearest_game_first_and_capped(monkeypatch):
    later = event(FAIR, start=START + timedelta(days=1))
    b = ob.build_board("nba", [later, event(FAIR)], NOW)
    assert [r["commence"] for r in b["rows"]] == sorted(r["commence"] for r in b["rows"])
    monkeypatch.setattr(ob, "MAX_ROWS", 1)
    capped = ob.build_board("nba", [later, event(FAIR)], NOW)
    assert capped["count"] == 1 and capped["summary"]["rows_cut"] == 1 and capped["rows"][0]["commence"] == b["rows"][0]["commence"]


def test_run_sport_writes_the_board_next_to_the_projections(mem_storage):
    dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={}, games=[], write=True, roster=None)
    assert "EVSweep/nba_props/odds_board_latest.json" in mem_storage
    dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={}, games=[], write=False, roster=None)


def test_public_odds_board_serves_filters_and_never_computes(monkeypatch):
    fut = datetime.now(timezone.utc) + timedelta(hours=5)
    board = ob.build_board("nba", [event(FAIR, start=fut)], datetime.now(timezone.utc))
    monkeypatch.setattr(ob, "read_board", lambda sp: board)
    out = pa.get_odds_board("NBA")
    assert out["count"] == 1 and out["stale"] is False and out["books"] == ["betmgm", "caesars", "draftkings", "fanduel"]
    assert pa.get_odds_board("nba", stat="rebounds")["rows"] == []
    day = dp.ct_day(board["rows"][0]["commence"])
    assert pa.get_odds_board("nba", day=day)["day"] == day and pa.get_odds_board("nba", day="2020-01-01")["rows"] == []
    monkeypatch.setattr(ob, "read_board", lambda sp: None)
    gone = pa.get_odds_board("nfl")
    assert gone["status"] == "unavailable" and gone["rows"] == []
    with pytest.raises(ValueError):
        pa.get_odds_board("mlb")
    with pytest.raises(ValueError):
        pa.get_odds_board("nba", day="10/17/2026")


def test_route_wires_params(monkeypatch):
    import main
    from unittest.mock import patch
    monkeypatch.setattr(main, "_auth_required", lambda req: None)
    c = main.app.test_client()
    with patch("main.get_odds_board", return_value={"sport": "nba", "rows": []}) as g:
        assert c.get("/api/public/odds/board?sport=nba&day=2026-10-21&stat=points&limit=50").status_code == 200
        g.assert_called_once_with("nba", day="2026-10-21", stat="points", limit=50)
    with patch("main.get_odds_board", side_effect=ValueError("bad")):
        assert c.get("/api/public/odds/board?sport=mlb").status_code == 400
