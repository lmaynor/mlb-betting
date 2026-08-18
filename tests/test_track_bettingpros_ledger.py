"""
Regression tests for the 2026-08-16 audit's track_bettingpros.py shared
call-ledger fix (finding B3.8): this file's own docstring documented "5x/day
free tier," but fast_alert_loop.py independently calls run() on its own
*/15 cadence with no shared counter or cross-awareness between the two
callers -- true combined volume is ~34x/day against a free public API with
no formal quota. Added a shared daily-call ledger (mirrors
snapshot_odds.py's _credits/{month}.json pattern) and a backoff circuit
breaker if BettingPros has been erroring a lot lately.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

import mlb.runners.track_bettingpros as tbp

# run()'s ledger_day is always the REAL wall-clock date (`now.date()`), not
# derived from the `run_date` argument -- by design, since a free-tier API
# quota resets on real calendar days regardless of which game-date is being
# backfilled. These tests used to hardcode a literal date string standing in
# for "today," which rotted the instant the calendar moved past it. Compute
# "today" the same way the code does instead, so the tests stay valid on
# any date they happen to run.
TODAY = datetime.now(timezone.utc).date().isoformat()
YESTERDAY = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))


@pytest.fixture
def stub_bp_noop(monkeypatch):
    """No real BettingPros calls, no real rows -- isolates the ledger
    bookkeeping from the actual fetch/parse pipeline."""
    monkeypatch.setattr(tbp.bp, "make_session", lambda: object())
    monkeypatch.setattr(tbp.bp, "resolve_markets", lambda arg: [])
    fetch_calls = []
    monkeypatch.setattr(tbp.bp, "fetch_events",
                        lambda sess, ds: (fetch_calls.append(ds), {})[1])
    return fetch_calls


def test_run_increments_the_shared_ledger(local_storage, stub_bp_noop):
    r1 = tbp.run(run_date=TODAY)
    assert r1["calls_today"] == 1
    r2 = tbp.run(run_date=TODAY)
    assert r2["calls_today"] == 2

    ledger = tbp._read_call_ledger(TODAY)
    assert ledger["calls"] == 2
    assert ledger["recent_outcomes"] == ["ok", "ok"]


def test_backoff_skips_the_run_without_touching_bettingpros(local_storage, stub_bp_noop):
    fetch_calls = stub_bp_noop
    tbp._write_call_ledger(TODAY, {
        "date": TODAY, "calls": 5,
        "recent_outcomes": ["error", "error", "error", "error", "error"],
    })

    result = tbp.run(run_date=TODAY)

    assert result["status"] == "skipped_backoff"
    assert fetch_calls == [], "backoff must return before making any real BettingPros calls"


def test_no_backoff_with_too_few_recent_calls_to_judge(local_storage, stub_bp_noop):
    """Even 100% errors shouldn't trip the breaker on a tiny sample --
    _BACKOFF_MIN_CALLS guards against overreacting to 1-2 blips."""
    tbp._write_call_ledger(TODAY, {
        "date": TODAY, "calls": 2, "recent_outcomes": ["error", "error"],
    })

    result = tbp.run(run_date=TODAY)
    assert result["status"] != "skipped_backoff"


def test_no_backoff_when_errors_are_a_minority(local_storage, stub_bp_noop):
    tbp._write_call_ledger(TODAY, {
        "date": TODAY, "calls": 10,
        "recent_outcomes": ["ok", "ok", "ok", "error", "ok", "ok", "ok", "ok"],
    })

    result = tbp.run(run_date=TODAY)
    assert result["status"] != "skipped_backoff"


def test_backoff_flag_env_var_forces_the_run_through_anyway(local_storage, stub_bp_noop, monkeypatch):
    monkeypatch.setenv("BP_SKIP_BACKOFF", "1")
    tbp._write_call_ledger(TODAY, {
        "date": TODAY, "calls": 5,
        "recent_outcomes": ["error"] * 5,
    })

    result = tbp.run(run_date=TODAY)
    assert result["status"] != "skipped_backoff"


def test_ledger_resets_for_a_new_day(local_storage, stub_bp_noop):
    tbp._write_call_ledger(YESTERDAY, {
        "date": YESTERDAY, "calls": 40, "recent_outcomes": ["ok"] * 10,
    })
    result = tbp.run(run_date=TODAY)
    assert result["calls_today"] == 1, "a new day must start its own ledger, not inherit yesterday's count"
