"""
Regression tests for the 2026-08-16 audit's capture_closing_lines.py fixes:

- C5.5: market_map's keys are SGO's abbreviation-style .names.short field,
  but HR bets store full medium team names ("Red Sox," not "BOS") -- the
  lookup used the bet's raw team names directly, so it failed silently for
  every HR bet.

- C5.6: no dispatch branch for GAME or F1H bet types at all -- both
  silently fell through to odds_val=None.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd
import pytest

import mlb.runners.capture_closing_lines as ccl


def _synthetic_event() -> dict:
    """Minimal SGO event: CLE (home) vs LAA (away), with GAME/F1H two-sided
    moneyline odds and one HR prop (Mike Trout), mirroring the real payload
    shape in tests/test_sgo_extractors.py's build_test_event()."""
    def _book(odds):
        return {"byBookmaker": {"draftkings": {"odds": odds, "available": True}}}

    return {
        "eventID": "evt-1",
        "status": {"startsAt": "2026-05-12T22:10:00.000Z"},
        "teams": {
            "home": {"names": {"long": "Cleveland Guardians", "short": "CLE", "medium": "Guardians"}},
            "away": {"names": {"long": "Los Angeles Angels", "short": "LAA", "medium": "Angels"}},
        },
        "players": {
            "MIKE_TROUT_1_MLB": {"playerID": "MIKE_TROUT_1_MLB", "name": "Mike Trout"},
        },
        "odds": {
            "points-home-game-ml-home": {"oddID": "points-home-game-ml-home", **_book("-150")},
            "points-away-game-ml-away": {"oddID": "points-away-game-ml-away", **_book("+130")},
            "points-home-1h-ml-home":   {"oddID": "points-home-1h-ml-home",   **_book("-120")},
            "points-away-1h-ml-away":   {"oddID": "points-away-1h-ml-away",   **_book("+105")},
            "batting_homeRuns-MIKE_TROUT_1_MLB-game-yn-yes": {
                "oddID": "batting_homeRuns-MIKE_TROUT_1_MLB-game-yn-yes",
                "statID": "batting_homeRuns", "statEntityID": "MIKE_TROUT_1_MLB",
                "playerID": "MIKE_TROUT_1_MLB", "periodID": "game",
                "betTypeID": "yn", "sideID": "yes", **_book("+650"),
            },
        },
    }


@pytest.fixture
def prime_snapshot(monkeypatch):
    monkeypatch.setattr(
        "mlb_core.odds.sgo.load_snapshot", lambda key: [_synthetic_event()]
    )


def _bets_df(rows: list[dict]) -> pd.DataFrame:
    base = {"id": 1, "system": "", "bet_type": "", "player": None,
            "away_team": "", "home_team": ""}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_game_ml_home_and_away_branches(prime_snapshot):
    bets = _bets_df([
        {"id": 101, "system": "GAME", "bet_type": "GAME_ML_HOME",
         "away_team": "LAA", "home_team": "CLE"},
        {"id": 102, "system": "GAME", "bet_type": "GAME_ML_AWAY",
         "away_team": "LAA", "home_team": "CLE"},
    ])
    closing = ccl._get_closing_odds_from_snapshot(bets)
    assert closing[101] == (-150.0, 130.0)
    assert closing[102] == (130.0, -150.0)


def test_f1h_home_and_away_branches(prime_snapshot):
    bets = _bets_df([
        {"id": 201, "system": "F1H", "bet_type": "F1H_HOME",
         "away_team": "LAA", "home_team": "CLE"},
        {"id": 202, "system": "F1H", "bet_type": "F1H_AWAY",
         "away_team": "LAA", "home_team": "CLE"},
    ])
    closing = ccl._get_closing_odds_from_snapshot(bets)
    assert closing[201] == (-120.0, 105.0)
    assert closing[202] == (105.0, -120.0)


def test_hr_bet_with_full_team_names_now_resolves(prime_snapshot):
    """The actual C5.5 bug: HR bets store medium/full team names, not SGO's
    abbreviations -- confirm the lookup succeeds with EITHER naming style."""
    bets = _bets_df([
        {"id": 301, "system": "HR", "bet_type": "HR_YES", "player": "Mike Trout",
         "away_team": "Los Angeles Angels", "home_team": "Cleveland Guardians"},
    ])
    closing = ccl._get_closing_odds_from_snapshot(bets)
    assert 301 in closing, "HR bet with full team names failed to match market_map (C5.5 regression)"
    assert closing[301][0] == 650.0


def test_hr_bet_with_abbreviated_team_names_still_resolves(prime_snapshot):
    """resolve_team() must be idempotent -- systems that already store
    abbreviations (the common case for every non-HR system) must keep
    working exactly as before."""
    bets = _bets_df([
        {"id": 302, "system": "HR", "bet_type": "HR_YES", "player": "Mike Trout",
         "away_team": "LAA", "home_team": "CLE"},
    ])
    closing = ccl._get_closing_odds_from_snapshot(bets)
    assert 302 in closing
    assert closing[302][0] == 650.0
