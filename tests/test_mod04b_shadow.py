"""Offline synthetic tests for MOD-04 B6 shadow banking and grading (`nhl.props.mod04_shadow`)."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from nhl.props import mod04_shadow as SH


def test_blend_p_endpoints_and_logit_formula():
    assert SH.blend_p(0.72, 0.45, c=0.0) == pytest.approx(0.45, abs=1e-12)
    assert SH.blend_p(0.72, 0.45, c=1.0) == pytest.approx(0.72, abs=1e-12)

    p_mkt = 0.40
    p_mod = 0.65
    c = 0.35
    expected = SH.sigmoid(SH.logit(p_mkt) + c * (SH.logit(p_mod) - SH.logit(p_mkt)))
    assert SH.blend_p(p_mod, p_mkt, c=c) == pytest.approx(expected, abs=1e-12)


def test_shadow_writer_round_trip_and_dual_logging_fields(tmp_path):
    pmf_a = [0.10, 0.25, 0.30, 0.20, 0.10, 0.05]  # P(Y > 2.5) = 0.35
    pmf_b = [0.05, 0.15, 0.25, 0.30, 0.15, 0.10]  # P(Y > 3.5) = 0.25

    rows = [
        {
            "date": "2026-11-24",
            "sport": "nhl",
            "family": "sog",
            "player_id": 101,
            "player_name": "Skater A",
            "game_id": 2026020100,
            "engine": "nhl_sog_trained_v1",
            "mu": 2.10,
            "alpha": 0.08,
            "pmf": pmf_a,
            "main_line": 2.5,
            # No market supplied -> p_served == p_model and served_note == "shadow_no_market"
        },
        {
            "date": "2026-11-24",
            "sport": "nba",
            "family": "rebounds",
            "player_id": 202,
            "player_name": "Player B",
            "game_id": "nba_g_1",
            "engine": "nba_rebounds_decomp_shadow_v1",
            "mu": 7.5,
            "alpha": 0.06,
            "pmf": pmf_b,
            "main_line": 3.5,
            "p_market": 0.50,
            "c": 0.5,
        },
    ]

    out_file = tmp_path / "shadow" / "latest.json"
    SH.write_shadow_predictions(rows, out_file, version="vtest", generated_at="2026-11-24T18:00:00Z")
    assert out_file.exists()

    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["version"] == "vtest"
    assert loaded["generated_at"] == "2026-11-24T18:00:00Z"
    assert len(loaded["predictions"]) == 2

    r0, r1 = loaded["predictions"]
    required_keys = {
        "date", "sport", "family", "player_id", "player_name", "game_id",
        "engine", "mu", "alpha", "pmf", "p_model", "p_served", "generated_at",
    }
    assert required_keys.issubset(set(r0.keys()))
    assert required_keys.issubset(set(r1.keys()))

    # Row 0: no market -> p_served equals p_model and served_note is set
    assert r0["p_model"]["2.5"] == pytest.approx(0.35, abs=1e-12)
    assert r0["p_served"] == r0["p_model"]
    assert r0["served_note"] == "shadow_no_market"

    # Row 1: market supplied -> p_model != p_served, p_served == blend_p(0.25, 0.50, 0.5)
    assert r1["p_model"]["3.5"] == pytest.approx(0.25, abs=1e-12)
    assert r1["p_served"]["3.5"] == pytest.approx(SH.blend_p(0.25, 0.50, 0.5), abs=1e-12)
    assert "served_note" not in r1


def test_shadow_gcs_writer_keys_with_mocked_storage(monkeypatch):
    from mlb_core import storage

    written: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "write_bytes", lambda data, key: written.__setitem__(key, data))

    row = {
        "date": "2026-11-24",
        "sport": "nhl",
        "family": "sog",
        "player_id": 101,
        "player_name": "Skater A",
        "game_id": 1,
        "engine": "nhl_sog_trained_v1",
        "mu": 2.0,
        "alpha": 0.05,
        "pmf": [0.2, 0.3, 0.3, 0.2],
        "main_line": 1.5,
    }
    k_nhl = SH.write_shadow_predictions_gcs([row], sport="nhl", family="sog")
    k_nba = SH.write_shadow_predictions_gcs([row], sport="nba", family="rebounds")

    assert k_nhl == "NHL/mod04_shadow/sog_latest.json"
    assert k_nba == "NBA/mod04_shadow/rebounds_latest.json"
    assert k_nhl in written and k_nba in written
    assert json.loads(written[k_nhl].decode("utf-8"))["predictions"][0]["p_model"]["1.5"] == pytest.approx(0.5)


def test_score_shadow_day_computes_logloss_and_brier(tmp_path):
    rows = [
        {
            "date": "2026-11-24",
            "sport": "nhl",
            "family": "sog",
            "player_id": 101,
            "player_name": "Skater A",
            "game_id": 1,
            "engine": "nhl_sog_trained_v1",
            "mu": 2.5,
            "alpha": 0.05,
            "pmf": [0.1, 0.2, 0.3, 0.4],  # P(Y > 2.5) = 0.4
            "main_line": 2.5,
        },
        {
            "date": "2026-11-24",
            "sport": "nhl",
            "family": "sog",
            "player_id": 102,
            "player_name": "Skater B",
            "game_id": 1,
            "engine": "nhl_sog_trained_v1",
            "mu": 1.5,
            "alpha": 0.05,
            "pmf": [0.2, 0.4, 0.3, 0.1],  # P(Y > 1.5) = 0.4
            "main_line": 1.5,
            "p_market": 0.5,
            "c": 0.5,
        },
    ]
    pred_path = tmp_path / "preds.json"
    SH.write_shadow_predictions(rows, pred_path)

    labels = pd.DataFrame({
        "game_id": [1, 1],
        "player_id": [101, 102],
        "actual": [3.0, 1.0],
    })
    graded = SH.score_shadow_day(pred_path, labels)
    assert len(graded) == 2

    g0 = graded.loc[graded["player_id"] == 101].iloc[0]
    assert g0["y_over"] == 1.0
    assert g0["brier"] == pytest.approx((0.4 - 1.0) ** 2, abs=1e-12)
    assert g0["log_loss"] == pytest.approx(-math.log(0.4), abs=1e-12)

    g1 = graded.loc[graded["player_id"] == 102].iloc[0]
    assert g1["y_over"] == 0.0
    assert g1["brier"] == pytest.approx((0.4 - 0.0) ** 2, abs=1e-12)
    assert g1["log_loss"] == pytest.approx(-math.log(0.6), abs=1e-12)
    assert np.isfinite(g1["brier_served"]) and np.isfinite(g1["log_loss_served"])
