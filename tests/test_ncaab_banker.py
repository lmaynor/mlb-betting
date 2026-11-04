"""NCAAB banker parsers (ev.ncaab_banker): BettingPros offers and Kalshi markets -> BestBook-shaped ladder quotes.
Shapes probed live 2026-11-03."""
from __future__ import annotations

from ev import ncaab_banker as nb

EVENT = {"away": "UNF", "home": "CBU"}


def _sel(**kw):
    return {"participant": None, "selection": "", "active": True, **kw}


def _book(bid, cost, line, **kw):
    return {"id": bid, "lines": [{"cost": cost, "line": line, "active": True, "is_off": False, **kw}]}


def test_bp_parse_event_conventions():
    offers = [
        {"market_id": 224, "player_id": None, "selections": [
            _sel(participant="UNF", books=[_book(10, 530, 1), _book(0, 500, 1), _book(37, 400, 1)]),
            _sel(participant="CBU", books=[_book(10, -780, 1), _book(0, -700, 1)])]},
        {"market_id": 226, "player_id": None, "selections": [
            _sel(participant="UNF", books=[_book(10, -105, 11.5)]),            # away +11.5 == home -11.5
            _sel(participant="CBU", books=[_book(10, -115, -11.5)])]},
        {"market_id": 225, "player_id": None, "selections": [
            _sel(selection="over", books=[_book(10, -115, 154.5)]), _sel(selection="under", books=[_book(10, -105, 154.5)])]},
        {"market_id": 269, "player_id": 7, "selections": [_sel(selection="over", books=[_book(10, -110, 12.5)])]},   # a prop: ignored
    ]
    qs = {(q["market"], q["line"]): q for q in nb.bp_parse_event(EVENT, offers)}
    assert qs[("ml", None)] == {"market": "ml", "line": None, "book": "fanduel", "a": 530, "b": -780}   # consensus (id 0) and DFS (37) dropped
    assert (qs[("spread", -11.5)]["a"], qs[("spread", -11.5)]["b"]) == (-105, -115)                  # HOME handicap, a = away
    assert (qs[("total", 154.5)]["a"], qs[("total", 154.5)]["b"]) == (-115, -105)                     # a = Over
    assert len(qs) == 3


def _m(ticker, event, yes_ask, no_ask, strike=None):
    return {"ticker": ticker, "event_ticker": event, "yes_ask_dollars": yes_ask, "no_ask_dollars": no_ask, "floor_strike": strike}


def test_kalshi_event_parsing_and_price_rules():
    meta, ml = nb.kalshi_parse_event("KXNCAAMBGAME", "KXNCAAMBGAME-26NOV07HAMPGW", [
        _m("KXNCAAMBGAME-26NOV07HAMPGW-HAMP", "e", "0.40", "0.62"), _m("KXNCAAMBGAME-26NOV07HAMPGW-GW", "e", "0.62", "0.40")])
    assert (meta["away_team"], meta["home_team"]) == ("HAMP", "GW") and len(ml) == 2
    _, tot = nb.kalshi_parse_event("KXNCAAMBTOTAL", "KXNCAAMBTOTAL-26NOV03SDSTKSU", [
        _m("KXNCAAMBTOTAL-26NOV03SDSTKSU-123", "e", "0.99", "0.20", 122.5),       # yes side is a 99c placeholder: dropped
        _m("KXNCAAMBTOTAL-26NOV03SDSTKSU-128", "e", "0.90", "0.11", 127.5)])
    by = {q["line"]: q for q in tot}
    assert by[122.5]["a"] is None and by[122.5]["b"] == nb.prob_to_american(0.20)
    assert by[127.5]["a"] == nb.prob_to_american(0.90) and by[127.5]["b"] == nb.prob_to_american(0.11)


def test_prob_to_american():
    assert nb.prob_to_american(0.5) == -100 and nb.prob_to_american(0.2) == 400 and nb.prob_to_american(0.75) == -300
    assert nb.prob_to_american(0.0) is None and nb.prob_to_american(None) is None
