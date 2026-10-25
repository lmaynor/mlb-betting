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


# ------------------------------------------------------------------------------------- alt lines, curve, row priority
def alt_ladder(point, over, under, books=("draftkings", "fanduel"), player="Jo Doe"):
    return [book(b, over, under, point=point, player=player, mkey="player_points_alternate") for b in books]


def over_only(book_key, point, over, player="Jo Doe", mkey="player_points_alternate"):
    return {"key": book_key, "markets": [{"key": mkey, "outcomes": [
        {"name": "Over", "description": player, "price": over, "point": point}]}]}


def by_line(board, player="Jo Doe"):
    return {r["line"]: r for r in board["rows"] if r["player"] == player}


LADDER = [*FAIR, *alt_ladder(29.5, 160, -190), *alt_ladder(34.5, 300, -400)]


def test_modal_line_is_main_and_shifted_rungs_are_alt_with_a_falling_curve():
    rows = by_line(ob.build_board("nba", [event(LADDER)], NOW))
    assert set(rows) == {24.5, 29.5, 34.5}
    assert [rows[p]["is_alt"] for p in (24.5, 29.5, 34.5)] == [False, True, True]
    assert all(r["modal_point"] == 24.5 for r in rows.values())
    curve = [rows[p]["curve_fair_over"] for p in (24.5, 29.5, 34.5)]
    assert all(c is not None for c in curve)
    assert curve[0] > curve[1] > curve[2]
    assert 0.01 <= curve[2] and curve[0] <= 0.99 and curve[0] == pytest.approx(0.5, abs=0.01)


def test_a_one_point_shift_is_alt_but_a_half_point_is_not():
    shifted = [book(b, o, u, point=pt) for pt, o, u in ((25.0, -105, -115), (25.5, 105, -125)) for b in ("draftkings", "fanduel")]
    board = ob.build_board("nba", [event([*FAIR, *shifted])], NOW)
    rows = by_line(board)
    assert rows[25.0]["is_alt"] is False                 # within ALT_GAP of the 24.5 modal line, a main-market line
    assert rows[25.5]["is_alt"] is True                  # exactly 1.0 away


def test_single_book_alt_rung_is_kept_only_when_actionable_against_the_curve():
    great = over_only("draftkings", 39.5, 900)                        # curve ~0.14 -> 0.14 * 10 - 1 = +40%
    poor = over_only("draftkings", 44.5, 300, mkey="player_points_alternate")      # curve ~0.08 -> -68%
    rows = by_line(ob.build_board("nba", [event([*LADDER, great, poor])], NOW))
    assert 39.5 in rows and rows[39.5]["is_alt"] is True and len(rows[39.5]["books"]) == 1
    assert rows[39.5]["books"][0] == {"book": "draftkings", "over": 900, "under": None}
    assert 44.5 not in rows


def test_single_book_alt_rung_without_a_curve_is_dropped_even_at_a_huge_price():
    # a lone anchor on a count stat (points) has no tail model, so there is no curve and no edge to judge
    lone = over_only("draftkings", 39.5, 5000)
    rows = by_line(ob.build_board("nba", [event([*FAIR, lone])], NOW))
    assert set(rows) == {24.5} and rows[24.5]["curve_fair_over"] is None


def test_cross_book_near_arb_alt_rung_is_kept_with_one_book_per_side():
    # over +120 at one book and under -110 at another on the same alt rung: margin = 1 - (1/2.2 + 1/1.909) > -0.02
    a, b = over_only("draftkings", 39.5, 120), {"key": "fanduel", "markets": [{"key": "player_points_alternate", "outcomes": [
        {"name": "Under", "description": "Jo Doe", "price": -110, "point": 39.5}]}]}
    rows = by_line(ob.build_board("nba", [event([*FAIR, a, b])], NOW))
    assert 39.5 in rows and {q["book"] for q in rows[39.5]["books"]} == {"draftkings", "fanduel"}


def test_main_lines_are_never_evicted_by_alt_rungs_under_the_cap(monkeypatch):
    early = event([*FAIR, *alt_ladder(29.5, 160, -190), *alt_ladder(34.5, 300, -400), *alt_ladder(39.5, 500, -700)])
    later = event([book(b, -110, -110, player="Al Roe", point=20.5) for b in ("draftkings", "fanduel", "betmgm", "caesars")],
                  start=START + timedelta(days=1))
    monkeypatch.setattr(ob, "MAX_ROWS", 3)
    b = ob.build_board("nba", [early, later], NOW)
    mains = [r for r in b["rows"] if not r["is_alt"]]
    assert {r["player"] for r in mains} == {"Jo Doe", "Al Roe"}          # nearest-game-first alone would have kept 3 early rows
    assert b["count"] == 3 and b["summary"]["rows_cut"] == 2
    assert [r["commence"] for r in b["rows"]] == sorted(r["commence"] for r in b["rows"])


def test_fit_curve_is_monotone_clamped_and_does_not_extrapolate_far():
    f = ob.fit_curve([(10.5, 0.8, 3), (20.5, 0.55, 3), (30.5, 0.30, 3)], "rec_yds")
    vals = [f(x) for x in (5.5, 10.5, 15.5, 20.5, 30.5, 40.5)]
    assert vals == sorted(vals, reverse=True) and len(set(vals)) == len(vals)
    assert f(60.5) is None and f(-40.5) is None                         # more than one span past the end anchors
    noisy = ob.fit_curve([(10.5, 0.4, 2), (20.5, 0.6, 2)], "points")     # rises: repaired to a flat curve, no extrapolation
    assert noisy(15.5) == noisy(10.5) and noisy(30.5) is None
    assert 0.01 <= ob.fit_curve([(10.5, 0.99, 3), (11.5, 0.001, 3)], "points")(10.5) <= 0.99


def test_fit_curve_single_anchor_uses_the_normal_tail_for_yardage_only():
    f = ob.fit_curve([(64.5, 0.5, 4)], "rec_yds")
    assert f(64.5) == pytest.approx(0.5, abs=1e-4) and 0.01 <= f(79.5) < 0.5 and f(79.5) > f(89.5)
    assert f(300.5) is None                                              # past one sd from the anchor
    assert ob.fit_curve([(24.5, 0.5, 4)], "points") is None              # no tail model for a count stat
    assert ob.fit_curve([(64.5, 0.5, 2)], "rec_yds") is None             # too few books behind a lone anchor


def test_board_payload_carries_alt_fields_and_summary_counts():
    b = ob.build_board("nba", [event(LADDER)], NOW)
    assert {"is_alt", "modal_point", "curve_fair_over", "curve_n"} <= set(b["rows"][0])
    assert b["summary"]["alt_rows"] == 2 and b["summary"]["curve_rows"] == 3
