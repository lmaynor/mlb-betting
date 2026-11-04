"""
Tests for the rolling price history on the NBA / NHL game-line board (ev.game_odds_board `history`) and its read-time handling
in mlb.runners.public_api.get_game_odds_board. Storage is an in-memory dict (never GCS).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import mlb.runners.public_api as api
from ev import game_odds_board as gob
from tests.test_game_odds_board import bm, event, h2h, spreads, totals

A, H = "Boston Celtics", "Miami Heat"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
GK = "nba:2026-11-02:BOS@MIA"


def _meta(t):
    return {"snapshot_ts": t.isoformat(), "age_min": 1.0}


def _ev(ml, sp_line, sp, tot_line, tot, start=START, extra=(), eid="e1", away=A, home=H):
    """ml/sp/tot: {book: (a, b)}; the same books on every market."""
    books = []
    for k in ml:
        books.append(bm(k, h2h(away, home, *ml[k]), spreads(away, home, sp_line, *sp[k]), totals(tot_line, *tot[k])))
    return event(away, home, books + list(extra), start=start, eid=eid)


def _devig(a, b):
    def dec(x):
        return 1 + x / 100 if x > 0 else 1 + 100 / -x
    ia, ib = 1 / dec(a), 1 / dec(b)
    return ia / (ia + ib)


BASE = _ev(
    ml={"fanduel": (-250, 210), "draftkings": (-255, 215), "betmgm": (-245, 205), "pinnacle": (-260, 220)},
    sp_line=6.5, sp={"fanduel": (-110, -110), "draftkings": (-108, -112), "betmgm": (-115, -105), "pinnacle": (-102, -118)},
    tot_line=220.5, tot={"fanduel": (-110, -110), "draftkings": (-105, -115), "betmgm": (-105, -115), "pinnacle": (-108, -108)})


def test_first_run_one_point_per_game_market():
    b = gob.build_game_board("nba", [BASE], NOW, _meta(NOW))
    h = b["history"]
    assert sorted(h) == [f"{GK}|ml", f"{GK}|spread", f"{GK}|total"]
    assert all(len(s["points"]) == 1 for s in h.values())
    ml = h[f"{GK}|ml"]["points"][0]
    prices = {"fanduel": (-250, 210), "draftkings": (-255, 215), "betmgm": (-245, 205), "pinnacle": (-260, 220)}
    want = sum(_devig(*v) for v in prices.values()) / 4                      # pinnacle counts toward p_a
    assert ml["t"] == b["as_of"] == NOW.isoformat() and ml["line"] is None and ml["n"] == 4
    assert ml["p_a"] == pytest.approx(want, abs=1e-4)
    assert ml["best_a"] == -245 and ml["best_b"] == 215                      # non-offshore only: pinnacle's 220 never best
    sp = h[f"{GK}|spread"]["points"][0]
    assert sp["line"] == 6.5 and sp["n"] == 4 and sp["best_a"] == -108 and sp["best_b"] == -105
    want = sum(_devig(a, bb) for a, bb in [(-110, -110), (-108, -112), (-115, -105), (-102, -118)]) / 4
    assert sp["p_a"] == pytest.approx(want, abs=1e-4)
    tot = h[f"{GK}|total"]["points"][0]
    assert tot["line"] == 220.5 and tot["best_a"] == -105 and tot["best_b"] == -110 and tot["n"] == 4
    assert json.loads(json.dumps(b))["history"] == h                         # JSON-serialisable


def test_modal_line_is_the_main_line():
    # three books on 6.5, two on 7.0 -> main spread line is 6.5; 7.0 never appears in history
    e = _ev(ml={"fanduel": (-250, 210), "draftkings": (-255, 215), "betmgm": (-245, 205), "caesars": (-250, 210), "bet365": (-250, 210)},
            sp_line=6.5, sp={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm", "caesars", "bet365")},
            tot_line=220.5, tot={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm", "caesars", "bet365")})
    # move caesars + bet365 to 7.0 spread
    for bk in e["bookmakers"]:
        if bk["key"] in ("caesars", "bet365"):
            bk["markets"][1] = spreads(A, H, 7.0, -112, -108)
    b = gob.build_game_board("nba", [e], NOW, _meta(NOW))
    assert b["history"][f"{GK}|spread"]["points"][0]["line"] == 6.5
    assert b["history"][f"{GK}|spread"]["points"][0]["n"] == 3


def test_second_run_appends_and_differs_and_is_idempotent():
    b1 = gob.build_game_board("nba", [BASE], NOW, _meta(NOW))
    later = NOW + timedelta(minutes=30)
    moved = _ev(
        ml={"fanduel": (-280, 235), "draftkings": (-285, 240), "betmgm": (-275, 230), "pinnacle": (-290, 245)},
        sp_line=7.5, sp={"fanduel": (-110, -110), "draftkings": (-112, -108), "betmgm": (-110, -110), "pinnacle": (-105, -115)},
        tot_line=221.5, tot={"fanduel": (-110, -110), "draftkings": (-110, -110), "betmgm": (-110, -110), "pinnacle": (-110, -110)})
    b2 = gob.build_game_board("nba", [moved], later, _meta(later), prev_history=b1["history"])
    for m in ("ml", "spread", "total"):
        pts = b2["history"][f"{GK}|{m}"]["points"]
        assert len(pts) == 2 and pts[0]["t"] < pts[1]["t"] and pts[0] != pts[1]
    sp = b2["history"][f"{GK}|spread"]["points"]
    assert (sp[0]["line"], sp[1]["line"]) == (6.5, 7.5)
    ml = b2["history"][f"{GK}|ml"]["points"]
    assert ml[1]["p_a"] > ml[0]["p_a"] and ml[1]["best_a"] == -275
    # same as_of again: no duplicate
    b3 = gob.build_game_board("nba", [moved], later, _meta(later), prev_history=b2["history"])
    assert b3["history"] == b2["history"]


def test_prune_old_points_cap_and_vanished_games():
    b1 = gob.build_game_board("nba", [BASE], NOW, _meta(NOW))
    old_t = (NOW - timedelta(hours=40)).isoformat()
    prev = json.loads(json.dumps(b1["history"]))
    for s in prev.values():
        s["points"].insert(0, {**s["points"][0], "t": old_t})
    # a series for a game that is not on the board any more
    prev["nba:2026-11-01:LAL@DEN|ml"] = {"points": [{**prev[f"{GK}|ml"]["points"][1], "t": (NOW - timedelta(hours=1)).isoformat()}]}
    later = NOW + timedelta(minutes=30)
    b2 = gob.build_game_board("nba", [BASE], later, _meta(later), prev_history=prev)
    assert "nba:2026-11-01:LAL@DEN|ml" not in b2["history"]
    for m in ("ml", "spread", "total"):
        ts = [p["t"] for p in b2["history"][f"{GK}|{m}"]["points"]]
        assert old_t not in ts and ts == [NOW.isoformat(), later.isoformat()]
    # cap
    many = {f"{GK}|ml": {"points": [{"t": (NOW - timedelta(seconds=300 - i)).isoformat(), "line": None, "p_a": 0.5, "best_a": -110,
                                      "best_b": -110, "n": 2} for i in range(300)]}}
    b3 = gob.build_game_board("nba", [BASE], NOW, _meta(NOW), prev_history=many)
    pts = b3["history"][f"{GK}|ml"]["points"]
    assert len(pts) == gob.HISTORY_MAX_POINTS and pts[-1]["t"] == NOW.isoformat()
    assert [p["t"] for p in pts] == sorted(p["t"] for p in pts)


def test_garbage_previous_history_does_not_raise():
    for junk in (None, [], "x", 5, {"a": 1}, {"nba:x|ml": {"points": "no"}}, {"nba:x|ml": {"points": [None, {"t": "bad"}, 3]}},
                 {"noPipe": {"points": []}}):
        b = gob.build_game_board("nba", [BASE], NOW, _meta(NOW), prev_history=junk)
        assert len(b["history"][f"{GK}|ml"]["points"]) == 1


def test_merge_failure_still_writes_board(monkeypatch):
    monkeypatch.setattr(gob, "_history_points", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    b = gob.build_game_board("nba", [BASE], NOW, _meta(NOW))
    assert b["count"] > 0 and b["history"] == {}


def test_pinnacle_counts_toward_p_a_but_never_best():
    e = event(A, H, [bm("pinnacle", h2h(A, H, -400, 350)), bm("fanduel", h2h(A, H, -300, 250)), bm("draftkings", h2h(A, H, -310, 255))])
    p = gob.build_game_board("nba", [e], NOW, _meta(NOW))["history"][f"{GK}|ml"]["points"][0]
    assert p["best_a"] == -300 and p["best_b"] == 255                      # pinnacle's +350 / -400 is better on b but excluded
    want = (_devig(-400, 350) + _devig(-300, 250) + _devig(-310, 255)) / 3
    assert p["p_a"] == pytest.approx(want, abs=1e-4) and p["n"] == 3


def test_fewer_than_three_two_way_books_gives_null_p_a():
    # 4 books quote the line but only 2 quote both sides
    e = event(A, H, [bm("fanduel", h2h(A, H, -300, 250)), bm("draftkings", h2h(A, H, -310, 255)),
                     bm("betmgm", {"key": "h2h", "outcomes": [{"name": A, "price": -305}]}),
                     bm("caesars", {"key": "h2h", "outcomes": [{"name": H, "price": 250}]})])
    p = gob.build_game_board("nba", [e], NOW, _meta(NOW))["history"][f"{GK}|ml"]["points"][0]
    assert p["p_a"] is None and p["n"] == 4 and p["best_a"] == -300 and p["best_b"] == 255


def test_no_rows_has_empty_history():
    b = gob.build_game_board("nba", [], NOW)
    assert b["history"] == {} and b["rows"] == []
    assert gob.build_game_board("nba", [BASE], NOW, _meta(NOW), prev_history={f"{GK}|ml": {"points": []}})["history"]


# ------------------------------------------------------------------------------------------------ run() persistence
@pytest.fixture
def mem(monkeypatch):
    import mlb_core.storage as st
    files: dict = {}
    api._GAME_ODDS_CACHE.clear()
    monkeypatch.setattr(st, "exists", lambda k, *a, **kw: k in files)
    monkeypatch.setattr(st, "read_bytes", lambda k, *a, **kw: files[k])
    monkeypatch.setattr(st, "write_bytes", lambda data, k, *a, **kw: files.__setitem__(k, data))
    return files


def _feed(monkeypatch, events_by_call):
    from nba.game import odds_feed
    calls = iter(events_by_call)
    cur = {}

    def fake(key, now):
        cur["ev"], cur["meta"] = next(calls)
        return cur["ev"], cur["meta"]
    monkeypatch.setattr(odds_feed, "read_banked_game_lines", fake)
    from ev import bestbook_events
    monkeypatch.setattr(bestbook_events, "read_latest_game_lines", lambda *a, **k: (None, {}))


def test_run_persists_and_reads_previous_history(mem, monkeypatch):
    t2 = NOW + timedelta(minutes=30)
    _feed(monkeypatch, [([BASE], _meta(NOW)), ([BASE], _meta(t2)), ([BASE], _meta(t2))])
    gob.run("nba", NOW)
    key = gob.OUT_KEY.format(sport="nba")
    assert len(json.loads(mem[key])["history"][f"{GK}|ml"]["points"]) == 1
    gob.run("nba", t2)
    gob.run("nba", t2)                                                     # same as_of again: idempotent
    h = json.loads(mem[key])["history"]
    assert [p["t"] for p in h[f"{GK}|ml"]["points"]] == [NOW.isoformat(), t2.isoformat()]


def test_run_with_corrupt_previous_payload(mem, monkeypatch):
    key = gob.OUT_KEY.format(sport="nba")
    for junk in (b"not json", b'{"history": 7}', b"[1,2]", b""):
        mem[key] = junk
        _feed(monkeypatch, [([BASE], _meta(NOW))])
        p = gob.run("nba", NOW)
        assert len(p["history"][f"{GK}|ml"]["points"]) == 1


def test_run_unavailable_has_history_and_keeps_previous_file(mem, monkeypatch):
    _feed(monkeypatch, [([BASE], _meta(NOW)), (None, {"reason": "none"})])
    gob.run("nba", NOW)
    before = dict(mem)
    p = gob.run("nba", NOW + timedelta(minutes=30))
    assert p["status"] == "unavailable" and p["history"] == {} and mem == before


# ------------------------------------------------------------------------------------------------ public api
def _board(now, n_games=2):
    start = (now + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    start2 = (now + timedelta(hours=30)).isoformat().replace("+00:00", "Z")       # a later ET day
    e1 = _ev(ml={"fanduel": (-250, 210), "draftkings": (-255, 215), "betmgm": (-245, 205)}, sp_line=6.5,
             sp={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm")}, tot_line=220.5,
             tot={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm")}, start=start)
    e2 = _ev(ml={"fanduel": (-150, 130), "draftkings": (-155, 135), "betmgm": (-145, 125)}, sp_line=3.5,
             sp={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm")}, tot_line=210.5,
             tot={k: (-110, -110) for k in ("fanduel", "draftkings", "betmgm")}, start=start2, eid="e2",
             away="Los Angeles Lakers", home="Denver Nuggets")
    return gob.build_game_board("nba", [e1, e2][:n_games], now, _meta(now)), start, start2


def _serve(monkeypatch, payload):
    import mlb_core.storage as st
    api._GAME_ODDS_CACHE.clear()
    files = {gob.OUT_KEY.format(sport="nba"): json.dumps(payload).encode()} if payload is not None else {}
    monkeypatch.setattr(st, "exists", lambda k, *a, **kw: k in files)
    monkeypatch.setattr(st, "read_bytes", lambda k, *a, **kw: files[k])


def _games_of(history):
    return {k.rsplit("|", 1)[0] for k in history}


def test_api_history_follows_rows(monkeypatch):
    now = datetime.now(timezone.utc)
    built, start, start2 = _board(now)
    gks = [g["game_key"] for g in built["games"]]
    assert len(gks) == 2 and len(built["history"]) == 6
    _serve(monkeypatch, built)
    res = api.get_game_odds_board("nba")
    assert set(res["history"]) == set(built["history"])
    assert all(set(s) == {"points"} for s in res["history"].values())
    # market filter: only that market's series
    assert {k.rsplit("|", 1)[1] for k in api.get_game_odds_board("nba", market="spread")["history"]} == {"spread"}
    # day filter: only that day's game
    day1 = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(gob._ET).date().isoformat()
    r = api.get_game_odds_board("nba", day=day1)
    assert _games_of(r["history"]) == {r["rows"][0]["game_key"]} == {g["game_key"] for g in r["games"]}
    assert api.get_game_odds_board("nba", day="2001-01-01")["history"] == {}
    # limit: rows cut to the first game's first row; history only for games present in the returned rows
    r = api.get_game_odds_board("nba", limit=1)
    assert len(r["rows"]) == 1 and r["count"] == built["count"]
    assert _games_of(r["history"]) == {r["rows"][0]["game_key"]} and len(r["history"]) == 3


def test_api_started_games_drop_their_history(monkeypatch):
    now = datetime.now(timezone.utc)
    built, start, start2 = _board(now)
    _serve(monkeypatch, built)
    later = now + timedelta(hours=5)                                       # game 1 (3 h out) has started, game 2 (30 h) has not
    with patch.object(api, "_datetime", wraps=datetime) as dt:
        dt.now.return_value = later
        res = api.get_game_odds_board("nba")
    live = {r["game_key"] for r in res["rows"]}
    assert len(live) == 1 and _games_of(res["history"]) == live and len(res["history"]) == 3
    with patch.object(api, "_datetime", wraps=datetime) as dt:
        dt.now.return_value = now + timedelta(hours=40)
        res = api.get_game_odds_board("nba")
    assert res["rows"] == [] and res["history"] == {}


def test_api_history_key_always_present(monkeypatch):
    _serve(monkeypatch, None)
    assert api.get_game_odds_board("nba")["history"] == {}
    now = datetime.now(timezone.utc)
    built, *_ = _board(now)
    built.pop("history")                                                   # a pre-history file on disk
    _serve(monkeypatch, built)
    res = api.get_game_odds_board("nba")
    assert res["history"] == {} and res["count"] > 0
