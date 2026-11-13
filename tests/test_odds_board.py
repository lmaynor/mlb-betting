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
    yes = {"key": "pointsbet", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Yes", "description": "Jo Doe", "price": 120, "point": 24.5}]}]}
    b = ob.build_board("nba", [event([*FAIR, yes])], NOW)
    q = {x["book"]: x for x in b["rows"][0]["books"]}["pointsbet"]
    assert q["over"] == 120 and q["under"] is None


def test_rogue_quote_deviating_from_peer_consensus_is_dropped():
    # 4 books quote +2800 to +3000, 1 rogue book quotes +10000 (e.g. First TD mapped to Anytime TD)
    books = [
        {"key": "bet365", "markets": [{"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Tylan Wallace", "price": 2800, "point": 0.5}]}]},
        {"key": "betmgm", "markets": [{"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Tylan Wallace", "price": 3000, "point": 0.5}]}]},
        {"key": "caesars", "markets": [{"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Tylan Wallace", "price": 2700, "point": 0.5}]}]},
        {"key": "fanatics", "markets": [{"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Tylan Wallace", "price": 3000, "point": 0.5}]}]},
        {"key": "fanduel", "markets": [{"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Tylan Wallace", "price": 10000, "point": 0.5}]}]},
    ]
    b = ob.build_board("nfl", [event(books, away="Kansas City Chiefs", home="Baltimore Ravens")], NOW)
    assert b["count"] == 1
    row = b["rows"][0]
    book_names = [q["book"] for q in row["books"]]
    assert "fanduel" not in book_names
    assert set(book_names) == {"bet365", "betmgm", "caesars", "fanatics"}



def _td_book(key, price):
    return {"key": key, "markets": [{"key": "player_anytime_td", "outcomes": [
        {"name": "Over", "description": "Tylan Wallace", "price": price, "point": 0.5}]}]}


def test_a_genuine_soft_price_survives_when_only_three_or_four_books_quote():
    # +450 against +250/+260 is a real soft-book price (decimal 5.5 vs ~3.55 = 1.55x) and +800 against three +250s is much softer
    # (9.0 vs 3.5 = 2.6x). Neither may be dropped: the old filter (> 1.8x the median with >= 3 books) ate the second.
    for prices in ((250, 260, 450), (250, 250, 250, 800)):
        books = [_td_book(f"book{i}", p) for i, p in enumerate(prices)]
        b = ob.build_board("nfl", [event(books, away="Kansas City Chiefs", home="Baltimore Ravens")], NOW)
        kept = {q["book"]: q["over"] for q in b["rows"][0]["books"]}
        assert kept == {f"book{i}": p for i, p in enumerate(prices)}, prices


def test_rogue_filter_needs_five_books_and_a_3x_payout_ratio():
    # five quoting books: +1800 (decimal 19) vs median 3.6 (+260) is > 3x -> dropped; +700 (8.0) is 2.2x -> kept
    base = [250, 260, 270, 250]
    books = [_td_book(f"book{i}", p) for i, p in enumerate(base + [700])]
    b = ob.build_board("nfl", [event(books, away="Kansas City Chiefs", home="Baltimore Ravens")], NOW)
    assert "book4" in {q["book"] for q in b["rows"][0]["books"]}
    books = [_td_book(f"book{i}", p) for i, p in enumerate(base + [1800])]
    b = ob.build_board("nfl", [event(books, away="Kansas City Chiefs", home="Baltimore Ravens")], NOW)
    assert "book4" not in {q["book"] for q in b["rows"][0]["books"]}


def test_zero_price_does_not_crash_the_rogue_filter():
    books = [_td_book(f"book{i}", p) for i, p in enumerate([250, 260, 270, 250, 0])]
    b = ob.build_board("nfl", [event(books, away="Kansas City Chiefs", home="Baltimore Ravens")], NOW)
    assert b["count"] == 1
    assert ob._safe_decimal(0) is None and ob._safe_decimal(None) is None and ob._safe_decimal(-110) == pytest.approx(1.9091, abs=1e-3)


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
        pa.get_odds_board("nhl2")
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
        assert c.get("/api/public/odds/board?sport=nhl2").status_code == 400


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
    assert rows[39.5]["books"][0] == {"book": "draftkings", "over": 900, "under": None, "ts": None}
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


def test_cap_keeps_groups_whole_and_skips_a_group_that_cannot_fit(monkeypatch):
    # Ladders B1 item 4 supersedes the old flat-cut guarantee ("mains never evicted by alts"): the cap is now
    # group-atomic, so a (game, player, stat) group is kept whole or skipped whole. Here the early group
    # (main + 3 alt rungs = 4 candidate rows) cannot fit a 3-row budget and is skipped entire, main included;
    # the later one-row group fits and is kept. Groups are walked in (min tier, commence) priority order, so in
    # production-sized budgets a group is only ever skipped once the budget is nearly exhausted.
    early = event([*FAIR, *alt_ladder(29.5, 160, -190), *alt_ladder(34.5, 300, -400), *alt_ladder(39.5, 500, -700)])
    later = event([book(b, -110, -110, player="Al Roe", point=20.5) for b in ("draftkings", "fanduel", "betmgm", "caesars")],
                  start=START + timedelta(days=1))
    monkeypatch.setattr(ob, "MAX_ROWS", 3)
    b = ob.build_board("nba", [early, later], NOW)
    mains = [r for r in b["rows"] if not r["is_alt"]]
    assert {r["player"] for r in mains} == {"Al Roe"}
    assert b["count"] == 1 and b["summary"]["rows_cut"] == 4
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


# ------------------------------------------------------------------------------------------------------------- MLB
MLB_FAIR = [book(b, -115, -105, point=5.5, player="Gerrit Cole", mkey="player_strikeouts")
            for b in ("draftkings", "fanduel", "betmgm")]


def mlb_event(books, eid="m1", start=START):
    return event(books, start=start, away="New York Yankees", home="Boston Red Sox") | {"id": eid}


def test_mlb_board_maps_markets_and_teams():
    yes = {"key": "caesars", "markets": [{"key": "player_home_runs", "outcomes": [
        {"name": "Yes", "description": "Aaron Judge", "price": 320, "point": 0.5}]}]}
    b = ob.build_board("mlb", [mlb_event([*MLB_FAIR, yes, book("fanduel", 300, -400, point=0.5, player="Aaron Judge", mkey="player_home_runs")])], NOW)
    rows = {(r["player"], r["stat"]): r for r in b["rows"]}
    k = rows[("Gerrit Cole", "strikeouts")]
    assert k["line"] == 5.5 and k["away"] == "NYY" and k["home"] == "BOS" and k["game_key"].startswith("mlb:")
    hr = rows[("Aaron Judge", "home_runs")]
    assert {q["book"]: q["over"] for q in hr["books"]} == {"caesars": 320, "fanduel": 300}
    assert b["stats"] == ["home_runs", "strikeouts"]


def test_load_mlb_events_takes_the_newest_snapshot_per_day(monkeypatch):
    import json
    import mlb_core.storage as st
    import ev.odds_board as obm
    files = {
        "OddsAccum/baseball_mlb/raw/2026-10-15/props_1500.json": json.dumps([mlb_event(MLB_FAIR, "old")]),
        "OddsAccum/baseball_mlb/raw/2026-10-15/props_1700.json": json.dumps([mlb_event(MLB_FAIR, "new")]),
        "OddsAccum/baseball_mlb/raw/2026-10-15/game_lines_1700.json": "[]",
    }
    monkeypatch.setattr(st, "list_keys", lambda prefix: [k for k in files if k.startswith(prefix)])
    monkeypatch.setattr(st, "read_bytes", lambda key: files[key].encode())
    monkeypatch.setattr(st, "stat", lambda key: {"mtime_utc": NOW - timedelta(minutes=20)})
    events, meta = obm.load_mlb_events(NOW)
    assert [e["id"] for e in events] == ["new"] and meta["age_min"] == 20.0
    monkeypatch.setattr(st, "list_keys", lambda prefix: [])
    assert obm.load_mlb_events(NOW)[0] is None


def test_run_mlb_writes_only_when_there_is_a_snapshot(monkeypatch, mem_storage):
    monkeypatch.setattr(ob, "load_mlb_events", lambda now: ([mlb_event(MLB_FAIR)], {"age_min": 5.0, "lines_source": ["parlayapi"]}))
    out = ob.run_mlb(NOW)
    assert out["status"] == "live" and "EVSweep/mlb_props/odds_board_latest.json" in mem_storage
    mem_storage.clear()
    monkeypatch.setattr(ob, "load_mlb_events", lambda now: (None, {"reason": "none"}))
    assert ob.run_mlb(NOW)["status"] == "unavailable" and not mem_storage


def test_public_odds_board_accepts_mlb(monkeypatch):
    fut = datetime.now(timezone.utc) + timedelta(hours=5)
    board = ob.build_board("mlb", [mlb_event(MLB_FAIR, start=fut)], datetime.now(timezone.utc))
    monkeypatch.setattr(ob, "read_board", lambda sp: board)
    out = pa.get_odds_board("MLB", stat="strikeouts")
    assert out["count"] == 1 and out["sport"] == "mlb"


def test_public_odds_board_old_board_with_past_games_is_idle_and_not_stale_only_in_the_mlb_offseason(monkeypatch):
    # Old board with games in the past (e.g. offseason MLB)
    past = datetime.now(timezone.utc) - timedelta(hours=25)
    board = {
        "sport": "mlb",
        "ts": past.isoformat(),
        "status": "live",
        "count": 1,
        "rows": [{"commence": past.isoformat(), "player": "Aaron Judge", "stat": "strikeouts", "books": []}],
        "games": [{"start": past.isoformat(), "game_key": "old"}],
        "stats": ["strikeouts"],
        "lines_source": ["parlayapi"],
        "lines_age_minutes": 1500.0,
    }
    monkeypatch.setattr(ob, "read_board", lambda sp: board)
    monkeypatch.setattr("mlb_core.config.is_mlb_offseason", lambda *a, **k: True)
    out = pa.get_odds_board("MLB")
    assert out["count"] == 0
    assert out["status"] == "idle"
    assert out["stale"] is False

    # the same old board in season (or any other sport) means the feed stopped: report it stale
    monkeypatch.setattr("mlb_core.config.is_mlb_offseason", lambda *a, **k: False)
    out = pa.get_odds_board("MLB")
    assert out["count"] == 0 and out["status"] == "idle" and out["stale"] is True
    nba = {**board, "sport": "nba"}
    monkeypatch.setattr(ob, "read_board", lambda sp: nba)
    monkeypatch.setattr("mlb_core.config.is_mlb_offseason", lambda *a, **k: True)
    assert pa.get_odds_board("NBA")["stale"] is True            # the MLB offseason says nothing about the NBA feed


def test_public_odds_board_fresh_empty_slate_is_not_stale(monkeypatch):
    now = datetime.now(timezone.utc)
    board = {"sport": "nba", "ts": (now - timedelta(minutes=10)).isoformat(), "status": "idle", "count": 0, "rows": [],
             "games": [], "stats": [], "lines_source": ["parlayapi"], "lines_age_minutes": 5.0}
    monkeypatch.setattr(ob, "read_board", lambda sp: board)
    out = pa.get_odds_board("NBA")
    assert out["count"] == 0 and out["status"] == "idle" and out["stale"] is False



# ---- junk offshore books are dropped at the source; pinnacle stays as the sharp reference (2026-11-02) --------------
JUNK = ("bovada", "betonline", "betonlineag", "mybookie", "betus", "matchbook", "betfair", "lowvig")


def test_junk_offshore_set_is_offshore_minus_pinnacle():
    from ev.props import JUNK_OFFSHORE_BOOKS, OFFSHORE_BOOKS
    assert isinstance(JUNK_OFFSHORE_BOOKS, frozenset)
    assert "pinnacle" in OFFSHORE_BOOKS and "pinnacle" not in JUNK_OFFSHORE_BOOKS
    assert JUNK_OFFSHORE_BOOKS == OFFSHORE_BOOKS - {"pinnacle"} and set(JUNK) <= JUNK_OFFSHORE_BOOKS


def test_junk_offshore_quotes_never_reach_a_props_row_but_pinnacle_does():
    junk = [book(j, -105, -115) for j in JUNK]
    b = ob.build_board("nba", [event([*FAIR, *junk, book("pinnacle", -105, -115)])], NOW)
    assert b["count"] == 1
    names = {q["book"] for q in b["rows"][0]["books"]}
    assert names == {"betmgm", "caesars", "draftkings", "fanduel", "pinnacle"}
    assert not names & set(JUNK) and not set(JUNK) & set(b["books"])
    assert b["offshore_books"] == ["pinnacle"]


def test_props_row_that_only_had_junk_plus_one_real_book_is_dropped():
    # bovada + betonline + one real book was a 3-book row before; without the junk it is a single-book row and vanishes
    only = [book("draftkings", -110, -110, point=31.5, player="Edge Guy"), book("bovada", -110, -110, point=31.5, player="Edge Guy"),
            book("betonline", -112, -108, point=31.5, player="Edge Guy")]
    b = ob.build_board("nba", [event([*FAIR, *only])], NOW)
    assert all(r["player"] != "Edge Guy" for r in b["rows"])
    # and a row quoted ONLY by junk books vanishes outright, with no offshore_books left over
    b2 = ob.build_board("nba", [event([book("bovada", -110, -110), book("betonline", -110, -110), book("mybookie", -110, -110)])], NOW)
    assert b2["count"] == 0 and b2["offshore_books"] == [] and b2["books"] == []


def test_junk_books_do_not_feed_the_curve_or_modal_line():
    # junk books quote a different line than the real ones: they must not create a modal line / row of their own
    real = [book(b, -110, -110, point=24.5) for b in ("draftkings", "fanduel")]
    junk = [book(j, -110, -110, point=26.5) for j in ("bovada", "betonline", "mybookie")]
    b = ob.build_board("nba", [event([*real, *junk])], NOW)
    assert [r["line"] for r in b["rows"]] == [24.5] and b["rows"][0]["modal_point"] == 24.5


# ---- NHL line annotation (2026-11-13) --------------------------------------------------------------------------------
def test_annotate_nhl_lines_hint_match():
    by_team = {("EDM", "connor mcdavid"): "EDM-F1"}
    by_norm = {"connor mcdavid": ("EDM", "EDM-F1")}
    rows = [{"player": "Connor McDavid", "player_norm": "connor mcdavid", "team": "EDM"}]
    cnt = ob._annotate_nhl_lines(rows, by_team, by_norm)
    assert cnt == 1
    assert rows[0]["line_id"] == "EDM-F1"
    assert rows[0]["team"] == "EDM"


def test_annotate_nhl_lines_unique_norm_backfills_team():
    by_team = {("EDM", "connor mcdavid"): "EDM-F1"}
    by_norm = {"connor mcdavid": ("EDM", "EDM-F1")}
    rows = [{"player": "Connor McDavid", "player_norm": "connor mcdavid", "team": None}]
    cnt = ob._annotate_nhl_lines(rows, by_team, by_norm)
    assert cnt == 1
    assert rows[0]["line_id"] == "EDM-F1"
    assert rows[0]["team"] == "EDM"


def test_annotate_nhl_lines_ambiguous_norm_untouched():
    by_team = {("CAR", "sebastian aho"): "CAR-F1", ("NYI", "sebastian aho"): "NYI-D3"}
    by_norm = {}  # ambiguous norm excluded from by_norm
    rows = [{"player": "Sebastian Aho", "player_norm": "sebastian aho", "team": None}]
    cnt = ob._annotate_nhl_lines(rows, by_team, by_norm)
    assert cnt == 0
    assert "line_id" not in rows[0]
    assert rows[0]["team"] is None


def test_annotate_nhl_lines_never_overwrites_team():
    by_team = {("EDM", "connor mcdavid"): "EDM-F1"}
    by_norm = {"connor mcdavid": ("EDM", "EDM-F1")}
    rows = [{"player": "Connor McDavid", "player_norm": "connor mcdavid", "team": "NYR"}]
    cnt = ob._annotate_nhl_lines(rows, by_team, by_norm)
    assert cnt == 0
    assert "line_id" not in rows[0]
    assert rows[0]["team"] == "NYR"


def test_load_lines_maps_caches_and_filters_ambiguous(monkeypatch):
    import json
    import mlb_core.storage as st
    ob._LINES_CACHE.update({"t": 0.0, "by_team": {}, "by_norm": {}})
    artifact = {
        "version": 1,
        "as_of": "2024-11-20",
        "teams": {
            "EDM": {
                "lines": [
                    {
                        "line_id": "EDM-F1",
                        "players": [{"player_id": 8478402, "name": "Connor McDavid", "player_norm": "connor mcdavid"}],
                    }
                ]
            },
            "CAR": {
                "lines": [
                    {
                        "line_id": "CAR-F1",
                        "players": [{"player_id": 8478427, "name": "Sebastian Aho", "player_norm": "sebastian aho"}],
                    }
                ]
            },
            "NYI": {
                "lines": [
                    {
                        "line_id": "NYI-D3",
                        "players": [{"player_id": 8480222, "name": "Sebastian Aho", "player_norm": "sebastian aho"}],
                    }
                ]
            },
        },
    }
    calls = []

    def fake_read(key):
        calls.append(key)
        return json.dumps(artifact).encode("utf-8")

    monkeypatch.setattr(st, "read_bytes", fake_read)
    by_team, by_norm = ob._load_lines_maps()
    assert len(calls) == 1
    assert calls[0] == ob.LINES_KEY
    assert by_team[("EDM", "connor mcdavid")] == "EDM-F1"
    assert by_team[("CAR", "sebastian aho")] == "CAR-F1"
    assert by_team[("NYI", "sebastian aho")] == "NYI-D3"
    assert by_norm["connor mcdavid"] == ("EDM", "EDM-F1")
    assert "sebastian aho" not in by_norm

    # second call within TTL hits cache
    by_team2, by_norm2 = ob._load_lines_maps()
    assert len(calls) == 1
    assert by_team2 is by_team and by_norm2 is by_norm


def test_load_lines_maps_storage_error_falls_back_empty_and_caches(monkeypatch):
    import mlb_core.storage as st
    ob._LINES_CACHE.update({"t": 0.0, "by_team": {}, "by_norm": {}})

    def fail_read(key):
        raise RuntimeError("GCS unavailable")

    monkeypatch.setattr(st, "read_bytes", fail_read)
    by_team, by_norm = ob._load_lines_maps()
    assert by_team == {} and by_norm == {}
    assert ob._LINES_CACHE["t"] > 0


def test_build_board_nhl_lines_annotation_and_summary_count(monkeypatch):
    ob._LINES_CACHE.update({"t": 0.0, "by_team": {}, "by_norm": {}})
    monkeypatch.setattr(ob, "_load_lines_maps", lambda: (
        {("EDM", "jo doe"): "EDM-F1"},
        {"jo doe": ("EDM", "EDM-F1")}
    ))
    b_nhl = ob.build_board("nhl", [event(FAIR, away="Edmonton Oilers", home="Calgary Flames")], NOW)
    assert b_nhl["summary"]["lines_rows"] == 1
    assert b_nhl["rows"][0]["line_id"] == "EDM-F1"

    b_nba = ob.build_board("nba", [event(FAIR)], NOW)
    assert b_nba["summary"]["lines_rows"] == 0
    assert "line_id" not in b_nba["rows"][0]


# ------------------------------------------------------------------------------------------------- Ladders B1 (items 1-8)
def test_quote_carries_market_last_update_ts():
    mkt = {"key": "player_points", "last_update": "2026-10-15T14:30:00Z", "outcomes": [
        {"name": "Over", "description": "Jo Doe", "price": -110, "point": 24.5},
        {"name": "Under", "description": "Jo Doe", "price": -110, "point": 24.5},
    ]}
    ev = event([{"key": "draftkings", "markets": [mkt]}, book("fanduel", -110, -110)])
    b = ob.build_board("nba", [ev], NOW)
    dk = next(x for x in b["rows"][0]["books"] if x["book"] == "draftkings")
    assert dk["ts"] == "2026-10-15T14:30:00Z"
    fd = next(x for x in b["rows"][0]["books"] if x["book"] == "fanduel")
    assert fd["ts"] is None


def test_placeholder_cap_flags_and_best_price_exclusion():
    b1 = book("draftkings", -110, -110)
    b2 = book("fanduel", -105, -115)
    b3 = book("betmgm", 200, -99900)
    b = ob.build_board("nba", [event([b1, b2, b3])], NOW)
    row = b["rows"][0]
    mgm = next(x for x in row["books"] if x["book"] == "betmgm")
    assert mgm.get("under_cap") is True
    assert "over_cap" not in mgm
    assert row["is_placeholder_cap"] is True
    assert row["best_under"] is not None
    assert row["best_under"]["book"] == "draftkings"
    assert row["best_under"]["american"] == -110


def test_rung_consensus_and_gate_data_numerical_sanity():
    import numpy as np
    from ev.props import _mad
    b1 = book("draftkings", -110, -110)
    b2 = book("fanduel", -105, -115)
    b3 = book("betmgm", -120, 100)
    b4 = book("caesars", 100, -120)
    b = ob.build_board("nba", [event([b1, b2, b3, b4])], NOW)
    row = b["rows"][0]
    fairs = [ob._two_sided_fair(bk) for bk in row["books"]]
    assert row["n_ref"] == 4
    assert row["n_fresh_ref"] == 4
    assert row["p_cons"] == round(float(np.median(fairs)), 4)
    assert row["disp"] == round(_mad(fairs), 4)
    assert 0.0 < row["rung_vig"] < 0.10


def test_best_over_leave_one_out_stats():
    b1 = book("draftkings", 150, -180)  # known best over (+150)
    b2 = book("fanduel", -110, -110)
    b3 = book("betmgm", -110, -110)
    b4 = book("caesars", -110, -110)
    b = ob.build_board("nba", [event([b1, b2, b3, b4])], NOW)
    row = b["rows"][0]
    bo = row["best_over"]
    assert bo["book"] == "draftkings"
    assert bo["american"] == 150
    assert bo["n_ref_loo"] == 3
    assert bo["p_cons_loo"] == 0.5
    assert bo["disp_loo"] == 0.0
    assert row["n_ref"] == 4


def test_lagging_book_excluded_from_fresh_consensus():
    import numpy as np
    def book_with_ts(key, over, under, ts, player="Jo Doe", point=24.5):
        b = book(key, over, under, player=player, point=point)
        b["markets"][0]["last_update"] = ts
        return b

    t_fresh = (NOW - timedelta(minutes=10)).isoformat()
    t_ok = (NOW - timedelta(minutes=30)).isoformat()
    t_stale = (NOW - timedelta(minutes=150)).isoformat()  # 140 min lag behind t_fresh (> 120 min)

    b1 = book_with_ts("draftkings", -110, -110, t_fresh)
    b2 = book_with_ts("fanduel", -105, -115, t_ok)
    b3 = book_with_ts("betmgm", 150, -180, t_stale)  # excluded from consensus
    b4 = book("caesars", -110, -110)  # no ts -> included

    b = ob.build_board("nba", [event([b1, b2, b3, b4])], NOW)
    row = b["rows"][0]
    assert row["n_ref"] == 3
    included_fairs = [ob._two_sided_fair(bk) for bk in row["books"] if bk["book"] in ("draftkings", "fanduel", "caesars")]
    assert row["p_cons"] == round(float(np.median(included_fairs)), 4)


def test_group_metadata_and_ladder_opens():
    b_24_1 = book("draftkings", -110, -110, point=24.5)
    b_24_2 = book("fanduel", -110, -110, point=24.5)
    b_24_3 = book("betmgm", -110, -110, point=24.5)
    b_29_1 = book("draftkings", 160, -190, point=29.5, mkey="player_points_alternate")
    b_29_2 = {"key": "caesars", "markets": [{"key": "player_points_alternate", "outcomes": [
        {"name": "Over", "description": "Jo Doe", "price": 170, "point": 29.5}
    ]}]}

    ev = event([b_24_1, b_24_2, b_24_3, b_29_1, b_29_2])
    b1 = ob.build_board("nba", [ev], NOW, prev_opens={})
    row = b1["rows"][0]
    assert row["grp_books_any"] == 4
    assert row["grp_books_2sided"] == 3

    gk, pn, st = row["game_key"], row["player_norm"], row["stat"]
    gkey = f"{gk}|{pn}|{st}"
    assert gkey in b1["ladder_opens"]
    assert b1["ladder_opens"][gkey]["modal"] == 24.5
    assert b1["ladder_opens"][gkey]["ts"] == NOW.isoformat()
    assert row["modal_open"] == 24.5
    assert row["modal_first_ts"] == NOW.isoformat()

    prev = {gkey: {"modal": 23.5, "ts": "2026-10-15T10:00:00+00:00"}}
    b2 = ob.build_board("nba", [ev], NOW, prev_opens=prev)
    row2 = b2["rows"][0]
    assert row2["modal_open"] == 23.5
    assert row2["modal_first_ts"] == "2026-10-15T10:00:00+00:00"
    assert b2["ladder_opens"][gkey]["modal"] == 23.5


def test_stale_opener_flag_on_book():
    def nfl_book_ts(key, over, under, ts, player="Patrick Mahomes", point=250.5):
        return {"key": key, "markets": [{
            "key": "player_pass_yds",
            "last_update": ts,
            "outcomes": [
                {"name": "Over", "description": player, "price": over, "point": point},
                {"name": "Under", "description": player, "price": under, "point": point},
            ]
        }]}

    first_ts = "2026-10-15T10:00:00+00:00"
    b_stale = nfl_book_ts("draftkings", -110, -110, "2026-10-15T10:03:00+00:00")
    b_fresh = nfl_book_ts("fanduel", -110, -110, "2026-10-15T11:00:00+00:00")
    b_nots = nfl_book_ts("betmgm", -110, -110, None)

    ev = event([b_stale, b_fresh, b_nots], away="Kansas City Chiefs", home="Baltimore Ravens")
    b_tmp = ob.build_board("nfl", [ev], NOW)
    row_tmp = b_tmp["rows"][0]
    gk, pn, st = row_tmp["game_key"], row_tmp["player_norm"], row_tmp["stat"]
    gkey = f"{gk}|{pn}|{st}"

    prev = {gkey: {"modal": 248.5, "ts": first_ts}}  # moved by 2.0 >= 1.5 (yardage)
    b = ob.build_board("nfl", [ev], NOW, prev_opens=prev)
    row = b["rows"][0]
    dk = next(x for x in row["books"] if x["book"] == "draftkings")
    fd = next(x for x in row["books"] if x["book"] == "fanduel")
    mgm = next(x for x in row["books"] if x["book"] == "betmgm")

    assert dk.get("stale_opener") is True
    assert "stale_opener" not in fd
    assert "stale_opener" not in mgm


def test_group_atomic_cap_preserves_whole_groups(monkeypatch):
    g1_1 = [book(b, -110, -110, point=24.5, player="Jo Doe") for b in ("draftkings", "fanduel")]
    g1_2 = [book(b, 160, -190, point=29.5, player="Jo Doe", mkey="player_points_alternate") for b in ("draftkings", "fanduel")]
    g2_1 = [book(b, -110, -110, point=20.5, player="Al Roe") for b in ("draftkings", "fanduel")]
    g2_2 = [book(b, 160, -190, point=25.5, player="Al Roe", mkey="player_points_alternate") for b in ("draftkings", "fanduel")]

    ev1 = event([*g1_1, *g1_2], start=START)
    ev2 = event([*g2_1, *g2_2], start=START + timedelta(days=1))

    monkeypatch.setattr(ob, "MAX_ROWS", 3)
    b = ob.build_board("nba", [ev1, ev2], NOW)

    assert b["count"] == 2
    assert b["summary"]["rows_cut"] == 2
    assert {r["player"] for r in b["rows"]} == {"Jo Doe"}
    assert {r["line"] for r in b["rows"]} == {24.5, 29.5}
    assert b["count"] <= 3

    # Smaller subsequent group fits in remaining budget
    g3 = [book(b, -110, -110, point=15.5, player="Bob Poe") for b in ("draftkings", "fanduel")]
    ev3 = event([*g3], start=START + timedelta(days=2))
    b2 = ob.build_board("nba", [ev1, ev2, ev3], NOW)
    assert {r["player"] for r in b2["rows"]} == {"Jo Doe", "Bob Poe"}
    assert b2["count"] == 3
    assert b2["summary"]["rows_cut"] == 2
