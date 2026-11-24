"""tests/test_rfq_nfl_unlocks_rul20261123.py -- RUL-20261123 item 15 (Branch 2).

Covers:
- T-3 fix: NFL raw snapshot times come from the sweep filenames (the GCS objects were
  bulk-re-uploaded on 2026-11-12, collapsing every `updated` stamp into one 6-second
  window). Early-morning UTC sweeps belong to the folder's ET day via the NEXT UTC day.
- The snapshot clustering no longer collapses a day of sweeps into one snapshot.
- The BestBook ladder store builds NFL game-line legs through the live path's own
  conversion, stamped at fetched_at (point-in-time honest: silent before the stamp).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import ev.kalshi_rfq_replay as replay
from ev.kalshi_rfq_replay import _nfl_raw_capture_ts, raw_prop_snapshots, store_from_bestbook_ladders


def _epoch(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp()


def test_capture_ts_evening_sweep_same_utc_day():
    # Folder day = ET date; 23:01:42 UTC on Oct 24 is 19:01 ET Oct 24 -> same UTC day.
    key = "EVSweep/nfl_props/raw/2026-10-24/abc123_230142.json"
    assert _nfl_raw_capture_ts(key, 0.0) == _epoch(2026, 10, 24, 23, 1, 42)


def test_capture_ts_early_utc_sweep_belongs_to_previous_et_day():
    # 01:01:36 UTC on Oct 25 is 21:01 ET Oct 24 -> the folder day 2026-10-24 via the NEXT
    # UTC day (verified against the cycle frames' ts: 2026-10-25T01:01:36Z).
    key = "EVSweep/nfl_props/raw/2026-10-24/abc123_010136.json"
    assert _nfl_raw_capture_ts(key, 0.0) == _epoch(2026, 10, 25, 1, 1, 36)


def test_capture_ts_unparseable_name_falls_back():
    assert _nfl_raw_capture_ts("EVSweep/nfl_props/raw/2026-10-24/no_time_here.json", 42.0) == 42.0


def test_raw_snapshots_no_longer_collapse(monkeypatch):
    # Two sweep cycles on one folder day, every object stamped with the SAME bulk-upload
    # time (the 2026-11-12 re-upload). Pre-fix this produced ONE snapshot; the filename
    # times must produce two, stamped at the sweeps.
    upload_ts = _epoch(2026, 11, 12, 22, 31, 25)
    keys = [
        ("EVSweep/nfl_props/raw/2026-10-24/evA_010136.json", upload_ts),
        ("EVSweep/nfl_props/raw/2026-10-24/evB_010140.json", upload_ts),
        ("EVSweep/nfl_props/raw/2026-10-24/evA_050136.json", upload_ts),
        ("EVSweep/nfl_props/raw/2026-10-24/evB_050200.json", upload_ts),
    ]
    monkeypatch.setattr(replay, "_list_with_ts", lambda prefix, storage=None: keys)
    payload = [{"event_id": "evA", "commence_time": "2026-10-25T18:00:00Z",
                "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills", "bookmakers": []}]
    monkeypatch.setattr(replay, "_read_cached", lambda k, c, storage=None: json.dumps(payload).encode())
    snaps = raw_prop_snapshots("nfl", ["2026-10-24"])
    assert len(snaps) == 2
    # Cluster stamped at its last file's capture time. 05:02 UTC is 01:02 ET the same day,
    # so that cluster is the earlier one; the 01:01 UTC sweep belongs to Oct 25 UTC.
    assert snaps[0][0] == _epoch(2026, 10, 24, 5, 2, 0)
    assert snaps[1][0] == _epoch(2026, 10, 25, 1, 1, 40)


def test_bestbook_store_builds_nfl_game_legs():
    fetched = _epoch(2026, 11, 23, 12, 0, 0)
    games = [{
        "away_team": "BUF", "home_team": "KC", "commence_time": "2026-11-23T18:00:00+00:00",
        "quotes": [
            {"book": "draftkings", "market": "ml", "a": -130, "b": 110},
            {"book": "fanduel", "market": "ml", "a": -125, "b": 105},
            {"book": "draftkings", "market": "spread", "line": -2.5, "a": -110, "b": -110},
            {"book": "fanduel", "market": "spread", "line": -2.5, "a": -105, "b": -115},
            {"book": "draftkings", "market": "total", "line": 47.5, "a": -110, "b": -110},
            {"book": "fanduel", "market": "total", "line": 47.5, "a": -108, "b": -112},
        ],
    }]
    store = store_from_bestbook_ladders("nfl", [(fetched, games)])
    assert len(store) == 1
    # Silent before the stamp (point-in-time honest), live after it.
    assert store.book_at(fetched - 60.0, 7200.0) is None
    book = store.book_at(fetched + 60.0, 7200.0)
    assert book is not None
    ml = book.get("KXNFLGAME-26NOV23BUFKC-KC", fetched + 60.0)
    assert ml is not None and 0.0 < ml.p_cons_yes < 1.0 and ml.n_books == 2
    total = book.get("KXNFLTOTAL-26NOV23BUFKC-48", fetched + 60.0)
    assert total is not None
    spread = book.get("KXNFLSPREAD-26NOV23BUFKC-KC3", fetched + 60.0)
    assert spread is not None
