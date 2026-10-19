"""ev.bettingpros_events: live BettingPros crawl -> ParlayAPI-shaped events (offline; recorded-shape fixtures, no network)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ev import bettingpros_events as be
from ev import bettingpros_live as bl
from ev.props import STAT_KEYS_BY_SPORT, build_consensus, parse_event_props, stat_of
from mlb_core.odds import bettingpros_props as bpp

NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)


def _line(cost, line=64.5, main=True, off=False):
    return {"cost": cost, "line": line, "main": main, "best": False, "is_off": off, "updated": "2026-10-17 14:55:00"}


def _offer(player, over, under, event_id=1, team="IND"):
    def sel(side, books):
        return {"selection": side, "label": side.title(), "books": [{"id": b, "lines": ls} for b, ls in books.items()]}
    return {"event_id": event_id, "player_id": 1, "participants": [{"id": "1", "name": player, "player": {"team": team}}],
            "selections": [sel("over", over), sel("under", under)]}


def _ev(away="IND", home="TEN", hours=4, eid=1, status="scheduled"):
    return {eid: {"away": away, "home": home, "scheduled": (NOW + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S"),
                  "season_type": "REG", "status": status}}


# ------------------------------------------------------------------------------------------------ tables
@pytest.mark.parametrize("sport", ["nfl", "nhl", "nba"])
def test_parlay_key_table_is_consistent_with_ev_props(sport):
    stats = set(bl.LABEL_TO_STAT[sport].values())
    assert set(be.PARLAY_KEY[sport]) == stats                       # one canonical key per crawled stat, nothing missing
    for stat, key in be.PARLAY_KEY[sport].items():
        assert stat_of(key, sport) == (stat, False)                 # ev.props reads the key back as the same stat
        assert key in STAT_KEYS_BY_SPORT[sport][stat]
        assert stat_of(key + "_alternate", sport) == (stat, True)


def test_emitted_books_are_real_books_only():
    assert {"draftkings", "fanduel", "novig", "prophetx"} <= be.EMITTED_BOOKS
    assert not be.EMITTED_BOOKS & {"kalshi", "underdog", "prizepicks", "sleeper", "fliff", "betr", "dabble", "consensus"}


# ------------------------------------------------------------------------------------------------ conversion
def test_rows_become_a_parlayapi_event_that_ev_props_parses_back():
    over = {12: [_line(-110)], 10: [_line(-115)], 60: [_line(-105)], 68: [_line(-100)], 37: [_line(-112)],
            0: [_line(-111)], 33: [_line(-108)]}
    under = {k: [_line(-110)] for k in over}
    ev_map = _ev()
    rows = bpp.build_prop_rows("NFL", "rec_yds", 99, "2026-10-17", [_offer("Tyler Warren", over, under)], ev_map)
    events, counts = be.build_events("nfl", ev_map, {1: rows}, NOW)
    assert len(events) == 1 and counts["events_priced"] == 1 and counts["quotes"] == 4
    e = events[0]
    assert (e["commence_time"], e["away_team"], e["home_team"], e["source"]) == (
        (NOW + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"), "IND", "TEN", "bettingpros")
    assert {b["key"] for b in e["bookmakers"]} == {"draftkings", "fanduel", "novig", "espnbet"}   # kalshi/dfs/consensus out
    outs = e["bookmakers"][0]["markets"][0]
    assert outs["key"] == "player_reception_yds"
    assert {o["name"] for o in outs["outcomes"]} == {"Over", "Under"}
    assert outs["outcomes"][0]["description"] == "Tyler Warren (IND)"          # the (TEAM) form ParlayAPI books use
    quotes, _meta = parse_event_props(e, "nfl")
    assert {q.book for q in quotes} == {"draftkings", "fanduel", "novig", "espnbet"}
    q = next(q for q in quotes if q.book == "novig")
    assert (q.stat, q.player, q.player_norm, q.team_hint, q.point, q.over, q.under, q.alt) == (
        "rec_yds", "Tyler Warren", "tyler warren", "IND", 64.5, -105, -110, False)
    c = build_consensus(quotes)[("tyler warren", "rec_yds", 64.5)]
    assert c.n_books == 4


def test_alternate_rungs_one_sided_and_off_lines():
    over = {12: [_line(-110, 64.5, True), _line(+150, 74.5, False), _line(-300, 54.5, False, off=True)],
            10: [_line(+130, 64.5)]}
    under = {12: [_line(-110, 64.5, True)]}                                    # FanDuel has no under: one-sided
    ev_map = _ev()
    rows = bpp.build_prop_rows("NFL", "rec_yds", 99, "d", [_offer("Tyler Warren", over, under)], ev_map)
    events, _ = be.build_events("nfl", ev_map, {1: rows}, NOW)
    by_book = {b["key"]: b for b in events[0]["bookmakers"]}
    keys = sorted(m["key"] for m in by_book["draftkings"]["markets"])
    assert keys == ["player_reception_yds", "player_reception_yds_alternate"]       # the 74.5 ladder rung is alt
    quotes, _ = parse_event_props(events[0], "nfl")
    assert {(q.point, q.alt) for q in quotes if q.book == "draftkings"} == {(64.5, False), (74.5, True)}
    assert not any(q.point == 54.5 for q in quotes)                            # is_off line never emitted
    fd = next(q for q in quotes if q.book == "fanduel")
    assert fd.under is None and fd.over == 130 and not fd.two_sided


def test_started_unknown_team_and_unpriced_events_are_counted_not_emitted():
    ev_map = {**_ev(eid=1), **_ev(eid=2, hours=-1), **_ev(eid=3, away="Nowhere Nobodies"), **_ev(eid=4)}
    good = bpp.build_prop_rows("NFL", "rec_yds", 99, "d", [_offer("A B", {12: [_line(-110)]}, {12: [_line(-110)]}, 1)], ev_map)
    late = bpp.build_prop_rows("NFL", "rec_yds", 99, "d", [_offer("C D", {12: [_line(-110)]}, {12: [_line(-110)]}, 2)], ev_map)
    events, c = be.build_events("nfl", ev_map, {1: good, 2: late}, NOW)
    assert [e["bp_event_id"] for e in events] == [1]
    assert (c["events_seen"], c["events_started"], c["events_unknown_team"], c["events_no_quotes"]) == (4, 1, 1, 1)


def test_nba_team_codes_that_canon_team_cannot_read_are_fixed():
    assert be.bp_team("nba", "UTH") == "UTA" and be.bp_team("nba", "nor") == "NOP" and be.bp_team("nfl", "JAC") == "JAC"
    ev_map = _ev("UTH", "NOR")
    rows = bpp.build_prop_rows("NBA", "pts", 99, "d", [_offer("Lauri Markkanen", {12: [_line(-110, 24.5)]},
                                                              {12: [_line(-110, 24.5)]}, team="UTH")], ev_map)
    events, c = be.build_events("nba", ev_map, {1: rows}, NOW)
    assert c["events_unknown_team"] == 0 and (events[0]["away_team"], events[0]["home_team"]) == ("UTA", "NOP")
    assert events[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["description"] == "Lauri Markkanen (UTA)"


def test_per_game_player_market_anytime_td_is_parsed_as_one_sided_yes_at_half_point():
    # recorded shape (2026-10-17): ONE offer per game, one selection per PLAYER, empty `selection`, line 1, DST rows present
    def psel(pid, label, books):
        return {"participant": pid, "selection": "", "label": label,
                "books": [{"id": b, "lines": [_line(c, 1, True)]} for b, c in books.items()]}
    offer = {"event_id": 1, "player_id": None, "participants": [
        {"id": "20162", "name": "Brevin Jordan", "player": {"position": "TE", "team": "HOU"}},
        {"id": "8120", "name": "Houston Texans", "player": {"position": "DST", "team": "HOU"}}],
        "selections": [psel("20162", "Brevin Jordan", {12: +1800, 10: +1700, 68: +1500, 37: +1000}),
                       psel("8120", "Houston Texans", {12: +900})]}
    # the generic parser reads the label as the side and loses everything -- the reason for the dedicated builder
    assert bl.rows_to_quotes(bpp.build_prop_rows("NFL", "anytime_td", 78, "d", [offer], _ev()), "nfl") == []
    rows = be.build_per_game_player_rows("NFL", "anytime_td", 78, "d", [offer], _ev())
    assert {r["player"] for r in rows} == {"Brevin Jordan"} and {r["line"] for r in rows} == {0.5}
    events, _ = be.build_events("nfl", _ev(), {1: rows}, NOW)
    quotes, _ = parse_event_props(events[0], "nfl")
    assert {(q.book, q.stat, q.point, q.over, q.under) for q in quotes} == {
        ("draftkings", "anytime_td", 0.5, 1800, None), ("fanduel", "anytime_td", 0.5, 1700, None)}


# ------------------------------------------------------------------------------------------------ crawl
class _FakeBP:
    """Stands in for bpp's three network calls; counts calls so pacing / request accounting can be asserted."""

    def __init__(self, monkeypatch, events_by_date, offers_by_label, fail_label=None):
        self.events_calls, self.offer_calls = [], []
        monkeypatch.setattr(bpp, "make_session", lambda sport: _Sess())
        monkeypatch.setattr(bpp, "fetch_events_detail", lambda s, sp, ds: self._events(ds, events_by_date))
        monkeypatch.setattr(bpp, "resolve_markets",
                            lambda s, sp, wanted=None: {lab: {"slug": bpp.SPORTS[sp][lab], "market_id": i}
                                                        for i, lab in enumerate(offers_by_label, 100)})
        self.offers, self.fail = offers_by_label, fail_label
        monkeypatch.setattr(bpp, "fetch_offers_paged", self._offers)
        monkeypatch.setattr(be, "_sleep", lambda base: None)

    def _events(self, ds, by_date):
        self.events_calls.append(ds)
        return dict(by_date.get(ds, {}))

    def _offers(self, sess, sport, market_id, event_ids, delay=0.8):
        label = list(self.offers)[market_id - 100]
        self.offer_calls.append((label, list(event_ids), delay))
        if label == self.fail:
            raise RuntimeError("500")
        sess.get("x")                                   # one counted HTTP call per market
        return self.offers[label]


class _Sess:
    headers = {}

    def get(self, *a, **k):
        return None


def test_crawl_window_dates_markets_and_request_accounting(monkeypatch):
    d0 = NOW.astimezone(be._ET).date()
    ds = lambda n: (d0 + timedelta(days=n)).isoformat()                                  # noqa: E731
    events = {ds(0): _ev("IND", "TEN", 3, eid=1), ds(2): _ev("KC", "BUF", 50, eid=2), ds(9): _ev("A", "B", 200, eid=9)}
    offers = {"rec_yds": [_offer("Tyler Warren", {12: [_line(-110)], 60: [_line(-105)]}, {12: [_line(-110)], 60: [_line(-110)]}, 1),
                          _offer("Xavier Worthy", {12: [_line(-115, 55.5)]}, {12: [_line(-105, 55.5)]}, 2, team="KC")],
              "receptions": []}
    fake = _FakeBP(monkeypatch, events, offers)
    res = be.crawl_sport("nfl", now=NOW, days=7, market_sleep=0)
    assert fake.events_calls == [ds(i) for i in range(0, 8)]                              # today .. +7 inclusive, ET days
    assert res["window"]["dates"][0] == ds(0) and len(res["window"]["dates"]) == 8
    assert {tuple(sorted(c[1])) for c in fake.offer_calls} == {(1, 2)}                    # ONE request set for all events
    assert [e["bp_event_id"] for e in res["events"]] == [1, 2]
    assert res["counts"]["events_priced"] == 2 and res["counts"]["quotes"] == 3
    assert res["requests"] == 2 and res["errors"] == [] and res["markets_requested"] == 2
    assert res["per_market"]["rec_yds"]["rows"] == 6 and res["newest_quote_update"] == "2026-10-17 14:55:00"
    assert be.publishable(res) == (True, "priced")


def test_crawl_default_windows_and_a_failing_market_does_not_abort_the_rest(monkeypatch):
    assert be.DEFAULT_DAYS == {"nfl": 7, "nhl": 3, "nba": 7}
    d0 = NOW.astimezone(be._ET).date().isoformat()
    offers = {"sog": [_offer("Jack Hughes", {12: [_line(-120, 3.5)]}, {12: [_line(100, 3.5)]}, 1, team="NJ")], "goals": []}
    fake = _FakeBP(monkeypatch, {d0: _ev("NJ", "WSH", 3)}, offers, fail_label="goals")
    res = be.crawl_sport("nhl", now=NOW, market_sleep=0)
    assert len(fake.events_calls) == 4                                                    # today + 3
    assert [e["error"] for e in res["errors"]] == ["500"] or res["errors"][0]["market"] == "goals"
    assert res["events"] and be.publishable(res)[0] is True


def test_crawl_event_listing_failure_raises_so_nothing_is_published(monkeypatch):
    monkeypatch.setattr(bpp, "make_session", lambda sport: _Sess())

    def boom(*a, **k):
        raise RuntimeError("401 key rotated")
    monkeypatch.setattr(bpp, "fetch_events_detail", boom)
    monkeypatch.setattr(be, "_sleep", lambda base: None)
    with pytest.raises(RuntimeError):
        be.crawl_sport("nba", now=NOW)
    with pytest.raises(ValueError):
        be.crawl_sport("mlb", now=NOW)


def test_publishable_rules():
    base = {"counts": {"events_priced": 0, "events_upcoming": 0}, "errors": [], "markets_requested": 5}
    assert be.publishable(base) == (True, "no upcoming games")                           # offseason: truthfully empty
    assert be.publishable({**base, "counts": {"events_priced": 0, "events_upcoming": 6}}) == (
        True, "games listed but no prices posted yet")
    outage = {**base, "counts": {"events_priced": 0, "events_upcoming": 6}, "errors": [{}] * 5}
    assert be.publishable(outage) == (False, "every market failed")                      # never blank the last good file


# ------------------------------------------------------------------------------------------------ storage
@pytest.fixture
def mem_storage(monkeypatch):
    import mlb_core.storage as st
    store: dict = {}
    monkeypatch.setattr(st, "write_bytes", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(st, "read_bytes", lambda key: store[key])
    monkeypatch.setattr(st, "exists", lambda key: key in store)
    return store


def _result(fetched, events, quotes=3):
    return {"sport": "nba", "fetched_at": fetched.isoformat(), "events": events,
            "counts": {"quotes": quotes, "books": ["draftkings"]}}


def test_write_and_read_latest_roundtrip_and_freshness(mem_storage):
    r = _result(NOW - timedelta(minutes=20), [{"id": "bp1"}])
    assert be.write_latest(r) == "Odds/bettingpros_live/nba/latest.json"
    evs, meta = be.read_latest("nba", NOW, 240)
    assert evs == [{"id": "bp1"}] and meta["age_min"] == 20.0 and meta["quotes"] == 3
    evs, meta = be.read_latest("nba", NOW + timedelta(hours=6), 240)
    assert evs is None and "old" in meta["reason"] and meta["age_min"] == pytest.approx(380.0)    # too old: refused with a reason


def test_read_latest_missing_corrupt_and_empty_never_raise(mem_storage):
    evs, meta = be.read_latest("nhl", NOW, 240)
    assert evs is None and "no BettingPros live file" in meta["reason"]
    mem_storage["Odds/bettingpros_live/nhl/latest.json"] = b"{not json"
    assert be.read_latest("nhl", NOW, 240)[0] is None
    mem_storage["Odds/bettingpros_live/nhl/latest.json"] = json.dumps({"events": []}).encode()
    assert "no fetched_at" in be.read_latest("nhl", NOW, 240)[1]["reason"]
    be.write_latest(_result(NOW, []) | {"sport": "nhl"})
    evs, meta = be.read_latest("nhl", NOW, 240)
    assert evs is None and "no priced games" in meta["reason"]


# ------------------------------------------------------------------------------------------------ runner
def test_runner_writes_bucket_by_default_and_a_local_file_with_out(monkeypatch, mem_storage, tmp_path):
    from mlb.runners import bettingpros_live_props as runner
    good = {"sport": "nba", "fetched_at": NOW.isoformat(), "events": [{"id": "bp1"}], "window": {}, "per_market": {},
            "counts": {"events_priced": 1, "events_upcoming": 1, "quotes": 3, "books": ["draftkings"]}, "errors": [],
            "markets_requested": 3, "requests": 12, "seconds": 5.0, "newest_quote_update": None}
    monkeypatch.setattr(be, "crawl_sport", lambda sport, **k: dict(good))
    assert runner.main(["--sport", "nba"]) == 0
    assert "Odds/bettingpros_live/nba/latest.json" in mem_storage
    mem_storage.clear()
    out = tmp_path / "r.json"
    assert runner.main(["--sport", "nba", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["events"] == [{"id": "bp1"}] and mem_storage == {}


def test_runner_outage_keeps_the_last_good_file_and_exits_nonzero(monkeypatch, mem_storage):
    from mlb.runners import bettingpros_live_props as runner
    mem_storage["Odds/bettingpros_live/nba/latest.json"] = b'{"keep": true}'
    bad = {"sport": "nba", "fetched_at": NOW.isoformat(), "events": [], "window": {}, "per_market": {},
           "counts": {"events_priced": 0, "events_upcoming": 5}, "errors": [{}] * 3, "markets_requested": 3,
           "requests": 3, "seconds": 1.0, "newest_quote_update": None}
    monkeypatch.setattr(be, "crawl_sport", lambda sport, **k: dict(bad))
    assert runner.main(["--sport", "nba"]) == 1
    assert mem_storage["Odds/bettingpros_live/nba/latest.json"] == b'{"keep": true}'
    monkeypatch.setattr(be, "crawl_sport", lambda sport, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert runner.main(["--sport", "nba"]) == 1
    assert mem_storage["Odds/bettingpros_live/nba/latest.json"] == b'{"keep": true}'
