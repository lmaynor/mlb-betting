"""
Tests for ev.game_odds_board (the NBA / NHL per-book game-line price matrix) and its public route.
Events use the real ParlayAPI shape: bookmakers[].markets[] keyed h2h / spreads / totals with team-name outcomes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import mlb.runners.public_api as api
from ev import game_odds_board as gob

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=5)).isoformat().replace("+00:00", "Z")       # 23:00 UTC = 18:00 ET, 2026-11-02
VALID_KEY = "test-key"


def h2h(away, home, a, h):
    return {"key": "h2h", "outcomes": [{"name": away, "price": a}, {"name": home, "price": h}]}


def spreads(away, home, home_line, a, h):
    return {"key": "spreads", "outcomes": [{"name": away, "price": a, "point": -home_line},
                                           {"name": home, "price": h, "point": home_line}]}


def totals(line, o, u):
    return {"key": "totals", "outcomes": [{"name": "Over", "price": o, "point": line},
                                          {"name": "Under", "price": u, "point": line}]}


def bm(key, *markets):
    return {"key": key, "title": key, "markets": list(markets)}


def event(away, home, books, start=START, eid="e1"):
    return {"id": eid, "away_team": away, "home_team": home, "commence_time": start, "bookmakers": books}


def nba_event(**kw):
    A, H = "Boston Celtics", "Miami Heat"
    return event(A, H, [
        bm("fanduel", h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110), totals(220.5, -110, -110)),
        bm("draftkings", h2h(A, H, -255, 215), spreads(A, H, 6.5, -108, -112), totals(221.5, -110, -110)),
        bm("betmgm", h2h(A, H, -245, 205), spreads(A, H, 7.0, -115, -105), totals(220.5, -105, -115)),
        bm("pinnacle", h2h(A, H, -260, 220), spreads(A, H, 6.5, -102, -118), totals(220.5, -108, -108)),
        bm("prizepicks", h2h(A, H, -300, 250)),
    ], **kw)


def rows_of(board, market, line="any"):
    return [r for r in board["rows"] if r["market"] == market and (line == "any" or r["line"] == line)]


def test_orientation_lines_and_sides():
    b = gob.build_game_board("nba", [nba_event()], NOW, {"age_min": 7.0, "snapshot_ts": "2026-11-02T17:53:00+00:00"})
    assert b["validated"] is False and b["status"] == "live" and b["lines_source"] == "oddsaccum_game_lines"
    assert b["as_of"] == "2026-11-02T17:53:00+00:00" and b["lines_age_minutes"] == 7.0
    ml = rows_of(b, "ml")
    assert len(ml) == 1 and ml[0]["line"] is None and ml[0]["key"] == "nba:2026-11-02:BOS@MIA|ml|ml"
    fd = {x["book"]: x for x in ml[0]["books"]}["fanduel"]
    assert fd == {"book": "fanduel", "a": -250, "b": 210}                       # a = away (BOS), b = home (MIA)
    # Miami (home) +6.5 => the row's line is the HOME line +6.5; Boston (away) is -6.5 and is side a
    sp = rows_of(b, "spread", 6.5)[0]
    assert {x["book"]: (x["a"], x["b"]) for x in sp["books"]} == {
        "draftkings": (-108, -112), "fanduel": (-110, -110), "pinnacle": (-102, -118)}
    tot = rows_of(b, "total", 220.5)[0]
    assert {x["book"]: (x["a"], x["b"]) for x in tot["books"]}["betmgm"] == (-105, -115)   # a = over, b = under


def test_one_row_per_game_market_line_and_two_book_rule():
    b = gob.build_game_board("nba", [nba_event()], NOW)
    spread_lines = sorted(r["line"] for r in rows_of(b, "spread"))
    assert spread_lines == [6.5]                      # betmgm's 7.0 is a lone book: not a comparison, dropped
    assert sorted(r["line"] for r in rows_of(b, "total")) == [220.5]    # draftkings 221.5 alone, dropped
    # a book with a different line joins when a second book quotes it
    A, H = "Boston Celtics", "Miami Heat"
    ev = nba_event()
    ev["bookmakers"].append(bm("caesars", spreads(A, H, 7.0, -112, -108)))
    b2 = gob.build_game_board("nba", [ev], NOW)
    assert sorted(r["line"] for r in rows_of(b2, "spread")) == [6.5, 7.0]
    # sorted by abs distance from the modal (6.5) line
    assert [r["line"] for r in rows_of(b2, "spread")] == [6.5, 7.0]


def test_dfs_dropped_and_none_price_skipped():
    A, H = "Boston Celtics", "Miami Heat"
    ev = event(A, H, [bm("prizepicks", h2h(A, H, -300, 250)), bm("underdog", h2h(A, H, -300, 250)),
                      bm("fanduel", h2h(A, H, -250, 210)),
                      bm("betmgm", {"key": "h2h", "outcomes": [{"name": A, "price": None}, {"name": H, "price": 0}]})])
    b = gob.build_game_board("nba", [ev], NOW)
    assert b["rows"] == [] and b["status"] == "awaiting_lines"                   # fanduel alone: DFS dropped, betmgm unpriced
    ev["bookmakers"].append(bm("caesars", {"key": "h2h", "outcomes": [{"name": A, "price": -240}, {"name": H, "price": None}]}))
    b = gob.build_game_board("nba", [ev], NOW)                                  # a one-sided quote still counts as a book
    row = b["rows"][0]
    assert {x["book"]: (x["a"], x["b"]) for x in row["books"]} == {"caesars": (-240, None), "fanduel": (-250, 210)}
    assert "prizepicks" not in b["books"] and "underdog" not in b["books"]


def test_outlier_rule_needs_five_books():
    A, H = "Boston Celtics", "Miami Heat"
    base = [("fanduel", 200), ("draftkings", 205), ("betmgm", 195)]
    # 3 + 1 rogue = 4 books: the median is too fragile, nothing is filtered
    b4 = gob.build_game_board("nba", [event(A, H, [bm(k, h2h(A, H, -240, p)) for k, p in base + [("caesars", 2000)]])], NOW)
    assert {x["book"]: x["b"] for x in b4["rows"][0]["books"]}["caesars"] == 2000
    # 5 books: the 2000 (decimal 21 vs median ~3.0) is dropped from that side only
    b5 = gob.build_game_board("nba", [event(A, H, [bm(k, h2h(A, H, -240, p)) for k, p in base + [("caesars", 2000), ("bet365", 200)]])], NOW)
    c = {x["book"]: x for x in b5["rows"][0]["books"]}["caesars"]
    assert c["b"] is None and c["a"] == -240


def test_started_game_dropped_at_build_and_at_read(monkeypatch):
    past = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    b = gob.build_game_board("nba", [nba_event(start=past)], NOW)
    assert b["rows"] == [] and b["summary"]["events_started"] == 1
    # read time: a stored board whose game has since started
    built = gob.build_game_board("nba", [nba_event()], NOW)
    _serve(monkeypatch, "nba", built)
    later = NOW + timedelta(hours=6)
    with patch.object(api, "_datetime", wraps=datetime) as dt:
        dt.now.return_value = later
        res = api.get_game_odds_board("nba")
    assert res["rows"] == [] and res["count"] == 0 and res["status"] == "awaiting_lines"


def test_team_codes_match_engines_phx_and_lak():
    from nba.teams import team_code
    from nhl.config import team_abbrev
    suns = event("Phoenix Suns", "New Orleans Pelicans", [bm("fanduel", h2h("Phoenix Suns", "New Orleans Pelicans", -120, 100)),
                                                         bm("draftkings", h2h("Phoenix Suns", "New Orleans Pelicans", -118, -102))])
    b = gob.build_game_board("nba", [suns], NOW)
    assert b["games"][0]["game_key"] == "nba:2026-11-02:PHX@NOP" and team_code("Phoenix Suns") == "PHX"
    kings = event("Los Angeles Kings", "Tampa Bay Lightning", [
        bm("fanduel", h2h("Los Angeles Kings", "Tampa Bay Lightning", 120, -140),
           spreads("Los Angeles Kings", "Tampa Bay Lightning", -1.5, 230, -280)),
        bm("betmgm", h2h("Los Angeles Kings", "Tampa Bay Lightning", 125, -145),
           spreads("Los Angeles Kings", "Tampa Bay Lightning", -1.5, 225, -270))])
    b = gob.build_game_board("nhl", [kings], NOW)
    g = b["games"][0]
    assert g["game_key"] == "nhl:2026-11-02:LAK@TBL" and (g["away"], g["home"]) == (team_abbrev("Los Angeles Kings"), "TBL")
    assert g["n_rows"] == 2
    sp = rows_of(b, "spread")[0]
    assert sp["line"] == -1.5                          # home (TBL) -1.5 is the HOME line
    assert {x["book"]: (x["a"], x["b"]) for x in sp["books"]}["fanduel"] == (230, -280)


def test_game_key_matches_daily_projections():
    from ev import daily_projections as dp
    start = datetime(2026, 11, 3, 1, 30, tzinfo=timezone.utc)                    # 20:30 ET Nov 2
    assert gob._game_key("nba", start, "BOS", "MIA") == dp.game_key_of("nba", start, "BOS", "MIA")


# ------------------------------------------------------------------------------------------------ public api
def _serve(monkeypatch, sport, payload):
    import mlb_core.storage as st
    api._GAME_ODDS_CACHE.clear()
    key = gob.OUT_KEY.format(sport=sport)
    files = {key: json.dumps(payload).encode()} if payload is not None else {}
    monkeypatch.setattr(st, "exists", lambda k, *a, **kw: k in files)
    monkeypatch.setattr(st, "read_bytes", lambda k, *a, **kw: files[k])
    return files


def test_api_filters_validation_and_contract(monkeypatch):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    built = gob.build_game_board("nba", [nba_event(start=start)], now, {"snapshot_ts": now.isoformat(), "age_min": 1.0})
    _serve(monkeypatch, "nba", built)
    res = api.get_game_odds_board("nba")
    assert res["status"] == "live" and res["stale"] is False and res["validated"] is False
    assert res["lines_source"] == "oddsaccum_game_lines" and res["count"] == len(res["rows"]) == built["count"]
    assert res["offshore_books"] == ["pinnacle"] and "prizepicks" not in res["books"]
    assert res["games"][0]["n_rows"] == res["count"]
    assert {r["market"] for r in api.get_game_odds_board("nba", market="spread")["rows"]} == {"spread"}
    assert {r["market"] for r in api.get_game_odds_board("nba", market="ML")["rows"]} == {"ml"}
    assert api.get_game_odds_board("nba", limit=1)["rows"].__len__() == 1
    assert api.get_game_odds_board("nba", limit=1)["count"] == built["count"]
    et_day = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(gob._ET).date().isoformat()
    assert api.get_game_odds_board("nba", day=et_day)["count"] == built["count"]
    assert api.get_game_odds_board("nba", day="2001-01-01")["rows"] == []
    for bad in (dict(sport="mlb"), dict(sport="nba", market="props"), dict(sport="nba", day="11/02/2026"), dict(sport="nba", day="2026-02-30")):
        with pytest.raises(ValueError):
            api.get_game_odds_board(**bad)


def test_api_stale_and_unavailable(monkeypatch):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(minutes=300)).isoformat()   # > STALE_MINUTES (270), < odds_feed.MAX_AGE_MIN (360)
    built = gob.build_game_board("nhl", [event("Los Angeles Kings", "Tampa Bay Lightning", [
        bm("fanduel", h2h("Los Angeles Kings", "Tampa Bay Lightning", 120, -140)),
        bm("betmgm", h2h("Los Angeles Kings", "Tampa Bay Lightning", 125, -145))], start=start)], now, {"snapshot_ts": old})
    _serve(monkeypatch, "nhl", built)
    res = api.get_game_odds_board("nhl")
    assert res["status"] == "stale" and res["stale"] is True and res["age_minutes"] > gob.STALE_MINUTES and res["count"] == 1
    _serve(monkeypatch, "nhl", None)
    res = api.get_game_odds_board("nhl")
    assert res["status"] == "unavailable" and res["rows"] == [] and res["as_of"] is None and res["age_minutes"] is None
    assert res["stale"] is True and res["lines_source"] == "oddsaccum_game_lines" and res["validated"] is False


def test_public_game_odds_route():
    mock_engine = MagicMock()
    with patch("main._get_engine", return_value=mock_engine):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = VALID_KEY
        with m.app.test_client() as c:
            assert c.get("/api/public/games/odds?sport=nba").status_code == 401
            assert c.options("/api/public/games/odds").status_code == 204
            hdr = {"X-API-Key": VALID_KEY}
            ret = {"sport": "nba", "status": "live", "count": 0, "rows": []}
            with patch("main.get_game_odds_board", return_value=ret) as g:
                r = c.get("/api/public/games/odds?sport=nba&day=2026-11-02&market=ml&limit=5", headers=hdr)
                assert r.status_code == 200 and r.get_json()["status"] == "live"
                g.assert_called_once_with("nba", day="2026-11-02", market="ml", limit=5)
            with patch("main.get_game_odds_board", side_effect=ValueError("unknown game odds sport 'mlb'")):
                assert c.get("/api/public/games/odds?sport=mlb", headers=hdr).status_code == 400
            assert c.get("/api/public/games/odds?sport=nba&limit=abc", headers=hdr).status_code == 400
            assert c.get("/api/public/games/odds?sport=nba&market=props", headers=hdr).status_code == 400
            assert c.get("/api/public/games/odds?sport=nba&day=nope", headers=hdr).status_code == 400


# --------------------------------------------------------------------------------------------- run / persistence
@pytest.fixture
def mem(monkeypatch):
    import mlb_core.storage as st
    blobs: dict = {}
    monkeypatch.setattr(st, "write_bytes", lambda data, key, *a, **k: blobs.__setitem__(key, bytes(data)))
    monkeypatch.setattr(st, "read_bytes", lambda key, *a, **k: blobs[key])
    monkeypatch.setattr(st, "exists", lambda key, *a, **k: key in blobs)
    return blobs


def _bank(mem, sport_key, events, ts):
    ptr = f"OddsAccum/{sport_key}/latest_game_lines.json"
    raw = f"OddsAccum/{sport_key}/raw/2026-11-02/game_lines_1800.json"
    mem[ptr] = json.dumps({"sport": sport_key, "kind": "game_lines", "date": "2026-11-02", "hhmm": "1800",
                           "events": len(events), "timestamp": ts.isoformat()}).encode()
    mem[raw] = json.dumps(events).encode()


def test_run_writes_then_keeps_previous_good_file_on_failure(mem):
    out = gob.OUT_KEY.format(sport="nba")
    _bank(mem, "basketball_nba", [nba_event()], NOW - timedelta(minutes=10))
    good = gob.run("nba", NOW)
    assert good["status"] == "live" and good["count"] > 0 and out in mem
    assert good["as_of"] == (NOW - timedelta(minutes=10)).isoformat() and good["lines_age_minutes"] == 10.0
    before = mem[out]
    # the snapshot goes stale (> MAX_AGE_MIN): stale payload, rows [], reason; the file is untouched
    later = NOW + timedelta(hours=8)
    bad = gob.run("nba", later)
    assert bad["status"] == "stale" and bad["rows"] == [] and "refusing stale" in bad["reason"] and mem[out] == before
    # pointer missing entirely: unavailable, file untouched
    del mem["OddsAccum/basketball_nba/latest_game_lines.json"]
    gone = gob.run("nba", later)
    assert gone["status"] == "unavailable" and gone["rows"] == [] and gone["reason"] and mem[out] == before
    # write_game_board refuses failure payloads directly too
    assert gob.write_game_board(bad, "nba") is False and mem[out] == before
    with pytest.raises(ValueError):
        gob.run("mlb", NOW)


def test_write_failure_never_raises(monkeypatch):
    import mlb_core.storage as st

    def boom(*a, **k):
        raise OSError("gcs down")
    monkeypatch.setattr(st, "write_bytes", boom)
    assert gob.write_game_board({"status": "live", "rows": []}, "nba") is False


def test_sample_row_json():
    b = gob.build_game_board("nba", [nba_event()], NOW)
    row = rows_of(b, "total", 220.5)[0]
    assert set(row) == {"key", "sport", "game_key", "away", "home", "commence", "market", "line", "books"}


def test_swapped_sides_book_is_dropped_but_a_merely_different_price_is_not():
    """Real case 2026-11-01: betmgm quoted the home side where every other book had the away side (a fake ~28% arb)."""
    A, H = "Utah Mammoth", "Philadelphia Flyers"
    peers = [("bet365", -180, 150), ("bovada", -180, 155), ("caesars", -179, 150), ("draftkings", -185, 154), ("fanduel", -182, 150)]
    books = [bm(k, h2h(A, H, a, b)) for k, a, b in peers]
    books.append(bm("betmgm", h2h(A, H, 150, -185)))            # flipped
    books.append(bm("hardrock", h2h(A, H, -150, 125)))          # a genuinely softer price, same favourite
    b = gob.build_game_board("nhl", [event(A, H, books)], NOW)
    got = {x["book"]: x for x in b["rows"][0]["books"]}
    assert "betmgm" not in got and b["summary"]["flipped_quotes_dropped"] == 1            # both sides gone, the row keeps its other books
    assert got["hardrock"]["a"] == -150 and got["fanduel"]["a"] == -182


def test_flip_guard_needs_four_two_sided_books():
    A, H = "Utah Mammoth", "Philadelphia Flyers"
    b = gob.build_game_board("nhl", [event(A, H, [bm("bet365", h2h(A, H, -180, 150)), bm("fanduel", h2h(A, H, -182, 150)),
                                                  bm("betmgm", h2h(A, H, 150, -185))])], NOW)
    assert {x["book"]: x for x in b["rows"][0]["books"]}["betmgm"]["a"] == 150          # only 3 books: not enough to call it


# ---- junk offshore books are dropped at the source; pinnacle stays as the sharp reference (2026-11-02) --------------
def test_junk_offshore_books_dropped_pinnacle_kept_in_game_board():
    A, H = "Boston Celtics", "Miami Heat"
    junk = ("bovada", "betonline", "mybookie", "betus", "matchbook", "betfair")
    ev = event(A, H, [
        bm("fanduel", h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110), totals(220.5, -110, -110)),
        bm("draftkings", h2h(A, H, -255, 215), spreads(A, H, 6.5, -108, -112), totals(220.5, -110, -110)),
        bm("pinnacle", h2h(A, H, -260, 220), spreads(A, H, 6.5, -102, -118), totals(220.5, -108, -108)),
        *[bm(j, h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110), totals(220.5, -110, -110)) for j in junk],
    ])
    b = gob.build_game_board("nba", [ev], NOW)
    assert b["status"] == "live"
    assert b["books"] == ["draftkings", "fanduel", "pinnacle"] and b["offshore_books"] == ["pinnacle"]
    for r in b["rows"]:
        assert not {x["book"] for x in r["books"]} & set(junk)
        assert "pinnacle" in {x["book"] for x in r["books"]}


def test_game_row_with_only_junk_plus_one_real_book_is_dropped():
    A, H = "Boston Celtics", "Miami Heat"
    ev = event(A, H, [
        bm("fanduel", h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110)),
        bm("bovada", h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110), totals(224.5, -110, -110)),
        bm("betonline", h2h(A, H, -250, 210), spreads(A, H, 6.5, -110, -110), totals(224.5, -110, -110)),
    ])
    b = gob.build_game_board("nba", [ev], NOW)
    assert b["rows"] == [] and b["status"] == "awaiting_lines"      # ml/spread are single-book once junk is gone; 224.5 was junk-only
    assert b["offshore_books"] == [] and b["books"] == []


def test_junk_offshore_dropped_from_bestbook_ladder_quotes():
    bb = [{"commence_time": START, "away_team": "Boston Celtics", "home_team": "Miami Heat", "quotes": [
        {"book": "bovada", "market": "spread", "line": 9.5, "a": -110, "b": -110},
        {"book": "betonline", "market": "spread", "line": 9.5, "a": -110, "b": -110},
        {"book": "pinnacle", "market": "spread", "line": 9.5, "a": -105, "b": -115},
        {"book": "fanduel", "market": "spread", "line": 9.5, "a": -110, "b": -110}]}]
    b = gob.build_game_board("nba", [], NOW, bb_games=bb, bb_meta={"fetched_at": "2026-11-02T17:55:00+00:00"})
    assert [r["line"] for r in b["rows"]] == [9.5]
    assert {x["book"] for x in b["rows"][0]["books"]} == {"fanduel", "pinnacle"} and b["offshore_books"] == ["pinnacle"]
