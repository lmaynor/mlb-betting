"""
Tests for mlb_core.odds.parlay_adapter.

The load-bearing test is the golden round-trip: adapter output fed into the REAL
sgo.py extractors must produce the same shapes/values the runners expect. All
offline -- id_resolver caches primed via monkeypatch (no MLB Stats API calls).
"""

import pytest

from mlb_core.data import id_resolver as R
from mlb_core.odds import parlay_adapter as A
from mlb_core.odds import sgo


# --------------------------------------------------------------------------- #
# fixtures: a minimal ParlayAPI slate (CLE @ ... wait: away=Angels, home=Guardians)
# Mirrors the SGO test's CLE-LAA game so we can reuse the team/player identities.
# --------------------------------------------------------------------------- #

DATE = "2024-05-01"


def _prime(monkeypatch):
    # resolve_team gives ARI/.. ; here Angels->LAA, Guardians->CLE (dk_scraper map).
    monkeypatch.setitem(R._schedule_cache, DATE, {("LAA", "CLE"): [745101]})
    monkeypatch.setitem(R._player_cache, DATE[:4], (
        {"mike trout": {545361}, "jose ramirez": {608070}, "slade cecconi": {669373}},
        {("mike trout", "LAA"): 545361, ("jose ramirez", "CLE"): 608070,
         ("slade cecconi", "CLE"): 669373},
    ))


def _game_lines_event():
    return {
        "id": "parlay-evt-1",
        "home_team": "Cleveland Guardians",
        "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Cleveland Guardians", "price": -130},
                {"name": "Los Angeles Angels", "price": 110},
            ]}]},
            {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Cleveland Guardians", "price": -125},
                {"name": "Los Angeles Angels", "price": 105},
            ]}]},
        ],
    }


def _props_event():
    return {
        "id": "parlay-evt-1",
        "home_team": "Cleveland Guardians",
        "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "player_home_runs", "outcomes": [   # yes/no market shape
                    {"name": "Yes", "description": "Mike Trout", "price": 280, "point": 0.5},
                    {"name": "No", "description": "Mike Trout", "price": -360, "point": 0.5},
                ]},
                {"key": "player_outs", "outcomes": [        # real outs key
                    {"name": "Over Slade Cecconi", "description": "Slade Cecconi", "price": -120, "point": 16.5},
                    {"name": "Under Slade Cecconi", "description": "Slade Cecconi", "price": 100, "point": 16.5},
                ]},
                {"key": "player_hits", "outcomes": [
                    {"name": "Over Jose Ramirez", "description": "Jose Ramirez", "price": -115, "point": 1.5},
                    {"name": "Under Jose Ramirez", "description": "Jose Ramirez", "price": -105, "point": 1.5},
                ]},
            ]},
            {"key": "fanatics", "markets": [   # non-DK/FD onshore book must survive
                {"key": "player_home_runs", "outcomes": [
                    {"name": "Yes", "description": "Mike Trout", "price": 300, "point": 0.5},
                    {"name": "No", "description": "Mike Trout", "price": -380, "point": 0.5},
                ]},
            ]},
        ],
    }


# --------------------------------------------------------------------------- #
# adapter -> SGO shape
# --------------------------------------------------------------------------- #

def test_adapter_builds_sgo_event(monkeypatch):
    _prime(monkeypatch)
    ev = A.parlay_to_sgo_event(_game_lines_event(), _props_event(), DATE)
    assert ev is not None
    assert ev["eventID"] == "745101"                       # == game_pk (load-bearing)
    assert set(ev["players"].keys()) == {"545361", "608070", "669373"}  # MLBAM ids
    assert ev["teams"]["home"]["names"]["short"] == "CLE"
    # game ML + HR yn + hits over/under + OUTS (player_outs key) entries present
    assert "points-home-game-ml-home" in ev["odds"]
    assert "batting_homeRuns-545361-game-yn-yes" in ev["odds"]
    assert "batting_hits-608070-game-ou-over" in ev["odds"]
    assert "batting_hits-608070-game-ou-under" in ev["odds"]
    assert "pitching_outs-669373-game-ou-over" in ev["odds"]   # player_outs handled


def _hr_alt_line_props_event(alt_line_first: bool):
    """DraftKings offering BOTH the standard "1+ HR" outcome (point=1.0,
    a normal price) AND a much-longer "2+ HR" alt-line (point=2.0) for the
    same player under the identical market key -- the real shape observed
    2026-08-18 for Ronald Acuna Jr. (+400 at point=1.0 vs +4300 at point=2.0,
    DraftKings listed the alt-line second). `alt_line_first` flips the
    outcome order to prove the fix doesn't depend on JSON key order, since
    that's never guaranteed from an API."""
    standard = {"name": "Yes", "description": "Mike Trout", "price": 280, "point": 1.0}
    alt_line = {"name": "Yes", "description": "Mike Trout", "price": 4500, "point": 2.0}
    outcomes = [alt_line, standard] if alt_line_first else [standard, alt_line]
    return {
        "id": "parlay-evt-1",
        "home_team": "Cleveland Guardians",
        "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "player_home_runs", "outcomes": outcomes},
            ]},
        ],
    }


@pytest.mark.parametrize("alt_line_first", [False, True])
def test_hr_alt_line_does_not_clobber_the_real_price(monkeypatch, alt_line_first):
    """Regression (2026-08-18): a "2+ HR" alt-line outcome from the same book
    silently overwrote the standard "1+ HR" price because _props_odds grouped
    only by (market key, player), with no line dimension -- HR has no line
    downstream at all (pure yes/no), so the alt-line always must lose."""
    _prime(monkeypatch)
    odds, players = A._props_odds(
        _hr_alt_line_props_event(alt_line_first), "LAA", "CLE", DATE, game_pk=745101,
    )
    trout_hr = next(o for o in odds.values() if o["statID"] == "batting_homeRuns")
    assert trout_hr["byBookmaker"]["draftkings"]["odds"] == "280", (
        "the standard 1+ HR price must win regardless of outcome order in the API response"
    )


def _k_ladder_props_event(ladder_first: bool):
    """DraftKings offering a full "2+ through 9+ strikeouts" one-sided ladder
    (real shape observed 2026-08-19 for Chris Bassitt) alongside its actual
    two-sided Over/Under 4.5 main line, all under the identical market key/
    player/book. `ladder_first` flips ordering to prove the fix doesn't
    depend on JSON key order."""
    main_over  = {"name": "Over Slade Cecconi",  "description": "Slade Cecconi", "price": -121, "point": 4.5}
    main_under = {"name": "Under Slade Cecconi", "description": "Slade Cecconi", "price": -105, "point": 4.5}
    ladder = [
        {"name": "Yes", "description": "Slade Cecconi", "price": p, "point": float(n)}
        for n, p in [(2, -3400), (3, -1400), (4, -400), (5, 150), (6, 550), (7, 1500)]
    ]
    outcomes = (ladder + [main_over, main_under] if ladder_first
                else [main_over, main_under] + ladder)
    return {
        "id": "parlay-evt-1",
        "home_team": "Cleveland Guardians",
        "away_team": "Los Angeles Angels",
        "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "player_strikeouts", "outcomes": outcomes},
            ]},
        ],
    }


@pytest.mark.parametrize("ladder_first", [False, True])
def test_ou_alt_line_ladder_does_not_clobber_the_real_line(monkeypatch, ladder_first):
    """Regression (2026-08-19): the HR fix above only guarded kind=="hr_yn";
    the O/U markets (hits/total bases/strikeouts/outs/earned runs) shared the
    same unconditional last-write-wins collapse. A one-sided alt-line ladder
    ("2+ Ks" through "9+ Ks", real DraftKings shape) landed in the same
    accumulator slot as the real two-sided Over/Under 4.5 line and could
    clobber it depending on JSON order. Fix: prefer whichever point has BOTH
    over and under quoted (the real line) over any one-sided ladder rung."""
    _prime(monkeypatch)
    odds, players = A._props_odds(
        _k_ladder_props_event(ladder_first), "LAA", "CLE", DATE, game_pk=745101,
    )
    over  = next(o for o in odds.values() if o["sideID"] == "over"  and o["statID"] == "pitching_strikeouts")
    under = next(o for o in odds.values() if o["sideID"] == "under" and o["statID"] == "pitching_strikeouts")
    assert over["byBookmaker"]["draftkings"]["odds"] == "-121"
    assert over["byBookmaker"]["draftkings"]["overUnder"] == "4.5"
    assert under["byBookmaker"]["draftkings"]["odds"] == "-105"
    assert under["byBookmaker"]["draftkings"]["overUnder"] == "4.5"


def test_adapter_drops_unresolvable_game(monkeypatch):
    # no schedule cache entry -> game_pk None -> drop
    monkeypatch.setitem(R._schedule_cache, DATE, {})
    monkeypatch.setitem(R._player_cache, DATE[:4], ({}, {}))
    assert A.parlay_to_sgo_event(_game_lines_event(), _props_event(), DATE) is None


# --------------------------------------------------------------------------- #
# GOLDEN round-trip: adapter output -> real extractors
# --------------------------------------------------------------------------- #

def test_roundtrip_through_real_extractors(monkeypatch):
    _prime(monkeypatch)
    ev = A.parlay_to_sgo_event(_game_lines_event(), _props_event(), DATE)
    events = [ev]

    # HR: FanDuel +300 is best across DK +280 / FD +300
    hr = sgo.extract_hr_props(events)
    assert "Mike Trout" in hr
    assert hr["Mike Trout"]["odds"] == 300
    assert hr["Mike Trout"]["event_id"] == "745101"
    assert hr["Mike Trout"]["away_team"] == "Los Angeles Angels"

    # BATTER_HITS: both sides present, line 1.5
    hits = sgo.extract_batter_hits_odds(events)
    assert "Jose Ramirez" in hits
    assert hits["Jose Ramirez"]["line"] == 1.5
    assert hits["Jose Ramirez"]["over_odds"] == -115
    assert hits["Jose Ramirez"]["under_odds"] == -105

    # GAME ML: home best -125 (DK -130 / FD -125 -> -125 higher), away best 110
    gm = sgo.extract_game_ml_odds(events)
    assert "745101" in gm
    assert gm["745101"]["home_odds"] == -125
    assert gm["745101"]["away_odds"] == 110

    # OUTS: player_outs key -> pitching_outs extractor, line 16.5
    outs = sgo.extract_outs_odds(events)
    assert "Slade Cecconi" in outs
    assert outs["Slade Cecconi"]["line"] == 16.5


def test_every_parlay_market_maps_and_synthesizes():
    from nba.config import PARLAY_PROP_MARKETS
    for mkey in PARLAY_PROP_MARKETS["baseball_mlb"]:
        assert mkey in A.PROP_MARKET_MAP, f"{mkey} not handled by adapter"


# --------------------------------------------------------------------------- #
# merge: ParlayAPI covered + SGO inning markets
# --------------------------------------------------------------------------- #

def _sgo_inning_event():
    # SGO-native event for the same game, carrying NRFI (inning market).
    return {
        "eventID": "745101",
        "status": {"startsAt": "2024-05-01T22:10:00Z"},
        "teams": {"away": {"names": {"medium": "Angels"}},
                  "home": {"names": {"medium": "Guardians"}}},
        "players": {},
        "odds": {
            "points-all-1i-ou-over": {"oddID": "points-all-1i-ou-over", "statID": "points",
                "byBookmaker": {"draftkings": {"odds": "120", "available": True}}},
            "points-all-1i-ou-under": {"oddID": "points-all-1i-ou-under", "statID": "points",
                "byBookmaker": {"draftkings": {"odds": "-150", "available": True}}},
            # a covered market on the SGO side that should NOT override ParlayAPI:
            "points-home-game-ml-home": {"oddID": "points-home-game-ml-home",
                "byBookmaker": {"draftkings": {"odds": "-200", "available": True}}},
        },
    }


def test_merge_splices_inning_markets(monkeypatch):
    _prime(monkeypatch)
    parlay = A.parlay_slate_to_sgo_events([_game_lines_event()], {"parlay-evt-1": _props_event()}, DATE)
    merged = A.merge_events(parlay, [_sgo_inning_event()])
    assert len(merged) == 1
    ev = merged[0]
    # NRFI spliced in from SGO
    assert "points-all-1i-ou-over" in ev["odds"]
    # game ML stays ParlayAPI's (home best -125, not SGO's -200)
    gm = sgo.extract_game_ml_odds([ev])
    assert gm["745101"]["home_odds"] == -125
    # NRFI extractor now works off the merged event
    nrfi = sgo.extract_nrfi_odds([ev])
    assert "745101" in nrfi


def test_merge_keeps_sgo_only_game_with_restamped_eventid(monkeypatch):
    _prime(monkeypatch)
    # no ParlayAPI events; SGO-only game must survive with eventID == game_pk
    merged = A.merge_events([], [_sgo_inning_event()])
    assert len(merged) == 1
    assert merged[0]["eventID"] == "745101"
