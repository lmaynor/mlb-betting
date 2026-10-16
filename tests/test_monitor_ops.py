"""monitor_ops: multi-sport scheduler coverage (enabled jobs alert, PAUSED jobs never do) + the NFL refresh sentinel."""
import json
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mlb.runners import monitor_ops as mo

ENABLED, PAUSED = "ENABLED", "PAUSED"


def _job(name, code, state=ENABLED, attempt=datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)):
    return SimpleNamespace(name=f"projects/p/locations/us-central1/jobs/{name}", state=state,
                           status=SimpleNamespace(code=code), last_attempt_time=attempt)


@pytest.fixture
def fake_scheduler(monkeypatch):
    """Inject a stand-in for google.cloud.scheduler_v1 (not installed in every env) serving `fake.jobs`."""
    fake = types.ModuleType("google.cloud.scheduler_v1")
    fake.jobs = []
    fake.Job = SimpleNamespace(State=SimpleNamespace(PAUSED=PAUSED, ENABLED=ENABLED))

    class Client:
        def list_jobs(self, parent):
            return list(fake.jobs)
    fake.CloudSchedulerClient = Client
    import google.cloud
    monkeypatch.setitem(sys.modules, "google.cloud.scheduler_v1", fake)
    monkeypatch.setattr(google.cloud, "scheduler_v1", fake, raising=False)
    return fake


def test_enabled_multisport_schedulers_alert_on_nonzero_status(fake_scheduler):
    fake_scheduler.jobs = [
        _job("mlb-settle", 0),
        _job("nfl-refresh-data", 13),
        _job("nhl-refresh-data", 7),
        _job("nba-refresh-data", 2),
        _job("nba-espn-players", 14),
        _job("odds-bank-nhl-1500", 13),
        _job("props-alerts-nba-1530", 7),
        _job("props-grade-nhl-0900", 2),
        _job("ev-nfl-props-sweep", 13),
        _job("never-run-yet", -1),
    ]
    out = mo._check_schedulers()
    flagged = sorted(m.split("`")[1] for m in out)
    assert flagged == ["ev-nfl-props-sweep", "nba-espn-players", "nba-refresh-data", "nfl-refresh-data",
                       "nhl-refresh-data", "odds-bank-nhl-1500", "props-alerts-nba-1530", "props-grade-nhl-0900"]
    assert any("code=13" in m and "nfl-refresh-data" in m for m in out)


def test_paused_schedulers_never_alert_even_with_a_failing_last_status(fake_scheduler):
    fake_scheduler.jobs = [
        _job("odds-bank-nhl-1500", 13, state=PAUSED),           # intentionally paused off-season job
        _job("props-alerts-nhl-1530", 7, state=PAUSED),
        _job("nfl-refresh-data", 13, state=ENABLED),
    ]
    out = mo._check_schedulers()
    assert len(out) == 1 and "nfl-refresh-data" in out[0]
    fake_scheduler.jobs = [_job("props-alerts-nhl-1530", 7, state=PAUSED)]
    assert mo._check_schedulers() == []                          # resumed later -> alerts again (state flips to ENABLED)
    fake_scheduler.jobs = [_job("props-alerts-nhl-1530", 7, state=ENABLED)]
    assert len(mo._check_schedulers()) == 1


def test_scheduler_api_error_is_reported_not_raised(fake_scheduler):
    def boom(parent):
        raise RuntimeError("permission denied")
    fake_scheduler.CloudSchedulerClient.list_jobs = lambda self, parent: boom(parent)
    out = mo._check_schedulers()
    assert out and out[0].startswith("Scheduler API error")


def _sentinel(monkeypatch, payload):
    """payload None -> object absent; an Exception instance -> read raises; else the JSON dict."""
    monkeypatch.setattr("mlb_core.storage.exists", lambda key: key == mo.NFL_SENTINEL_KEY and payload is not None)

    def read_bytes(key):
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()
    monkeypatch.setattr("mlb_core.storage.read_bytes", read_bytes)


def test_nfl_sentinel_error_is_reported_with_failure_detail(monkeypatch):
    _sentinel(monkeypatch, {"status": "error", "timestamp": "2026-10-14T13:00:00+00:00",
                            "failures": [{"item": "player_stats_2026", "status": "failed", "error": "HTTP 503 from nflverse"}]})
    out = mo._check_nfl_sentinel()
    assert len(out) == 1 and "NFL refresh failed" in out[0] and "player_stats_2026" in out[0] and "HTTP 503" in out[0]


@pytest.mark.parametrize("payload", [{"status": "ok"}, {"status": "partial", "failures": [{"item": "x"}]}, None,
                                      RuntimeError("gcs down")])
def test_nfl_sentinel_is_silent_unless_status_is_error(monkeypatch, payload):
    _sentinel(monkeypatch, payload)
    assert mo._check_nfl_sentinel() == []


def test_run_includes_the_nfl_sentinel_and_posts_one_alert(monkeypatch):
    for name in ("_check_schedulers", "_check_odds_history_freshness", "_check_data_masters",
                 "_check_snapshot_freshness", "_check_build_sentinels", "_check_stuck_bets",
                 "_check_feature_freshness", "_check_model_artifacts"):
        monkeypatch.setattr(mo, name, lambda: [])
    posted = []
    monkeypatch.setattr(mo, "_post_ops_alert", lambda failures, run_date: posted.append(failures))
    _sentinel(monkeypatch, {"status": "ok"})
    assert mo.run("2026-10-14")["healthy"] is True and posted == []
    _sentinel(monkeypatch, {"status": "error"})
    out = mo.run("2026-10-14")
    assert out["healthy"] is False and len(out["failures"]) == 1 and posted == [out["failures"]]


def test_monitor_ops_source_is_ascii_only():
    with open(mo.__file__, "rb") as fh:
        fh.read().decode("ascii")                                # raises UnicodeDecodeError on any raw multibyte char


def _contracts(monkeypatch, payload):
    monkeypatch.setattr("mlb_core.storage.exists", lambda key: key == mo.NFL_CONTRACTS_KEY and payload is not None)

    def read_bytes(key):
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()
    monkeypatch.setattr("mlb_core.storage.read_bytes", read_bytes)


def test_nfl_contracts_failure_is_reported_with_the_failing_checks(monkeypatch):
    _contracts(monkeypatch, {"ok": False, "season": 2026, "generated_at": "2026-10-16T13:40:00+00:00", "checks": [
        {"name": "grain_schedules", "status": "pass", "detail": {}},
        {"name": "coverage_pbp", "status": "fail", "detail": {"missing": 2, "missing_games": ["2026_05_X_Y"]}},
        {"name": "schedule_lines_upcoming", "status": "warn", "detail": {}}]})
    out = mo._check_nfl_contracts()
    assert len(out) == 1 and "FAILED (1 check(s)" in out[0] and "coverage_pbp" in out[0]
    assert "grain_schedules" not in out[0] and "schedule_lines_upcoming" not in out[0]


@pytest.mark.parametrize("payload", [{"ok": True, "checks": [{"name": "x", "status": "warn"}]}, None,
                                      RuntimeError("gcs down")])
def test_nfl_contracts_are_silent_when_ok_missing_or_unreadable(monkeypatch, payload):
    _contracts(monkeypatch, payload)
    assert mo._check_nfl_contracts() == []
