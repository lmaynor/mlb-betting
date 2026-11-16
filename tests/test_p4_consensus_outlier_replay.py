"""Unit tests for the RES-08 P4 stage-1 replay primitives.

The de-vig is checked against the house implementation
(mlb_core.odds.utils.devig_two_way) so the replay cannot drift from the
codebase's canonical math.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mlb_core.odds.utils import devig_two_way  # noqa: E402
from p4_consensus_outlier_replay import (  # noqa: E402
    _anchor_stats,
    cluster_mean_stats,
    devig_pair,
    implied,
    norm_name,
    payout_profit,
)


def test_implied_and_payout():
    assert implied(-110) == pytest.approx(110 / 210)
    assert implied(150) == pytest.approx(100 / 250)
    assert payout_profit(-110, True, False) == pytest.approx(100 / 110)
    assert payout_profit(150, True, False) == pytest.approx(1.5)
    assert payout_profit(150, False, False) == -1.0
    assert payout_profit(150, True, True) == 0.0


def test_devig_matches_house():
    rng = np.random.default_rng(7)
    po = rng.uniform(0.03, 0.97, 500)
    pu = np.clip(1.02 - po + rng.uniform(0, 0.08, 500), 0.02, 0.99)
    fo, fu = devig_pair(po, pu)
    for a, b, x, y in zip(po, pu, fo, fu):
        method = "log" if (a <= 0.35 or a >= 0.65 or b <= 0.35 or b >= 0.65) else "proportional"
        ha, hb = devig_two_way(float(a), float(b), method)
        assert x == pytest.approx(ha, abs=1e-9)
        assert y == pytest.approx(hb, abs=1e-9)
    assert np.allclose(fo + fu, 1.0, atol=1e-9)


def test_anchor_stats_leave_one_out():
    fair = np.array([0.50, 0.60, 0.40])
    w = np.array([2.0, 1.0, 1.0])
    means, ranges = _anchor_stats(fair, w, np.array([0, 1, 2]))
    # target 0: others weighted mean = (0.60 + 0.40) / 2
    assert means[0] == pytest.approx(0.50)
    assert ranges[0] == pytest.approx(0.20)
    # target 1: others = (2*0.50 + 0.40) / 3
    assert means[1] == pytest.approx(1.40 / 3.0)


def test_cluster_mean_stats_toy():
    vals = np.array([1.0, 1.0, -1.0, -1.0])
    cl = np.array(["g1", "g1", "g2", "g2"])
    st = cluster_mean_stats(vals, cl)
    assert st["mean"] == pytest.approx(0.0)
    assert st["n_clusters"] == 2
    assert st["se"] > 0


def test_norm_name():
    assert norm_name("Luka Dončić") == "luka doncic"
    assert norm_name("Karl-Anthony Towns") == "karl anthony towns"
    assert norm_name("Robert Williams III") == "robert williams"
    assert norm_name("PJ Tucker Jr.") == "pj tucker"
