"""Offline tests for the BettingPros NFL/NHL/NBA prop crawler (long-format parser, resume, date logic)."""
from datetime import date

import pytest

from mlb.runners import backfill_bettingpros_props as runner
from mlb_core.odds import bettingpros as bp
from mlb_core.odds import bettingpros_props as bpp


def _line(cost, line=1.5, main=True, best=False, is_off=False):
    return {"cost": cost, "line": line, "main": main, "best": best, "is_off": is_off, "updated": "2025-12-11 00:31:36"}


def _offer(event_id=1, player="J.T. Miller", over_books=None, under_books=None, opening=True):
    def sel(side, books):
        s = {"selection": side, "label": side.title(),
             "books": [{"id": bid, "lines": lines} for bid, lines in books.items()]}
        if opening:
            s["opening_line"] = {"line": 1.5, "cost": -160, "book_id": 10, "created": "2025-12-09 23:48:47"}
        return s
    return {"event_id": event_id, "player_id": 15888,
            "participants": [{"id": "15888", "name": player,
                              "player": {"team": "NYR", "position": "C"}}],
            "selections": [sel("over", over_books or {}), sel("under", under_books or {})]}


EV = {1: {"away": "NYR", "home": "CHI", "scheduled": "2025-12-11 00:30:00", "season_type": "REG", "status": "closed"}}


def test_rows_keep_every_book_and_classify():
    offer = _offer(over_books={0: [_line(-194)], 10: [_line(-192)], 60: [_line(-110)], 38: [_line(-120)],
                               37: [_line(-115)], 2: [_line(-130)], 999: [_line(-105)]},
                   under_books={10: [_line(+150)]})
    rows = bpp.build_prop_rows("NHL", "shots", 321, "2025-12-10", [offer], EV)
    by_book = {(r["book_id"], r["side"], r["is_open"]): r for r in rows}
    assert by_book[(60, "over", False)]["book_kind"] == "exchange"     # Novig kept (MLB's trimmed map dropped it)
    assert by_book[(38, "over", False)]["book_kind"] == "exchange"     # ProphetX
    assert by_book[(37, "over", False)]["book_kind"] == "dfs"
    assert by_book[(2, "over", False)]["book_kind"] == "sharp"
    assert by_book[(0, "over", False)]["book_kind"] == "consensus"
    assert by_book[(999, "over", False)]["book"] == "book_999"          # unknown id is kept, never dropped
    assert by_book[(10, "under", False)]["odds"] == 150
    assert {r["side"] for r in rows} == {"over", "under"}
    r = rows[0]
    assert (r["away"], r["home"], r["season_type"], r["player"], r["team"]) == ("NYR", "CHI", "REG", "J.T. Miller", "NYR")


def test_opening_line_becomes_one_open_row_per_side():
    rows = bpp.build_prop_rows("NHL", "shots", 321, "d", [_offer(over_books={10: [_line(-192)]})], EV)
    opens = [r for r in rows if r["is_open"]]
    assert len(opens) == 2 and all(r["book_kind"] == "open" and r["book"] == "FanDuel" for r in opens)


def test_off_board_and_null_quotes_are_skipped():
    offer = _offer(opening=False, over_books={10: [_line(-110, is_off=True), _line(None)], 12: [_line(-105)]})
    rows = bpp.build_prop_rows("NHL", "shots", 321, "d", [offer], EV)
    assert [r["book_id"] for r in rows] == [12]


def test_multiple_lines_per_book_each_emitted():
    offer = _offer(opening=False, over_books={10: [_line(-110, 1.5), _line(+120, 2.5, main=False)]})
    rows = bpp.build_prop_rows("NHL", "shots", 321, "d", [offer], EV)
    assert sorted(r["line"] for r in rows) == [1.5, 2.5]


def test_frame_schema_and_empty_frame():
    df = bpp.to_frame([])
    assert list(df.columns) == bpp.COLUMNS and len(df) == 0
    df2 = bpp.to_frame(bpp.build_prop_rows("NHL", "shots", 321, "d", [_offer(over_books={10: [_line(-192)]})], EV))
    assert str(df2["odds"].dtype) == "Int64" and str(df2["line"].dtype) == "float64"


def test_crawl_dates_season_months_and_order():
    d = bpp.crawl_dates("NHL", "2023-06-20", "2023-10-03", newest_first=True)
    assert d[0] == "2023-10-03" and d[-1] == "2023-10-01" or d[-1] >= "2023-06-20"
    assert all(int(x[5:7]) in bpp.SPORT_MONTHS["NHL"] for x in d)          # July-Sept skipped
    assert "2023-07-15" not in d
    assert bpp.crawl_dates("NFL", "2019-01-01", "2020-12-31") == []        # clamped before SPORT_START
    asc = bpp.crawl_dates("NBA", "2025-11-01", "2025-11-05", newest_first=False)
    assert asc == sorted(asc) and len(asc) == 5


def test_is_closed_day_excludes_today():
    assert bpp.is_closed_day("2026-10-13", today=date(2026, 10, 14))
    assert not bpp.is_closed_day("2026-10-14", today=date(2026, 10, 14))


def test_resolve_markets_uses_live_catalog_and_skips_missing(monkeypatch):
    catalog = {"markets": [
        {"id": 321, "slug": "shots", "category": "player-props", "period": "game"},
        {"id": 999, "slug": "most-shots", "category": "player-props", "period": "game"},
        {"id": 318, "slug": "goals", "category": "player-props", "period": "1st-period"},   # wrong period
    ]}
    monkeypatch.setattr(bp, "api_get", lambda s, p, params: catalog)
    got = bpp.resolve_markets(None, "NHL")
    assert got == {"sog": {"slug": "shots", "market_id": 321}}


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(bpp.time, "sleep", lambda s: None)
    return tmp_path


def _fake_api(calls):
    def api_get(sess, path, params):
        calls.append((path, dict(params)))
        if path == "markets":
            return {"markets": [{"id": 321, "slug": "shots", "category": "player-props", "period": "game"}]}
        if path == "events":
            if params["date"] == "2025-12-09":
                return {"events": []}
            return {"events": [{"id": 1, "home": "CHI", "visitor": "NYR", "scheduled": "2025-12-11 00:30:00",
                                "season_type": "REG", "status": "closed"}]}
        if path == "offers":
            page = params["page"]
            o = _offer(player=f"P{page}", over_books={10: [_line(-110)]})
            o["player_id"] = 100 + page
            return {"offers": [o],
                    "_pagination": {"total_pages": 2}}
        raise AssertionError(path)
    return api_get


def test_crawl_paginates_writes_markers_and_resumes(local_store, monkeypatch):
    calls = []
    monkeypatch.setattr(bp, "api_get", _fake_api(calls))
    prefix = bpp.default_prefix("NHL")
    kw = dict(wanted=["sog"], prefix=prefix, delay=0, market_sleep=0, date_sleep=0, max_dates=0,
              newest_first=True, sess=object())
    stats = runner.crawl("NHL", "2025-12-09", "2025-12-10", **kw)
    assert stats == {"dates": 1, "rows": 6, "errors": 0, "empty_days": 1}   # 2 pages x (2 open rows + 1 book row)
    # the empty day has a 0-row marker so it is never re-asked
    assert bpp.done_dates(prefix, "shots") == {"2025-12-09", "2025-12-10"}
    df = bpp.read_props("NHL", "shots")
    assert set(df["player"]) == {"P1", "P2"}
    n_calls = len(calls)
    runner.crawl("NHL", "2025-12-09", "2025-12-10", **kw)
    assert [c for c in calls[n_calls:] if c[0] in ("events", "offers")] == []   # resumed: nothing re-fetched


def test_crawl_never_writes_today(local_store, monkeypatch):
    monkeypatch.setattr(bp, "api_get", _fake_api([]))
    today = date.today().isoformat()
    stats = runner.crawl("NHL", today, today, wanted=["sog"], prefix=bpp.default_prefix("NHL"), delay=0,
                         market_sleep=0, date_sleep=0, max_dates=0, newest_first=True, sess=object())
    assert stats["dates"] == 0 and bpp.done_dates(bpp.default_prefix("NHL"), "shots") == set()


def test_crawl_error_does_not_mark_done_and_counts(local_store, monkeypatch):
    def boom(sess, path, params):
        if path == "markets":
            return {"markets": [{"id": 321, "slug": "shots", "category": "player-props", "period": "game"}]}
        raise RuntimeError("429 giving up")
    monkeypatch.setattr(bp, "api_get", boom)
    stats = runner.crawl("NHL", "2025-12-10", "2025-12-10", wanted=["sog"], prefix=bpp.default_prefix("NHL"),
                         delay=0, market_sleep=0, date_sleep=0, max_dates=0, newest_first=True, sess=object())
    assert stats["errors"] == 1 and bpp.done_dates(bpp.default_prefix("NHL"), "shots") == set()


def test_read_props_dedups_event_seen_under_two_date_partitions(local_store):
    prefix = bpp.default_prefix("NHL")
    rows = bpp.build_prop_rows("NHL", "shots", 321, "2025-12-10", [_offer(over_books={10: [_line(-192)]})], EV)
    later = bpp.build_prop_rows("NHL", "shots", 321, "2025-12-11", [_offer(over_books={10: [_line(-200)]})], EV)
    bpp.write_day(prefix, "shots", "2025-12-10", rows)
    bpp.write_day(prefix, "shots", "2025-12-11", later)
    df = bpp.read_props("NHL", "shots")
    fd_over = df[(df.book_id == 10) & (df.side == "over") & (~df.is_open)]
    assert len(fd_over) == 1 and int(fd_over.odds.iloc[0]) == -200      # later partition wins
    assert len(df) == len(df.drop_duplicates(subset=bpp.DEDUP_KEY))
