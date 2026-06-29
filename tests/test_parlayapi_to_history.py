"""
Tests for mlb.analysis.parlayapi_to_history (ParlayAPI OddsAccum -> odds_history).

Offline: local storage mode (tmp MLB_BASE_DATA), id_resolver caches primed.
"""

import json

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
            {"key": "player_home_runs", "outcomes": [
                {"name": "Over Mike Trout", "description": "Mike Trout", "price": 280, "point": 0.5},
                {"name": "Under Mike Trout", "description": "Mike Trout", "price": -360, "point": 0.5}]},
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
