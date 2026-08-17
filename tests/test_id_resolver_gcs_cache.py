"""
Regression tests for the 2026-08-16 audit's id_resolver GCS-persistence fix
(finding B3.1): _schedule_cache and _player_cache were pure in-process
dicts with zero persistence -- rebuilt from the MLB Stats API on every cold
start, plausibly contributing to occasional /snapshot-odds deadline-exceeded
failures (180s scheduler deadline). Fixed by persisting each to a small GCS
JSON object (via mlb_core.storage) keyed by date/season with a same-day TTL.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import json

import pytest

from mlb_core.data import id_resolver as R

_TEST_DATE = "2099-01-01"       # unlikely to collide with any other test
_TEST_SEASON = "2099"


@pytest.fixture(autouse=True)
def _clean_module_caches(monkeypatch, tmp_path):
    """Local storage mode + guaranteed cleanup of the module-level dicts
    this test deliberately mutates (they're not fixtures, so nothing else
    resets them between tests)."""
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    R._schedule_cache.pop(_TEST_DATE, None)
    R._player_cache.pop(_TEST_SEASON, None)
    yield
    R._schedule_cache.pop(_TEST_DATE, None)
    R._player_cache.pop(_TEST_SEASON, None)


def _fake_schedule_json():
    return {"dates": [{"games": [
        {"gamePk": 700001, "teams": {
            "away": {"team": {"id": 147}},   # NYY
            "home": {"team": {"id": 111}},   # BOS
        }},
    ]}]}


def _fake_players_json():
    return {"people": [
        {"id": 800001, "fullName": "Test Playerson", "currentTeam": {"id": 147}},
    ]}


# ---------------------------------------------------------------------------
# Serialization round-trips (tuple keys / sets aren't JSON-safe as-is)
# ---------------------------------------------------------------------------

def test_schedule_index_json_roundtrip_including_doubleheader():
    index = {("NYY", "BOS"): [700001, 700002], ("LAA", "OAK"): [700003]}
    restored = R._schedule_index_from_json(R._schedule_index_to_json(index))
    assert restored == index


def test_player_index_json_roundtrip():
    name_to_ids = {"test playerson": {800001, 800002}}
    name_team_to_id = {("test playerson", "NYY"): 800001}
    obj = R._player_index_to_json(name_to_ids, name_team_to_id)
    restored_names, restored_team = R._player_index_from_json(obj)
    assert restored_names == name_to_ids
    assert restored_team == name_team_to_id


# ---------------------------------------------------------------------------
# game_pks_for_date: GCS persistence + same-day reuse across a cold start
# ---------------------------------------------------------------------------

def test_schedule_persists_and_survives_a_simulated_cold_start(monkeypatch):
    fetch_calls = []
    monkeypatch.setattr(R, "_fetch_schedule", lambda date: (fetch_calls.append(date), _fake_schedule_json())[1])

    first = R.game_pks_for_date(_TEST_DATE)
    assert first == {("NYY", "BOS"): [700001]}
    assert fetch_calls == [_TEST_DATE]

    # Simulate a cold start: a fresh process has an empty in-memory cache,
    # but the SAME GCS object from the first call is still there.
    R._schedule_cache.pop(_TEST_DATE, None)

    def _fail(date):
        raise AssertionError("must not hit the live API when a fresh, same-day GCS cache entry exists")
    monkeypatch.setattr(R, "_fetch_schedule", _fail)

    second = R.game_pks_for_date(_TEST_DATE)
    assert second == {("NYY", "BOS"): [700001]}


def test_schedule_gcs_entry_expires_after_a_day_passes(monkeypatch):
    """A same-day TTL means yesterday's persisted entry must NOT be reused --
    the live fetch must fire again."""
    from mlb_core.storage import write_bytes
    stale_payload = {
        "fetched_on": "2020-01-01",  # deliberately not today
        "data": R._schedule_index_to_json({("XXX", "YYY"): [999999]}),
    }
    write_bytes(json.dumps(stale_payload).encode(), R._gcs_schedule_key(_TEST_DATE))

    fetch_calls = []
    monkeypatch.setattr(R, "_fetch_schedule", lambda date: (fetch_calls.append(date), _fake_schedule_json())[1])

    result = R.game_pks_for_date(_TEST_DATE)
    assert fetch_calls == [_TEST_DATE], "a stale (not-today) GCS entry must not short-circuit the live fetch"
    assert result == {("NYY", "BOS"): [700001]}


# ---------------------------------------------------------------------------
# season_player_index: same two properties
# ---------------------------------------------------------------------------

def test_player_index_persists_and_survives_a_simulated_cold_start(monkeypatch):
    fetch_calls = []
    monkeypatch.setattr(R, "_fetch_players", lambda season: (fetch_calls.append(season), _fake_players_json())[1])

    first_names, first_team = R.season_player_index(_TEST_SEASON)
    assert first_names == {"test playerson": {800001}}
    assert fetch_calls == [_TEST_SEASON]

    R._player_cache.pop(_TEST_SEASON, None)

    def _fail(season):
        raise AssertionError("must not hit the live API when a fresh, same-day GCS cache entry exists")
    monkeypatch.setattr(R, "_fetch_players", _fail)

    second_names, second_team = R.season_player_index(_TEST_SEASON)
    assert second_names == {"test playerson": {800001}}
    assert second_team == {("test playerson", "NYY"): 800001}
