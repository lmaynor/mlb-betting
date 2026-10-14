"""Offline tests for the ParlayAPI historical closing-line backfill (budget guard, paging, resume)."""
import json

import pytest

from nba.odds import parlay_historical as ph


class Resp:
    def __init__(self, rows, status=200, headers=None):
        self.status_code, self._rows, self.headers = status, rows, headers or {}
        self.text = ""

    def json(self):
        return self._rows


class FakeSession:
    """Serves `pages` (list of row-lists) by offset; records every call. Each call 'costs' 10 credits."""

    def __init__(self, pages, remaining=15000, status=200):
        self.pages, self.remaining, self.status, self.calls = pages, remaining, status, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params))
        self.remaining -= 10
        hdr = {"x-requests-last": "10", "x-requests-remaining": str(self.remaining)}
        if self.status != 200:
            return Resp([], self.status, hdr)
        idx = params["offset"] // max(1, len(self.pages[0])) if self.pages and self.pages[0] else 0
        return Resp(self.pages[idx] if idx < len(self.pages) else [], 200, hdr)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    return tmp_path


def _rows(n, book="novig"):
    return [{"bookmaker": book, "player": f"p{i}", "market_key": "player_goals", "line": 0.5,
             "over_odds": -110, "under_odds": -110} for i in range(n)]


def test_single_page_writes_raw_and_done_marker(store):
    s, b = FakeSession([_rows(3)]), ph.Budget(100, 0)
    out = ph.backfill_day(s, "k", "icehockey_nhl", "2026-10-13", "props", b)
    assert out["status"] == "ok" and out["rows"] == 3 and out["pages"] == 1 and b.spent == 10
    key = ph.history_key("icehockey_nhl", "2026-10-13", "props", "p000")
    assert len(json.loads((store / key).read_text())) == 3
    assert (store / ph.history_key("icehockey_nhl", "2026-10-13", "props", "done")).exists()
    p = s.calls[0]
    assert p["bookmakers"] == ph.PROP_BOOKS and "player_goals" in p["markets"] and p["date"] == "2026-10-13"


def test_pages_until_short_page_and_offsets_advance(store, monkeypatch):
    monkeypatch.setattr(ph, "PAGE_LIMIT", 4)
    s = FakeSession([_rows(4), _rows(4), _rows(1)])
    out = ph.backfill_day(s, "k", "americanfootball_nfl", "2026-10-11", "props", ph.Budget(1000, 0))
    assert out["pages"] == 3 and out["rows"] == 9
    assert [c["offset"] for c in s.calls] == [0, 4, 8]


def test_done_day_is_skipped_without_spending(store):
    b = ph.Budget(100, 0)
    ph.backfill_day(FakeSession([_rows(2)]), "k", "icehockey_nhl", "2026-10-13", "props", b)
    s2 = FakeSession([_rows(2)])
    assert ph.backfill_day(s2, "k", "icehockey_nhl", "2026-10-13", "props", b)["status"] == "skipped_done"
    assert s2.calls == []


def test_run_cap_stops_before_overspending(store):
    b = ph.Budget(10, 0)
    s = FakeSession([_rows(2)])
    assert ph.backfill_day(s, "k", "icehockey_nhl", "2026-10-12", "props", b)["status"] == "ok"
    out = ph.backfill_day(s, "k", "icehockey_nhl", "2026-10-11", "props", b)
    assert out["status"] == "budget_stop" and "cap" in out["reason"] and len(s.calls) == 1
    assert not (store / ph.history_key("icehockey_nhl", "2026-10-11", "props", "done")).exists()


def test_shared_pool_floor_stops(store):
    b = ph.Budget(1000, 2500)
    b.remaining = 2505
    out = ph.backfill_day(FakeSession([_rows(2)]), "k", "icehockey_nhl", "2026-10-12", "props", b)
    assert out["status"] == "budget_stop" and "floor" in out["reason"]


def test_game_lines_use_default_book_and_game_markets(store):
    s = FakeSession([_rows(2, "pinnacle")])
    ph.backfill_day(s, "k", "americanfootball_nfl", "2026-10-12", "game_lines", ph.Budget(100, 0))
    assert s.calls[0]["markets"] == ph.GAME_MARKETS and "bookmakers" not in s.calls[0]


def test_http_error_raises(store):
    with pytest.raises(RuntimeError):
        ph.fetch_page(FakeSession([], status=422), "k", "icehockey_nhl", "2026-09-01", "player_goals", None, 0)


def test_plan_excludes_today_and_covers_every_sport_kind():
    calls = ph.plan(list(ph.PROP_MARKETS), 7, True)
    assert len(calls) == 7 * 3 * 2
    from datetime import date
    assert all(d != date.today().isoformat() for _, d, _ in calls)


def test_nfl_market_aliases_cover_pinnacle_and_novig_spellings():
    nfl = ph.PROP_MARKETS["americanfootball_nfl"]
    assert {"player_rec_yds", "player_receiving_yards", "player_reception_yds"} <= set(nfl)
