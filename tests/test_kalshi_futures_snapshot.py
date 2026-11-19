"""tests/test_kalshi_futures_snapshot.py -- BNK-14 Kalshi futures daily snapshotter tests.

Covers:
  - Row-building from inline fixture payloads shaped like real Kalshi API responses:
      (a) two-sided market -> bid/ask/last parsed as floats
      (b) ask-only market (bid "0.0000") -> yes_bid == 0.0, not None
      (c) market missing volume/open-interest fields -> nulls, row still produced
      (d) ladder market with floor_strike -> strike fields carried
      (e) series with zero open events -> zero rows, manifest entry with row_count 0
  - Config coverage: every key of probe_summary.json is in EXPLICIT_SERIES
  - Manifest and idempotence tests: writing twice to --out-dir produces identical paths and valid data
  - Discovery filtering: draft series excluded, pattern families included
  - Failure handling: partial day banking with non-zero exit and failed_series recorded
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
import pandas as pd
import pytest

from ev import kalshi_futures_snapshot as snap


# -----------------------------------------------------------------------------
# Fixtures shaped like real Kalshi API samples
# -----------------------------------------------------------------------------
SAMPLE_TWO_SIDED_MARKET = {
    "ticker": "KXNBA-27-BOS",
    "event_ticker": "KXNBA-27",
    "yes_sub_title": "Boston",
    "status": "active",
    "yes_bid_dollars": "0.0900",
    "yes_ask_dollars": "0.1000",
    "last_price_dollars": "0.1000",
    "volume_fp": "2720382.22",
    "open_interest_fp": "2520854.43",
    "strike_type": "structured",
    "floor_strike": None,
    "cap_strike": None,
}

SAMPLE_ASK_ONLY_MARKET = {
    "ticker": "KXNBA-27-ATL",
    "event_ticker": "KXNBA-27",
    "yes_sub_title": "Atlanta",
    "status": "active",
    "yes_bid_dollars": "0.0000",
    "yes_ask_dollars": "0.0100",
    "last_price_dollars": "0.0100",
    "volume_fp": "1920854.52",
    "open_interest_fp": "1780853.13",
    "strike_type": "structured",
    "floor_strike": None,
    "cap_strike": None,
}

SAMPLE_MISSING_VOLUME_MARKET = {
    "ticker": "KXBIG10REG-26-MICH",
    "event_ticker": "KXBIG10REG-26",
    "yes_sub_title": "Michigan",
    "status": "active",
    "yes_bid_dollars": "0.1500",
    "yes_ask_dollars": "0.2000",
    # volume_fp, open_interest_fp, last_price_dollars omitted
}

SAMPLE_LADDER_MARKET = {
    "ticker": "KXNBAWINS-27ATL-20",
    "event_ticker": "KXNBAWINS-27ATL",
    "yes_sub_title": "20+ wins",
    "status": "active",
    "yes_bid_dollars": "0.9500",
    "yes_ask_dollars": "1.0000",
    "last_price_dollars": "0.9800",
    "volume_fp": "10.00",
    "open_interest_fp": "10.00",
    "strike_type": "greater_or_equal",
    "floor_strike": 20,
    "cap_strike": None,
}


# -----------------------------------------------------------------------------
# Row-building tests
# -----------------------------------------------------------------------------
class TestRowBuilding:
    def test_two_sided_market_parsed_as_floats(self):
        """(a) Two-sided market -> bid/ask/last parsed as floats."""
        row = snap.build_market_row(
            market=SAMPLE_TWO_SIDED_MARKET,
            series_ticker="KXNBA",
            event_ticker="KXNBA-27",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at="2026-11-19T06:10:00Z",
            dt="2026-11-19",
            league="nba",
            family="champion",
        )

        assert row["venue"] == "kalshi"
        assert row["series_ticker"] == "KXNBA"
        assert row["event_ticker"] == "KXNBA-27"
        assert row["market"] == "KXNBA-27"
        assert row["market_ticker"] == "KXNBA-27-BOS"
        assert row["outcome"] == "Boston"
        assert row["yes_sub_title"] == "Boston"
        assert isinstance(row["yes_bid"], float)
        assert row["yes_bid"] == 0.09
        assert isinstance(row["yes_ask"], float)
        assert row["yes_ask"] == 0.10
        assert isinstance(row["last_price"], float)
        assert row["last_price"] == 0.10
        assert row["volume_fp"] == 2720382.22
        assert row["open_interest_fp"] == 2520854.43
        assert row["status"] == "active"
        assert row["fee_type"] == "quadratic"
        assert row["fee_multiplier"] == 1.0
        assert row["league"] == "nba"
        assert row["family"] == "champion"
        assert row["dt"] == "2026-11-19"
        assert row["captured_at"] == "2026-11-19T06:10:00Z"

    def test_ask_only_market_keeps_zero_bid(self):
        """(b) Ask-only market (bid '0.0000') -> yes_bid == 0.0, not None."""
        row = snap.build_market_row(
            market=SAMPLE_ASK_ONLY_MARKET,
            series_ticker="KXNBA",
            event_ticker="KXNBA-27",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at="2026-11-19T06:10:00Z",
            dt="2026-11-19",
            league="nba",
            family="champion",
        )

        assert row["yes_bid"] is not None
        assert row["yes_bid"] == 0.0
        assert row["yes_ask"] == 0.01
        assert row["last_price"] == 0.01

    def test_missing_volume_open_interest_fields(self):
        """(c) Market missing volume/open-interest/last-price -> nulls, row still produced."""
        row = snap.build_market_row(
            market=SAMPLE_MISSING_VOLUME_MARKET,
            series_ticker="KXBIG10REG",
            event_ticker="KXBIG10REG-26",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at="2026-11-19T06:10:00Z",
            dt="2026-11-19",
            league="ncaab",
            family="conference",
        )

        assert row["yes_bid"] == 0.15
        assert row["yes_ask"] == 0.20
        assert row["last_price"] is None
        assert row["volume_fp"] is None
        assert row["open_interest_fp"] is None
        assert row["floor_strike"] is None
        assert row["cap_strike"] is None
        assert row["market_ticker"] == "KXBIG10REG-26-MICH"
        assert row["outcome"] == "Michigan"

    def test_ladder_market_carries_strike_fields(self):
        """(d) Ladder market with floor_strike -> strike fields carried."""
        row = snap.build_market_row(
            market=SAMPLE_LADDER_MARKET,
            series_ticker="KXNBAWINS",
            event_ticker="KXNBAWINS-27ATL",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at="2026-11-19T06:10:00Z",
            dt="2026-11-19",
            league="nba",
            family="win_totals",
        )

        assert row["strike_type"] == "greater_or_equal"
        assert row["floor_strike"] == 20.0
        assert row["cap_strike"] is None
        assert row["outcome"] == "20+ wins"

    def test_zero_open_events_dormant_series(self):
        """(e) Series with zero open events -> zero rows, manifest entry with row_count 0."""
        fake_data = {
            "/events": {"events": [], "cursor": None},
            "/series/KXNFLDROY": {"series": {"ticker": "KXNFLDROY", "fee_type": "quadratic", "fee_multiplier": 1}},
        }

        def fake_fetch(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "/events":
                return fake_data["/events"]
            return fake_data.get(path, {})

        client = snap.KalshiFuturesClient(fetch_json_fn=fake_fetch)
        rows, manifest, failed = snap.collect_snapshot(
            client=client,
            target_series=["KXNFLDROY"],
            dt="2026-11-19",
        )

        assert len(rows) == 0
        assert len(failed) == 0
        assert "KXNFLDROY" in manifest["per_series"]
        series_info = manifest["per_series"]["KXNFLDROY"]
        assert series_info["rows"] == 0
        assert series_info["row_count"] == 0
        assert series_info["events"] == 0
        assert series_info["league"] == "nfl"
        assert series_info["family"] == "awards"


# -----------------------------------------------------------------------------
# Config coverage test
# -----------------------------------------------------------------------------
def test_probe_summary_keys_covered_in_explicit_series():
    """Config coverage test: every key of probe_summary.json is in EXPLICIT_SERIES."""
    probe_summary_path = Path(os.path.expanduser("~/workspace/beezy/kalshi-futures-work/probe_summary.json"))
    if not probe_summary_path.exists():
        pytest.skip(f"{probe_summary_path} not found")

    with open(probe_summary_path) as f:
        probe_summary = json.load(f)

    probe_keys = set(probe_summary.keys())
    explicit_keys = set(snap.EXPLICIT_SERIES.keys())

    missing = probe_keys - explicit_keys
    assert not missing, f"probe_summary.json keys missing from EXPLICIT_SERIES: {missing}"


# -----------------------------------------------------------------------------
# Manifest & Idempotence test
# -----------------------------------------------------------------------------
def test_manifest_and_idempotence(tmp_path: Path):
    """Manifest/idempotence test using --out-dir semantics.

    Building partition payload twice for same dt yields identical object names
    and overwrites properly.
    """
    dt = "2026-11-19"
    captured_at = "2026-11-19T06:10:00Z"

    sample_market_1 = dict(SAMPLE_TWO_SIDED_MARKET)
    sample_market_2 = dict(SAMPLE_LADDER_MARKET)

    rows = [
        snap.build_market_row(
            market=sample_market_1,
            series_ticker="KXNBA",
            event_ticker="KXNBA-27",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at=captured_at,
            dt=dt,
            league="nba",
            family="champion",
        ),
        snap.build_market_row(
            market=sample_market_2,
            series_ticker="KXNBAWINS",
            event_ticker="KXNBAWINS-27ATL",
            fee_type="quadratic",
            fee_multiplier=1.0,
            captured_at=captured_at,
            dt=dt,
            league="nba",
            family="win_totals",
        ),
    ]

    manifest = {
        "dt": dt,
        "captured_at": captured_at,
        "total_rows": len(rows),
        "total_requests": 3,
        "per_series": {
            "KXNBA": {"family": "champion", "league": "nba", "rows": 1, "row_count": 1, "events": 1},
            "KXNBAWINS": {"family": "win_totals", "league": "nba", "rows": 1, "row_count": 1, "events": 1},
        },
        "families": {
            "champion": {"series_count": 1, "rows": 1, "events": 1},
            "win_totals": {"series_count": 1, "rows": 1, "events": 1},
        },
        "discovered_series": [],
        "failed_series": [],
    }

    # Pass 1: Write first time
    parquet_p1, manifest_p1 = snap.save_snapshot(
        rows=rows,
        manifest=manifest,
        dt=dt,
        out_dir=tmp_path,
    )

    # Pass 2: Write second time (same dt)
    parquet_p2, manifest_p2 = snap.save_snapshot(
        rows=rows,
        manifest=manifest,
        dt=dt,
        out_dir=tmp_path,
    )

    # Both passes produce identical object paths
    assert parquet_p1 == parquet_p2
    assert manifest_p1 == manifest_p2

    expected_parquet = tmp_path / f"Futures/kalshi/dt={dt}/snapshot.parquet"
    expected_manifest = tmp_path / f"Futures/kalshi/dt={dt}/manifest.json"

    assert Path(parquet_p1) == expected_parquet
    assert Path(manifest_p1) == expected_manifest
    assert expected_parquet.exists()
    assert expected_manifest.exists()

    # Validate Parquet contents
    df = pd.read_parquet(expected_parquet)
    assert len(df) == 2
    assert list(df["market_ticker"]) == ["KXNBA-27-BOS", "KXNBAWINS-27ATL-20"]
    assert df["yes_bid"].iloc[0] == 0.09
    assert df["floor_strike"].iloc[1] == 20.0

    # Validate Manifest contents
    with open(expected_manifest) as f:
        loaded_manifest = json.load(f)
    assert loaded_manifest["dt"] == dt
    assert loaded_manifest["total_rows"] == 2
    assert "KXNBA" in loaded_manifest["per_series"]
    assert loaded_manifest["per_series"]["KXNBA"]["rows"] == 1


# -----------------------------------------------------------------------------
# Discovery & Draft exclusion tests
# -----------------------------------------------------------------------------
class TestDiscovery:
    def test_draft_series_excluded(self):
        """Draft series (KXNBADRAFT*, KXNFLDRAFT*) must be excluded from sleeve discovery."""
        assert snap.is_draft_series("KXNBADRAFT1") is True
        assert snap.is_draft_series("KXNBADRAFTOU") is True
        assert snap.is_draft_series("KXNFLDRAFT1ST") is True
        assert snap.is_draft_series("KXNFLDRAFTQB") is True
        assert snap.is_draft_series("KXNBA") is False
        assert snap.is_draft_series("KXNFLMVP") is False

        catalog = [
            {"ticker": "KXNBADRAFT1"},
            {"ticker": "KXNFLDRAFT1ST"},
            {"ticker": "KXLEADERMLBHR"},
            {"ticker": "KXMLBWINS-BOS"},
            {"ticker": "KXNFLSEASONPASSYDS"},
            {"ticker": "KXMARMADROUND"},
            {"ticker": "KXBIG10REG"},
            {"ticker": "KXRANDOMOTHER"},
        ]

        discovered = snap.discover_series(catalog, explicit_tickers=set(snap.EXPLICIT_SERIES.keys()))

        # Discovered contains pattern matches
        assert "KXLEADERMLBHR" in discovered
        assert "KXMLBWINS-BOS" in discovered
        assert "KXNFLSEASONPASSYDS" in discovered
        assert "KXMARMADROUND" in discovered
        assert "KXBIG10REG" in discovered

        # Draft and non-pattern series are not discovered
        assert "KXNBADRAFT1" not in discovered
        assert "KXNFLDRAFT1ST" not in discovered
        assert "KXRANDOMOTHER" not in discovered

    def test_infer_metadata(self):
        """Metadata inference correctly tags leagues and families."""
        assert snap.infer_metadata("KXNBA") == ("nba", "champion")
        assert snap.infer_metadata("KXSB") == ("nfl", "champion")
        assert snap.infer_metadata("KXNHLPRES") == ("nhl", "presidents_trophy")
        assert snap.infer_metadata("KXNCAAFPLAYOFF") == ("ncaaf", "cfp")
        assert snap.infer_metadata("KXNCAAMBNAISMITH") == ("ncaab", "naismith")
        assert snap.infer_metadata("KXLEADERMLBHR") == ("mlb", "stat_leader")
        assert snap.infer_metadata("KXLEADERNFLPYDS") == ("nfl", "stat_leader")
        assert snap.infer_metadata("KXMLBWINS-NYY") == ("mlb", "win_totals")
        assert snap.infer_metadata("KXNFLSEASONPASSYDS") == ("nfl", "season_totals")
        assert snap.infer_metadata("KXMARMADROUND") == ("ncaab", "round_reach")
        assert snap.infer_metadata("KXACCREG") == ("ncaab", "conference")
        assert snap.infer_metadata("KXUNKNOWN123") == ("other", "other")


# -----------------------------------------------------------------------------
# Error handling & partial banking tests
# -----------------------------------------------------------------------------
class TestFailureHandling:
    def test_partial_day_banked_with_failure_recorded(self, tmp_path: Path):
        """Series fetch failure records in failed_series and manifest while remaining series bank."""
        def fake_fetch(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "/series/KXNBA":
                return {"series": {"ticker": "KXNBA", "fee_type": "quadratic", "fee_multiplier": 1}}
            if path == "/events":
                st = params.get("series_ticker")
                if st == "KXNBA":
                    return {
                        "events": [
                            {
                                "event_ticker": "KXNBA-27",
                                "markets": [SAMPLE_TWO_SIDED_MARKET],
                            }
                        ],
                        "cursor": None,
                    }
                if st == "KXFAILING":
                    raise ConnectionError("Simulated network failure")
            return {}

        client = snap.KalshiFuturesClient(fetch_json_fn=fake_fetch)
        rows, manifest, failed = snap.collect_snapshot(
            client=client,
            target_series=["KXNBA", "KXFAILING"],
            dt="2026-11-19",
        )

        assert len(rows) == 1
        assert rows[0]["series_ticker"] == "KXNBA"
        assert failed == ["KXFAILING"]
        assert manifest["failed_series"] == ["KXFAILING"]
        assert "error" in manifest["per_series"]["KXFAILING"]
        assert manifest["per_series"]["KXFAILING"]["rows"] == 0

        # Can still save snapshot cleanly
        parquet_p, manifest_p = snap.save_snapshot(
            rows=rows,
            manifest=manifest,
            dt="2026-11-19",
            out_dir=tmp_path,
        )
        assert Path(parquet_p).exists()
        assert Path(manifest_p).exists()


# -----------------------------------------------------------------------------
# CLI execution tests
# -----------------------------------------------------------------------------
class TestCli:
    def test_cli_success(self, tmp_path: Path):
        """CLI main() executes successfully with --series and --out-dir."""
        def fake_fetch(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "/series/KXNBA":
                return {"series": {"ticker": "KXNBA", "fee_type": "quadratic", "fee_multiplier": 1}}
            if path == "/events":
                return {
                    "events": [
                        {
                            "event_ticker": "KXNBA-27",
                            "markets": [SAMPLE_TWO_SIDED_MARKET],
                        }
                    ],
                    "cursor": None,
                }
            return {}

        client = snap.KalshiFuturesClient(fetch_json_fn=fake_fetch)
        exit_code = snap.main(
            argv=["--series", "KXNBA", "--date", "2026-11-19", "--out-dir", str(tmp_path)],
            client=client,
        )
        assert exit_code == 0

        parquet_file = tmp_path / "Futures/kalshi/dt=2026-11-19/snapshot.parquet"
        manifest_file = tmp_path / "Futures/kalshi/dt=2026-11-19/manifest.json"
        assert parquet_file.exists()
        assert manifest_file.exists()

    def test_cli_nonzero_on_failed_series(self, tmp_path: Path):
        """CLI main() returns non-zero when a series fails, but still banks the rest."""
        def fake_fetch(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "/series/KXNBA":
                return {"series": {"ticker": "KXNBA", "fee_type": "quadratic", "fee_multiplier": 1}}
            if path == "/events":
                st = params.get("series_ticker")
                if st == "KXNBA":
                    return {
                        "events": [
                            {
                                "event_ticker": "KXNBA-27",
                                "markets": [SAMPLE_TWO_SIDED_MARKET],
                            }
                        ],
                        "cursor": None,
                    }
                raise RuntimeError("Fake network crash")
            return {}

        client = snap.KalshiFuturesClient(fetch_json_fn=fake_fetch)
        exit_code = snap.main(
            argv=["--series", "KXNBA,KXFAIL", "--date", "2026-11-19", "--out-dir", str(tmp_path)],
            client=client,
        )
        assert exit_code == 1

        # Manifest and parquet were still written
        parquet_file = tmp_path / "Futures/kalshi/dt=2026-11-19/snapshot.parquet"
        manifest_file = tmp_path / "Futures/kalshi/dt=2026-11-19/manifest.json"
        assert parquet_file.exists()
        assert manifest_file.exists()

        with open(manifest_file) as f:
            manifest_data = json.load(f)
        assert manifest_data["failed_series"] == ["KXFAIL"]
        assert manifest_data["total_rows"] == 1

