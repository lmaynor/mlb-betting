"""Unit test for mlb_core.odds.sgo extractors.

Builds a minimal SGO event payload matching the shape we observed from the
real CLE-LAA probe (2026-05-12), runs each extractor, and verifies the
output shapes and values.

Run from repo root:
  python3 tests/test_sgo_extractors.py
"""
import sys
from pathlib import Path

# Allow `python3 tests/test_sgo_extractors.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlb_core.odds import sgo


def build_test_event() -> dict:
    """Synthesize one event mirroring the real CLE-LAA payload structure."""
    return {
        "eventID":  "3aVfMmr4ceT43CTodkNV",
        "leagueID": "MLB",
        "sportID":  "BASEBALL",
        "type":     "match",
        "info": {
            "venue": {"name": "Progressive Field", "city": "Cleveland"},
        },
        "status": {
            "started":   False,
            "completed": False,
            "startsAt":  "2026-05-12T22:10:00.000Z",
            "oddsAvailable": True,
        },
        "teams": {
            "home": {
                "teamID": "CLEVELAND_GUARDIANS_MLB",
                "names":  {"long": "Cleveland Guardians", "short": "CLE", "medium": "Guardians"},
            },
            "away": {
                "teamID": "LOS_ANGELES_ANGELS_MLB",
                "names":  {"long": "Los Angeles Angels", "short": "LAA", "medium": "Angels"},
            },
        },
        "players": {
            "ANGEL_MARTINEZ_1_MLB": {
                "playerID": "ANGEL_MARTINEZ_1_MLB",
                "teamID":   "CLEVELAND_GUARDIANS_MLB",
                "name":     "Angel Martínez",
            },
            "SLADE_CECCONI_1_MLB": {
                "playerID": "SLADE_CECCONI_1_MLB",
                "teamID":   "CLEVELAND_GUARDIANS_MLB",
                "name":     "Slade Cecconi",
            },
            "MIKE_TROUT_1_MLB": {
                "playerID": "MIKE_TROUT_1_MLB",
                "teamID":   "LOS_ANGELES_ANGELS_MLB",
                "name":     "Mike Trout",
            },
        },
        "odds": {
            # NRFI
            "points-all-1i-ou-over": {
                "oddID":     "points-all-1i-ou-over",
                "statID":    "points",
                "periodID":  "1i",
                "betTypeID": "ou",
                "sideID":    "over",
                "fairOdds":     "+102",
                "openBookOdds": "+210",
                "byBookmaker": {
                    "draftkings": {"odds": "-105", "overUnder": "0.5", "available": True},
                },
            },
            "points-all-1i-ou-under": {
                "oddID":     "points-all-1i-ou-under",
                "statID":    "points",
                "periodID":  "1i",
                "betTypeID": "ou",
                "sideID":    "under",
                "fairOdds":     "-115",
                "openBookOdds": "-250",
                "byBookmaker": {
                    "draftkings": {"odds": "-115", "overUnder": "0.5", "available": True},
                },
            },
            # F5
            "points-all-1ix5-ou-over": {
                "oddID":     "points-all-1ix5-ou-over",
                "statID":    "points",
                "periodID":  "1ix5",
                "betTypeID": "ou",
                "sideID":    "over",
                "fairOdds":     "+115",
                "openBookOdds": "-120",
                "byBookmaker": {
                    "draftkings": {"odds": "-120", "overUnder": "4.5", "available": True},
                },
            },
            "points-all-1ix5-ou-under": {
                "oddID":     "points-all-1ix5-ou-under",
                "statID":    "points",
                "periodID":  "1ix5",
                "betTypeID": "ou",
                "sideID":    "under",
                "fairOdds":     "-130",
                "openBookOdds": "+100",
                "byBookmaker": {
                    "draftkings": {"odds": "+100", "overUnder": "4.5", "available": True},
                },
            },
            # HR Martínez — full set (yn-yes/no + ou-over/under at 0.5)
            "batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-yn-yes": {
                "oddID":         "batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-yn-yes",
                "statID":        "batting_homeRuns",
                "statEntityID":  "ANGEL_MARTINEZ_1_MLB",
                "playerID":      "ANGEL_MARTINEZ_1_MLB",
                "periodID":      "game",
                "betTypeID":     "yn",
                "sideID":        "yes",
                "fairOdds":      "+810",
                "openBookOdds":  "+999",
                "byBookmaker":   {
                    "draftkings": {"odds": "+840", "available": True},
                    "fanduel":    {"odds": "+920", "available": True},
                },
            },
            "batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-ou-over": {
                "oddID":         "batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-ou-over",
                "statID":        "batting_homeRuns",
                "statEntityID":  "ANGEL_MARTINEZ_1_MLB",
                "playerID":      "ANGEL_MARTINEZ_1_MLB",
                "periodID":      "game",
                "betTypeID":     "ou",
                "sideID":        "over",
                "fairOdds":      "+810",
                "byBookmaker":   {
                    "draftkings": {"odds": "+840", "overUnder": "0.5", "available": True},
                },
            },
            # HR Trout — ONLY ou-over (no yn variant)  → exercises the fallback path
            "batting_homeRuns-MIKE_TROUT_1_MLB-game-ou-over": {
                "oddID":         "batting_homeRuns-MIKE_TROUT_1_MLB-game-ou-over",
                "statID":        "batting_homeRuns",
                "statEntityID":  "MIKE_TROUT_1_MLB",
                "playerID":      "MIKE_TROUT_1_MLB",
                "periodID":      "game",
                "betTypeID":     "ou",
                "sideID":        "over",
                "fairOdds":      "+250",
                "byBookmaker":   {
                    "draftkings": {"odds": "+275", "overUnder": "0.5", "available": True},
                },
            },
            # K props for Cecconi
            "pitching_strikeouts-SLADE_CECCONI_1_MLB-game-ou-over": {
                "oddID":         "pitching_strikeouts-SLADE_CECCONI_1_MLB-game-ou-over",
                "statID":        "pitching_strikeouts",
                "statEntityID":  "SLADE_CECCONI_1_MLB",
                "playerID":      "SLADE_CECCONI_1_MLB",
                "periodID":      "game",
                "betTypeID":     "ou",
                "sideID":        "over",
                "fairOdds":      "+100",
                "openBookOdds":  "-104",
                "byBookmaker":   {
                    "draftkings": {"odds": "-144", "overUnder": "4.5", "available": True},
                },
            },
            "pitching_strikeouts-SLADE_CECCONI_1_MLB-game-ou-under": {
                "oddID":         "pitching_strikeouts-SLADE_CECCONI_1_MLB-game-ou-under",
                "statID":        "pitching_strikeouts",
                "statEntityID":  "SLADE_CECCONI_1_MLB",
                "playerID":      "SLADE_CECCONI_1_MLB",
                "periodID":      "game",
                "betTypeID":     "ou",
                "sideID":        "under",
                "fairOdds":      "-115",
                "openBookOdds":  "-110",
                "byBookmaker":   {
                    "draftkings": {"odds": "+115", "overUnder": "4.5", "available": True},
                },
            },
        },
    }


def test_extract_nrfi_odds():
    out = sgo.extract_nrfi_odds([build_test_event()])
    assert len(out) == 1, f"expected 1 event, got {len(out)}"
    row = out["3aVfMmr4ceT43CTodkNV"]
    assert row["away_team"] == "Angels"
    assert row["home_team"] == "Guardians"
    assert row["nrfi_odds"] == -115, f"expected nrfi_odds=-115, got {row['nrfi_odds']}"
    assert row["yrfi_odds"] == -105, f"expected yrfi_odds=-105, got {row['yrfi_odds']}"
    assert row["bookmaker"] == "draftkings"
    assert row["fair_nrfi"] == -115
    assert row["fair_yrfi"] == 102
    assert row["open_nrfi"] == -250
    assert row["open_yrfi"] == 210
    print("  ✓ extract_nrfi_odds")


def test_extract_f5_odds():
    out = sgo.extract_f5_odds([build_test_event()])
    assert len(out) == 1
    row = out["3aVfMmr4ceT43CTodkNV"]
    assert row["over_odds"] == -120
    assert row["under_odds"] == 100
    assert row["line"] == 4.5
    assert row["fair_over"] == 115
    assert row["fair_under"] == -130
    print("  ✓ extract_f5_odds")


def test_extract_hr_props():
    out = sgo.extract_hr_props([build_test_event()])
    # Two players should appear: Martínez (yn primary) and Trout (ou fallback)
    assert "Angel Martínez" in out, f"missing Martínez. keys: {list(out.keys())}"
    assert "Mike Trout" in out, f"missing Trout (ou fallback). keys: {list(out.keys())}"

    martinez = out["Angel Martínez"]
    assert martinez["odds"] == 840
    assert martinez["away_team"] == "Angels"
    assert martinez["home_team"] == "Guardians"
    assert martinez["event_id"] == "3aVfMmr4ceT43CTodkNV"
    assert martinez["line"] == 0.5
    assert martinez["fair_odds"] == 810
    assert martinez["open_odds"] == 999
    # Should be the yn-yes oddID, not the ou-over
    assert "yn-yes" in martinez["source_oddid"]

    trout = out["Mike Trout"]
    assert trout["odds"] == 275
    assert "ou-over" in trout["source_oddid"]
    print("  ✓ extract_hr_props (yn primary + ou fallback)")


def test_extract_k_odds():
    out = sgo.extract_k_odds([build_test_event()])
    assert "Slade Cecconi" in out
    cec = out["Slade Cecconi"]
    assert cec["over_odds"] == -144
    assert cec["under_odds"] == 115
    assert cec["line"] == 4.5
    assert cec["fair_over"] == 100
    assert cec["fair_under"] == -115
    print("  ✓ extract_k_odds")


def test_normalize_name():
    assert sgo.normalize_name("Angel Martínez") == "angel martinez"
    assert sgo.normalize_name("José Ramírez") == "jose ramirez"
    assert sgo.normalize_name("  Mike Trout  ") == "mike trout"
    assert sgo.normalize_name("") == ""
    assert sgo.normalize_name(None) == ""
    print("  ✓ normalize_name")


def test_skips_when_dk_unavailable():
    """If DK has no price on a market, that player should be dropped."""
    event = build_test_event()
    # Remove DK from Martínez yn-yes, leave only fanduel
    event["odds"]["batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-yn-yes"]["byBookmaker"] = {
        "fanduel": {"odds": "+920", "available": True},
    }
    # Also remove the OU fallback so Martínez has no DK at all
    del event["odds"]["batting_homeRuns-ANGEL_MARTINEZ_1_MLB-game-ou-over"]
    out = sgo.extract_hr_props([event])
    assert "Angel Martínez" not in out, "should drop player when DK unavailable"
    print("  ✓ skips_when_dk_unavailable")


def test_skips_when_dk_marked_unavailable():
    """If DK is in byBookmaker but available=False, drop."""
    event = build_test_event()
    event["odds"]["batting_homeRuns-MIKE_TROUT_1_MLB-game-ou-over"]["byBookmaker"]["draftkings"]["available"] = False
    out = sgo.extract_hr_props([event])
    assert "Mike Trout" not in out
    print("  ✓ skips_when_dk_marked_unavailable")


def test_handles_empty_events():
    assert sgo.extract_nrfi_odds([]) == {}
    assert sgo.extract_f5_odds([]) == {}
    assert sgo.extract_hr_props([]) == {}
    assert sgo.extract_k_odds([]) == {}
    print("  ✓ handles_empty_events")


def main():
    print("Testing mlb_core.odds.sgo extractors...")
    test_normalize_name()
    test_extract_nrfi_odds()
    test_extract_f5_odds()
    test_extract_hr_props()
    test_extract_k_odds()
    test_skips_when_dk_unavailable()
    test_skips_when_dk_marked_unavailable()
    test_handles_empty_events()
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
