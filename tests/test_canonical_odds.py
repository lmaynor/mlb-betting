"""tests/test_canonical_odds.py -- Unit and Conformance Tests for BNK-12 Canonical Odds.

Tests:
  - Normalizer: merges D.J./DJ Moore; A.J. Brown vs Amon-Ra St. Brown distinctness.
  - Registry: AN 68 -> draftkings AND BP 68 -> kalshi; AN 15 -> consensus_an; unknown -> None.
  - Round-trip fidelity: small AN NBA props fixture reconciles raw == canonical + quarantine.
  - Quarantine counting: ambiguous, team_mismatch, unknown_book recorded in report and 0 in canonical.
  - Incomplete-is-not-invalid: BP fixture with no updated stamp and one-sided milestone kept with NULLs.
  - V1: replicated football style payload fails with slate_mismatch and publishes nothing.
  - OddsAccum: raw JSON with 2 bookmakers -> per-book rows with last_update preserved.
  - Kalshi futures: ask-only row (bid 0.0, ask 0.01) banks with price_decimal=100.0, mid_prob=None.
  - Idempotence: publish twice -> already_published; force=True -> supersedes.
  - validate_published_frame passes on all writer outputs in tests.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import pandas as pd

from mlb_core import storage
from mlb_core.odds.canonical.ingest import publish, write_registry
from mlb_core.odds.canonical.normalize import NameCollisionTracker, normalize_name
from mlb_core.odds.canonical.read import filter_canonical, load_day, load_report
from mlb_core.odds.canonical.registry import (
    book_from_an_id,
    book_from_bp_id,
    book_from_odds_api_key,
    load_books,
)
from mlb_core.odds.canonical.resolve import MasterIndex, build_generic_master_index
from mlb_core.odds.canonical.schema import (
    CANONICAL_COLUMNS,
    empty_frame,
    validate_published_frame,
)
from mlb_core.odds.canonical.writers import (
    write_an,
    write_bp,
    write_kalshi_futures,
    write_oddsaccum,
)


# ===========================================================================
# 1. Normalizer Test
# ===========================================================================

def test_normalizer_and_collision_handling():
    # Merges D.J. Moore and DJ Moore
    assert normalize_name("D.J. Moore") == "dj moore"
    assert normalize_name("DJ Moore") == "dj moore"
    assert normalize_name("D.J. Moore") == normalize_name("DJ Moore")

    # NEVER merges A.J. Brown with Amon-Ra St. Brown
    aj = normalize_name("A.J. Brown")
    amon_ra = normalize_name("Amon-Ra St. Brown")
    assert aj == "aj brown"
    assert amon_ra == "amon ra st brown"
    assert aj != amon_ra

    # Master index name resolution step: assert they never resolve to the same master
    master = MasterIndex("nfl")
    master.add_game(event_id="ev_det_phi", game_date_et="2026-11-15", away_team="DET", home_team="PHI")
    master.add_player(master_player_id="pid_aj", name="A.J. Brown", team="PHI", game_date_et="2026-11-15")
    master.add_player(master_player_id="pid_amon_ra", name="Amon-Ra St. Brown", team="DET", game_date_et="2026-11-15")

    # Resolve A.J. Brown
    mid_1, team_1, step_1 = master.resolve_player(
        source="actionnetwork", source_player_id=None, player_name="A.J. Brown",
        event_id="ev_det_phi", game_date_et="2026-11-15", away_team="DET", home_team="PHI",
    )
    assert mid_1 == "pid_aj"
    assert team_1 == "PHI"
    assert step_1 == "step2_name_event_teams"

    # Resolve Amon-Ra St. Brown
    mid_2, team_2, step_2 = master.resolve_player(
        source="actionnetwork", source_player_id=None, player_name="Amon-Ra St. Brown",
        event_id="ev_det_phi", game_date_et="2026-11-15", away_team="DET", home_team="PHI",
    )
    assert mid_2 == "pid_amon_ra"
    assert team_2 == "DET"
    assert step_2 == "step2_name_event_teams"
    assert mid_1 != mid_2

    # Collision tracker: test that two distinct master players sharing one key produces collision
    tracker = NameCollisionTracker()
    tracker.register("Michael Carter", master_id="mc_1")
    tracker.register("Michael Carter", master_id="mc_2")
    assert tracker.is_collision("michael carter") is True


# ===========================================================================
# 2. Registry Test
# ===========================================================================

def test_registry_lookups_and_collision_pair():
    # AN 68 -> DraftKings, BP 68 -> Kalshi (the collision pair)
    assert book_from_an_id(68) == "draftkings"
    assert book_from_bp_id(68) == "kalshi"
    assert book_from_an_id("68") == "draftkings"
    assert book_from_bp_id("68") == "kalshi"

    # AN 15 -> consensus_an with bettable False
    assert book_from_an_id(15) == "consensus_an"
    books_df = load_books()
    consensus_row = books_df[books_df["canonical_book_id"] == "consensus_an"]
    assert not consensus_row.empty
    assert bool(consensus_row.iloc[0]["bettable"]) is False
    assert consensus_row.iloc[0]["bettable"] == False
    assert consensus_row.iloc[0]["kind"] == "derived_series"

    # Numeric collision AN 49 (fliff) vs BP 49 (hard_rock)
    assert book_from_an_id(49) == "fliff"
    assert book_from_bp_id(49) == "hard_rock"

    # Unknown ID -> None (never fabricated book_{id})
    assert book_from_an_id(999999) is None
    assert book_from_bp_id(999999) is None
    assert book_from_odds_api_key("unknown_bookie_xyz") is None


# ===========================================================================
# 3. Round-Trip Fidelity Test (AN NBA Props)
# ===========================================================================

def test_round_trip_fidelity_an_nba_props():
    master = MasterIndex("nba")
    master.add_game(event_id="295437", game_date_et="2026-11-15", away_team="IND", home_team="PHI")
    master.add_player(master_player_id="pid_tatum", name="Jayson Tatum", team="PHI", game_date_et="2026-11-15")

    raw_fixture = pd.DataFrame([
        # 1. Milestone row (book 68, threshold 10, is_milestone=True)
        {
            "date": "2026-11-15", "event_id": "295437", "player_id": "198227", "player_name": "Jayson Tatum",
            "team_id": 163, "team_abbr": "PHI", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "rebounds_milestone_10plus",
            "source_type": "core_bet_type_202_741_player_rebounds_milestones_10_or_more", "is_milestone": True,
            "threshold": 10.0, "book_id": 68, "book_name": "draftkings", "side": "noside", "line": 10.0,
            "odds": 150, "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "28709841", "outcome_id": "3206548711", "scraped_at": "2026-11-16T08:00:00Z",
        },
        # 2. Consensus series row (book 15)
        {
            "date": "2026-11-15", "event_id": "295437", "player_id": "198227", "player_name": "Jayson Tatum",
            "team_id": 163, "team_abbr": "PHI", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False,
            "threshold": None, "book_id": 15, "book_name": "consensus", "side": "over", "line": 26.5,
            "odds": -110, "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "28709842", "outcome_id": "3206548712", "scraped_at": "2026-11-16T08:00:00Z",
        },
        # 3. Opener series row (book 30)
        {
            "date": "2026-11-15", "event_id": "295437", "player_id": "198227", "player_name": "Jayson Tatum",
            "team_id": 163, "team_abbr": "PHI", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False,
            "threshold": None, "book_id": 30, "book_name": "open", "side": "under", "line": 26.5,
            "odds": -110, "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "28709843", "outcome_id": "3206548713", "scraped_at": "2026-11-16T08:00:00Z",
        },
    ])

    c_df, q_df, report = write_an(
        raw_fixture,
        league="nba",
        master=master,
        requested_date="2026-11-15",
        source_key="Odds/actionnetwork_nba/test/2026-11-15.parquet",
    )

    # Reconcile count: raw_rows == canonical + quarantine + documented_exclusions
    assert len(raw_fixture) == len(c_df) + len(q_df)
    assert len(q_df) == 0
    assert len(c_df) == 3

    # Each canonical row's source_ref resolves to a fixture row
    for _, row in c_df.iterrows():
        assert "Odds/actionnetwork_nba/test/2026-11-15.parquet#" in row["source_ref"]
        assert row["price_kind"] == "closing_snapshot_unstamped"

    # Milestone row check: side is "yes", threshold preserved, no "no" twin
    ms_row = c_df[c_df["market_class"] == "milestone"].iloc[0]
    assert ms_row["side"] == "yes"
    assert ms_row["line"] == 10.0
    assert (c_df["side"] == "no").sum() == 0

    # Book mappings
    assert set(c_df["book_id"]) == {"draftkings", "consensus_an", "open_an"}

    # validate_published_frame passes
    validate_published_frame(c_df)


# ===========================================================================
# 4. Quarantine Counting Test
# ===========================================================================

def test_quarantine_counting():
    master = MasterIndex("nba")
    master.add_game(event_id="ev1", game_date_et="2026-11-15", away_team="IND", home_team="PHI")
    # Namesake pair on the two event teams (BOS and LAL or IND and PHI)
    master.add_player(master_player_id="mid_ind", name="Chris Smith", team="IND", game_date_et="2026-11-15")
    master.add_player(master_player_id="mid_phi", name="Chris Smith", team="PHI", game_date_et="2026-11-15")
    # Stephen Curry belongs to GSW (neither IND nor PHI)
    master.add_player(master_player_id="mid_curry", name="Stephen Curry", team="GSW", game_date_et="2026-11-15")

    raw_fixture = pd.DataFrame([
        # (i) Namesake pair on the two event teams -> ambiguous
        {
            "date": "2026-11-15", "event_id": "ev1", "player_id": None, "player_name": "Chris Smith",
            "team_id": None, "team_abbr": None, "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False, "threshold": None,
            "book_id": 68, "book_name": "draftkings", "side": "over", "line": 15.5, "odds": -110,
            "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "m1", "outcome_id": "o1", "scraped_at": "2026-11-16T08:00:00Z",
        },
        # (ii) Player on neither event team -> team_mismatch
        {
            "date": "2026-11-15", "event_id": "ev1", "player_id": None, "player_name": "Stephen Curry",
            "team_id": None, "team_abbr": "GSW", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False, "threshold": None,
            "book_id": 68, "book_name": "draftkings", "side": "over", "line": 28.5, "odds": -110,
            "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "m2", "outcome_id": "o2", "scraped_at": "2026-11-16T08:00:00Z",
        },
        # (iii) Unknown book id -> unknown_book
        {
            "date": "2026-11-15", "event_id": "ev1", "player_id": None, "player_name": "Chris Smith",
            "team_id": None, "team_abbr": None, "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False, "threshold": None,
            "book_id": 999999, "book_name": "unknown", "side": "over", "line": 15.5, "odds": -110,
            "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "m3", "outcome_id": "o3", "scraped_at": "2026-11-16T08:00:00Z",
        },
    ])

    c_df, q_df, report = write_an(raw_fixture, league="nba", master=master, requested_date="2026-11-15")

    # Assert counts by reason in report and zero in canonical
    assert len(c_df) == 0
    assert len(q_df) == 3
    reasons = report["by_reason"]
    assert reasons.get("ambiguous") == 1
    assert reasons.get("team_mismatch") == 1
    assert reasons.get("unknown_book") == 1
    validate_published_frame(c_df)


# ===========================================================================
# 5. Incomplete Is Not Invalid Test (BP Props)
# ===========================================================================

def test_incomplete_is_not_invalid_bp():
    master = MasterIndex("nba")
    master.add_game(event_id="ev_bp1", game_date_et="2026-11-15", away_team="IND", home_team="PHI")
    master.add_player(master_player_id="mid_bp1", name="Jayson Tatum", team="PHI", game_date_et="2026-11-15")

    raw_fixture = pd.DataFrame([
        # Row with missing updated stamp
        {
            "date": "2026-11-15", "sport": "NBA", "market": "points", "market_id": "m1", "event_id": "ev_bp1",
            "scheduled": "2026-11-15T18:00:00Z", "season_type": "Regular", "event_status": "closed",
            "away": "IND", "home": "PHI", "player_id": "123", "player": "Jayson Tatum", "team": "PHI",
            "position": "SF", "line": 25.5, "side": "over", "book_id": 12, "book": "DraftKings",
            "book_kind": "book", "odds": -110, "is_main": True, "is_best": False, "is_open": False,
            "updated": None,  # Missing updated stamp!
        },
        # One-sided milestone row
        {
            "date": "2026-11-15", "sport": "NBA", "market": "points-milestone", "market_id": "m2", "event_id": "ev_bp1",
            "scheduled": "2026-11-15T18:00:00Z", "season_type": "Regular", "event_status": "closed",
            "away": "IND", "home": "PHI", "player_id": "123", "player": "Jayson Tatum", "team": "PHI",
            "position": "SF", "line": 30.0, "side": "over", "book_id": 10, "book": "FanDuel",
            "book_kind": "book", "odds": 180, "is_main": False, "is_best": False, "is_open": False,
            "updated": "2026-11-15T17:30:00Z",
        },
    ])

    c_df, q_df, report = write_bp(raw_fixture, sport="NBA", master=master)

    # Incomplete rows MUST NOT be quarantined
    assert len(q_df) == 0
    assert len(c_df) == 2

    # Check row 1 preserved with NULL source_updated_at and honest price_kind
    row_1 = c_df[c_df["book_id"] == "draftkings"].iloc[0]
    assert pd.isna(row_1["source_updated_at"]) or row_1["source_updated_at"] is None
    assert row_1["price_kind"] == "last_quote_close_proxy"

    # Check row 2
    row_2 = c_df[c_df["book_id"] == "fanduel"].iloc[0]
    assert row_2["source_updated_at"] == "2026-11-15T17:30:00Z"
    validate_published_frame(c_df)


# ===========================================================================
# 6. V1 Slate Match Hard Failure Test
# ===========================================================================

def test_v1_slate_match_hard_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))

    master = MasterIndex("nba")
    # All game start times are on 2026-11-15
    replicated_payload = pd.DataFrame([
        {
            "date": "2026-10-01",  # Labeled date is October 1
            "event_id": "ev_rep", "player_id": "pid1", "player_name": "Jayson Tatum",
            "team_id": 163, "team_abbr": "PHI", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False, "threshold": None,
            "book_id": 68, "book_name": "draftkings", "side": "over", "line": 25.5, "odds": -110,
            "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "m1", "outcome_id": "o1", "scraped_at": "2026-11-16T08:00:00Z",
        }
    ])

    # We requested 2026-10-01, but the payload contains only 2026-11-15 games
    c_df, q_df, report = write_an(replicated_payload, league="nba", master=master, requested_date="2026-10-01")

    # Hard failure: 0 canonical rows, all quarantined as slate_mismatch
    assert len(c_df) == 0
    assert len(q_df) == 1
    assert q_df.iloc[0]["reject_reason"] == "slate_mismatch"
    assert report.get("v1", {}).get("hard_failure") is True

    # Publish returns published with 0 canonical rows
    res = publish("actionnetwork", "nba", "2026-10-01", c_df, q_df, report)
    assert res["status"] == "published"
    loaded = load_day("nba", "2026-10-01")
    assert loaded.empty
    validate_published_frame(c_df)


# ===========================================================================
# 7. OddsAccum Raw JSON Per-Book Test
# ===========================================================================

def test_oddsaccum_raw_json_preserves_per_book():
    master = MasterIndex("nba")
    master.add_game(event_id="ev_bos_lal", game_date_et="2026-11-15", away_team="LAL", home_team="BOS")
    master.add_player(master_player_id="pid_tatum", name="Jayson Tatum", team="BOS", game_date_et="2026-11-15")

    raw_payload = [
        {
            "id": "ev_bos_lal",
            "sport_key": "basketball_nba",
            "commence_time": "2026-11-15T23:30:00Z",
            "home_team": "Boston Celtics",
            "away_team": "Los Angeles Lakers",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-11-15T23:15:00Z",
                    "markets": [
                        {
                            "key": "player_points",
                            "outcomes": [
                                {"name": "Over", "description": "Jayson Tatum", "price": -110, "point": 26.5},
                                {"name": "Under", "description": "Jayson Tatum", "price": -110, "point": 26.5},
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "last_update": "2026-11-15T23:20:00Z",
                    "markets": [
                        {
                            "key": "player_points",
                            "outcomes": [
                                {"name": "Over", "description": "Jayson Tatum", "price": -108, "point": 26.5},
                                {"name": "Under", "description": "Jayson Tatum", "price": -112, "point": 26.5},
                            ],
                        }
                    ],
                },
            ],
        }
    ]

    c_df, q_df, report = write_oddsaccum(
        raw_payload,
        sport_key="basketball_nba",
        captured_ts="2026-11-15T23:25:00Z",
        master=master,
    )

    # Per-book rows must exist (proves we read raw, not best-book CSV)
    assert len(c_df) == 4
    assert len(q_df) == 0
    dk_rows = c_df[c_df["book_id"] == "draftkings"]
    fd_rows = c_df[c_df["book_id"] == "fanduel"]
    assert len(dk_rows) == 2
    assert len(fd_rows) == 2

    # last_update preserved as source_updated_at
    assert (dk_rows["source_updated_at"] == "2026-11-15T23:15:00Z").all()
    assert (fd_rows["source_updated_at"] == "2026-11-15T23:20:00Z").all()
    assert (c_df["price_kind"] == "live_snapshot").all()
    validate_published_frame(c_df)


# ===========================================================================
# 8. Kalshi Futures Ask-Only Test
# ===========================================================================

def test_kalshi_futures_ask_only():
    df = pd.DataFrame([
        {
            "venue": "kalshi",
            "series_ticker": "KXMLBHR",
            "event_ticker": "KXMLBHR-26JUL231507TBTOR",
            "market": "mlb_hr",
            "market_ticker": "KXMLBHR-26JUL231507TBTOR-TB",
            "outcome": "TB",
            "yes_bid": 0.0,
            "yes_ask": 0.01,
            "last_price": 0.01,
            "captured_at": "2026-07-23T15:00:00Z",
            "dt": "2026-07-23",
            "league": "mlb",
            "family": "futures",
        }
    ])

    c_df, q_df, report = write_kalshi_futures(df, dt="2026-07-23")

    assert len(c_df) == 1
    row = c_df.iloc[0]

    # Ask-only price conversion: 1 / 0.01 = 100.0
    assert row["price_decimal"] == 100.0
    assert row["ask_decimal"] == 100.0
    assert pd.isna(row["bid_decimal"]) or row["bid_decimal"] is None
    assert pd.isna(row["mid_prob"]) or row["mid_prob"] is None
    assert pd.isna(row["price_american"]) or row["price_american"] is None
    assert row["price_kind"] == "exchange_book"
    assert row["side"] == "yes"
    assert row["market_class"] == "binary_contract"

    # Player columns are None for binary_contract (game-line nullability rule)
    assert row["player_id"] is None
    assert row["player_name"] is None

    # Outcome carried in source_extras
    extras = json.loads(row["source_extras"])
    assert extras["outcome"] == "TB"

    validate_published_frame(c_df)


# ===========================================================================
# 9. Idempotence and Superseding Test
# ===========================================================================

def test_write_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    pub_keys = write_registry()
    assert "books" in pub_keys
    assert "markets" in pub_keys
    assert "teams" in pub_keys
    assert "player_aliases_nba" in pub_keys
    for key in pub_keys.values():
        assert storage.exists(key)


def test_read_layer_functions(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))

    # Day file
    sample_df = pd.DataFrame([{
        "league": "nba", "event_id": "ev1", "game_date_et": "2026-11-15",
        "start_time_utc": "2026-11-15T18:00:00Z", "away_team": "IND", "home_team": "PHI",
        "player_id": "p1", "player_name": "Tatum", "player_team": "PHI", "market": "points",
        "market_class": "main", "line": 25.5, "side": "over", "book_id": "draftkings",
        "price_american": -110.0, "price_decimal": 1.909091, "implied_raw": 0.52381,
        "bid_decimal": None, "ask_decimal": None, "mid_prob": None, "is_main_line": True,
        "observed_at": "2026-11-15T18:00:00Z", "source_updated_at": None,
        "price_kind": "closing_snapshot_unstamped", "source": "actionnetwork",
        "source_ref": "ref#1", "source_player_id": "1", "source_player_team": "PHI",
        "source_extras": "{}",
    }])
    storage.write_parquet(sample_df, "CanonicalOdds/nba/2026-11-15.parquet")
    loaded = load_day("nba", "2026-11-15")
    assert len(loaded) == 1

    # Filter
    filtered = filter_canonical(loaded, market="points", book_id="draftkings", player_id="p1")
    assert len(filtered) == 1
    none_filtered = filter_canonical(loaded, market="rebounds")
    assert len(none_filtered) == 0

def test_publish_idempotence(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))

    master = MasterIndex("nba")
    master.add_game(event_id="ev_idemp", game_date_et="2026-11-15", away_team="IND", home_team="PHI")
    master.add_player(master_player_id="pid_tatum", name="Jayson Tatum", team="PHI", game_date_et="2026-11-15")

    raw_fixture = pd.DataFrame([
        {
            "date": "2026-11-15", "event_id": "ev_idemp", "player_id": "198227", "player_name": "Jayson Tatum",
            "team_id": 163, "team_abbr": "PHI", "home_abbr": "PHI", "away_abbr": "IND",
            "start_time": "2026-11-15T18:00:00.000Z", "game_status": "complete", "market": "points",
            "source_type": "core_bet_type_27_points", "is_milestone": False, "threshold": None,
            "book_id": 68, "book_name": "draftkings", "side": "over", "line": 26.5, "odds": -110,
            "is_alt_market": False, "line_status": "normal", "period": "event",
            "market_id": "m1", "outcome_id": "o1", "scraped_at": "2026-11-16T08:00:00Z",
        }
    ])

    c_df, q_df, report = write_an(raw_fixture, league="nba", master=master, requested_date="2026-11-15")

    # First publish: returns status "published"
    res1 = publish("actionnetwork", "nba", "2026-11-15", c_df, q_df, report)
    assert res1["status"] == "published"
    run_id1 = res1["ingest_run_id"]
    parquet_path = tmp_path / "CanonicalOdds" / "nba" / "2026-11-15.parquet"
    bytes_first = parquet_path.read_bytes()

    # Second publish with same input: returns status "already_published"
    res2 = publish("actionnetwork", "nba", "2026-11-15", c_df, q_df, report)
    assert res2["status"] == "already_published"
    assert res2["ingest_run_id"] == run_id1
    bytes_second = parquet_path.read_bytes()
    assert bytes_first == bytes_second

    # Publish with force=True: supersedes with new ingest_run_id
    res3 = publish("actionnetwork", "nba", "2026-11-15", c_df, q_df, report, force=True)
    assert res3["status"] == "published"
    assert res3["ingest_run_id"] != run_id1


# ===========================================================================
# 10. Validate Published Frame Invariants Test
# ===========================================================================

def test_validate_published_frame_invariants():
    # Valid frame
    valid_df = pd.DataFrame([{
        "league": "nba", "event_id": "ev1", "game_date_et": "2026-11-15",
        "start_time_utc": "2026-11-15T18:00:00Z", "away_team": "IND", "home_team": "PHI",
        "player_id": "p1", "player_name": "Tatum", "player_team": "PHI", "market": "points",
        "market_class": "main", "line": 25.5, "side": "over", "book_id": "draftkings",
        "price_american": -110.0, "price_decimal": 1.909091, "implied_raw": 0.52381,
        "bid_decimal": None, "ask_decimal": None, "mid_prob": None, "is_main_line": True,
        "observed_at": "2026-11-15T18:00:00Z", "source_updated_at": None,
        "price_kind": "closing_snapshot_unstamped", "source": "actionnetwork",
        "source_ref": "ref#1", "source_player_id": "1", "source_player_team": "PHI",
        "source_extras": "{}",
    }])
    validate_published_frame(valid_df)

    # Invariant 1: Duplicate uniqueness key
    dup_df = pd.concat([valid_df, valid_df], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate uniqueness keys"):
        validate_published_frame(dup_df)

    # Invariant 2: player_team outside event pair
    bad_pt_df = valid_df.copy()
    bad_pt_df.loc[0, "player_team"] = "GSW"
    with pytest.raises(ValueError, match="outside event pair"):
        validate_published_frame(bad_pt_df)

    # Invariant 3: Milestone with side 'no'
    bad_ms_df = valid_df.copy()
    bad_ms_df.loc[0, "market_class"] = "milestone"
    bad_ms_df.loc[0, "side"] = "no"
    with pytest.raises(ValueError, match="milestone rows have side 'no'"):
        validate_published_frame(bad_ms_df)

    # Invariant 4: Prop row with null player_id
    bad_prop_df = valid_df.copy()
    bad_prop_df.loc[0, "player_id"] = None
    with pytest.raises(ValueError, match="prop rows have null player_id"):
        validate_published_frame(bad_prop_df)

    # But game lines with null player_id are valid
    game_line_df = valid_df.copy()
    game_line_df.loc[0, "market_class"] = "game_line"
    game_line_df.loc[0, "player_id"] = None
    game_line_df.loc[0, "player_name"] = None
    game_line_df.loc[0, "player_team"] = None
    validate_published_frame(game_line_df)
