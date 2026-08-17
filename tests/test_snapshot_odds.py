"""
Tests for mlb.runners.snapshot_odds orchestration (ParlayAPI provider path).

Offline: local storage (tmp), id_resolver primed, ParlayApiClient + SgoClient
monkeypatched with fakes. Covers the new risky logic: credit guard, SGO inning
merge vs carry-forward, and target-date offset.
"""

import json

import pytest

from mlb_core.data import id_resolver as R
import mlb.runners.snapshot_odds as S

DATE = "2024-05-01"


def _game_lines():
    return [{
        "id": "p1", "home_team": "Cleveland Guardians", "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Cleveland Guardians", "price": -130},
            {"name": "Los Angeles Angels", "price": 110}]}]}],
    }]


def _props_obj():
    return {
        "id": "p1", "home_team": "Cleveland Guardians", "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "player_home_runs", "outcomes": [
                {"name": "Yes", "description": "Mike Trout", "price": 280, "point": 0.5},
                {"name": "No", "description": "Mike Trout", "price": -360, "point": 0.5}]}]}],
    }


def _sgo_inning_event():
    return {"eventID": "745101", "status": {"startsAt": "2024-05-01T22:10:00Z"},
            "teams": {"away": {"names": {"medium": "Angels"}},
                      "home": {"names": {"medium": "Guardians"}}},
            "players": {},
            "odds": {"points-all-1i-ou-over": {"oddID": "points-all-1i-ou-over",
                "byBookmaker": {"draftkings": {"odds": "120", "available": True}}}}}


class _FakeParlay:
    def __init__(self, *a, **k):
        self.credits_remaining = None
    def get_slate(self, sport, markets=None):
        return _game_lines()
    def get_event_props(self, sport, eid, markets):
        return _props_obj()


class _FakeSgo:
    def __init__(self, *a, **k):
        pass
    def fetch_mlb_slate(self, run_date=None):
        return [_sgo_inning_event()]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.setitem(R._schedule_cache, DATE, {("LAA", "CLE"): [745101]})
    monkeypatch.setitem(R._player_cache, "2024",
                        ({"mike trout": {545361}}, {("mike trout", "LAA"): 545361}))
    monkeypatch.setattr("nba.odds.parlayapi.ParlayApiClient", _FakeParlay)
    monkeypatch.setattr("mlb_core.odds.sgo.SgoClient", _FakeSgo)


def test_credit_tally_roundtrip(env):
    assert S._read_credits("2024-05") == 0
    S._add_credits("2024-05", 100)
    S._add_credits("2024-05", 50)
    assert S._read_credits("2024-05") == 150
    assert S._read_credits("2024-06") == 0   # different month resets


def test_include_sgo_merges_inning(env):
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=True)
    assert meta["props_pulled"] is True
    ev = events[0]
    assert ev["eventID"] == "745101"
    assert "points-all-1i-ou-over" in ev["odds"]          # SGO inning spliced
    assert "batting_homeRuns-545361-game-yn-yes" in ev["odds"]  # ParlayAPI HR


def test_carry_forward_when_no_sgo(env):
    # seed a prior latest.json that carries an inning market
    from mlb_core import storage
    prior = [{"eventID": "745101", "odds": {"points-all-1i-ou-over": {
        "oddID": "points-all-1i-ou-over",
        "byBookmaker": {"draftkings": {"odds": "118", "available": True}}}}}]
    storage.write_bytes(json.dumps(prior).encode(), "Odds/sgo/latest.json")
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=False)
    assert meta["include_sgo"] is False
    assert "points-all-1i-ou-over" in events[0]["odds"]    # carried forward, no SGO call


def test_credit_guard_skips_props_over_ceiling(env, monkeypatch):
    monkeypatch.setattr(S, "CREDIT_CEILING", 1)            # force over-budget
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=True)
    assert meta["props_pulled"] is False                  # props skipped
    # game ML still present (cheap), but no player props
    assert "points-home-game-ml-home" in events[0]["odds"]
    assert not any(k.startswith("batting_homeRuns") for k in events[0]["odds"])


def test_target_date_offset():
    # day_offset shifts the target ET date forward
    assert S._target_date("2024-05-01", 0) == "2024-05-01"
    assert S._target_date("2024-05-01", 1) == "2024-05-02"


# ── Regression coverage for the 2026-08-16 audit's credit-ledger
# month-boundary fix (finding B3.9) ─────────────────────────────────────────

def test_credit_ledger_uses_call_date_not_slate_date_at_month_boundary(env, monkeypatch):
    """The call happens on the last day of a month (Aug 31), but
    target_date (the slate -- day_offset=1's 'bank tomorrow's openers'
    mode always pushes this forward) has already rolled into the next
    month (Sep 1). The credit ledger -- and the pace ceiling judging it --
    must both key off the REAL call date, not the slate date."""
    real_datetime = S.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is S._ET:
                return real_datetime(2026, 8, 31, 20, 0, tzinfo=S._ET)
            return real_datetime(2026, 8, 31, 23, 59, tzinfo=tz)

    monkeypatch.setattr(S, "datetime", _FrozenDatetime)

    S._gather_parlay("2026-09-01", "Odds/sgo/latest.json", include_sgo=True)

    assert S._read_credits("2026-08") > 0, (
        "spend must land in August's ledger (the real call month), not "
        "September's (the slate month) -- B3.9 regression"
    )
    assert S._read_credits("2026-09") == 0, (
        "September's ledger must not be pre-charged before the month even starts"
    )


# ── Regression coverage for the 2026-08-16 audit's ODDS_PRIMARY/include_sgo
# safe-default fix (finding A2) ─────────────────────────────────────────────

def test_unset_include_sgo_defaults_to_false_not_true(env):
    """An omitted include_sgo must resolve to the cheap/safe direction (no
    SGO call), never silently treat a missing flag as an SGO-touching run."""
    from mlb_core import storage
    prior = [{"eventID": "745101", "odds": {"points-all-1i-ou-over": {
        "oddID": "points-all-1i-ou-over",
        "byBookmaker": {"draftkings": {"odds": "118", "available": True}}}}}]
    storage.write_bytes(json.dumps(prior).encode(), "Odds/sgo/latest.json")
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=None)
    assert meta["include_sgo"] is False
    # carried forward from the prior snapshot, not freshly fetched from SGO
    assert "points-all-1i-ou-over" in events[0]["odds"]


class _PoisonedSgo:
    """Fails loudly if SGO is ever touched -- used to prove the empty-merge
    fallback does not sneak in an SGO call on an include_sgo=False run."""
    def __init__(self, *a, **k):
        raise AssertionError("SGO must not be called on an include_sgo=False run")


def test_empty_merge_with_include_sgo_false_does_not_fall_back_to_sgo(env, monkeypatch):
    monkeypatch.setattr(_FakeParlay, "get_slate", lambda self, sport, markets=None: [])
    monkeypatch.setattr("mlb_core.odds.sgo.SgoClient", _PoisonedSgo)
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=False)
    assert events == []
    assert meta.get("fallback") != "sgo"


def test_empty_merge_with_include_sgo_true_still_falls_back_to_sgo(env, monkeypatch):
    """The 4 windows that were already budgeted to touch SGO this run may
    still fall back to it if the merge is genuinely empty -- that adds no
    *new* SGO load beyond the fetch this run already made."""
    monkeypatch.setattr(_FakeParlay, "get_slate", lambda self, sport, markets=None: [])
    monkeypatch.setattr(_FakeSgo, "fetch_mlb_slate", lambda self, run_date=None: [])
    events, meta = S._gather_parlay(DATE, "Odds/sgo/latest.json", include_sgo=True)
    assert meta.get("fallback") == "sgo"
    assert events == []
