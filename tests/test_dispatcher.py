"""Tests for the OPS-15 scheduler dispatcher (ev/dispatcher/).

No network: the Run API is faked. The equivalence-critical pieces are the
cron semantics (cron.py) and the due-entry selection against the real
checked-in table (table.json).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ev.dispatcher.cron import Cron, parse_field
from ev.dispatcher.generate_table import build_table
from ev.dispatcher.run import audit, due_entries, fire_due, intended_fires, load_table

UTC = timezone.utc
CT = ZoneInfo("America/Chicago")
TABLE = load_table()


# ---------------------------------------------------------------- cron fields

def test_parse_field_forms():
    assert parse_field("*", 0, 59) == frozenset(range(60))
    assert parse_field("5", 0, 59) == frozenset({5})
    assert parse_field("1,2,3", 0, 59) == frozenset({1, 2, 3})
    assert parse_field("9-12", 0, 59) == frozenset({9, 10, 11, 12})
    assert parse_field("*/30", 0, 59) == frozenset({0, 30})
    assert parse_field("10-20/2", 0, 59) == frozenset({10, 12, 14, 16, 18, 20})
    assert parse_field("5/20", 0, 59) == frozenset({5, 25, 45})
    with pytest.raises(ValueError):
        parse_field("60", 0, 59)
    with pytest.raises(ValueError):
        parse_field("*/0", 0, 59)


def test_cron_dom_dow_or_rule():
    # Both restricted: either matches. Nov 2026: the 15th is a Sunday.
    cron = Cron("0 12 15 * 1")  # noon on the 15th OR on Mondays
    assert cron.matches_local(datetime(2026, 11, 15, 12, 0))  # Sunday the 15th
    assert cron.matches_local(datetime(2026, 11, 16, 12, 0))  # Monday the 16th
    assert not cron.matches_local(datetime(2026, 11, 17, 12, 0))  # Tuesday 17th


def test_cron_dow_seven_is_sunday():
    assert Cron("0 8 * * 0").matches_local(datetime(2026, 11, 15, 8, 0))
    assert Cron("0 8 * * 7").matches_local(datetime(2026, 11, 15, 8, 0))
    assert not Cron("0 8 * * 7").matches_local(datetime(2026, 11, 16, 8, 0))


def test_cron_matches_across_timezones_and_dst_seasons():
    cron = Cron("0 6 * * *")  # 06:00 America/Chicago daily
    assert cron.matches(datetime(2026, 11, 16, 12, 0, tzinfo=UTC), CT)   # CST
    assert cron.matches(datetime(2026, 7, 16, 11, 0, tzinfo=UTC), CT)    # CDT
    assert not cron.matches(datetime(2026, 11, 16, 11, 0, tzinfo=UTC), CT)


def test_expand_skips_spring_forward_gap_and_fires_fall_back_once():
    gap = Cron("30 2 * * *").expand(
        CT, datetime(2027, 3, 14, tzinfo=UTC), datetime(2027, 3, 15, tzinfo=UTC))
    assert gap == []  # 02:30 does not exist on 2027-03-14 in Chicago
    fold = Cron("30 1 * * *").expand(
        CT, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))
    assert len(fold) == 1  # ambiguous 01:30 fires once


def test_expand_sunday_burst_count():
    # novig-paper-executor-sweep-sun-rest shape: minutes at :10 across
    # 0,2,4,12-20,22 UTC on Sundays = 13 fires.
    fires = Cron("10 0,2,4,12-20,22 * * 0").expand(
        ZoneInfo("Etc/UTC"),
        datetime(2026, 11, 15, tzinfo=UTC), datetime(2026, 11, 16, tzinfo=UTC))
    assert len(fires) == 13


# ------------------------------------------------------- table generation

def _fake_dump(**overrides):
    base = {
        "schedule": "0 6 * * *",
        "timeZone": "America/Chicago",
        "state": "ENABLED",
        "httpTarget": {
            "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/demo:run",
        },
    }
    base.update(overrides)
    return {"demo-daily": base}


def _decisions():
    return {
        "source": "test",
        "rule": "test",
        "entries": [{
            "scheduler": "demo-daily", "job": "demo", "cron": "0 6 * * *",
            "timezone": "America/Chicago", "wave": 1, "wave_note": "w",
        }],
        "excluded": [],
    }


def test_build_table_happy_path():
    table = build_table(_fake_dump(), _decisions())
    assert [e["scheduler"] for e in table["entries"]] == ["demo-daily"]
    assert table["grid_minutes_utc"] == [0]


def test_build_table_rejects_drift():
    with pytest.raises(SystemExit):
        build_table(_fake_dump(state="PAUSED"), _decisions())
    with pytest.raises(SystemExit):
        build_table(_fake_dump(schedule="0 7 * * *"), _decisions())
    body = {"httpTarget": {
        "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/demo:run",
        "body": "e30="}}
    with pytest.raises(SystemExit):
        build_table(_fake_dump(**body), _decisions())


# ------------------------------------------------------- due selection (real table)

def test_due_entries_real_table():
    at = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Mon 06:00 CT
    names = {e["scheduler"] for e in due_entries(TABLE, at)}
    assert "closing-panel-daily" in names
    at = datetime(2026, 11, 15, 12, 30, tzinfo=UTC)
    names = {e["scheduler"] for e in due_entries(TABLE, at)}
    assert "props-grade-nhl-1" in names
    at = datetime(2026, 11, 15, 17, 35, tzinfo=UTC)
    names = {e["scheduler"] for e in due_entries(TABLE, at)}
    assert "props-alerts-nhl-1" in names
    # Nothing is due at an off-grid minute.
    at = datetime(2026, 11, 16, 12, 7, tzinfo=UTC)
    assert due_entries(TABLE, at) == []


def test_table_grid_covers_every_entry_minute():
    grid = set(TABLE["grid_minutes_utc"])
    assert len(TABLE["entries"]) == 42
    for entry in TABLE["entries"]:
        assert Cron(entry["cron"]).minutes <= grid


def test_simulated_sunday_matches_shadow_proof_day():
    # 2026-11-15 (Sunday) is the shadow-proof day: 94 intended fires.
    start = datetime(2026, 11, 15, tzinfo=CT).astimezone(UTC)
    end = start + timedelta(days=1)
    fires = intended_fires(TABLE, start, end)
    assert len(fires) == 94


# ------------------------------------------------------- firing behavior

class FakeApi:
    def __init__(self, existing=(), failing=()):
        self.existing = set(existing)
        self.failing = set(failing)
        self.ran: list[str] = []

    def list_executions(self, job, start, end):
        return [start] if job in self.existing else []

    def run_job(self, job):
        self.ran.append(job)
        return (job not in self.failing), "fake"


def test_fire_due_dry_run_never_calls_api(capsys):
    at = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    assert fire_due(TABLE, at, None, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "DISPATCH DRY" in out and "closing-panel" in out


def test_fire_due_fires_and_skips_existing(capsys):
    at = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    due = due_entries(TABLE, at)
    assert len(due) == 1  # only closing-panel-daily at Mon 06:00 CT
    api = FakeApi(existing={"closing-panel"})
    assert fire_due(TABLE, at, api, dry_run=False) == 0
    assert api.ran == []  # skipped: execution already created this minute
    assert "DISPATCH SKIP" in capsys.readouterr().out

    api = FakeApi()
    assert fire_due(TABLE, at, api, dry_run=False) == 0
    assert api.ran == ["closing-panel"]


def test_fire_due_failure_is_loud(capsys):
    at = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    api = FakeApi(failing={"closing-panel"})
    assert fire_due(TABLE, at, api, dry_run=False) == 1
    assert "DISPATCH FAIL" in capsys.readouterr().out


# ------------------------------------------------------- audit behavior

class AuditApi:
    def __init__(self, starts_by_job):
        self.starts_by_job = starts_by_job

    def list_executions(self, job, start, end):
        return [s for s in self.starts_by_job.get(job, []) if start <= s < end]


def test_audit_ok_and_miss(capsys):
    table = {
        "entries": [
            {"scheduler": "a-daily", "job": "job-a", "cron": "0 6 * * *",
             "timezone": "America/Chicago", "wave": 1, "wave_note": ""},
            {"scheduler": "b-daily", "job": "job-b", "cron": "30 6 * * *",
             "timezone": "America/Chicago", "wave": 1, "wave_note": ""},
        ]
    }
    day = datetime(2026, 11, 16, tzinfo=CT)
    api = AuditApi({"job-a": [datetime(2026, 11, 16, 12, 0, 30, tzinfo=UTC)]})
    assert audit(table, api, day) == 0
    out = capsys.readouterr().out
    assert "AUDIT OK scheduler=a-daily" in out
    assert "AUDIT MISS scheduler=b-daily" in out
    assert "misses=1" in out


def test_table_file_is_valid_and_complete():
    raw = json.loads((Path(__file__).parent.parent /
                      "ev/dispatcher/table.json").read_text())
    assert len(raw["entries"]) == 42
    assert raw["grid_minutes_utc"] == [0, 6, 10, 15, 20, 30, 35, 36, 40, 45, 50]
