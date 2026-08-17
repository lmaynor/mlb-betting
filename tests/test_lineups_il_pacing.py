"""
Regression test for the 2026-08-16 audit's IL-roster pacing fix (finding
B3.6): _fetch_40man_il made 30 sequential, unpaced HTTP calls (one per MLB
team) where every other 30-team loop in this file sleeps 0.1-0.3s between
calls.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
from unittest.mock import MagicMock

import mlb_core.data.lineups as lineups


def test_fetch_40man_il_paces_every_team_call(monkeypatch):
    sleep_calls = []

    def _fake_get(url, timeout=10):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"roster": []}
        return resp

    monkeypatch.setattr(lineups._session, "get", _fake_get)
    monkeypatch.setattr(lineups.time, "sleep", lambda s: sleep_calls.append(s))

    result = lineups._fetch_40man_il(want_pitchers_only=False, log_label="test")

    assert result == set()
    assert len(sleep_calls) == len(lineups._MLB_TEAM_IDS), (
        f"expected one paced sleep per team ({len(lineups._MLB_TEAM_IDS)}), "
        f"got {len(sleep_calls)}"
    )
    assert all(0.1 <= s <= 0.3 for s in sleep_calls), (
        f"sleep durations outside the documented 0.1-0.3s jitter range: {sleep_calls}"
    )


def test_fetch_40man_il_still_paces_after_a_team_failure(monkeypatch):
    """The sleep must fire even when a team's request raises -- otherwise
    a string of failing teams (e.g. a transient MLB API blip) would burn
    through the remaining calls unpaced."""
    sleep_calls = []

    def _fake_get(url, timeout=10):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(lineups._session, "get", _fake_get)
    monkeypatch.setattr(lineups.time, "sleep", lambda s: sleep_calls.append(s))

    result = lineups._fetch_40man_il(want_pitchers_only=True, log_label="test")

    assert result == set()
    assert len(sleep_calls) == len(lineups._MLB_TEAM_IDS)
