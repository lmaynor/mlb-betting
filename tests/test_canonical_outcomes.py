"""tests/test_canonical_outcomes.py -- BNK-12 Neon-delta coverage (RUL-20261119):
deterministic hash outcome ids, mandatory period/season key components,
fail-closed venue outcome map, the five named views, and the two-tier
watermark (zero re-translation)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from mlb_core.odds.canonical import ingest as ingest_mod
from mlb_core.odds.canonical import read as read_mod
from mlb_core.odds.canonical.outcomes import (
    build_outcomes,
    future_natural_key,
    outcome_id,
    prop_natural_key,
    resolve_venue_outcome,
    season_for,
)
from mlb_core.odds.canonical.schema import empty_frame


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    yield


def _prop_row(**kw):
    base = {
        "league": "nba", "event_id": "nba-2026-01-05-LAL@DEN", "player_id": "2544",
        "market": "points", "market_class": "main", "line": 25.5, "side": "over",
        "book_id": "draftkings", "observed_at": "2026-01-05T18:00:00Z",
        "player_name": "Test Player", "player_team": "LAL",
        "away_team": "LAL", "home_team": "DEN", "game_date_et": "2026-01-05",
        "start_time_utc": "2026-01-06T00:00:00Z", "price_american": -110,
        "price_decimal": 1.909, "implied_raw": 0.524, "bid_decimal": None,
        "ask_decimal": None, "mid_prob": None, "is_main_line": True,
        "source_updated_at": None, "price_kind": "closing_snapshot_unstamped",
        "source": "actionnetwork", "source_ref": "fixture:0", "source_player_id": "99",
        "source_player_team": "LAL", "source_extras": "{}",
    }
    base.update(kw)
    return base


def test_outcome_id_is_deterministic_hash_never_sequential():
    k1 = ("nba", "ev1", "2544", "points", "full_game", "25.5", "over")
    assert outcome_id(k1) == outcome_id(tuple(k1))
    assert outcome_id(k1).startswith("oc_")
    k2 = ("nba", "ev1", "2544", "points", "full_game", "26.5", "over")
    assert outcome_id(k1) != outcome_id(k2)


def test_period_is_a_mandatory_key_component():
    full = _prop_row()
    first_q = _prop_row(source_extras=json.dumps({"period": "1q"}))
    assert prop_natural_key(full)[4] == "full_game"  # default present, never absent
    assert prop_natural_key(first_q)[4] == "1q"
    assert outcome_id(prop_natural_key(full)) != outcome_id(prop_natural_key(first_q))


def test_future_key_carries_season():
    assert season_for("nba", "2026-11-19") == "2026"
    assert season_for("nba", "2027-03-01") == "2026"
    assert season_for("mlb", "2026-06-01") == "2026"
    row = {"book_id": "kalshi", "market": "KXNBA", "league": "nba",
           "game_date_et": "2026-11-19", "source_extras": json.dumps({"outcome": "Boston Celtics"})}
    key = future_natural_key(row)
    assert key == ("kalshi", "KXNBA", "Boston Celtics", "2026")
    row27 = dict(row, game_date_et="2027-11-19")
    assert future_natural_key(row27)[3] == "2027"
    assert outcome_id(key) != outcome_id(future_natural_key(row27))


def test_venue_outcome_map_fail_closed():
    # Kalshi resolves by the documented identity rule.
    assert resolve_venue_outcome("kalshi", "KXNBA", "Boston Celtics", season="2026") == (
        "kalshi", "KXNBA", "Boston Celtics", "2026")
    # An unmapped venue/outcome does NOT resolve: caller quarantines it.
    assert resolve_venue_outcome("somevenue", "MKT", "Outcome", season="2026") is None


def test_build_outcomes_groups_by_natural_key():
    df = pd.DataFrame([_prop_row(), _prop_row(observed_at="2026-01-05T19:00:00Z"),
                       _prop_row(line=26.5)])
    out = build_outcomes(df)
    assert len(out) == 2
    two = out[out["n_quotes"] == 2].iloc[0]
    assert two["first_observed"] == "2026-01-05T18:00:00Z"
    assert two["last_observed"] == "2026-01-05T19:00:00Z"
    assert two["period"] == "full_game"


def test_views_over_published_fixture():
    prop = _prop_row()
    fut = _prop_row(market_class="binary_contract", player_id=None, player_name="",
                    market="KXNBA", side="yes", line=None, book_id="kalshi",
                    source="kalshi", price_kind="exchange_book")
    df = pd.DataFrame([prop, fut])
    res = ingest_mod.publish("actionnetwork", "nba", "2026-01-05", df,
                             pd.DataFrame(), {"rows_in": 2})
    assert res["status"] == "published"
    assert len(read_mod.canonical_quotes("nba", "2026-01-05")) == 2
    futs = read_mod.canonical_futures("2026-01-05", leagues=["nba"])
    assert len(futs) == 1 and futs.iloc[0]["market"] == "KXNBA"
    outs = read_mod.canonical_outcomes("nba", "2026-01-05")
    assert set(outs["kind"]) == {"prop", "future"}
    pe = read_mod.canonical_prop_eval("nba", "2026-01-05")
    assert len(pe) == 1 and pe.iloc[0]["market"] == "points"  # futures row excluded


def test_unmapped_quarantine_view_filters_reason():
    q = pd.DataFrame([
        {"reject_reason": "unmapped_outcome", "x": 1},
        {"reject_reason": "unresolved", "x": 2},
    ])
    ingest_mod.publish("kalshi", "futures", "2026-01-05", empty_frame(), q, {"rows_in": 2})
    um = read_mod.unmapped_quarantine("kalshi", "2026-01-05")
    assert len(um) == 1 and um.iloc[0]["x"] == 1


def test_watermark_records_publish_and_blocks_retranslation():
    df = pd.DataFrame([_prop_row()])
    res = ingest_mod.publish("actionnetwork", "nba", "2025-12-01", df, pd.DataFrame(), {"rows_in": 1})
    assert res["status"] == "published"
    assert ingest_mod.watermarked("actionnetwork", "nba", "2025-12-01")
    assert not ingest_mod.watermarked("actionnetwork", "nba", "2025-12-02")
    # Tier-2 CLI discipline: a watermarked date is filtered out (zero re-translation).
    import argparse
    import importlib.util
    import os
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "canonical_translate.py"))
    spec = importlib.util.spec_from_file_location("canonical_translate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    args = argparse.Namespace(tier=2, force=False, root="")
    assert mod.tier_dates(["2025-12-01", "2025-12-02"], args, "actionnetwork", "nba") == ["2025-12-02"]
    args1 = argparse.Namespace(tier=1, force=False, root="")
    assert mod.tier_dates(["2025-12-02"], args1, "actionnetwork", "nba") == []  # pre-floor refused in Tier 1
    assert mod.tier_dates(["2026-11-19"], args1, "actionnetwork", "nba") == ["2026-11-19"]
