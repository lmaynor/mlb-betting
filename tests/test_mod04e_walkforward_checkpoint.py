"""Tests for MOD-04e E1 checkpoint/merge helpers (resilient chunked relaunch).

The checkpoint path is pure I/O: append_fold_rows writes exactly the rows the
in-memory path feeds to compute_metrics, and build_families_from_rows must
reproduce identical metrics from the checkpoint files, including across
per-season chunk files.
"""
import json

import numpy as np
import pytest

from scripts import mod04e_walkforward as wf


def _rows(seed: int, n: int, xgb: bool):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, size=n).astype(float)
    pm = np.clip(0.35 + 0.3 * y + rng.normal(0, 0.05, n), 0.05, 0.95)
    pk = np.full(n, 0.5)
    if xgb:
        px = np.clip(0.4 + 0.2 * y, 0.05, 0.95)
        px[::7] = np.nan  # XGBoost missing on some rows, like the real harness
    else:
        px = np.full(n, np.nan)
    return y, pm, pk, px


def test_append_and_merge_matches_direct_metrics_across_chunks(tmp_path):
    collected: dict[str, dict[str, list]] = {}
    paths = []
    for season in (2024, 2025):
        p = tmp_path / f"rows_{season}.jsonl"
        paths.append(p)
        for fam_idx, fam in enumerate(("receptions", "interceptions")):
            acc = collected.setdefault(fam, {"y": [], "pm": [], "pk": [], "px": []})
            for wk in (1, 2):
                y, pm, pk, px = _rows(seed=season * 100 + wk * 10 + fam_idx, n=30, xgb=(fam == "receptions"))
                wf.append_fold_rows(p, family=fam, season=season, week=wk, y=y, pm=pm, pk=pk, px=px)
                acc["y"] += y.tolist()
                acc["pm"] += pm.tolist()
                acc["pk"] += pk.tolist()
                acc["px"] += px.tolist()

    merged = wf.build_families_from_rows(paths, families=["receptions", "interceptions"])
    for fam in ("receptions", "interceptions"):
        acc = collected[fam]
        direct = wf.compute_metrics(
            np.array(acc["y"]),
            np.array(acc["pm"]),
            np.array(acc["pk"]),
            np.array(acc["px"]) if wf.XGB_SPEC_MAP.get(fam) is not None else None,
        )
        got = merged[fam]
        assert got["n"] == direct["n"] == 120
        assert got["model_ll"] == pytest.approx(direct["model_ll"])
        assert got["market_ll"] == pytest.approx(direct["market_ll"])
        assert got["delta_ll"] == pytest.approx(direct["delta_ll"])
        assert got["z"] == pytest.approx(direct["z"])
        assert got["ece"] == pytest.approx(direct["ece"])
        if direct["xgb_ll"] is None:
            assert got["xgb_ll"] is None
        else:
            assert got["xgb_ll"] == pytest.approx(direct["xgb_ll"])

    # interceptions has no XGB spec: xgb_ll must stay None even though px was checkpointed
    assert merged["interceptions"]["xgb_ll"] is None
    assert merged["receptions"]["xgb_ll"] is not None


def test_append_fold_rows_writes_null_for_nan_and_empty_family_entry(tmp_path):
    p = tmp_path / "rows.jsonl"
    wf.append_fold_rows(
        p,
        family="receptions",
        season=2024,
        week=1,
        y=[1.0, 0.0],
        pm=[0.6, 0.4],
        pk=[0.5, 0.5],
        px=[np.nan, 0.7],
    )
    rec = json.loads(p.read_text().strip())
    assert rec["family"] == "receptions"
    assert rec["season"] == 2024 and rec["week"] == 1
    assert rec["px"][0] is None
    assert rec["px"][1] == pytest.approx(0.7)

    merged = wf.build_families_from_rows([p], families=["receptions", "rush_yds"])
    assert merged["receptions"]["n"] == 2
    # A requested family with no checkpointed rows gets the empty-metrics entry,
    # matching run_walkforward's empty-consensus behavior.
    assert merged["rush_yds"]["n"] == 0
    assert merged["rush_yds"]["model_ll"] is None
    assert merged["rush_yds"]["market"] == wf.MARKET_MAP["rush_yds"]
