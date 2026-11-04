"""
BestBook full-game ML / spread / total ladders (ev.bestbook_game_lines), their capture in the live crawl, and their union into the
game-line price board (ev.game_odds_board). Row shapes are the ones probed live 2026-11-01 (NBA LAC@GSW, NHL OTT@ANA): one binary
yes/no row per rung, rung label with a REAL minus sign, `line` = the AWAY team's handicap, quotes per venue code.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ev import bestbook_events as bbe
from ev import bestbook_game_lines as bgl
from ev import game_odds_board as gob

MINUS = chr(0x2212)
GAME = "nba:2026-11-02-LAC-GSW"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=5)).isoformat()


def pct(american):
    p = 100.0 / (american + 100.0) if american > 0 else -american / (-american + 100.0)
    return round(p * 100.0, 4)


def q(venue, yes, no):
    out = {"venue": venue}
    if yes is not None:
        out.update(yes=pct(yes), yesNative=f"{yes:+d}".replace("-", MINUS))
    if no is not None:
        out.update(no=pct(no), noNative=f"{no:+d}".replace("-", MINUS))
    return out


def row(rid, family, rung, line, quotes, rung_team=None, section="lines"):
    return {"id": f"match:{GAME}|{rid}", "venue": "match", "game": GAME, "gameTeams": ["LAC", "GSW"], "section": section,
            "family": f"{GAME}|{family}", "rung": rung, "line": line, "rungTeam": rung_team, "team": None, "quotes": quotes}


def ml(team, quotes):
    return row(f"ml|{team}", "ml", f"{team} wins", 0, quotes, rung_team=team)


def spread(team, x, quotes):
    away_line = -x if team == "LAC" else x                     # the row's `line` is the AWAY handicap, not the rung team's
    return row(f"spread|full|{team}|{x:g}", "spread|full", f"{team} {MINUS}{x:g}", away_line, quotes, rung_team=team)


def total(x, quotes):
    return row(f"total|full|{x:g}", "total|full", f"Over {x:g}", x, quotes)


def by(quotes, market, line, book):
    hit = [r for r in quotes if r["market"] == market and r["line"] == line and r["book"] == book]
    assert len(hit) == 1, (market, line, book, hit)
    return hit[0]


def real_rows():
    return [
        # ML: LAC (away) +114 / GSW (home) -134 at fanduel, seen from both teams' rows
        ml("LAC", [q("fd", 114, -134), q("dk", 105, -125), q("kalshi", 117, -122)]),
        ml("GSW", [q("fd", -134, 114), q("dk", -125, 105)]),
        # spread: 'GSW -1.5' (home lays) and 'LAC -1.5' (away lays), the two rows of the same market pair
        spread("GSW", 1.5, [q("fd", -124, 102), q("dk", -118, -102)]),
        spread("LAC", 1.5, [q("fd", 124, -154), q("dk", 113, -141)]),
        spread("GSW", 2.5, [q("fd", -104, -116), q("dk", -103, -120)]),
        total(220.5, [q("fd", -114, -106), q("dk", -112, -108), q("pp", -110, -110)]),
        total(221.5, [q("fd", -106, -116)]),                                   # one venue only
        # ignored families: period, team total, three-way, a player prop
        row("total|1h|110.5", "total|1h", "Over 110.5", 110.5, [q("fd", -110, -110)], section="periods:1h"),
        row("teamtotal|GSW|110.5", "teamtotal|GSW", "Over 110.5", 110.5, [q("fd", -110, -110)]),
        row("pwin3|rt|LAC", "pwin3|rt", "LAC wins", None, [q("fd", 300, -400)], rung_team="LAC"),
    ]


def test_orientation_home_handicap_and_sides():
    quotes = bgl.game_line_quotes(real_rows())
    fd = by(quotes, "ml", None, "fanduel")
    assert (fd["a"], fd["b"]) == (114, -134)                                  # a = AWAY, b = HOME, folded from both team rows
    # 'GSW -1.5' (the home team lays 1.5) -> home handicap -1.5: yes = home = b, no = away +1.5 = a
    assert (lambda r: (r["a"], r["b"]))(by(quotes, "spread", -1.5, "fanduel")) == (102, -124)
    # 'LAC -1.5' (the away team lays 1.5) -> home handicap +1.5: yes = away = a, no = home +1.5 = b
    assert (lambda r: (r["a"], r["b"]))(by(quotes, "spread", 1.5, "fanduel")) == (124, -154)
    # monotone ladder: the home -2.5 price is better than the home -1.5 price (a bigger number to lay pays more)
    assert by(quotes, "spread", -2.5, "fanduel")["b"] > by(quotes, "spread", -1.5, "fanduel")["b"]
    tot = by(quotes, "total", 220.5, "fanduel")
    assert (tot["a"], tot["b"]) == (-114, -106)                               # a = Over, b = Under


def test_only_full_game_ml_spread_total_rows_and_only_bettable_venues():
    quotes = bgl.game_line_quotes(real_rows())
    assert {r["market"] for r in quotes} == {"ml", "spread", "total"}
    assert not [r for r in quotes if r["book"] == "prizepicks"]               # a DFS venue is not an executable price
    assert not [r for r in quotes if r["line"] == 110.5]                      # period + team-total lines never leak in


def test_books_keep_their_native_price_and_exchanges_drop_placeholders():
    rows = [ml("LAC", [q("kalshi", 117, -122), {"venue": "novig", "yes": 1.0, "no": 99.0, "yesNative": "1c", "noNative": "99c"}])]
    quotes = bgl.game_line_quotes(rows)
    assert (by(quotes, "ml", None, "kalshi")["a"], by(quotes, "ml", None, "kalshi")["b"]) == (117, -122)
    assert not [r for r in quotes if r["book"] == "novig"]                    # 1c / 99c exchange placeholders are not quotes
    # a BOOK's long-shot rung is real and stays
    long_shot = bgl.game_line_quotes([spread("LAC", 27.5, [q("fd", 2200, -10000)])])
    assert (long_shot[0]["a"], long_shot[0]["b"]) == (2200, -10000)


def test_one_sided_quote_and_unsigned_rung_and_malformed_rows_never_raise():
    rows = [spread("LAC", 3.5, [q("fd", 150, None)]),
            row("spread|full|LAC|9", "spread|full", "LAC nine", 9, [q("fd", 100, -120)], rung_team="LAC"),     # no signed number
            row("spread|full|XXX|3", "spread|full", f"XXX {MINUS}3", 3, [q("fd", 100, -120)], rung_team="XXX"),  # not a game team
            {"id": "junk", "section": "lines", "family": f"{GAME}|ml", "gameTeams": None},
            None]
    quotes = bgl.game_line_quotes(rows)
    assert [(r["market"], r["line"], r["a"], r["b"]) for r in quotes] == [("spread", 3.5, 150, None)]


def test_prune_single_book_keeps_only_rungs_two_venues_quote():
    quotes = bgl.prune_single_book(bgl.game_line_quotes(real_rows()))
    assert not [r for r in quotes if r["line"] == 221.5]
    assert {r["book"] for r in quotes if r["line"] == 220.5} == {"fanduel", "draftkings"}


# ---- the crawl keeps the ladders, in their own file ----------------------------------------------------------------------------

def test_crawl_collects_game_lines_from_the_same_snapshot_and_writes_them_separately():
    slate = [{"key": GAME, "sport": "nba", "away": "LAC", "home": "GSW", "start": START.replace("+00:00", "Z")}]
    gl = []
    events, counts = bbe.build_events_from_snapshots("nba", slate, {GAME: {"rows": real_rows()}}, NOW, game_lines=gl)
    assert events == [] and counts["game_lines_games"] == 1 and counts["game_lines_quotes"] > 0
    assert gl[0]["away_team"] == "LAC" and gl[0]["home_team"] == "GSW" and gl[0]["bb_key"] == GAME
    assert {r["market"] for r in gl[0]["quotes"]} == {"ml", "spread", "total"}

    result = {"sport": "nba", "fetched_at": NOW.isoformat(), "window": {"days": 7}, "events": [], "game_lines": gl, "counts": counts}
    assert bbe.publishable(result)[0] is True                                   # lines alone are worth publishing
    written = {}
    with patch("mlb_core.storage.write_bytes", side_effect=lambda b, k: written.__setitem__(k, json.loads(b))):
        bbe.write_latest(result)
    assert "game_lines" not in written["Odds/bestbook_live/nba/latest.json"]    # the props reader never parses the ladders
    assert written["Odds/bestbook_live/nba/game_lines_latest.json"]["games"] == gl

    with patch("mlb_core.storage.read_bytes", return_value=json.dumps(written["Odds/bestbook_live/nba/game_lines_latest.json"]).encode()):
        games, meta = bbe.read_latest_game_lines("nba", NOW + timedelta(minutes=30), 180.0)
        assert games == gl and meta["age_min"] == 30.0
        games, meta = bbe.read_latest_game_lines("nba", NOW + timedelta(minutes=400), 180.0)
        assert games is None and "old" in meta["reason"]


def test_a_crawl_without_ladders_keeps_the_previous_game_lines_file():
    result = {"sport": "nba", "fetched_at": NOW.isoformat(), "events": [{"id": "x"}], "game_lines": [], "counts": {}}
    written = {}
    with patch("mlb_core.storage.write_bytes", side_effect=lambda b, k: written.__setitem__(k, b)):
        bbe.write_latest(result)
    assert list(written) == ["Odds/bestbook_live/nba/latest.json"]


# ---- the board unions the two sources ------------------------------------------------------------------------------------------

def bb_games(quotes=None, start=START):
    return [{"bb_key": GAME, "away_team": "LAC", "home_team": "GSW", "commence_time": start,
             "quotes": quotes if quotes is not None else bgl.prune_single_book(bgl.game_line_quotes(real_rows()))}]


def pa_event(fd_ml=(110, -130)):
    A, H = "LA Clippers", "Golden State Warriors"
    return {"id": "e1", "away_team": A, "home_team": H, "commence_time": START, "bookmakers": [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [{"name": A, "price": fd_ml[0]}, {"name": H, "price": fd_ml[1]}]}]},
        {"key": "caesars", "markets": [{"key": "h2h", "outcomes": [{"name": A, "price": 112}, {"name": H, "price": -132}]}]},
    ]}


def board(events, pa_age=40.0, bb_age=5.0, games=None):
    meta = {"age_min": pa_age, "snapshot_ts": (NOW - timedelta(minutes=pa_age)).isoformat()} if events else None
    bb_meta = {"age_min": bb_age, "fetched_at": (NOW - timedelta(minutes=bb_age)).isoformat()}
    return gob.build_game_board("nba", events, NOW, meta, games if games is not None else bb_games(), bb_meta)


def ml_row(b):
    return next(r for r in b["rows"] if r["market"] == "ml")


def test_bestbook_alone_builds_a_board_with_alternate_rungs():
    b = board(None)
    assert b["status"] == "live" and b["lines_source"] == "bestbook" and b["summary"]["bestbook_games"] == 1
    lines = sorted(r["line"] for r in b["rows"] if r["market"] == "spread")
    assert lines == [-2.5, -1.5, 1.5]                                           # alternates, not just one main number
    assert b["rows"][0]["game_key"] == "nba:2026-11-02:LAC@GSW"                  # the engines' own game key
    assert b["as_of"] == (NOW - timedelta(minutes=5)).isoformat() and b["lines_age_minutes"] == 5.0


def test_fresher_bestbook_replaces_a_book_it_shares_and_parlayapi_still_adds_its_own_books():
    b = board([pa_event()], pa_age=40.0, bb_age=5.0)
    books = {x["book"]: (x["a"], x["b"]) for x in ml_row(b)["books"]}
    assert books["fanduel"] == (114, -134)                                      # BestBook (5 min old) beat ParlayAPI (40 min old)
    assert books["caesars"] == (112, -132)                                       # a book only ParlayAPI has stays
    assert b["lines_source"] == "oddsaccum_game_lines+bestbook"
    assert b["lines_age_minutes"] == 5.0 and b["as_of"] == (NOW - timedelta(minutes=5)).isoformat()


def test_fresher_parlayapi_keeps_its_price_and_never_mixes_two_snapshots():
    b = board([pa_event()], pa_age=5.0, bb_age=90.0)
    books = {x["book"]: (x["a"], x["b"]) for x in ml_row(b)["books"]}
    assert books["fanduel"] == (110, -130)
    assert books["draftkings"] == (105, -125)                                   # a book only BestBook has is still added
    assert books["fanduel"][0] == 110 and books["fanduel"][1] == -130           # both sides from ONE snapshot


def test_a_fresher_one_sided_quote_does_not_overwrite_a_complete_one():
    games = bb_games([{"market": "ml", "line": None, "book": "fanduel", "a": 120, "b": None},
                      {"market": "ml", "line": None, "book": "draftkings", "a": 105, "b": -125}])
    b = board([pa_event()], pa_age=40.0, bb_age=5.0, games=games)
    assert {x["book"]: (x["a"], x["b"]) for x in ml_row(b)["books"]}["fanduel"] == (110, -130)


def test_a_swapped_bestbook_quote_is_caught_by_the_flip_guard():
    quotes = [{"market": "ml", "line": None, "book": bk, "a": -250, "b": 210} for bk in ("fanduel", "draftkings", "betmgm", "pinnacle")]
    quotes.append({"market": "ml", "line": None, "book": "thescore", "a": 210, "b": -250})          # sides swapped
    b = board(None, games=bb_games(quotes))
    assert "thescore" not in {x["book"] for x in ml_row(b)["books"]} and b["summary"]["flipped_quotes_dropped"] == 1


def test_ladder_is_capped_per_game_and_market_but_moneyline_is_not():
    quotes = []
    for i in range(60):                                                         # a 60-rung totals ladder, two venues each
        for bk in ("fanduel", "draftkings"):
            quotes.append({"market": "total", "line": 190.5 + i, "book": bk, "a": -110, "b": -110})
    quotes += [{"market": "ml", "line": None, "book": bk, "a": 114, "b": -134} for bk in ("fanduel", "draftkings")]
    b = board(None, games=bb_games(quotes))
    totals = [r for r in b["rows"] if r["market"] == "total"]
    assert len(totals) == gob.LADDER_MAX_ROWS and len([r for r in b["rows"] if r["market"] == "ml"]) == 1


def test_started_unmapped_dfs_and_malformed_bestbook_entries_are_skipped():
    started = bb_games(start=(NOW - timedelta(minutes=1)).isoformat())
    assert board(None, games=started)["count"] == 0
    junk = bb_games([{"market": "ml", "line": None, "book": "underdog", "a": 100, "b": -120},     # DFS
                     {"market": "ml", "line": None, "book": "fanduel", "a": 0, "b": 5},           # impossible prices
                     {"market": "spread", "line": None, "book": "fanduel", "a": -110, "b": -110},  # no line
                     {"market": "teamtotal", "line": 5.5, "book": "fanduel", "a": -110, "b": -110},
                     "garbage"])
    assert board(None, games=junk)["count"] == 0
    unmapped = [{**bb_games()[0], "away_team": "ZZZ"}]
    assert board(None, games=unmapped)["summary"]["events_unmapped"] == 1


def test_run_builds_from_bestbook_alone_and_is_unavailable_with_neither(monkeypatch):
    from nba.game import odds_feed
    monkeypatch.setattr(odds_feed, "read_banked_game_lines", lambda key, now: (None, {"reason": "no pointer", "age_min": None}))
    games = bb_games()
    fresh = NOW - timedelta(minutes=10)
    monkeypatch.setattr(bbe, "read_latest_game_lines", lambda s, n, m: (games, {"fetched_at": fresh.isoformat(), "age_min": 10.0}))
    b = gob.run("nba", now=NOW, write=False)
    assert b["status"] == "live" and b["lines_source"] == "bestbook" and b["count"] > 0

    monkeypatch.setattr(bbe, "read_latest_game_lines", lambda s, n, m: (None, {"reason": "no file"}))
    b = gob.run("nba", now=NOW, write=False)
    assert b["status"] == "unavailable" and b["rows"] == []

    def boom(*a, **k):
        raise RuntimeError("gcs down")
    monkeypatch.setattr(bbe, "read_latest_game_lines", boom)
    assert gob.run("nba", now=NOW, write=False)["status"] == "unavailable"       # a dead second source never raises
