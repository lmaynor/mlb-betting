"""Offline synthetic tests for MOD-04 B2 saves shadow engine (`nhl.props.saves_sota`)."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nhl.props import countmodel as CM
from nhl.props import saves_sota as SS
from nhl.props import xgfeat as X


def _synth_saves_feat(n_rows: int = 960, seed: int = 7) -> pd.DataFrame:
    """Synthetic saves feature frame covering SAVES_FEATURES with >= 800 training rows before 2025-11-01."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_rows // 4, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid = 2025020000
    for d in dates:
        for pid in (11, 21, 31, 41):
            gid += 1
            base_sa = float(rng.uniform(22.0, 36.0))
            sv_pct = float(rng.uniform(0.885, 0.925))
            row = {
                "game_id": gid,
                "player_id": pid,
                "date": d,
                "g_sa_l5": base_sa + rng.normal(0, 1.5),
                "g_sa_l10": base_sa + rng.normal(0, 1.0),
                "g_sa_l30": base_sa,
                "g_saves_l10": base_sa * sv_pct,
                "g_sv_l60": sv_pct,
                "g_gsax100_l20": float(rng.normal(0.0, 2.0)),
                "g_gsax100_l60": float(rng.normal(0.0, 1.5)),
                "g_xga_shot_l30": float(rng.uniform(0.075, 0.115)),
                "g_hd_share_l30": float(rng.uniform(0.18, 0.32)),
                "g_toi_l10": float(rng.uniform(56.0, 61.0)),
                "g_short_l20": float(rng.uniform(0.0, 0.15)),
                "g_rest": float(rng.integers(1, 5)),
                "g_starts_7d": float(rng.integers(1, 4)),
                "g_n_prior": 25.0,
                "t_sa_l10": base_sa + rng.normal(0, 1.2),
                "t_sa_l30": base_sa,
                "t_sa_ewm": base_sa,
                "t_xga_l30": base_sa * 0.095,
                "t_atta_l30": base_sa + 12.0,
                "t_hda_l30": base_sa * 0.25,
                "t_ppa_l30": 4.5,
                "t_rest": 2.0,
                "o_sf_l10": base_sa + rng.normal(0, 1.2),
                "o_sf_l30": base_sa,
                "o_sf_ewm": base_sa,
                "o_xgf_l30": base_sa * 0.095,
                "o_attf_l30": base_sa + 12.0,
                "o_hdf_l30": base_sa * 0.25,
                "o_ppf_l30": 4.5,
                "o_rest": 2.0,
                "lg_sf_l30": 29.0,
                "lg_sv_l30": 0.905,
                "is_home": int(pid in (11, 31)),
                "analytic_exp_sa": base_sa,
            }
            mu_true = base_sa * sv_pct
            # Overdispersed NegBin draw
            alpha_true = 0.04 if pid in (11, 21) else 0.08
            n_param = 1.0 / alpha_true
            p_param = n_param / (n_param + mu_true)
            row["saves"] = int(rng.negative_binomial(n_param, p_param))
            rows.append(row)
    return pd.DataFrame(rows)


def test_shadow_contract_and_constants():
    assert SS.ENGINE_LABEL == "nhl_saves_sota_v2"
    assert set(SS.XG_ABLATION_FEATURES) == set(X._SAVES_XG_ONLY)
    src = Path(SS.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "paper_track" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "paper_track" not in mod
            for alias in node.names:
                assert "paper_track" not in alias.name


@pytest.mark.parametrize("mu,alpha", [(28.0, 0.0), (28.0, 0.05), (32.5, 0.15), (15.0, 0.4)])
def test_pmf_sums_to_one_within_1e9_and_p_over_matches(mu, alpha):
    probs = SS.pmf(mu, alpha, kmax=200)
    assert len(probs) == 201
    assert (probs >= 0.0).all()
    assert np.isclose(probs.sum(), 1.0, atol=1e-9, rtol=0.0)
    assert SS.p_over(27.5, mu, alpha, kmax=200) == pytest.approx(float(probs[28:].sum()), abs=1e-12)


def test_damped_regime_monitor_bounds_and_disabled_default():
    mon = SS.DampedRegimeMonitor()
    assert mon.enabled is False
    assert mon.offset == 0.0

    # Extreme high-saving batch -> shadow_offset must be clipped at +0.05, offset stays 0.0 while disabled
    big_over = pd.DataFrame({"y": [50.0] * 20, "mu": [20.0] * 20})
    for _ in range(10):
        off = mon.update(big_over)
        assert off == 0.0
        assert mon.offset == 0.0
        assert -0.05 <= mon.shadow_offset <= 0.05
    assert mon.shadow_offset == pytest.approx(0.05)
    assert mon.apply(28.0) == pytest.approx(28.0)

    # Enable monitor and verify damping + negative clamp
    mon_on = SS.DampedRegimeMonitor(enabled=True)
    big_under = pd.DataFrame({"y": [5.0] * 20, "mu": [35.0] * 20})
    for _ in range(10):
        mon_on.update(big_under)
        assert -0.05 <= mon_on.offset <= 0.05
    assert mon_on.offset == pytest.approx(-0.05)
    assert mon_on.apply(30.0) == pytest.approx(30.0 * np.exp(-0.05))


def test_alphax_model_fits_and_predicts_bounded_dispersion():
    feat = _synth_saves_feat(n_rows=900, seed=7)
    art = CM.fit(feat, "saves", X.SAVES_FEATURES_NOXG, cutoff="2025-08-01", prior_col="g_n_prior")
    train_df = feat[feat["date"] < "2025-08-01"]
    test_df = feat[feat["date"] >= "2025-08-01"]

    ax = SS.AlphaXModel(features=X.SAVES_FEATURES_NOXG, seed=7).fit(art, train_df, target="saves")
    preds = ax.predict(test_df)

    assert len(preds) == len(test_df)
    assert np.isfinite(preds).all()
    assert (preds >= 0.01).all() and (preds <= 1.0).all()


def test_bias_alert_threshold_and_window_days():
    # Old rows (outside 14d window) are biased, recent 14d rows are calibrated -> False
    old = pd.DataFrame({
        "date": ["2026-10-01"] * 10,
        "p_model": [0.70] * 10,
        "outcome": [0.0] * 10,
    })
    recent_ok = pd.DataFrame({
        "date": [f"2026-11-{d:02d}" for d in range(10, 24)],
        "p_model": [0.53] * 14,
        "outcome": [0.50] * 14,  # diff = 0.03 <= 0.035
    })
    assert SS.bias_alert(pd.concat([old, recent_ok], ignore_index=True), window_days=14) is False

    # Recent rows exceed 0.035 -> True
    recent_bad = pd.DataFrame({
        "date": [f"2026-11-{d:02d}" for d in range(10, 24)],
        "p_model": [0.55] * 14,
        "outcome": [0.50] * 14,  # diff = 0.05 > 0.035
    })
    assert SS.bias_alert(recent_bad, window_days=14) is True


def test_walk_forward_compare_deterministic_and_expected_columns():
    feat = _synth_saves_feat(n_rows=1000, seed=7)
    features_xg = X.SAVES_FEATURES_NOXG + SS.XG_ABLATION_FEATURES
    df1, sum1 = SS.walk_forward_compare(
        feat, X.SAVES_FEATURES_NOXG, features_xg, test_from="2025-08-01", step_months=3
    )
    df2, sum2 = SS.walk_forward_compare(
        feat, X.SAVES_FEATURES_NOXG, features_xg, test_from="2025-08-01", step_months=3
    )

    expected_cols = {
        "variant", "date", "y", "mu", "alpha_fixed", "alpha_x",
        "ll_fixed", "ll_x", "brier_tail_fixed", "brier_tail_x",
    }
    assert expected_cols.issubset(set(df1.columns))
    assert set(df1["variant"].unique()) == {"fixed", "xg"}
    assert sum1["tail_threshold"] == 35
    assert sum1["engine"] == SS.ENGINE_LABEL
    pd.testing.assert_frame_equal(df1, df2)
    assert sum1 == sum2
