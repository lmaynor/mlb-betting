"""Offline tests for nfl.props.env_adjust (game-environment adjustment)."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from nfl.props import env_adjust as EA


def _sched():
    return pd.DataFrame([
        {"game_id": "g1", "season": 2025, "week": 1, "gameday": "2025-09-07", "home_team": "KC", "away_team": "LA",
         "spread_line": 3.0, "total_line": 50.0},
        {"game_id": "g2", "season": 2025, "week": 1, "gameday": "2025-09-07", "home_team": "JAX", "away_team": "OAK",
         "spread_line": np.nan, "total_line": 41.0}])


def test_game_env_table_signs_and_implied_totals():
    t = EA.game_env_table(_sched()).set_index(["game_id", "team"])
    assert t.loc[("g1", "KC"), "team_spread"] == 3.0 and t.loc[("g1", "LAR"), "team_spread"] == -3.0
    # home favoured by 3 in a 50 total: 26.5 - 23.5
    assert t.loc[("g1", "KC"), "implied_total"] == pytest.approx(26.5)
    assert t.loc[("g1", "LAR"), "implied_total"] == pytest.approx(23.5)
    assert ("g2", "JAC") in t.index and ("g2", "LV") in t.index            # relocations / aliases canonicalised
    assert math.isnan(t.loc[("g2", "JAC"), "implied_total"])               # a missing spread is NaN, never guessed


def test_features_center_on_a_league_average_team():
    x1, x2 = EA.env_features([22.5, 27.0, np.nan], [0.0, 7.0, 3.0])
    assert x1[0] == pytest.approx(0.0) and x1[1] > 0 and np.isnan(x1[2]) and x2[1] == pytest.approx(1.0)


def _synthetic(n=6000, b=(0.02, 0.9, 0.1), seed=0):
    rng = np.random.default_rng(seed)
    it = rng.uniform(15, 32, n)
    sp = rng.uniform(-10, 10, n)
    x1, x2 = EA.env_features(it, sp)
    mu = rng.uniform(2, 9, n)
    y = rng.poisson(mu * np.exp(b[0] + b[1] * x1 + b[2] * x2)).astype(float)
    return y, mu, x1, x2


def test_fit_recovers_known_coefficients():
    y, mu, x1, x2 = _synthetic()
    c = EA.fit_coefs(y, mu, x1, x2)
    assert c["n"] == 6000
    assert c["b0"] == pytest.approx(0.02, abs=0.04) and c["b1"] == pytest.approx(0.9, abs=0.12)
    assert c["b2"] == pytest.approx(0.1, abs=0.06)


def test_fit_on_pure_noise_shrinks_toward_zero_and_too_few_rows_returns_none():
    rng = np.random.default_rng(1)
    n = 3000
    x1, x2 = EA.env_features(rng.uniform(15, 32, n), rng.uniform(-10, 10, n))
    mu = rng.uniform(2, 9, n)
    c = EA.fit_coefs(rng.poisson(mu).astype(float), mu, x1, x2)
    assert abs(c["b1"]) < 0.1 and abs(c["b2"]) < 0.05
    assert EA.fit_coefs(*(a[:100] for a in (rng.poisson(mu).astype(float), mu, x1, x2))) is None


def test_adjust_modes_and_missing_line_fallback():
    mu = np.array([5.0, 5.0])
    x1, x2 = np.array([0.2, np.nan]), np.array([0.1, 0.1])
    assert np.array_equal(EA.adjust(mu, x1, x2, None), mu)
    assert np.allclose(EA.adjust(mu, x1, x2, {"k": 0.95, "n": 1}), 4.75)                # scale mode ignores the lines
    c = {"b0": -0.04, "b1": 1.0, "b2": 0.0, "n": 1}
    out = EA.adjust(mu, x1, x2, c)
    assert out[0] == pytest.approx(5.0 * math.exp(-0.04 + 0.2))
    assert out[1] == pytest.approx(5.0 * math.exp(-0.04))                              # missing line -> exp(b0) only
    assert EA.multiplier(c, 22.5 * math.exp(0.2), 0.0) == pytest.approx(math.exp(-0.04 + 0.2))
    assert EA.multiplier(c, None, None) == pytest.approx(math.exp(-0.04))
    assert EA.multiplier({"k": 0.9}, 30.0, 3.0) == 0.9 and EA.multiplier(None, 25.0, 1.0) == 1.0


def test_fit_scale_is_the_ratio_of_sums_and_needs_enough_rows():
    rng = np.random.default_rng(3)
    mu = rng.uniform(2, 9, 2000)
    y = rng.poisson(mu * 0.94).astype(float)
    c = EA.fit_scale(y, mu)
    assert c["k"] == pytest.approx(0.94, abs=0.02) and c["n"] == 2000
    assert EA.fit_scale(y[:50], mu[:50]) is None and EA.fit_scale(y, np.zeros(2000)) is None


def test_load_coefs_ignores_missing_file_and_null_stats(tmp_path):
    assert EA.load_calibration(str(tmp_path / "nope.json")) == {}
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"coefs": {"rec_yds": {"b0": 0, "b1": 0.5, "b2": 0, "n": 9}, "pass_yds": None}}))
    assert set(EA.load_calibration(str(p))) == {"rec_yds"}
    p.write_text("not json")
    assert EA.load_calibration(str(p)) == {}
