"""NCAAF banker parsers (ev.ncaaf_banker): BettingPros offers and Kalshi markets -> BestBook-shaped ladder quotes.
Market ids (198/199/200) and Kalshi series (KXNCAAFGAME / KXNCAAFSPREAD / KXNCAFTOTAL) probed live 2026-11-10."""
from __future__ import annotations

from ev import ncaab_banker as bb
from ev import ncaaf_banker as fb

EVENT = {"away": "OHI", "home": "MOH"}          # Ohio at Miami (OH)


def _sel(**kw):
    return {"participant": None, "selection": "", "active": True, **kw}


def _book(bid, cost, line, **kw):
    return {"id": bid, "lines": [{"cost": cost, "line": line, "active": True, "is_off": False, **kw}]}


def test_bp_markets_are_the_ncaaf_catalog_ids():
    assert fb.BP_MARKETS == {198: "ml", 199: "total", 200: "spread"}
    assert fb.SPORT == "ncaaf" and fb.OUT_KEY.endswith("ncaaf/game_lines_latest.json")


def test_bp_parse_event_conventions_ncaaf_ids():
    offers = [
        {"market_id": 198, "player_id": None, "selections": [
            _sel(participant="OHI", books=[_book(10, 150, 1), _book(0, 140, 1)]),          # consensus (id 0) dropped
            _sel(participant="MOH", books=[_book(10, -170, 1)])]},
        {"market_id": 200, "player_id": None, "selections": [
            _sel(participant="OHI", books=[_book(10, -110, 3.5)]),                          # away +3.5 == home -3.5
            _sel(participant="MOH", books=[_book(10, -110, -3.5)])]},
        {"market_id": 199, "player_id": None, "selections": [
            _sel(selection="over", books=[_book(10, -110, 47.5)]), _sel(selection="under", books=[_book(10, -110, 47.5)])]},
        {"market_id": 224, "player_id": None, "selections": [                               # an NCAAB id: not an NCAAF market
            _sel(participant="OHI", books=[_book(10, 500, 1)])]},
        {"market_id": 435, "player_id": 7, "selections": [_sel(selection="over", books=[_book(10, -110, 200.5)])]},   # a prop: ignored
    ]
    qs = {(q["market"], q["line"]): q for q in fb.bp_parse_event(EVENT, offers)}
    assert qs[("ml", None)] == {"market": "ml", "line": None, "book": "fanduel", "a": 150, "b": -170}
    assert (qs[("spread", -3.5)]["a"], qs[("spread", -3.5)]["b"]) == (-110, -110)          # HOME handicap, a = away
    assert (qs[("total", 47.5)]["a"], qs[("total", 47.5)]["b"]) == (-110, -110)            # a = Over
    assert len(qs) == 3


def test_ncaab_parser_is_unchanged_by_the_markets_parameter():
    offers = [{"market_id": 224, "player_id": None, "selections": [
        _sel(participant="UNF", books=[_book(10, 530, 1)]), _sel(participant="CBU", books=[_book(10, -780, 1)])]}]
    ev = {"away": "UNF", "home": "CBU"}
    assert bb.bp_parse_event(ev, offers) == bb.bp_parse_event(ev, offers, bb.BP_MARKETS)
    assert fb.bp_parse_event(ev, offers) == []                       # NCAAF ids do not see an NCAAB market


def test_kalshi_series_table_uses_the_one_a_total():
    assert fb.KALSHI_SERIES == {"KXNCAAFGAME": "ml", "KXNCAAFSPREAD": "spread", "KXNCAFTOTAL": "total"}


def _m(ticker, yes_ask, no_ask, strike=None):
    return {"ticker": ticker, "event_ticker": "e", "yes_ask_dollars": yes_ask, "no_ask_dollars": no_ask, "floor_strike": strike}


def test_kalshi_event_parsing_ncaaf():
    meta, ml = fb.kalshi_parse_event("KXNCAAFGAME", "KXNCAAFGAME-26NOV14OHIOMOH", [
        _m("KXNCAAFGAME-26NOV14OHIOMOH-OHIO", "0.40", "0.62"), _m("KXNCAAFGAME-26NOV14OHIOMOH-MOH", "0.62", "0.40")])
    assert (meta["away_team"], meta["home_team"]) == ("OHIO", "MOH") and len(ml) == 2
    _, tot = fb.kalshi_parse_event("KXNCAFTOTAL", "KXNCAFTOTAL-26NOV14OHIOMOH", [
        _m("KXNCAFTOTAL-26NOV14OHIOMOH-48", "0.50", "0.52", 47.5)])
    assert tot and tot[0]["market"] == "total" and tot[0]["line"] == 47.5
    assert fb.mt_date("KXNCAAFGAME-26NOV14OHIOMOH") == "2026-11-14"
