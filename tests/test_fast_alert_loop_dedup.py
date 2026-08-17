"""
Regression test for the 2026-08-16 audit's fast_alert_loop.py fix (finding
C6.3): capped intraday alerts got marked "notified" even though they were
never posted -- dedup state was persisted from the uncapped `new` set, not
the `posted = new.head(max_posts)` subset actually sent to Discord. If a
single scan found more than max_posts new +EV legs at once (plausible right
after a lineup-news cascade -- exactly the highest-value moment for this
pager), the overflow was permanently blacklisted from re-consideration for
the rest of the day.

Fixed by persisting dedup state from `posted`, and carrying overflow rows
to a deferred.parquet that gets first priority on the next run.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
The identical fix in kalshi_alert.py shares this exact mechanism -- not
separately tested here to avoid duplicating this same end-to-end scenario.
"""
import io

import pandas as pd
import pytest

import mlb.runners.fast_alert_loop as fal


def _synthetic_found(n: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "market": "hr_yn", "game_pk": 1000 + i, "player_id": 500 + i,
        "line": 0.5, "selection": "OVER", "book": "draftkings",
        "ev": 0.10 - i * 0.01, "snapshot_ts": "2026-08-17 12:00:00",
    } for i in range(n)])


@pytest.fixture
def fake_storage(monkeypatch):
    store: dict[str, bytes] = {}

    def _read_bytes(key):
        if key not in store:
            raise FileNotFoundError(key)
        return store[key]

    def _write_bytes(data, key):
        store[key] = data

    monkeypatch.setattr(fal.storage, "read_bytes", _read_bytes)
    monkeypatch.setattr(fal.storage, "write_bytes", _write_bytes)
    return store


@pytest.fixture
def stub_environment(monkeypatch):
    monkeypatch.setenv("FAL_MAX_POSTS", "3")
    monkeypatch.setenv("FAL_SKIP_SNAPSHOT", "1")
    monkeypatch.setattr(fal, "lineup_events", lambda day: (set(), []))
    # Avoid a real network call to statsapi.mlb.com -- resolve_player_names
    # already fails gracefully offline, but don't rely on that by accident.
    monkeypatch.setattr(fal, "resolve_player_names", lambda ids: {})


def _run_with_found(monkeypatch, found_df, notify_spy):
    monkeypatch.setattr(
        "mlb.analysis.outlier_scan.scan_markets", lambda *a, **kw: found_df.copy()
    )
    monkeypatch.setattr(fal, "notify", notify_spy)
    return fal.run(run_date="2026-08-17")


def test_overflow_not_blacklisted_and_gets_priority_next_run(
    fake_storage, stub_environment, monkeypatch
):
    found = _synthetic_found(5)  # max_posts=3, so 2 will overflow each run
    posted_calls = []

    def _spy_notify(posted, *a, **kw):
        posted_calls.append(posted[["game_pk"]].copy())

    # --- Run 1: 5 candidates, only 3 fit -----------------------------------
    result1 = _run_with_found(monkeypatch, found, _spy_notify)
    assert result1["new_alerts"] == 5
    posted_run1 = set(posted_calls[-1]["game_pk"])
    assert len(posted_run1) == 3

    notified = fal._read_parquet("Alerts/2026-08-17/notified.parquet")
    assert notified is not None
    assert set(notified["game_pk"]) == posted_run1, (
        "notified.parquet must contain exactly the posted rows, not all 5 "
        "found rows (C6.3 regression -- overflow permanently blacklisted)"
    )

    deferred = fal._read_parquet("Alerts/2026-08-17/deferred.parquet")
    assert deferred is not None
    overflow_run1 = set(deferred["game_pk"])
    assert len(overflow_run1) == 2
    assert overflow_run1 == (set(found["game_pk"]) - posted_run1)

    # --- Run 2: same 5 candidates still present (odds haven't moved) ------
    # The 3 already-posted must be excluded by dedup; the 2 deferred from
    # run 1 must be posted FIRST this time (priority boost), not re-bumped
    # by whatever's left.
    result2 = _run_with_found(monkeypatch, found, _spy_notify)
    assert result2["new_alerts"] == 2, (
        f"expected only the 2 previously-overflowed rows to still be 'new' "
        f"(the other 3 are dedup'd), got {result2['new_alerts']}"
    )
    posted_run2 = set(posted_calls[-1]["game_pk"])
    assert posted_run2 == overflow_run1, (
        "the previously-deferred overflow rows were not prioritized/posted "
        "on the very next run (C6.3's 'retry next run' half of the fix)"
    )

    # And now every row has been posted exactly once, ever.
    notified2 = fal._read_parquet("Alerts/2026-08-17/notified.parquet")
    assert set(notified2["game_pk"]) == set(found["game_pk"])
