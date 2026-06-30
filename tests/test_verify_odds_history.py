"""Tests for odds_history source-precedence + the verify audit (offline, local storage)."""

import pandas as pd
import pytest

from mlb.analysis import odds_history as oh


def test_dedupe_by_source_precedence():
    rows = [
        {"market": "hr_yn", "game_pk": 1, "selection": "OVER", "line": 0.5, "book": "dk",
         "snapshot_ts": "t", "source": "sgo", "american": 300},
        {"market": "hr_yn", "game_pk": 1, "selection": "OVER", "line": 0.5, "book": "dk",
         "snapshot_ts": "t", "source": "parlayapi", "american": 375},
        {"market": "hr_yn", "game_pk": 1, "selection": "OVER", "line": 0.5, "book": "dk",
         "snapshot_ts": "t", "source": "bettingpros", "american": 280},
    ]
    out = oh.dedupe_by_source(pd.DataFrame(rows))
    assert len(out) == 1
    assert out.iloc[0]["source"] == "parlayapi"   # highest precedence
    assert out.iloc[0]["american"] == 375


def test_verify_audit_flags_resolution_gap(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    from mlb_core import storage
    from mlb.analysis import verify_odds_history as V

    storage.write_csv(pd.DataFrame([{"game_pk": 5, "batter": 99, "pitcher": 7}]),
                      "Statcast/statcast_master.csv")
    rows = [
        {"sport": "mlb", "market": "hr_yn", "selection": "OVER", "line": 0.5, "book": "dk",
         "american": 300, "implied_prob": 0.25, "fair_prob": 0.2, "game_pk": 5, "player_id": 99,
         "game_date": "2024-05-01", "snapshot_ts": "t", "source": "parlayapi",
         "away_team": "X", "home_team": "Y"},
        {"sport": "mlb", "market": "hr_yn", "selection": "UNDER", "line": 0.5, "book": "dk",
         "american": -400, "implied_prob": 0.8, "fair_prob": 0.8, "game_pk": 5, "player_id": 99,
         "game_date": "2024-05-01", "snapshot_ts": "t", "source": "parlayapi",
         "away_team": "X", "home_team": "Y"},
        {"sport": "mlb", "market": "hr_yn", "selection": "OVER", "line": 0.5, "book": "novig",
         "american": 1000, "implied_prob": 0.09, "fair_prob": None, "game_pk": 5, "player_id": None,
         "game_date": "2024-05-01", "snapshot_ts": "t", "source": "parlayapi",
         "away_team": "X", "home_team": "Y"},
    ]
    oh.write_partition(pd.DataFrame(rows, columns=oh.SCHEMA_COLUMNS), "hr_yn", "2024-05-01")
    r = V.audit_market("hr_yn")
    assert r["game_pk_resolved"] == 1.0
    assert round(r["player_id_resolved"], 2) == 0.67     # 1 of 3 null
    assert r["realized_via"] == "batter"
    assert r["join_cov"] == 1.0                          # (5,99) in statcast batter set
    assert r["PASS"] is False                            # fails on player_id < 95%


def test_every_market_has_realized_mapping():
    from mlb.analysis import verify_odds_history as V
    from mlb.analysis.bettingpros_to_parquet import BP_TO_HISTORY
    canon = {mkt for mkt, _sys in BP_TO_HISTORY.values()}
    for mkt in canon:
        assert mkt in V.REALIZED, f"{mkt} has no realized-outcome source mapping"
