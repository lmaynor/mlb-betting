"""
Tests for mlb.analysis.parlayapi_to_history (ParlayAPI OddsAccum -> odds_history).

Offline: local storage mode (tmp MLB_BASE_DATA), id_resolver caches primed.
"""

import json

import pandas as pd
import pytest

from mlb_core import storage
from mlb_core.data import id_resolver as R
from nba.config import PARLAY_PROP_MARKETS
import mlb.analysis.parlayapi_to_history as P


DATE = "2024-05-01"


def _raw_props_obj():
    return {
        "id": "evt1", "home_team": "Cleveland Guardians",
        "away_team": "Los Angeles Angels", "commence_time": "2024-05-01T22:10:00Z",
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "player_home_runs", "outcomes": [   # yes/no @ 0.5
                {"name": "Yes", "description": "Mike Trout", "price": 280, "point": 0.5},
                {"name": "No", "description": "Mike Trout", "price": -360, "point": 0.5}]},
            {"key": "player_hits", "outcomes": [
                {"name": "Over Jose Ramirez", "description": "Jose Ramirez", "price": -115, "point": 1.5},
                {"name": "Under Jose Ramirez", "description": "Jose Ramirez", "price": -105, "point": 1.5}]},
        ]}],
    }


def _prime(monkeypatch, tmp_path):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.setitem(R._schedule_cache, DATE, {("LAA", "CLE"): [745101]})
    monkeypatch.setitem(R._player_cache, "2024", (
        {"mike trout": {545361}, "jose ramirez": {608070}},
        {("mike trout", "LAA"): 545361, ("jose ramirez", "CLE"): 608070}))
    storage.write_bytes(json.dumps([_raw_props_obj()]).encode(),
                        "OddsAccum/baseball_mlb/raw/2024-05-01/props_1900.json")


def test_all_parlay_markets_mapped():
    for mkey in PARLAY_PROP_MARKETS["baseball_mlb"]:
        short = mkey[len("player_"):] if mkey.startswith("player_") else mkey
        short = {"pitcher_outs": "outs", "pitching_outs": "outs"}.get(short, short)
        assert short in P.PARLAY_TO_HISTORY, f"{mkey} ({short}) unmapped"


def test_rows_for_date_resolves_and_devigs(monkeypatch, tmp_path):
    _prime(monkeypatch, tmp_path)
    rows = P.rows_for_date(DATE, "2026-06-29T00:00:00Z")
    assert len(rows) == 4
    hr_over = next(r for r in rows if r["market"] == "hr_yn" and r["selection"] == "OVER")
    assert hr_over["game_pk"] == 745101
    assert hr_over["player_id"] == 545361
    assert hr_over["american"] == 280
    assert hr_over["source"] == "parlayapi"
    assert hr_over["fair_prob"] is not None
    hits = [r for r in rows if r["market"] == "bhits_ou"]
    assert {r["selection"] for r in hits} == {"OVER", "UNDER"}
    assert all(r["line"] == 1.5 for r in hits)


def test_convert_writes_partitions(monkeypatch, tmp_path):
    pytest.importorskip("pyarrow")
    _prime(monkeypatch, tmp_path)
    res = P.convert(since=DATE, until=DATE, ingested_at="2026-06-29T00:00:00Z")
    assert res["rows"] == 4
    assert set(res["markets"]) == {"hr_yn", "bhits_ou"}
    from mlb.analysis import odds_history as oh
    back = oh.read_history("hr_yn")
    assert (back["source"] == "parlayapi").all()


def test_convert_does_not_clobber_a_concurrent_writers_rows(monkeypatch, tmp_path):
    """Finding C4.4: convert() must pass append=True to write_partition --
    otherwise a different writer to this exact (market, date) partition
    (e.g. bettingpros_to_parquet.py's historical backfill, whose date range
    is not code-enforced disjoint from this forward/live feed's) would have
    its rows silently discarded the moment this script's forward ingest next
    touches that same partition. Simulates that other writer directly via
    odds_history.write_partition, then confirms convert() doesn't wipe it."""
    pytest.importorskip("pyarrow")
    _prime(monkeypatch, tmp_path)
    from mlb.analysis import odds_history as oh

    other_writer_row = pd.DataFrame([{
        "sport": "mlb", "market": "hr_yn", "system": "HR",
        "game_pk": 745101, "game_date": DATE, "event_id": "bp-evt-1",
        "away_team": "LAA", "home_team": "CLE", "player_id": 545361,
        "selection": "OVER", "line": 0.5, "book": "average",
        "american": 275, "decimal": None, "implied_prob": None,
        "fair_prob": 0.3, "snapshot_ts": f"{DATE} 23:59:00",
        "is_open": False, "is_closing": True,
        "source": "bettingpros", "ingested_at": "2026-01-01T00:00:00Z",
    }])
    oh.write_partition(other_writer_row, "hr_yn", DATE, append=True)

    res = P.convert(since=DATE, until=DATE, ingested_at="2026-06-29T00:00:00Z")
    # write_partition returns the partition's post-merge row COUNT, not a
    # delta -- 5 = hr_yn's 2 parlayapi rows merged with the 1 pre-seeded
    # bettingpros row (3), plus bhits_ou's untouched 2 parlayapi rows.
    assert res["rows"] == 5

    back = oh.read_history("hr_yn")
    assert (back["source"] == "bettingpros").sum() == 1, (
        "the other writer's row was clobbered -- convert() is not merging "
        "into the shared partition (finding C4.4 regression)"
    )
    # hr_yn's own 2 parlayapi rows (OVER+UNDER); bhits_ou's other 2 parlayapi
    # rows live in a separate partition, not read by read_history("hr_yn").
    assert (back["source"] == "parlayapi").sum() == 2
