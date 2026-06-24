"""Tests for sgo.is_live_event -- the in-play/live odds guard.

Live (already-started) events must be flagged so runners skip them: scoring
in-play odds with pre-game count models causes dramatic overconfidence (the model
assumes a full game of plate appearances / outs remain).
"""
from datetime import datetime, timezone

from mlb_core.odds.sgo import is_live_event

NOW = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)


def test_future_event_not_live():
    assert is_live_event("2026-05-15T23:10:00Z", now=NOW) is False


def test_started_event_is_live():
    assert is_live_event("2026-05-15T17:05:00Z", now=NOW) is True


def test_missing_fails_open():
    # fail-open: unknown start time must NOT suppress the whole slate
    assert is_live_event("", now=NOW) is False
    assert is_live_event(None, now=NOW) is False


def test_unparseable_fails_open():
    assert is_live_event("not-a-date", now=NOW) is False


def test_grace_window():
    # 15 min before first pitch: live only if grace covers it
    assert is_live_event("2026-05-15T20:15:00Z", now=NOW, grace_min=30) is True
    assert is_live_event("2026-05-15T20:15:00Z", now=NOW, grace_min=0) is False


def test_naive_timestamp_treated_as_utc():
    assert is_live_event("2026-05-15T17:05:00", now=NOW) is True
