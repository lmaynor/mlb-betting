"""Offline synthetic tests for MOD-04 Cluster (b) daily shadow banking driver."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nba.props import rebounds_decomp as RD
from nhl.props import saves_sota as SS
from nhl.props import sog_trained as ST
from scripts import mod04b_saves_backtest as SV_BT
from scripts import mod04b_shadow_bank as SB


def _synth_saves_bank_frame(target_date: str = "2025-08-15", n_hist_days: int = 215, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hist_dates = pd.date_range("2025-01-01", periods=n_hist_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid = 2025020000
    for d in list(hist_dates) + [target_date]:
        is_tgt = d == target_date
        for pid in (11, 21, 31, 41):
            gid += 1
            base_sa = float(rng.uniform(22.0, 36.0))
            sv_pct = float(rng.uniform(0.885, 0.925))
            mu_true = base_sa * sv_pct
            alpha_true = 0.05
            n_param = 1.0 / alpha_true
            p_param = n_param / (n_param + mu_true)
            rows.append({
                "game_id": gid,
                "player_id": pid,
                "player_name": f"Goalie {pid}",
                "date": d,
                "is_target_date": is_tgt,
                "g_sa_l5": base_sa,
                "g_sa_l10": base_sa,
                "g_sa_l30": base_sa,
                "g_saves_l10": base_sa * sv_pct,
                "g_sv_l60": sv_pct,
                "g_gsax100_l20": 0.0,
                "g_gsax100_l60": 0.0,
                "g_xga_shot_l30": 0.09,
                "g_hd_share_l30": 0.25,
                "g_toi_l10": 59.0,
                "g_short_l20": 0.05,
                "g_rest": 2.0,
                "g_starts_7d": 2.0,
                "g_n_prior": 25.0,
                "t_sa_l10": base_sa,
                "t_sa_l30": base_sa,
                "t_sa_ewm": base_sa,
                "t_xga_l30": base_sa * 0.09,
                "t_atta_l30": base_sa + 10.0,
                "t_hda_l30": base_sa * 0.25,
                "t_ppa_l30": 4.0,
                "t_rest": 2.0,
                "o_sf_l10": base_sa,
                "o_sf_l30": base_sa,
                "o_sf_ewm": base_sa,
                "o_xgf_l30": base_sa * 0.09,
                "o_attf_l30": base_sa + 10.0,
                "o_hdf_l30": base_sa * 0.25,
                "o_ppf_l30": 4.0,
                "o_rest": 2.0,
                "lg_sf_l30": 29.0,
                "lg_sv_l30": 0.905,
                "is_home": int(pid in (11, 31)),
                "analytic_exp_sa": base_sa,
                "saves": np.nan if is_tgt else int(rng.negative_binomial(n_param, p_param)),
            })
    return pd.DataFrame(rows)


def _synth_sog_bank_frame(target_date: str = "2025-08-15", n_hist_days: int = 40, seed: int = 19) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hist_dates = pd.date_range("2025-07-01", periods=n_hist_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid = 2025025000
    for d in list(hist_dates) + [target_date]:
        is_tgt = d == target_date
        gid += 1
        for pid in (101, 102, 103, 104, 105):
            toi_m = float(rng.uniform(14.0, 21.0))
            att_r = float(rng.uniform(0.15, 0.25))
            on_r = 0.56
            att = int(rng.poisson(toi_m * att_r))
            rows.append({
                "game_id": gid,
                "player_id": pid,
                "name": f"Skater {pid}",
                "date": d,
                "is_target_date": is_tgt,
                "exp_toi_min": toi_m,
                "exp_toi_sd": 2.5,
                "att_rate_per_min": att_r,
                "ontarget_rate": on_r,
                "sog": np.nan if is_tgt else int(rng.binomial(att, on_r)) if att > 0 else 0,
            })
    return pd.DataFrame(rows)


def _synth_rebounds_bank_frame(target_date: str = "2025-08-15", n_hist_days: int = 40, seed: int = 23) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hist_dates = pd.date_range("2025-07-01", periods=n_hist_days, freq="D").strftime("%Y-%m-%d")
    rows = []
    gid_i = 0
    for d in list(hist_dates) + [target_date]:
        is_tgt = d == target_date
        gid_i += 1
        gid = f"nba_g_{gid_i}"
        for k in range(6):
            mu_min = float(rng.uniform(20.0, 34.0))
            sh_o = 0.16
            sh_d = 0.22
            rows.append({
                "game_id": gid,
                "player_id": f"nba_p_{k}",
                "name": f"Player {k}",
                "date": d,
                "is_target_date": is_tgt,
                "mu_min": mu_min,
                "sd_min": 4.0,
                "emsa_own_rim": 14.0,
                "emsa_own_long": 18.0,
                "emsa_opp_rim": 14.0,
                "emsa_opp_long": 18.0,
                "renorm_oreb_share": sh_o,
                "renorm_dreb_share": sh_d,
                "opp_oreb_rate": RD.LG_OREB_RATE,
                "opp_dreb_rate": RD.LG_DREB_RATE,
                "rebounds": np.nan if is_tgt else int(rng.poisson((mu_min / 48.0) * (sh_o * 11.0 + sh_d * 32.0))),
            })
    return pd.DataFrame(rows)


def test_governance_no_paper_track_or_promotion_check():
    for mod in (SV_BT, SB):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "paper_track" not in src
        assert "promotion_check" not in src
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "paper_track" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert "paper_track" not in (node.module or "")


def test_bank_day_writes_all_three_families_with_dual_logging(tmp_path):
    target_date = "2025-08-15"
    frames = {
        "saves": _synth_saves_bank_frame(target_date=target_date),
        "sog": _synth_sog_bank_frame(target_date=target_date),
        "rebounds": _synth_rebounds_bank_frame(target_date=target_date),
    }

    summary = SB.bank_day(target_date, frames, out_dir=tmp_path, write_gcs=False)
    assert set(summary.keys()) == {"saves", "sog", "rebounds"}

    expected = {
        "saves": (4, SS.ENGINE_LABEL, "24.5", "nhl"),
        "sog": (5, ST.ENGINE_LABEL, "2.5", "nhl"),
        "rebounds": (6, RD.ENGINE_LABEL, "5.5", "nba"),
    }
    for fam, (exp_n, exp_engine, exp_line, exp_sport) in expected.items():
        fpath = tmp_path / f"mod04b_shadow_{fam}_{target_date}.json"
        assert fpath.exists()
        payload = json.loads(fpath.read_text(encoding="utf-8"))
        preds = payload["predictions"]
        assert len(preds) == exp_n
        for row in preds:
            assert row["engine"] == exp_engine
            assert row["sport"] == exp_sport
            assert row["family"] == fam
            assert row["date"] == target_date
            assert np.isfinite(row["mu"]) and row["mu"] > 0.0
            assert np.isfinite(row["alpha"]) and row["alpha"] >= 0.0
            assert np.isclose(sum(row["pmf"]), 1.0, atol=1e-6)
            assert isinstance(row["p_model"], dict) and exp_line in row["p_model"]
            assert isinstance(row["p_served"], dict) and exp_line in row["p_served"]


def test_bank_day_blocked_writes_zero_files_and_cli_fails_honestly(tmp_path, capsys):
    target_date = "2025-08-15"
    sog_only_hist = _synth_sog_bank_frame(target_date=target_date)
    sog_no_target = sog_only_hist[~sog_only_hist["is_target_date"]].copy()

    out_dir = tmp_path / "blocked_out"
    with pytest.raises(SB.BlockedShadowBankError, match="BLOCKED"):
        SB.bank_day(
            target_date,
            {
                "sog": sog_no_target,
                "rebounds": _synth_rebounds_bank_frame(target_date=target_date),
            },
            out_dir=out_dir,
            write_gcs=False,
        )
    assert not out_dir.exists() or list(out_dir.iterdir()) == []

    rc = SB.main(["--date", target_date, "--out-dir", str(out_dir)])
    assert rc != 0
    captured = capsys.readouterr()
    assert "BLOCKED" in captured.err
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
