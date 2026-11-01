"""
tests/test_offseason.py -- verifies MLB off-season gating logic.

MLB off-season runs from after the World Series (Nov 5 cutoff, MLB_OFFSEASON_START=MM-DD overrides) through March 19.
Resumes March 20 (4 days before Opening Day).
MLB_FORCE_RUN=1 overrides off-season gating.
"""
from datetime import date
import os
from unittest.mock import patch

import pytest

from mlb_core.config import is_mlb_offseason
from main import _off_season


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MLB_OFFSEASON_START", raising=False)
    monkeypatch.delenv("MLB_FORCE_RUN", raising=False)


def test_is_mlb_offseason_dates():
    # In-season dates (March 20 through November 4: the World Series can run into early November)
    assert not is_mlb_offseason(date(2026, 3, 20))
    assert not is_mlb_offseason(date(2026, 4, 1))
    assert not is_mlb_offseason(date(2026, 7, 4))
    assert not is_mlb_offseason(date(2026, 10, 28))
    assert not is_mlb_offseason(date(2026, 10, 29))  # the old cutoff: a Game 6/7 can still be played
    assert not is_mlb_offseason(date(2026, 11, 4))

    # Off-season dates (November 5 through March 19)
    assert is_mlb_offseason(date(2026, 11, 5))       # Off-season begins
    assert is_mlb_offseason(date(2026, 11, 15))
    assert is_mlb_offseason(date(2026, 12, 25))
    assert is_mlb_offseason(date(2027, 1, 1))
    assert is_mlb_offseason(date(2027, 2, 14))
    assert is_mlb_offseason(date(2027, 2, 28))
    assert is_mlb_offseason(date(2027, 3, 1))
    assert is_mlb_offseason(date(2027, 3, 19))  # Last off-season day


def test_offseason_start_env_override(monkeypatch):
    monkeypatch.setenv("MLB_OFFSEASON_START", "10-29")
    assert is_mlb_offseason(date(2026, 10, 29)) and not is_mlb_offseason(date(2026, 10, 28))
    monkeypatch.setenv("MLB_OFFSEASON_START", "11-12")
    assert not is_mlb_offseason(date(2026, 11, 11)) and is_mlb_offseason(date(2026, 11, 12))
    for bad in ("garbage", "13-40", "11/05", ""):                      # malformed -> the Nov 5 default, never a crash
        monkeypatch.setenv("MLB_OFFSEASON_START", bad)
        assert not is_mlb_offseason(date(2026, 11, 4)) and is_mlb_offseason(date(2026, 11, 5))


def test_today_is_the_central_date_not_the_utc_date(monkeypatch):
    """10 pm Central on Nov 4 is already Nov 5 in UTC: a UTC date.today() flipped to the offseason a night early."""
    import mlb_core.config as cfg
    from datetime import datetime, timezone

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 11, 5, 4, 0)

    monkeypatch.setattr(cfg, "datetime", FakeDT)
    assert not cfg.is_mlb_offseason()                                  # Chicago is still Nov 4 (22:00 CST)


def test_is_mlb_offseason_force_override():
    with patch.dict(os.environ, {"MLB_FORCE_RUN": "1"}):
        assert not is_mlb_offseason(date(2026, 12, 25))
        assert not is_mlb_offseason(date(2026, 11, 15))


def test_main_off_season_helper():
    with patch.dict(os.environ, {"MLB_FORCE_RUN": ""}):
        # Mocking datetime.now in main._CT
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value = date(2026, 11, 5)
            assert _off_season()

            mock_dt.now.return_value.date.return_value = date(2027, 3, 19)
            assert _off_season()

            mock_dt.now.return_value.date.return_value = date(2027, 3, 20)
            assert not _off_season()

            mock_dt.now.return_value.date.return_value = date(2026, 10, 29)
            assert not _off_season()

            mock_dt.now.return_value.date.return_value = date(2026, 11, 4)
            assert not _off_season()


# -- scheduler pause / resume scripts ------------------------------------------------------------------------------------------
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
PAUSE = DEPLOY / "pause_mlb_offseason.sh"
RESUME = DEPLOY / "resume_mlb_season.sh"


def _exclude_regex():
    return re.compile(re.search(r"EXCLUDE_REGEX='([^']+)'", PAUSE.read_text()).group(1))


@pytest.mark.parametrize("job", ["mlb-tweet-picks-schedule", "mlb-tweet-recap-schedule", "mlb-tweet-reply-ammo-schedule",
                                 "mlb-settle", "mlb-free-plays", "mlb-monitor-ops", "mlb-kalshi-rfq-recorder-daily"])
def test_multisport_jobs_are_never_paused(job):
    assert _exclude_regex().match(job)


@pytest.mark.parametrize("job", ["mlb-snapshot-1555", "mlb-betting-morning", "mlb-capture-closing", "mlb-odds-alert-1",
                                 "mlb-monitor", "mlb-monitor-drift", "mlb-fast-alert-loop", "mlb-weekly-survival",
                                 "mlb-refresh-data", "mlb-kalshi-capture-closing", "mlb-retrain-weekly"])
def test_mlb_only_jobs_are_still_paused(job):
    assert not _exclude_regex().match(job)


def _fake_gcloud(tmp_path, jobs):
    """A stub `gcloud` on PATH: `scheduler jobs list` prints `jobs` (id, state, description; tab separated), every other call
    is appended to calls.log. Nothing real is ever contacted."""
    (tmp_path / "jobs.tsv").write_text("".join("\t".join(j) + "\n" for j in jobs))
    stub = tmp_path / "gcloud"
    stub.write_text("#!/bin/sh\n"
                    'if [ "$1 $2 $3" = "scheduler jobs list" ]; then cat "' + str(tmp_path / "jobs.tsv") + '"; exit 0; fi\n'
                    'echo "$@" >> "' + str(tmp_path / "calls.log") + '"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return tmp_path / "calls.log"


def _run(script, tmp_path, dry=False):
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "PROJECT_ID": "p", "DRY_RUN": "1" if dry else "0", "HOME": str(tmp_path)}
    r = subprocess.run([shutil.which("bash") or "/bin/bash", str(script)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_pause_marks_what_it_pauses_and_leaves_multisport_and_deliberate_pauses_alone(tmp_path):
    calls = _fake_gcloud(tmp_path, [
        ("mlb-snapshot-1555", "ENABLED", "snapshot job"),
        ("mlb-free-plays", "ENABLED", ""),
        ("mlb-monitor-ops", "ENABLED", ""),
        ("mlb-reset-today-bets", "PAUSED", "paused deliberately long ago"),
        ("props-alerts-nhl-1", "ENABLED", ""),
        ("parlay-accum-mlb-am", "ENABLED", ""),
    ])
    out = _run(PAUSE, tmp_path)
    log = calls.read_text()
    assert "scheduler jobs pause mlb-snapshot-1555" in log and "scheduler jobs pause parlay-accum-mlb-am" in log
    assert "offseason-paused" in log and "snapshot job [offseason-paused:" in log
    for untouched in ("mlb-free-plays", "mlb-monitor-ops", "mlb-reset-today-bets", "props-alerts-nhl-1"):
        assert untouched not in log
    assert "KEEPING ENABLED (Multi-sport): mlb-free-plays" in out and "ALREADY PAUSED" in out


def test_resume_only_resumes_jobs_the_pause_script_marked(tmp_path):
    calls = _fake_gcloud(tmp_path, [
        ("mlb-snapshot-1555", "PAUSED", "snapshot job [offseason-paused:2026-11-05]"),
        ("parlay-accum-mlb-am", "PAUSED", "[offseason-paused:2026-11-05]"),
        ("mlb-reset-today-bets", "PAUSED", "paused deliberately"),          # no marker: stays paused
        ("mlb-kalshi-alert-1605", "PAUSED", ""),                              # no marker: stays paused
        ("mlb-settle", "ENABLED", ""),
    ])
    out = _run(RESUME, tmp_path)
    log = calls.read_text()
    assert "scheduler jobs resume mlb-snapshot-1555" in log and "scheduler jobs resume parlay-accum-mlb-am" in log
    assert "mlb-reset-today-bets" not in log and "mlb-kalshi-alert-1605" not in log and "mlb-settle" not in log
    assert "--description=snapshot job" in log and "offseason-paused" not in log      # marker cleared on resume
    assert "LEAVING PAUSED (not paused by the offseason script): mlb-reset-today-bets" in out


@pytest.mark.parametrize("script,tag", [(PAUSE, "Would mark and pause"), (RESUME, "Would resume")])
def test_dry_run_never_mutates(tmp_path, script, tag):
    calls = _fake_gcloud(tmp_path, [("mlb-snapshot-1555", "ENABLED" if script is PAUSE else "PAUSED",
                                     "" if script is PAUSE else "[offseason-paused:2026-11-05]")])
    out = _run(script, tmp_path, dry=True)
    assert tag in out and not calls.exists()
