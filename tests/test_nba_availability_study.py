"""nba.props.availability_study plumbing (no model training here: the study itself is a CLI over real ESPN history)."""
import numpy as np
import pandas as pd
import pytest

from nba.props import availability_study as A
from nba.props import config as C
from tests.nba_props_helpers import make_masters
from tests.test_nba_props_espn_data import to_espn


@pytest.fixture(scope="module")
def built():
    pb, gm, tips = make_masters(n_days=45, seed=5)
    return A.build_variants(to_espn(pb, gm, tips))


def test_variants_share_rows_and_differ_only_in_availability_columns(built):
    X, meta, info = built
    av = list(C.AVAILABILITY_FEATURES)
    assert list(X["v1"].index) == list(X["history"].index) == list(X["history_excl_scratch"].index) == list(meta.index)
    assert not set(av) & set(X["v1"].columns) and set(av) <= set(X["history"].columns)
    base = [c for c in X["history"].columns if c not in av]
    pd.testing.assert_frame_equal(X["v1"], X["history"][base])
    assert info["n_scratches"] > 0
    assert info["mean_absent_excl_scratch"] <= info["mean_absent_history"] + 1e-12


def test_run_returns_an_empty_frame_when_no_season_splits_exist(built):
    X, meta, _ = built                                    # one season only: no train/val/test split is possible
    out = A.run(X, meta, ["points"], [2025], progress=lambda *_: None)
    assert out.empty and list(out.columns) == ["stat", "season", "variant", "row", "mu"]


def test_summarize_and_render_report_gain_versus_v1(built):
    X, meta, info = built
    rng = np.random.default_rng(1)
    rows = np.arange(0, len(meta), 2)
    y = meta["points"].to_numpy(float)[rows]
    recs = []
    for v, noise in (("v1", 3.0), ("history", 2.5), ("history_excl_scratch", 2.0)):
        recs.append(pd.DataFrame({"stat": "points", "season": 2025, "variant": v, "row": rows,
                                  "mu": np.maximum(y + rng.normal(0, noise, len(y)), 0.1)}))
    s = A.summarize(pd.concat(recs, ignore_index=True), meta)
    g = s.set_index("variant")
    assert g.loc["history_excl_scratch", "mae"] < g.loc["history", "mae"] < g.loc["v1", "mae"]
    assert g.loc["history_excl_scratch", "mae_gain_vs_v1"] > g.loc["history", "mae_gain_vs_v1"] > 0
    assert g.loc["history", "ci_lo"] < g.loc["history", "mae_gain_vs_v1"] < g.loc["history", "ci_hi"]
    md = A.render(s, info, [2025])
    assert "ACCURACY, not edge" in md
    for v in ("v1", "history", "history_excl_scratch"):
        assert f"| points | {v} | " in md                 # one headline row per variant
