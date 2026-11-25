"""Unit tests for ev.all_rows_store (MOD-03 b-ii)."""
from __future__ import annotations

import pytest

from ev import all_rows_store as store


def test_props_archive_conversion_and_dedupe():
    payload = {
        "sport": "nba",
        "day": "2026-11-10",
        "engine": "hier_v1",
        "updated_at": "2026-11-10T18:00:00Z",
        "rows": {
            "k1_over": {
                "sport": "nba",
                "stat": "points",
                "player": "Nikola Jokic",
                "player_norm": "nikola jokic",
                "team": "DEN",
                "opp": "LAL",
                "game_key": "DEN@LAL_2026-11-10",
                "commence": "2026-11-11T02:00:00Z",
                "has_line": True,
                "line": 24.5,
                "consensus_line": 24.5,
                "side": "over",
                "proj_mean": 26.2,
                "p_over_model": 0.58,
                "p_over_blend": 0.55,
                "p_over_market": 0.52,
                "best_over_odds": -105,
                "best_over_book": "fanduel",
                "best_under_odds": -115,
                "best_under_book": "draftkings",
                "n_books": 6,
                "engine": "hier_v1",
            },
            # Same (player_norm, stat, line), under-side variant -> must dedupe keeping first
            "k1_under": {
                "sport": "nba",
                "stat": "points",
                "player": "Nikola Jokic",
                "player_norm": "nikola jokic",
                "team": "DEN",
                "opp": "LAL",
                "game_key": "DEN@LAL_2026-11-10",
                "commence": "2026-11-11T02:00:00Z",
                "has_line": True,
                "line": 24.5,
                "consensus_line": 24.5,
                "side": "under",
                "proj_mean": 26.2,
                "p_over_model": 0.58,
                "p_over_blend": 0.55,
                "p_over_market": 0.52,
                "best_over_odds": -110,
                "best_over_book": "betmgm",
                "best_under_odds": -110,
                "best_under_book": "betmgm",
                "n_books": 6,
            },
            # Missing p_over_market -> devig from -110/-110 == 0.5
            "k2_devig": {
                "sport": "nba",
                "stat": "rebounds",
                "player": "Nikola Jokic",
                "player_norm": "nikola jokic",
                "team": "DEN",
                "opp": "LAL",
                "game_key": "DEN@LAL_2026-11-10",
                "commence": "2026-11-11T02:00:00Z",
                "has_line": True,
                "line": 12.5,
                "consensus_line": 11.5,
                "side": "over",
                "proj_mean": 13.0,
                "p_over_model": 0.54,
                "p_over_blend": 0.52,
                "p_over_market": None,
                "best_over_odds": -110,
                "best_over_book": "fanduel",
                "best_under_odds": -110,
                "best_under_book": "draftkings",
                "n_books": 4,
            },
            # Unpriced row -> excluded
            "k3_unpriced": {
                "sport": "nba",
                "stat": "assists",
                "player": "Jamal Murray",
                "player_norm": "jamal murray",
                "team": "DEN",
                "opp": "LAL",
                "game_key": "DEN@LAL_2026-11-10",
                "commence": "2026-11-11T02:00:00Z",
                "has_line": False,
                "line": None,
                "proj_mean": 6.5,
            },
        },
    }

    records, summary = store.convert_with_summary(
        payload, source="daily_projections", banked_at="2026-11-10T18:05:00Z"
    )
    assert summary["rows_in"] == 4
    assert summary["rows_banked"] == 2
    assert summary["rows_unpriced"] >= 1
    assert summary["rows_duplicate"] == 1
    assert len(records) == 2

    r0 = records[0]
    assert set(r0.keys()) == set(store.COLUMNS)
    assert r0 == {
        "sport": "nba",
        "day": "2026-11-10",
        "game_key": "DEN@LAL_2026-11-10",
        "commence": "2026-11-11T02:00:00Z",
        "player": "Nikola Jokic",
        "player_norm": "nikola jokic",
        "team": "DEN",
        "opp": "LAL",
        "stat": "points",
        "family": "points",
        "line": 24.5,
        "consensus_line": 24.5,
        "is_alt": False,
        "proj_mean": 26.2,
        "p_model": 0.58,
        "p_served": 0.55,
        "p_market": 0.52,
        "best_over_odds": -105.0,
        "best_over_book": "fanduel",
        "best_under_odds": -115.0,
        "best_under_book": "draftkings",
        "n_books": 6,
        "engine": "hier_v1",
        "source": "daily_projections",
        "banked_at": "2026-11-10T18:05:00Z",
    }

    r1 = records[1]
    assert r1["stat"] == "rebounds"
    assert r1["is_alt"] is True
    assert r1["p_market"] == pytest.approx(0.5)


def test_mlb_odds_board_conversion():
    payload = {
        "sport": "mlb",
        "ts": "2026-07-15T16:00:00Z",
        "count": 2,
        "rows": [
            {
                "key": "mlb_k1",
                "game_key": "NYY@BOS_2026-07-15",
                "commence": "2026-07-15T23:10:00Z",
                "away": "NYY",
                "home": "BOS",
                "player": "Gerrit Cole",
                "player_norm": "gerrit cole",
                "player_id": 12345,
                "team": "NYY",
                "stat": "pitcher_strikeouts",
                "line": 6.5,
                "modal_point": 6.5,
                "is_alt": False,
                "p_cons": 0.55,
                "books": [
                    {"book": "fanduel", "over": -120, "under": 100},
                    {"book": "draftkings", "over": -125, "under": 105},
                ],
                "best_over": {"book": "fanduel", "american": -120},
                "best_under": {"book": "draftkings", "american": 105},
                "n_ref": 2,
            },
            {
                "key": "mlb_k2",
                "game_key": "NYY@BOS_2026-07-15",
                "commence": "2026-07-15T23:10:00Z",
                "away": "NYY",
                "home": "BOS",
                "player": "Brayan Bello",
                "player_norm": "brayan bello",
                "player_id": 67890,
                "team": "BOS",
                "stat": "pitcher_strikeouts",
                "line": 5.5,
                "modal_point": 4.5,
                "p_cons": None,
                "books": [{"book": "fanduel", "over": -110, "under": -110}],
                "best_over": {"book": "fanduel", "american": -110},
                "best_under": {"book": "fanduel", "american": -110},
                "n_ref": 1,
            },
        ],
    }

    records, summary = store.convert_with_summary(
        payload, source="mlb_odds_board", banked_at="2026-07-15T16:05:00Z"
    )
    assert summary["rows_in"] == 2
    assert summary["rows_banked"] == 2

    r0 = records[0]
    assert r0["p_model"] is None
    assert r0["p_served"] is None
    assert r0["proj_mean"] is None
    assert r0["p_market"] == pytest.approx(0.55)
    assert r0["source"] == "mlb_odds_board"
    assert r0["family"] == r0["stat"] == "pitcher_strikeouts"
    assert r0["opp"] == "BOS"
    assert r0["day"] == "2026-07-15"
    assert r0["n_books"] == 2
    assert r0["best_over_book"] == "fanduel"
    assert r0["best_over_odds"] == -120.0
    assert r0["best_under_book"] == "draftkings"
    assert r0["best_under_odds"] == 105.0

    r1 = records[1]
    assert r1["p_market"] == pytest.approx(0.5)
    assert r1["opp"] == "NYY"
    assert r1["is_alt"] is True


def test_derive_mlb_family():
    assert store.derive_mlb_family("PITCHER_KS_OVER_6.5_BETMGM") == "PITCHER_KS"
    assert store.derive_mlb_family("pitcher_ks_betmgm") == "pitcher_ks"


def test_write_read_roundtrip_and_idempotent_rebank(tmp_path):
    records_two_families = [
        {
            "sport": "nba",
            "day": "2026-11-10",
            "game_key": "DEN@LAL_2026-11-10",
            "commence": "2026-11-11T02:00:00Z",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "team": "DEN",
            "opp": "LAL",
            "stat": "points",
            "family": "points",
            "line": 24.5,
            "consensus_line": 24.5,
            "is_alt": False,
            "proj_mean": 26.2,
            "p_model": 0.58,
            "p_served": 0.55,
            "p_market": 0.52,
            "best_over_odds": -105.0,
            "best_over_book": "fanduel",
            "best_under_odds": -115.0,
            "best_under_book": "draftkings",
            "n_books": 6,
            "engine": "hier_v1",
            "source": "daily_projections",
            "banked_at": "2026-11-10T18:05:00Z",
        },
        {
            "sport": "nba",
            "day": "2026-11-10",
            "game_key": "DEN@LAL_2026-11-10",
            "commence": "2026-11-11T02:00:00Z",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "team": "DEN",
            "opp": "LAL",
            "stat": "rebounds",
            "family": "rebounds",
            "line": 12.5,
            "consensus_line": 12.5,
            "is_alt": False,
            "proj_mean": 13.0,
            "p_model": 0.54,
            "p_served": 0.52,
            "p_market": 0.50,
            "best_over_odds": -110.0,
            "best_over_book": "fanduel",
            "best_under_odds": -110.0,
            "best_under_book": "draftkings",
            "n_books": 4,
            "engine": "hier_v1",
            "source": "daily_projections",
            "banked_at": "2026-11-10T18:05:00Z",
        },
    ]

    w1 = store.write_records(records_two_families, tmp_path)
    assert w1["partitions"] == 2
    assert w1["rows"] == 2
    assert (tmp_path / "sport=nba" / "day=2026-11-10" / "family=points" / "data.parquet").is_file()
    assert (tmp_path / "sport=nba" / "day=2026-11-10" / "family=rebounds" / "data.parquet").is_file()

    df = store.read_records(tmp_path)
    assert len(df) == 2
    pts = df[df["family"] == "points"].iloc[0]
    assert pts["player_norm"] == "nikola jokic"
    assert pts["p_model"] == pytest.approx(0.58)

    # Idempotent re-bank with same records -> count unchanged
    store.write_records(records_two_families, tmp_path)
    df_again = store.read_records(tmp_path)
    assert len(df_again) == 2

    # Smaller set for same day/family -> partition replaced (add two points rows first, then shrink to 1)
    two_points = [
        records_two_families[0],
        {**records_two_families[0], "player": "Jamal Murray", "player_norm": "jamal murray", "line": 20.5},
    ]
    store.write_records(two_points, tmp_path)
    df_two_pts = store.read_records(tmp_path, sport="nba", day="2026-11-10")
    assert len(df_two_pts[df_two_pts["family"] == "points"]) == 2

    # Replace points partition with smaller 1-row set
    store.write_records([records_two_families[0]], tmp_path)
    df_shrunk = store.read_records(tmp_path, sport="nba", day="2026-11-10")
    assert len(df_shrunk[df_shrunk["family"] == "points"]) == 1
