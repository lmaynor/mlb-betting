"""Offline synthetic tests for MOD-04 Cluster (b) walk-forward backtest scripts."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nba.props import rebounds_decomp as RD
from nhl.props import saves_sota as SS
from nhl.props import sog_trained as ST
from nhl.props import xgfeat as X
from scripts import mod04b_rebounds_backtest as RB_BT
from scripts import mod04b_saves_backtest as SV_BT
from scripts import mod04b_sog_backtest as SG_BT


def _synth_saves_feat(n_days: int = 235, seed: int = 7) -> pd.DataFrame:
    """Synthetic saves feature frame with >= 800 training rows before 2025-08-01."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid = 2025020000
    for d in dates:
        for pid in (11, 21, 31, 41):
            gid += 1
            base_sa = float(rng.uniform(22.0, 36.0))
            sv_pct = float(rng.uniform(0.885, 0.925))
            mu_true = base_sa * sv_pct
            alpha_true = 0.04 if pid in (11, 21) else 0.08
            n_param = 1.0 / alpha_true
            p_param = n_param / (n_param + mu_true)
            rows.append({
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
                "saves": int(rng.negative_binomial(n_param, p_param)),
            })
    return pd.DataFrame(rows)


def _synth_sog_feat(n_days: int = 120, seed: int = 19) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-10-01", periods=n_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid = 2025021000
    for d in dates:
        gid += 1
        for pid in range(101, 107):
            toi_m = float(rng.uniform(13.0, 22.0))
            toi_sd = float(rng.uniform(1.5, 3.5))
            att_r = float(rng.uniform(0.14, 0.28))
            on_r = float(rng.uniform(0.50, 0.65))
            att = int(rng.poisson(toi_m * att_r))
            sog = int(rng.binomial(att, on_r)) if att > 0 else 0
            rows.append({
                "game_id": gid,
                "player_id": pid,
                "date": d,
                "exp_toi_min": toi_m,
                "exp_toi_sd": toi_sd,
                "att_rate_per_min": att_r,
                "ontarget_rate": on_r,
                "attempts": att,
                "sog": sog,
            })
    return pd.DataFrame(rows)


def _synth_rebounds_feat(n_days: int = 120, seed: int = 23) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-10-01", periods=n_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid_i = 0
    for d in dates:
        gid_i += 1
        gid = f"nba_g_{gid_i}"
        for k in range(6):
            mu_min = float(rng.uniform(18.0, 35.0))
            sd_min = float(rng.uniform(2.5, 5.5))
            sh_o = float(rng.uniform(0.06, 0.28))
            sh_d = float(rng.uniform(0.12, 0.30))
            mu_o = (mu_min / 48.0) * sh_o * 11.0
            mu_d = (mu_min / 48.0) * sh_d * 32.0
            oreb = int(rng.poisson(mu_o))
            dreb = int(rng.poisson(mu_d))
            rows.append({
                "game_id": gid,
                "player_id": f"p_{k}",
                "date": d,
                "mu_min": mu_min,
                "sd_min": sd_min,
                "emsa_own_rim": 14.0,
                "emsa_own_long": 18.0,
                "emsa_opp_rim": 14.0,
                "emsa_opp_long": 18.0,
                "renorm_oreb_share": sh_o,
                "renorm_dreb_share": sh_d,
                "opp_oreb_rate": RD.LG_OREB_RATE,
                "opp_dreb_rate": RD.LG_DREB_RATE,
                "rebounds_offensive": oreb,
                "rebounds_defensive": dreb,
                "rebounds": oreb + dreb,
            })
    return pd.DataFrame(rows)


def test_saves_backtest_source_has_no_forbidden_substrings():
    src = Path(SV_BT.__file__).read_text(encoding="utf-8")
    assert "paper_track" not in src
    assert "promotion_check" not in src


def test_saves_run_backtest_verdicts_and_metrics():
    feat = _synth_saves_feat(n_days=235, seed=7)
    res = SV_BT.run_backtest(feat, test_from="2025-08-01", step_months=3)

    assert res["n"] > 0
    assert res["engine"] == SS.ENGINE_LABEL
    assert res["alpha_x_verdict"] in {"KEEP_ALPHA_X", "REVERT_TO_FIXED"}
    assert res["xgfeat_verdict"] in {"KEEP_NOXG", "KEEP_XG"}
    assert res["monitor_verdict"] in {"ENABLE_MONITOR", "KEEP_DISABLED"}

    for key in (
        "mean_ll_fixed",
        "mean_ll_x",
        "mean_brier_tail_fixed",
        "mean_brier_tail_x",
        "delta_ll_xg_vs_fixed",
        "ll_monitor_on",
        "ll_monitor_off",
        "delta_ll_monitor",
    ):
        assert key in res
        assert np.isfinite(res[key])


def test_monitor_walk_forward_detects_regime_shift_and_returns_both_lls():
    rng = np.random.default_rng(11)
    dates = pd.date_range("2026-01-05", periods=70, freq="D").strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(dates):
        for p in range(4):
            mu = 25.0
            # Positive regime shift (+15% true saves vs model mu)
            y = int(rng.poisson(mu * 1.15))
            rows.append({
                "game_id": i * 10 + p,
                "player_id": p + 1,
                "date": d,
                "y": y,
                "mu": mu,
                "alpha_fixed": 0.04,
            })
    df_shift = pd.DataFrame(rows)
    res_shift = SV_BT.monitor_walk_forward(df_shift)
    assert res_shift["n"] == len(df_shift)
    assert res_shift["n_batches"] >= 9
    assert np.isfinite(res_shift["ll_monitor_on"])
    assert np.isfinite(res_shift["ll_monitor_off"])
    assert res_shift["ll_monitor_on"] > res_shift["ll_monitor_off"]
    assert res_shift["delta_ll_monitor"] > 0.0


def test_sog_run_backtest_synthetic():
    feat = _synth_sog_feat(n_days=120, seed=19)
    assert set(ST.REQUIRED_FEAT_COLS).issubset(set(feat.columns))
    res = SG_BT.run_backtest(feat, test_from="2025-12-01", step_months=1)

    assert res["n"] > 0
    assert res["folds"] >= 1
    assert res["engine"] == ST.ENGINE_LABEL
    for key in ("mean_ll", "mae", "brier_2p5", "ll_2p5"):
        assert np.isfinite(res[key])


def test_rebounds_run_backtest_synthetic():
    feat = _synth_rebounds_feat(n_days=120, seed=23)
    assert set(RD.REQUIRED_FEAT_COLS).issubset(set(feat.columns))
    res = RB_BT.run_backtest(feat, test_from="2025-12-01", step_months=1)

    assert res["n"] > 0
    assert res["folds"] >= 1
    assert res["engine"] == RD.ENGINE_LABEL
    for key in ("mean_ll", "mae", "brier_5p5", "ll_5p5"):
        assert np.isfinite(res[key])


def test_backtest_clis_fail_honestly_on_missing_dirs(tmp_path):
    missing = str(tmp_path / "nonexistent")
    assert SV_BT.main(["--data-dir", missing, "--xg-dir", missing]) != 0
    assert SG_BT.main(["--data-dir", missing]) != 0
    assert RB_BT.main(["--data-dir", missing]) != 0
