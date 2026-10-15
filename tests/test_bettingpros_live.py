"""ev.bettingpros_live: BettingPros offers -> ev.props.Quote (offline, fixture offers)."""
from datetime import datetime, timedelta, timezone

import pytest

from ev import bettingpros_live as bl
from ev.props import Quote, build_consensus, norm_name, scan_book_outliers
from mlb_core.odds import bettingpros_props as bpp


def _line(cost, line=64.5, main=True):
    return {"cost": cost, "line": line, "main": main, "best": False, "is_off": False, "updated": "2026-10-14 17:00:00"}


def _offer(player="Tyler Warren", over=None, under=None, event_id=1):
    def sel(side, books):
        return {"selection": side, "label": side.title(), "books": [{"id": b, "lines": ls} for b, ls in books.items()]}
    return {"event_id": event_id, "player_id": 1, "participants": [{"id": "1", "name": player, "player": {"team": "IND"}}],
            "selections": [sel("over", over or {}), sel("under", under or {})]}


EV = {1: {"away": "IND", "home": "WAS", "scheduled": "2099-10-18 17:00:00", "season_type": "REG", "status": "scheduled"}}


def _rows(offer, label="rec_yds", sport="NFL"):
    return bpp.build_prop_rows(sport, label, 99, "2026-10-14", [offer], EV)


def test_two_sided_books_exchanges_included_and_excluded_books_dropped():
    over = {12: [_line(-110)], 10: [_line(-115)], 60: [_line(-105)], 38: [_line(-108)],      # dk fd novig prophetx
            68: [_line(-100)], 37: [_line(-112)], 0: [_line(-111)], 2: [_line(-109)], 999: [_line(-104)]}
    under = {k: [_line(-110)] for k in over}
    qs = bl.rows_to_quotes(_rows(_offer(over=over, under=under)), "nfl")
    assert sorted(q.book for q in qs) == ["draftkings", "fanduel", "novig", "prophetx"]
    # kalshi(68), prizepicks(37), consensus(0), pinnacle(2) and an unknown id (999) never become quotes
    q = next(q for q in qs if q.book == "novig")
    assert (q.stat, q.point, q.over, q.under, q.player_norm, q.team_hint, q.alt) == (
        "rec_yds", 64.5, -105, -110, "tyler warren", None, False)


def test_opening_line_rows_are_never_quotes_and_one_sided_books_stay_one_sided():
    offer = _offer(over={12: [_line(-110)], 10: [_line(+130)]}, under={12: [_line(-110)]})
    offer["selections"][0]["opening_line"] = {"line": 63.5, "cost": -120, "book_id": 10, "created": "x"}
    qs = {q.book: q for q in bl.rows_to_quotes(_rows(offer), "nfl")}
    assert set(qs) == {"draftkings", "fanduel"} and qs["fanduel"].under is None and qs["fanduel"].over == 130
    assert not qs["fanduel"].two_sided and qs["draftkings"].two_sided
    assert all(q.point == 64.5 for q in qs.values())                         # the open row (63.5) did not become a quote


def test_stat_mapping_matches_ev_props_canonical_names_and_ladder_is_alt():
    assert bl.SLUG_TO_STAT["nfl"]["passing-yards"] == "pass_yds" and bl.SLUG_TO_STAT["nhl"]["shots"] == "sog"
    assert bl.SLUG_TO_STAT["nba"]["points-assists-rebounds"] == "pra"
    off = _offer(over={12: [_line(-110, 64.5, True), _line(+150, 74.5, False)]}, under={12: [_line(-110, 64.5, True)]})
    qs = {q.point: q for q in bl.rows_to_quotes(_rows(off), "nfl")}
    assert qs[64.5].alt is False and qs[74.5].alt is True
    nba = _offer(player="Jalen Brunson", over={10: [_line(-115, 27.5)]}, under={10: [_line(-105, 27.5)]})
    q = bl.rows_to_quotes(_rows(nba, "pts", "NBA"), "nba")[0]
    assert q.stat == "points" and q.player_norm == "jalen brunson"
    assert bl.rows_to_quotes(_rows(off, "no_such_label"), "nfl") == []       # unknown stat label: dropped


def test_bp_quotes_feed_the_consensus_and_sink_a_machinery():
    books = {12: -110, 10: -110, 13: -110, 18: -110, 19: -110, 33: +115}
    over = {b: [_line(p)] for b, p in books.items()}
    under = {b: [_line(-110 if b != 33 else -135)] for b in books}
    qs = bl.rows_to_quotes(_rows(_offer(over=over, under=under)), "nfl")
    c = build_consensus(qs)[("tyler warren", "rec_yds", 64.5)]
    assert c.n_books == 6 and len(c.book_quotes) == 6
    out = scan_book_outliers(qs)
    assert [(r["book"], r["side"]) for r in out] == [("espnbet", "OVER")] and len(out[0]["all_books"]) == 6


def test_merge_prefers_parlay_and_only_fills_gaps():
    def q(book, point, over):
        return Quote(book, "rec_yds", False, "Tyler Warren", norm_name("Tyler Warren"), None, point, over, -110)
    parlay = [q("draftkings", 64.5, -110), q("fanduel", 63.5, -108)]
    bp = [q("draftkings", 64.5, -999), q("fanduel", 64.5, -112), q("novig", 64.5, -105)]
    merged = bl.merge_quotes(parlay, bp)
    got = {(x.book, x.point): x.over for x in merged}
    assert got == {("draftkings", 64.5): -110,                  # ParlayAPI price kept, BettingPros duplicate dropped
                   ("fanduel", 63.5): -108, ("fanduel", 64.5): -112,   # same book, NEW point: filled
                   ("novig", 64.5): -105}                       # new book: filled
    assert len(merged) == 4 and bl.merge_quotes([], []) == []


def test_upcoming_drops_finished_and_started_events():
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    fmt = lambda d: (now + d).strftime("%Y-%m-%d %H:%M:%S")                       # noqa: E731
    ev = {1: {"scheduled": fmt(timedelta(hours=3)), "status": "scheduled"},
          2: {"scheduled": fmt(timedelta(hours=-1)), "status": "scheduled"},
          3: {"scheduled": fmt(timedelta(hours=3)), "status": "closed"},
          4: {"scheduled": "garbage", "status": ""}}
    assert set(bl._upcoming(ev, now)) == {1, 4}


def test_fetch_bp_quotes_pipeline_skips_a_failing_market_and_makes_no_network_calls(monkeypatch):
    over = {12: [_line(-110)], 10: [_line(-110)]}
    under = {12: [_line(-110)], 10: [_line(-110)]}
    monkeypatch.setattr(bpp, "make_session", lambda sport: object())
    monkeypatch.setattr(bpp, "fetch_events_detail", lambda s, sp, ds: dict(EV))
    monkeypatch.setattr(bpp, "resolve_markets", lambda s, sp, w=None: {
        "rec_yds": {"slug": "receiving-yards", "market_id": 1}, "receptions": {"slug": "receptions", "market_id": 2},
        "bogus": {"slug": "x", "market_id": 3}})

    def offers(sess, sport, market_id, event_ids, delay=0.8):
        if market_id == 2:
            raise RuntimeError("503")
        return [_offer(over=over, under=under)]
    monkeypatch.setattr(bpp, "fetch_offers_paged", offers)
    qs = bl.fetch_bp_quotes("NFL", "2026-10-14", delay=0)
    assert sorted(q.book for q in qs) == ["draftkings", "fanduel"] and {q.stat for q in qs} == {"rec_yds"}
    with pytest.raises(ValueError):
        bl.fetch_bp_quotes("mlb", "2026-10-14")
    monkeypatch.setattr(bpp, "fetch_events_detail", lambda s, sp, ds: {})
    assert bl.fetch_bp_quotes("nhl", "2026-10-14") == []
