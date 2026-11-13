"""ev.alt_ladder_sink -- extraction of the discarded one-sided / ladder quote stream from the banked
EVSweep raw payloads into the rung-level sink. The event fixture is a REAL payload captured 2026-10-04
(Indianapolis @ Washington); Bovada posts true per-player milestone ladders in it. The Novig payload
is synthetic, shaped like Novig's ParlayAPI book (a true two-sided ladder) plus a Sleeper DFS ladder,
because the 2026-10-04 fixture predates Novig's presence in the payload."""
import json
import os
from datetime import datetime, timezone

from ev import alt_ladder_sink as sink

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ev")
EVENT = json.load(open(os.path.join(FIX, "nfl_props_event.json")))
QUOTE_TS = datetime(2026, 10, 4, 11, 0, 0, tzinfo=timezone.utc)

REQUIRED = {"source", "sport", "stat", "alt", "book", "is_dfs", "player", "player_norm", "player_id",
            "team_hint", "game_key", "event_id", "rung", "over_odds", "under_odds", "two_sided",
            "quote_ts", "commence_ts", "mins_to_kick", "raw_object"}


def _novig_payload():
    book = {"key": "novig", "markets": [{"key": "player_receiving_yards", "outcomes": []}]}
    for point, over, under in ((24.5, -110, -110), (49.5, 150, -180), (74.5, 320, -420)):
        book["markets"][0]["outcomes"] += [
            {"name": f"Over {point}", "description": "Test Receiver", "price": over, "point": point},
            {"name": f"Under {point}", "description": "Test Receiver", "price": under, "point": point},
        ]
    dfs = {"key": "sleeper", "markets": [{"key": "player_receiving_yards", "outcomes": []}]}
    for point in (39.5, 59.5, 79.5):
        dfs["markets"][0]["outcomes"] += [
            {"name": "Over Test Receiver", "description": "Test Receiver", "price": -112, "point": point},
            {"name": "Under Test Receiver", "description": "Test Receiver", "price": -112, "point": point},
        ]
    return {"id": "evt-novig", "commence_time": "2026-11-13T01:15:00Z",
            "home_team": "New York Giants", "away_team": "Washington Commanders",
            "bookmakers": [book, dfs]}


def test_fixture_ladder_and_onesided_quotes_land_with_all_fields():
    rows = sink.extract_rows(EVENT, QUOTE_TS, "raw/test.json")
    assert rows, "the fixture carries DFS/Bovada ladders; the sink must not be empty"
    for r in rows:
        assert REQUIRED <= set(r), f"row missing fields: {REQUIRED - set(r)}"
        assert r["commence_ts"] == "2026-10-04T13:30:00+00:00"     # every row carries the kickoff
        assert r["quote_ts"] == QUOTE_TS.isoformat()
        assert r["mins_to_kick"] == 150.0                          # pre-kickoff filter is computable
    books = {r["book"] for r in rows}
    assert "bovada" in books
    # Bovada's per-player milestone ladders (the fixture's true ladders; its DFS books quote
    # one line per player in this payload, so no DFS rows are expected from it)
    tay = [r for r in rows if r["book"] == "bovada" and r["player_norm"] == "jonathan taylor"
           and r["stat"] == "receptions"]
    assert sorted(r["rung"] for r in tay) == [2.5, 8.5, 9.5]


def test_onesided_quote_lands_with_missing_side_null():
    rows = sink.extract_rows(EVENT, QUOTE_TS)
    one = [r for r in rows if not r["two_sided"]]
    assert one, "the fixture has one-sided quotes (counted as n_onesided upstream)"
    for r in one:
        assert (r["over_odds"] is None) != (r["under_odds"] is None)


def test_main_line_only_books_do_not_land():
    rows = sink.extract_rows(EVENT, QUOTE_TS)
    # Caesars/DraftKings/Fanatics post a single two-sided main line per player in the fixture:
    # those quotes are already banked via the consensus path and must NOT be duplicated here
    # unless the specific quote is one-sided.
    for r in rows:
        if r["book"] in ("caesars", "draftkings", "fanatics"):
            assert not r["two_sided"], f"main-line quote leaked into the sink: {r}"


def test_novig_two_sided_ladder_lands_with_both_sides():
    rows = sink.extract_rows(_novig_payload(), QUOTE_TS)
    nov = [r for r in rows if r["book"] == "novig" and r["stat"] == "rec_yds"]
    assert sorted(r["rung"] for r in nov) == [24.5, 49.5, 74.5]
    for r in nov:
        assert r["two_sided"] and r["over_odds"] is not None and r["under_odds"] is not None
    top = next(r for r in nov if r["rung"] == 74.5)
    assert (top["over_odds"], top["under_odds"]) == (320, -420)
    # DFS milestone ladders land too, flagged as DFS (pick'em pricing, not book pricing)
    dfs = [r for r in rows if r["book"] == "sleeper"]
    assert sorted(r["rung"] for r in dfs) == [39.5, 59.5, 79.5]
    assert all(r["is_dfs"] for r in dfs)


def test_snapshot_ts_reconstructs_utc_instant_from_et_day_dir():
    assert sink.snapshot_ts("2026-11-12", "abc_200120.json") == \
        datetime(2026, 11, 12, 20, 1, 20, tzinfo=timezone.utc)
    # 00:30 UTC is 19:30 ET the previous evening, so a file stamped 003000 under ET day
    # 2026-11-12 was fetched at 00:30 UTC on 2026-11-13.
    assert sink.snapshot_ts("2026-11-12", "abc_003000.json") == \
        datetime(2026, 11, 13, 0, 30, 0, tzinfo=timezone.utc)


def test_extract_day_overwrites_day_file_and_sets_holds(monkeypatch):
    written, held = {}, []
    payload = json.dumps(_novig_payload()).encode()
    monkeypatch.setattr(sink, "list_raw_objects", lambda day: [f"EVSweep/nfl_props/raw/{day}/evt-novig_200120.json"])
    monkeypatch.setattr(sink, "read_object", lambda key: payload)
    monkeypatch.setattr(sink, "write_sink", lambda day, rows: written.update(day=day, rows=rows) or f"sink/{day}")
    monkeypatch.setattr(sink, "hold_object", lambda key: held.append(key) or True)
    stats = sink.extract_day("2026-11-12")
    assert stats["raw_files"] == 1 and stats["rows"] == 6 and stats["holds_set"] == 1
    assert held == ["EVSweep/nfl_props/raw/2026-11-12/evt-novig_200120.json"]
    assert written["day"] == "2026-11-12" and len(written["rows"]) == 6
    assert {r["quote_ts"] for r in written["rows"]} == {"2026-11-12T20:01:20+00:00"}
