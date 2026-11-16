"""ev.ownbettor.propmap (AUT-11): the verified Kalshi player-prop table.

Fixtures are real Kalshi API payloads captured Nov 16, 2026 (trimmed to the
fields the parser reads, values verbatim), plus mutations of them for the
reject/collision cases.  The doctrine under test: a mapping exists only where
Kalshi's own metadata asserts the whole identity; anything else stays
unmapped, including the collision classes that must never resolve.
"""
import json
from datetime import datetime, timezone

import pytest

from ev.ownbettor import propmap
from ev.ownbettor.propmap import PropTableResolver, build_table, prop_key
from ev.ownbettor.signals import Signal

# --------------------------------------------------------------- real fixtures
EVENT_PTS = {"event_ticker": "KXNBAPTS-26NOV16ORLBOS",
             "title": "Orlando vs Boston: Points", "sub_title": "ORL vs BOS (Nov 16)"}
EVENT_REB = {"event_ticker": "KXNBAREB-26NOV16ORLBOS",
             "title": "Orlando vs Boston: Rebounds", "sub_title": "ORL vs BOS (Nov 16)"}
EVENT_3PT = {"event_ticker": "KXNBA3PT-26NOV16ORLBOS",
             "title": "Orlando vs Boston: Threes", "sub_title": "ORL vs BOS (Nov 16)"}
EVENT_SAVE = {"event_ticker": "KXNHLSAVE-26NOV16VANUTA",
              "title": "Vancouver vs Utah: Saves", "sub_title": "VAN vs UTA (Nov 16)"}

MK_PTS = {
    "ticker": "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-25",
    "event_ticker": "KXNBAPTS-26NOV16ORLBOS",
    "title": "Anthony Black: 25+ points", "yes_sub_title": "Anthony Black: 25+",
    "floor_strike": 24.5, "strike_type": "greater",
    "custom_strike": {"basketball_player": "60bb439a-df6a-4fea-8312-4caa6a2c0fbe",
                      "basketball_team": "00c3e69d-d9b9-4ac3-bf79-19707fe43637"},
    "primary_participant_key": "basketball_player",
    "rules_primary": "If Anthony Black records 25+ points in the Orlando vs Boston "
                     "Pro Basketball game originally scheduled for Nov 16, 2026, "
                     "then the market resolves to Yes.",
    "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    "exchange_index": 3, "occurrence_datetime": "2026-11-17T03:00:00Z",
    "status": "active",
}
MK_PTS_20 = dict(MK_PTS,
                 ticker="KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-20",
                 title="Anthony Black: 20+ points", yes_sub_title="Anthony Black: 20+",
                 floor_strike=19.5,
                 rules_primary="If Anthony Black records 20+ points in the Orlando vs Boston "
                               "Pro Basketball game originally scheduled for Nov 16, 2026, "
                               "then the market resolves to Yes.")
MK_REB = {
    "ticker": "KXNBAREB-26NOV16ORLBOS-ORLJSUGGS4-8",
    "event_ticker": "KXNBAREB-26NOV16ORLBOS",
    "title": "Jalen Suggs: 8+ rebounds", "yes_sub_title": "Jalen Suggs: 8+",
    "floor_strike": 7.5, "strike_type": "greater",
    "custom_strike": {"basketball_player": "c382fa1a-0107-40fb-b0b7-03f1386f8797",
                      "basketball_team": "00c3e69d-d9b9-4ac3-bf79-19707fe43637"},
    "primary_participant_key": "basketball_player",
    "rules_primary": "If Jalen Suggs records 8+ rebounds in the Orlando vs Boston "
                     "Pro Basketball game originally scheduled for Nov 16, 2026, "
                     "then the market resolves to Yes.",
    "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    "exchange_index": 3, "occurrence_datetime": "2026-11-17T03:00:00Z",
    "status": "active",
}
MK_3PT = {
    "ticker": "KXNBA3PT-26NOV16ORLBOS-ORLWCARTER34-4",
    "event_ticker": "KXNBA3PT-26NOV16ORLBOS",
    "title": "Wendell Carter Jr.: 4+ threes", "yes_sub_title": "Wendell Carter Jr.: 4+",
    "floor_strike": 3.5, "strike_type": "greater",
    "custom_strike": {"basketball_player": "708d8ae3-92df-416c-9629-917d1d20edd6",
                      "basketball_team": "00c3e69d-d9b9-4ac3-bf79-19707fe43637"},
    "primary_participant_key": "basketball_player",
    "rules_primary": "If Wendell Carter Jr. records 4+ made three-pointers in the "
                     "Orlando vs Boston Pro Basketball game originally scheduled for "
                     "Nov 16, 2026, then the market resolves to Yes.",
    "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    "exchange_index": 3, "occurrence_datetime": "2026-11-17T03:00:00Z",
    "status": "active",
}
MK_SAVE = {
    "ticker": "KXNHLSAVE-26NOV16VANUTA-UTAKVEJMELKA70-19",
    "event_ticker": "KXNHLSAVE-26NOV16VANUTA",
    "title": "Karel Vejmelka: 19+ saves", "yes_sub_title": "Karel Vejmelka: 19+",
    "floor_strike": 18.5, "strike_type": "greater",
    "custom_strike": {"hockey_player": "a12a14aa-b7f5-42a5-a13e-e0a3d11618ad",
                      "hockey_team": "7d20591d-c6fa-4b85-8d69-ff05fb3649ac"},
    "primary_participant_key": "hockey_player",
    "rules_primary": "If Karel Vejmelka records 19+ saves in the Vancouver vs Utah "
                     "NHL game originally scheduled for Nov 16, 2026, then the "
                     "market resolves to Yes.",
    "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    "exchange_index": 0, "occurrence_datetime": "2026-11-17T05:30:00Z",
    "status": "active",
}

FEES = {"KXNBAPTS": 1.0, "KXNBAREB": 1.0, "KXNBA3PT": 1.0, "KXNHLSAVE": 1.0}
BUILT_AT = "2026-11-16T19:00:00Z"


def full_table():
    return build_table(
        {("nba", "points"): [MK_PTS, MK_PTS_20], ("nba", "rebounds"): [MK_REB],
         ("nba", "threes"): [MK_3PT], ("nhl", "saves"): [MK_SAVE]},
        {("nba", "points"): [EVENT_PTS], ("nba", "rebounds"): [EVENT_REB],
         ("nba", "threes"): [EVENT_3PT], ("nhl", "saves"): [EVENT_SAVE]},
        FEES, BUILT_AT)


def signal(**kw) -> Signal:
    base = dict(signal_id="sig-x", system="NBA_PTS", league="nba", stat="points",
                player="Anthony Black", event_id="ev1",
                matchup="Orlando Magic @ Boston Celtics", line=24.5, side="yes",
                p_hat=0.55, signal_price=0.50, limit_price_ceiling=0.51,
                net_ev_pct=9.0, game_day="2026-11-16",
                commence="2026-11-17T00:00:00+00:00", raw={})
    base.update(kw)
    return Signal(**base)


# --------------------------------------------------------------------- builder
def test_build_maps_all_four_families():
    t = full_table()
    assert t["entries"][prop_key("nba", "points", "anthony black", 24.5, "2026-11-16")]["ticker"] \
        == "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-25"
    assert t["entries"][prop_key("nba", "points", "anthony black", 19.5, "2026-11-16")]["ticker"] \
        == "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-20"
    assert t["entries"][prop_key("nba", "rebounds", "jalen suggs", 7.5, "2026-11-16")]["ticker"] \
        == "KXNBAREB-26NOV16ORLBOS-ORLJSUGGS4-8"
    # the Jr. suffix folds away under the stream's own normalization
    assert t["entries"][prop_key("nba", "threes", "wendell carter", 3.5, "2026-11-16")]["ticker"] \
        == "KXNBA3PT-26NOV16ORLBOS-ORLWCARTER34-4"
    assert t["entries"][prop_key("nhl", "saves", "karel vejmelka", 18.5, "2026-11-16")]["ticker"] \
        == "KXNHLSAVE-26NOV16VANUTA-UTAKVEJMELKA70-19"
    assert t["families"]["nhl|sog"]["series"] is None  # not listed, recorded honestly
    assert t["ambiguous"] == []


def test_reject_rules_mismatch():
    bad = dict(MK_PTS, rules_primary="If Anthony Black records 25+ points in the "
                                     "Orlando vs Boston Pro Basketball game originally "
                                     "scheduled for Nov 17, 2026, then the market resolves to Yes.")
    t = build_table({("nba", "points"): [bad]}, {("nba", "points"): [EVENT_PTS]}, FEES, BUILT_AT)
    assert t["entries"] == {}
    assert t["families"]["nba|points"]["rejected"] == {"rules_mismatch": 1}


def test_reject_strike_mismatch():
    bad = dict(MK_PTS, floor_strike=24.0)
    t = build_table({("nba", "points"): [bad]}, {("nba", "points"): [EVENT_PTS]}, FEES, BUILT_AT)
    assert t["families"]["nba|points"]["rejected"] == {"strike_mismatch": 1}
    bad2 = dict(MK_PTS, strike_type="greater_or_equal")
    t = build_table({("nba", "points"): [bad2]}, {("nba", "points"): [EVENT_PTS]}, FEES, BUILT_AT)
    assert t["families"]["nba|points"]["rejected"] == {"strike_mismatch": 1}


def test_reject_wrong_stat_word_for_family():
    # a points-shaped title inside the rebounds family is not a rebounds market
    t = build_table({("nba", "rebounds"): [dict(MK_PTS, event_ticker=EVENT_REB["event_ticker"])]},
                    {("nba", "rebounds"): [EVENT_REB]}, FEES, BUILT_AT)
    assert t["entries"] == {}
    assert t["families"]["nba|rebounds"]["rejected"] == {"title_shape": 1}


def test_reject_occurrence_from_another_night():
    bad = dict(MK_PTS, occurrence_datetime="2026-11-25T03:00:00Z")
    t = build_table({("nba", "points"): [bad]}, {("nba", "points"): [EVENT_PTS]}, FEES, BUILT_AT)
    assert t["families"]["nba|points"]["rejected"] == {"occurrence_date_mismatch": 1}


def test_collision_same_key_two_tickers_is_dropped():
    # the DEC-09 class: the same normalized player + line + day claimed by two
    # different markets (here a same-named player on the other team) -- the key
    # must vanish from the table, recorded as ambiguous, never resolve.
    twin = dict(MK_PTS, ticker="KXNBAPTS-26NOV16ORLBOS-BOSABLACK9-25")
    t = build_table({("nba", "points"): [MK_PTS, twin]},
                    {("nba", "points"): [EVENT_PTS]}, FEES, BUILT_AT)
    key = prop_key("nba", "points", "anthony black", 24.5, "2026-11-16")
    assert key not in t["entries"]
    assert t["ambiguous"] and t["ambiguous"][0]["key"] == key
    assert PropTableResolver(t).resolve(signal()) is None


# -------------------------------------------------------------------- resolver
def test_resolve_exact_hit():
    hit = PropTableResolver(full_table()).resolve(signal())
    assert hit is not None and hit.ticker == "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-25"
    assert hit.fee_multiplier == 1.0 and hit.exchange_index == 3
    assert hit.ranges == ((0.0, 1.0, 0.01),)


def test_resolve_suffix_name_and_under_side():
    r = PropTableResolver(full_table())
    hit = r.resolve(signal(system="NBA_THREES", stat="threes", player="Wendell Carter Jr.",
                           line=3.5, side="no"))
    assert hit is not None and hit.ticker == "KXNBA3PT-26NOV16ORLBOS-ORLWCARTER34-4"


def test_resolve_late_western_game_via_commence_date():
    # Utah home game: Kalshi's event date is the arena-local Nov 16 while the
    # signal's commence lands after ET midnight (05:30Z) and its game_day
    # stamp is the sighting day -- the candidate-date join still lands it.
    r = PropTableResolver(full_table())
    hit = r.resolve(signal(system="NHL_SAVES", league="nhl", stat="saves",
                           player="Karel Vejmelka", line=18.5,
                           matchup="Vancouver Canucks @ Utah Mammoth",
                           game_day="2026-11-16", commence="2026-11-17T02:30:00+00:00"))
    assert hit is not None and hit.ticker == "KXNHLSAVE-26NOV16VANUTA-UTAKVEJMELKA70-19"


def test_unmapped_when_line_not_carried():
    # Kalshi carries this player at 24.5/19.5 only; a 29.5 signal stays unmapped
    assert PropTableResolver(full_table()).resolve(signal(line=29.5)) is None


def test_unmapped_on_team_mismatch():
    r = PropTableResolver(full_table())
    assert r.resolve(signal(matchup="Orlando Magic @ Miami Heat")) is None
    assert r.resolve(signal(matchup="Boston Celtics @ Orlando Magic")) is not None  # pair, any order


def test_unmapped_family_not_listed():
    r = PropTableResolver(full_table())
    assert r.resolve(signal(system="NHL_SOG", league="nhl", stat="sog",
                            player="Karel Vejmelka", line=2.5,
                            matchup="Vancouver Canucks @ Utah Mammoth")) is None


def test_back_to_back_tie_broken_only_by_occurrence():
    ev17 = {"event_ticker": "KXNBAPTS-26NOV17ORLBOS", "title": "Orlando vs Boston: Points",
            "sub_title": "ORL vs BOS (Nov 17)"}
    mk17 = dict(MK_PTS, event_ticker=ev17["event_ticker"],
                ticker="KXNBAPTS-26NOV17ORLBOS-ORLABLACK0-25",
                occurrence_datetime="2026-11-18T03:00:00Z",
                rules_primary="If Anthony Black records 25+ points in the Orlando vs Boston "
                              "Pro Basketball game originally scheduled for Nov 17, 2026, "
                              "then the market resolves to Yes.")
    t = build_table({("nba", "points"): [MK_PTS, mk17]},
                    {("nba", "points"): [EVENT_PTS, ev17]}, FEES, BUILT_AT)
    r = PropTableResolver(t)
    # sighting stamped Nov 17 but the game is Nov 16's: both dates are
    # candidates, both entries match player/line/teams -- only the verified
    # occurrence offset may separate them, and it points at Nov 16's market.
    hit = r.resolve(signal(game_day="2026-11-17", commence="2026-11-17T00:00:00+00:00"))
    assert hit is not None and hit.ticker == "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-25"
    # with the occurrence evidence gone from both, the tie is unresolvable
    t2 = json.loads(json.dumps(t))
    for e in t2["entries"].values():
        e.pop("occurrence", None)
    assert PropTableResolver(t2).resolve(
        signal(game_day="2026-11-17", commence="2026-11-17T00:00:00+00:00")) is None


# ------------------------------------------------------------------ refresh I/O
class FakeFetcher:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def events(self, series, status="open"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return {"KXNBAPTS": [EVENT_PTS], "KXNBAREB": [EVENT_REB],
                "KXNBA3PT": [EVENT_3PT], "KXNHLSAVE": [EVENT_SAVE]}[series]

    def markets(self, series, status="open"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return {"KXNBAPTS": [MK_PTS], "KXNBAREB": [MK_REB],
                "KXNBA3PT": [MK_3PT], "KXNHLSAVE": [MK_SAVE]}[series]

    def series_fee_multiplier(self, series):
        self.calls += 1
        return 1.0


def _store(tmp_path):
    from ev.novig.shadow_bank import DirStore
    return DirStore(str(tmp_path / "store"))


def test_refresh_builds_banks_then_cache_serves(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 11, 16, 19, 30, tzinfo=timezone.utc)
    fetcher = FakeFetcher()
    table, status = propmap.load_or_refresh(store, now=now, fetcher=fetcher)
    assert status["source"] == "refresh" and status["entries"] == 4
    assert store.read(propmap.TABLE_KEY)  # banked
    calls = fetcher.calls
    table2, status2 = propmap.load_or_refresh(store, now=now, fetcher=fetcher)
    assert status2["source"] == "cache" and fetcher.calls == calls  # no refetch while fresh
    assert table2["entries"] == table["entries"]


def test_refresh_failure_falls_back_to_stale(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 11, 16, 19, 30, tzinfo=timezone.utc)
    table, _ = propmap.load_or_refresh(store, now=now, fetcher=FakeFetcher())
    later = datetime(2026, 11, 20, 19, 30, tzinfo=timezone.utc)  # stale by then
    table2, status = propmap.load_or_refresh(store, now=later, fetcher=FakeFetcher(fail=True))
    assert status["source"] == "stale_fallback" and table2["entries"] == table["entries"]


def test_refresh_failure_with_no_cache_is_empty_not_raised(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 11, 16, 19, 30, tzinfo=timezone.utc)
    table, status = propmap.load_or_refresh(store, now=now, fetcher=FakeFetcher(fail=True))
    assert status["source"] == "empty" and table["entries"] == {}


# ------------------------------------------------------- Los Angeles teams
def _la_table(teams, ticker):
    key = propmap.prop_key("nba", "rebounds", "brandin podziemski", 5.5, "2026-11-01")
    entry = {"ticker": ticker, "ranges": [[0.0, 1.0, 0.01]], "exchange_index": 3,
             "fee_multiplier": 1.0, "teams": teams,
             "occurrence": "2026-11-02T04:40:00Z"}
    return {"entries": {key: entry}}


def _la_signal(matchup):
    return signal(system="NBA_REB", stat="rebounds", player="Brandin Podziemski",
                  line=5.5, matchup=matchup, game_day="2026-11-01",
                  commence="2026-11-02T01:40:00+00:00")


def test_los_angeles_c_resolves_for_clippers_feed_spellings():
    table = _la_table(["Los Angeles C", "Golden State"],
                      "KXNBAREB-26NOV01LACGSW-GSWBPODZIEMSKI2-6")
    for matchup in ("L.A. Clippers @ Golden State Warriors",
                    "LA Clippers @ Golden State Warriors",
                    "Los Angeles Clippers @ Golden State Warriors"):
        hit = PropTableResolver(table).resolve(_la_signal(matchup))
        assert hit is not None and hit.ticker.endswith("GSWBPODZIEMSKI2-6"), matchup


def test_los_angeles_c_never_resolves_for_the_lakers():
    table = _la_table(["Los Angeles C", "Golden State"],
                      "KXNBAREB-26NOV01LACGSW-GSWBPODZIEMSKI2-6")
    for matchup in ("Los Angeles Lakers @ Golden State Warriors",
                    "LA Lakers @ Golden State Warriors",
                    "L.A. Lakers @ Golden State Warriors"):
        assert PropTableResolver(table).resolve(_la_signal(matchup)) is None, matchup


def test_los_angeles_l_resolves_for_lakers_not_clippers():
    key = propmap.prop_key("nba", "rebounds", "jakob poeltl", 5.5, "2026-11-01")
    entry = {"ticker": "KXNBAREB-26NOV01TORLAL-TORJPOELTL19-6",
             "ranges": [[0.0, 1.0, 0.01]], "exchange_index": 3,
             "fee_multiplier": 1.0, "teams": ["Toronto", "Los Angeles L"],
             "occurrence": "2026-11-02T05:10:00Z"}
    table = {"entries": {key: entry}}

    def sig(matchup):
        return signal(system="NBA_REB", stat="rebounds", player="Jakob Poeltl",
                      line=5.5, matchup=matchup, game_day="2026-11-01",
                      commence="2026-11-02T02:10:00+00:00")

    assert PropTableResolver(table).resolve(
        sig("Toronto Raptors @ Los Angeles Lakers")) is not None
    assert PropTableResolver(table).resolve(
        sig("Toronto Raptors @ LA Clippers")) is None
    assert PropTableResolver(table).resolve(
        sig("Toronto Raptors @ L.A. Clippers")) is None


# ------------------------------------------------------- paper-job integration
def test_paper_job_resolves_prop_signal_through_the_table(tmp_path):
    """The AUT-10 wiring: a FILLED prop signal that used to die as
    REJ_UNMAPPED now reaches the venue with the verified Kalshi ticker."""
    import gzip

    from mlb.execution.kalshi.book import Book

    from ev.novig.shadow_bank import DirStore
    from ev.ownbettor import paper_job
    from ev.ownbettor.audit import AuditLog
    from ev.ownbettor.config import BettorConfig
    from ev.ownbettor.venues.kalshi import StaticBookSource

    ticker = "KXNBAPTS-26NOV16ORLBOS-ORLABLACK0-25"
    row = {
        "signal_id": "sig-prop-1", "system": "NBA_PTS", "league": "nba",
        "matchup": "Orlando Magic @ Boston Celtics", "market_ticker": "ev1:points",
        "selection": "Anthony Black Over 24.5", "player": "Anthony Black",
        "player_norm": "anthony black", "stat": "points", "event_id": "ev1",
        "line": 24.5, "side": "yes",
        "gates_snapshot": {"p_hat": 0.545, "net_ev_pct": 9.0, "ev_kalshi_form_pct": 9.0,
                           "n_books": 5, "disp_mad": 0.01},
        "signal_price": 0.50, "limit_price_ceiling": 0.51,
        "venue": "novig", "venue_order_id": None, "status": "FILLED", "simulated": True,
        "game_day": "2026-11-16", "commence": "2026-11-17T00:00:00+00:00",
    }
    store = DirStore(str(tmp_path / "store"))
    payload = (json.dumps(row) + "\n").encode("utf-8")
    store.put(paper_job.LEDGER_KEY, gzip.compress(payload))
    book = Book(ticker, yes_bids=[(0.60, 50.0)], no_bids=[(0.50, 100.0)], fetched_at=0.0)
    cfg = BettorConfig(audit_dir=str(tmp_path / "audit"))
    summary = paper_job.run_job(store, book_source=StaticBookSource({ticker: book}),
                                cfg=cfg, prop_table=full_table(),
                                prop_status={"source": "test", "entries": 5})
    assert summary["signals_new"] == 1
    assert summary["outcomes"].get("REJ_UNMAPPED", 0) == 0
    assert summary["mapping"] == {"source": "test", "entries": 5}
    events = AuditLog(cfg.audit_path, mode="dry_run").all_events()
    intended = [e for e in events if e.get("event") == "order_intended"]
    assert intended and intended[0]["ticker"] == ticker
