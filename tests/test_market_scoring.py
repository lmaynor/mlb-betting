"""mlb_core.eval.market_scoring on synthetic events with known ground truth (offline, seeded)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb_core.eval import market_scoring as ms

DEC_110 = 1.0 + 100.0 / 110.0


def _frame(n=6000, seed=1, model="informed"):
    rng = np.random.default_rng(seed)
    dates = np.array([f"2024-01-{(i % 28) + 1:02d}" for i in range(n)])
    p_true = rng.uniform(0.25, 0.75, n)
    y = (rng.uniform(size=n) < p_true).astype(int)
    p_mkt = 0.5 + 0.5 * (p_true - 0.5)                    # the market sees half the signal
    if model == "informed":
        p_model = p_true                                       # sees all of it
    else:
        p_model = np.clip(rng.uniform(0.25, 0.75, n), 0.01, 0.99)   # pure noise
    return pd.DataFrame({"date": dates, "p_model": p_model, "p_mkt": p_mkt, "y": y,
                         "dec_a_med": DEC_110, "dec_b_med": DEC_110, "dec_a_best": DEC_110 + 0.05,
                         "dec_b_best": DEC_110 + 0.05})


def test_informed_model_beats_market_and_noise_loses():
    good = ms.score_market(_frame(model="informed"), "good")
    bad = ms.score_market(_frame(model="noise"), "bad")
    assert good["logloss"]["mean"] > 0 and good["logloss"]["z"] > 2
    assert bad["logloss"]["mean"] < 0 and bad["logloss"]["z"] < -5
    assert good["blend"]["c_model"] > 0.3 > bad["blend"]["c_model"]
    assert good["brier"]["bss"] > 0 > bad["brier"]["bss"]


def test_clustered_se_is_larger_when_a_day_effect_is_shared():
    rng = np.random.default_rng(0)
    days = np.repeat(np.arange(40), 25)
    x = rng.normal(size=40)[days] + 0.05 * rng.normal(size=len(days))     # almost all variance is per day
    naive = x.std(ddof=1) / np.sqrt(len(x))
    assert ms.clustered_mean(x, days)["se"] > 3 * naive


def test_no_model_control_is_minus_the_vig_and_best_price_helps():
    c = ms.line_shop_control(_frame())
    assert abs(c["roi_med"] - (DEC_110 - 2.0) / 2.0) < 1e-9
    assert abs(c["shopping_worth"] - 0.025) < 1e-9     # +0.05 decimal on one of the two legs, averaged


def test_edge_buckets_are_monotone_for_an_informed_model_and_flat_for_noise():
    good = ms.edge_buckets(_frame(n=40000, model="informed"))
    assert good["monotonic"]["gap_spearman"] > 0.5
    bad = ms.edge_buckets(_frame(n=40000, model="noise"))
    tab = bad["table"]
    assert (tab["gap"].iloc[-1] < tab["gap"].iloc[0]) or bad["monotonic"]["gap_spearman"] < 0.5


def test_clv_only_counts_unmoved_lines():
    d = _frame(n=400)
    d["dec_a_open"], d["dec_b_open"] = 2.0, 2.0
    d["line_open"], d["line_close"] = 3.5, 3.5
    d.loc[:199, "line_close"] = 4.0                      # moved: excluded
    out = ms.clv_price(d)
    assert out["n"] == 200 and out["mean"] > 0           # opening 2.0 vs closing ~1.909


def test_devig_pair_and_render_run():
    pa, pb = ms.devig_pair(-110, -110)
    assert abs(pa - 0.5) < 1e-9 and abs(pa + pb - 1) < 1e-9
    txt = ms.render_markdown(ms.score_market(_frame(), "demo"))
    assert "paired gain" in txt and "no-model control" in txt
