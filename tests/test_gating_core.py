"""Shared gated-evaluation machinery (mlb_core/eval/gating.py, gated_run.py, garble.py), offline and sport-free: planted signal / null
behaviour, family-wise error, pre-registration guards, the two-stage driver and the garble primitives."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from mlb_core.eval import garble as gb
from mlb_core.eval import gated_run as gr
from mlb_core.eval import gating as G

PHASE_MIN = 6


def _events(n=4000, days=200, informative_when_confident=False, seed=0, season=2021):
    """Spread-like events. The market is exact, or the model knows the truth only when it deviates a lot from the market."""
    rng = np.random.default_rng(seed)
    p_mkt = np.clip(rng.normal(0.5, 0.08, n), 0.2, 0.8)
    e = rng.normal(0, 0.06, n)
    p_model = np.clip(p_mkt + e, 0.02, 0.98)
    p_true = np.where(np.abs(e) > 0.05, p_model, p_mkt) if informative_when_confident else p_mkt
    y = (rng.random(n) < p_true).astype(int)
    kal = rng.normal(0, 3, n)
    return pd.DataFrame({
        "date": [f"{season}-{1 + (i % days) // 28:02d}-{1 + (i % days) % 28:02d}" for i in range(n)], "p_model": p_model,
        "p_mkt": p_mkt, "y": y, "season": season, "dec_a_med": 1.909, "dec_b_med": 1.909, "dec_a_best": 1.909,
        "dec_b_best": 1.909, "c_kal": kal, "c_feat": kal + rng.normal(0, 3, n), "c_mu": kal, "c_line": 0.0,
        "c_sd": rng.uniform(3, 6, n), "c_phase": rng.integers(1, 18, n), "c_dq": 0.0})


def test_planted_signal_is_found_by_the_edge_gate_and_survives_the_family_correction():
    ev = {"spread": _events(informative_when_confident=True, seed=1)}
    th = G.freeze_thresholds("x", ev, PHASE_MIN)
    res = G.run_split("x", ev, th)
    edge, none = res[("x", "spread", "EDGE")], res[("x", "spread", "NONE")]
    assert edge["gain"] > 0 and edge["gain_z"] > 2.5 and edge["c"] > 0 and edge["c_z"] > 2.5
    assert edge["gain"] > none["gain"]                       # the gate rescues a signal the ungated book hides
    conf = G.confirm(res, G.screen_dev(res), B=500)
    assert conf["cells"][("x", "spread", "EDGE")]["p_maxt_all"] < 0.05 and conf["any_strict"]


def test_market_exact_world_has_no_gate_that_wins():
    ev = {"spread": _events(seed=2)}
    res = G.run_split("x", ev, G.freeze_thresholds("x", ev, PHASE_MIN))
    conf = G.confirm(res, G.screen_dev(res), B=500)
    assert not conf["any_strict"] and not conf["any_confirmed"]
    assert all(res[k]["gain"] < 0 for k in G.tried(res) if res[k].get("gain") is not None and res[k]["n"] > 500)
    assert G.verdict(conf, res) in ("NO", "INCONCLUSIVE")


def test_family_wise_error_under_the_null_is_controlled():
    hits = 0
    for s in range(30):
        ev = {"spread": _events(n=900, days=90, seed=100 + s)}
        res = G.run_split("x", ev, G.freeze_thresholds("x", ev, PHASE_MIN))
        hits += G.confirm(res, [], B=300)["any_strict"]
    assert hits <= 5            # nominal 5% of 30 = 1.5; allow Monte-Carlo slack


def test_thresholds_never_read_outcomes():
    ev = {"spread": _events(seed=3)}
    th1 = G.freeze_thresholds("x", ev, PHASE_MIN)
    assert G.freeze_thresholds("x", {"spread": ev["spread"].assign(y=1 - ev["spread"]["y"])}, PHASE_MIN) == th1
    assert G.freeze_thresholds("x", {"spread": ev["spread"].drop(columns=["y"])}, PHASE_MIN) == th1


def test_gate_masks_and_degenerate_dq_gate():
    ev = {"m": _events(seed=4)}
    th = G.freeze_thresholds("x", ev, PHASE_MIN)
    mk = G.gate_masks(ev["m"], th, "m")
    cov = {k: float(v.mean()) for k, v in mk.items() if v is not None}
    assert abs(cov["EDGE"] - 0.25) < 0.02 and abs(cov["SD"] - 1 / 3) < 0.02
    assert mk["DQ"] is None                                  # c_dq all 0 -> removes no games -> degenerate, not tried
    assert (G.derive_conf(ev["m"])["agree"].dropna().isin([0.0, 1.0])).all()
    nf = {"t": ev["m"].assign(c_feat=np.nan)}                # a single-variant market: AGREE is not applicable
    assert G.gate_masks(nf["t"], G.freeze_thresholds("x", nf, PHASE_MIN), "t")["AGREE"] is None


def test_holm_mde_and_blend_offset():
    adj = G.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    assert G.mde(0.01) == pytest.approx(0.02485, abs=1e-4)
    assert G.n_needed(1000, 0.01, 0.01) == pytest.approx(1000 * (0.02485 / 0.01) ** 2, rel=5e-3)
    rng = np.random.default_rng(5)
    n = 20000
    pm = np.clip(rng.normal(0.5, 0.1, n), 0.1, 0.9)
    pq = np.clip(pm + rng.normal(0, 0.05, n), 0.05, 0.95)
    y = (rng.random(n) < pq).astype(int)                     # the model IS the truth -> c = 1
    out = G.blend_c_offset(pd.DataFrame({"p_model": pq, "p_mkt": pm, "y": y, "date": rng.integers(0, 300, n).astype(str)}))
    assert 0.8 < out["c"] < 1.2 and out["z_c"] > 5


def test_split_must_be_chronological():
    ev = {"m": _events(seed=6)}
    with pytest.raises(ValueError):
        G.split_by_season(ev, [2022, 2023], [2023, 2024])
    with pytest.raises(ValueError):
        G.split_by_season(ev, [2024], [2021])


# ---------------------------------------------------------------------------------------------------- driver
def _source(tmp_path, label="x"):
    rec = tmp_path / "records.csv"
    rec.write_text("placeholder records file\n")
    frames = [_events(n=900, days=90, seed=10 + s, season=2021 + s) for s in range(5)]
    ev = {"m": pd.concat(frames, ignore_index=True)}
    return gr.GatedSource(label=label, events_fn=lambda: ev, records_path=str(rec), dev_seasons=[2021, 2022, 2023],
                          held_seasons=[2024, 2025], phase_min=PHASE_MIN)


def test_two_stage_driver_freezes_then_scores_once(tmp_path):
    src, out = _source(tmp_path), str(tmp_path / "o")
    fz = gr.run_dev(src, out)
    assert fz["spec_version"] == G.SPEC_VERSION and fz["dev_cells_tried"] > 0
    assert json.load(open(f"{out}/frozen.json"))["frozen_sha"] == fz["frozen_sha"]
    res = gr.run_held(src, out, B=200)
    assert res["verdict"] in ("YES", "NO", "INCONCLUSIVE")
    with pytest.raises(SystemExit):                          # a second look at held-out is refused
        gr.run_held(src, out, B=200)
    gr.run_held(src, out, allow_rescore=True, B=200)
    assert "RE-SCORED" in open(f"{out}/held_report.md").read()
    fz2 = json.load(open(f"{out}/frozen.json"))
    fz2["survivors"] = ["x|m|EDGE"]                                  # tampering after the freeze is refused
    json.dump(fz2, open(f"{out}/frozen.json", "w"))
    with pytest.raises(SystemExit):
        gr.run_held(src, out, allow_rescore=True, B=200)


def test_held_refuses_without_a_freeze_or_with_other_records(tmp_path):
    src = _source(tmp_path)
    with pytest.raises(SystemExit):
        gr.run_held(src, str(tmp_path / "none"))
    out = str(tmp_path / "o")
    gr.run_dev(src, out)
    open(src.records_path, "w").write("a different records file\n")
    with pytest.raises(SystemExit):
        gr.run_held(src, out)


def test_core_modules_do_not_import_a_sport_package():
    import importlib
    import sys
    for mod in ("mlb_core.eval.gating", "mlb_core.eval.gated_run", "mlb_core.eval.garble"):
        importlib.import_module(mod)
    for name in ("nfl", "nba", "nhl"):
        src = open(sys.modules["mlb_core.eval.gating"].__file__).read() + open(sys.modules["mlb_core.eval.gated_run"].__file__).read() \
            + open(sys.modules["mlb_core.eval.garble"].__file__).read()
        assert f"import {name}" not in src and f"from {name}" not in src and f"from {name}." not in src


# ---------------------------------------------------------------------------------------------------- garble primitives
def test_garble_primitives():
    df = pd.DataFrame({"a": np.arange(10.0), "k": list("abcdefghij")})
    g = gb.garble_columns(df, ["a"], [False] * 4 + [True] * 6, seed=1)
    assert g["a"].iloc[:4].tolist() == [0, 1, 2, 3] and sorted(g["a"].iloc[4:]) == [4, 5, 6, 7, 8, 9]
    assert g["a"].iloc[4:].tolist() != [4, 5, 6, 7, 8, 9] and g["k"].tolist() == df["k"].tolist()
    nn = pd.DataFrame({"a": [1.0, np.nan, 3.0, 4.0, np.nan, 6.0]})
    gn = gb.garble_columns(nn, ["a"], [True] * 6, seed=2)
    assert gn["a"].isna().tolist() == nn["a"].isna().tolist()          # missing stays missing: no game is dropped by the garble
    a = pd.DataFrame({"id": [1, 2, 3], "d": ["2024-01-01", "2024-01-02", "2024-01-03"], "x": [1.0, np.nan, 3.0]})
    b = a.assign(x=[1.0, np.nan, 9.0])
    assert len(gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02")) == 0       # NaN == NaN, change is after the cut-off
    assert len(gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02", after=True)) == 1
    only_a = a.iloc[:2]                                                              # a game missing from one run is a difference
    assert len(gb.prediction_diff(a, only_a, "id", ["x"], "d", "2024-01-03")) == 1
    s = gb.summarize(gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02"), gb.prediction_diff(a, b, "id", ["x"], "d", "2024-01-02", after=True), 2)
    assert s["leak"] is False and s["power_ok"] is True
