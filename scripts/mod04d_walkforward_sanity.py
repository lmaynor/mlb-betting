#!/usr/bin/env python3
"""MOD-04 Cluster (d) walk-forward sanity harness (SYNTHETIC OR LOCAL DATA ONLY).

Does NOT touch production serving weights or external buckets.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mod04d.distributions import (  # noqa: E402
    brier_score,
    calibration_slope,
    ece,
    fit_blend_c,
    log_loss,
    negbin_pmf,
    pmf_log_likelihood,
    pmf_p_over,
    blend_probs,
)
from mod04d.features import build_rolling_training_frame  # noqa: E402
from mod04d.nba_points import compare_points_distributions  # noqa: E402
from mod04d.nba_threes import fit_threes  # noqa: E402
from mod04d.nhl_scoring import SKATER_FEATURES, predict, predict_pmf, train_bundle  # noqa: E402


def _make_synthetic_nba_boxscores(seed: int = 42, n_players: int = 36, n_dates: int = 32) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-11-01", periods=n_dates, freq="D")
    teams = ["BOS", "NYK", "DEN", "OKC", "LAL", "MIA"]
    rows: list[dict] = []

    p_teams = [teams[i % len(teams)] for i in range(n_players)]
    p_min_base = rng.uniform(18.0, 35.0, size=n_players)
    p_fga_pm = rng.uniform(0.32, 0.62, size=n_players)
    p_fta_pm = rng.uniform(0.06, 0.20, size=n_players)
    p_3pa_share = rng.uniform(0.20, 0.55, size=n_players)
    p_3p_true = rng.uniform(0.30, 0.42, size=n_players)
    p_2p_true = rng.uniform(0.47, 0.58, size=n_players)
    p_ft_true = rng.uniform(0.72, 0.88, size=n_players)

    for d_idx, dt in enumerate(dates):
        pace_day = rng.normal(98.0, 3.0, size=len(teams))
        for pid in range(n_players):
            tm_idx = pid % len(teams)
            pace = pace_day[tm_idx] / 98.0
            mins = float(np.clip(rng.normal(p_min_base[pid], 4.2), 8.0, 42.0))
            fga = int(rng.poisson(max(1.0, mins * pace * p_fga_pm[pid])))
            fg3a = int(rng.binomial(fga, p_3pa_share[pid]))
            fg2a = fga - fg3a
            fta = int(rng.poisson(max(0.2, mins * pace * p_fta_pm[pid])))

            # Slightly over-dispersed shot-making via Beta-Binomial latent p
            p3_g = float(np.clip(rng.beta(25.0 * p_3p_true[pid], 25.0 * (1.0 - p_3p_true[pid])), 0.05, 0.85))
            fg3m = int(rng.binomial(fg3a, p3_g))
            fg2m = int(rng.binomial(fg2a, p_2p_true[pid]))
            ftm = int(rng.binomial(fta, p_ft_true[pid]))
            pts = ftm + 2 * fg2m + 3 * fg3m

            rows.append(
                {
                    "game_date": dt.strftime("%Y-%m-%d"),
                    "game_id": 10000 + d_idx * 10 + (tm_idx // 2),
                    "player_id": 100 + pid,
                    "team": p_teams[pid],
                    "minutes": mins,
                    "fga": fga,
                    "fgm": fg2m + fg3m,
                    "fg2a": fg2a,
                    "fg2m": fg2m,
                    "fg3a": fg3a,
                    "fg3m": fg3m,
                    "fta": fta,
                    "ftm": ftm,
                    "points": pts,
                    "oreb": int(rng.poisson(1.1)),
                    "tov": int(rng.poisson(1.8)),
                    "team_poss": pace_day[tm_idx],
                    "starter": 1 if p_min_base[pid] >= 25.0 else 0,
                }
            )
    return pd.DataFrame(rows)


def _make_synthetic_nhl_skater(seed: int = 7, n_rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-10-15", periods=45, freq="D")
    df = pd.DataFrame(index=range(n_rows))
    df["date"] = rng.choice([d.strftime("%Y-%m-%d") for d in dates], size=n_rows)
    df["game_id"] = np.arange(50000, 50000 + n_rows)
    df["player_id"] = rng.integers(1, 60, size=n_rows)
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)

    df["toi_l10"] = rng.uniform(10.0, 23.0, size=n_rows)
    df["toi_ewm"] = df["toi_l10"] + rng.normal(0.0, 0.7, size=n_rows)
    df["ixg_p60_l20"] = rng.uniform(0.3, 1.5, size=n_rows)
    df["ixg_p60_l60"] = df["ixg_p60_l20"] * rng.uniform(0.85, 1.15, size=n_rows)
    df["ixg_ev_p60_l30"] = df["ixg_p60_l20"] * 0.75
    df["ixg_pp_g_l30"] = rng.uniform(0.0, 0.35, size=n_rows)
    df["att_p60_l20"] = rng.uniform(6.0, 18.0, size=n_rows)
    df["att_p60_l60"] = df["att_p60_l20"]
    df["sog_p60_l30"] = df["att_p60_l20"] * 0.55
    df["hd_p60_l30"] = df["ixg_p60_l20"] * 3.0
    df["goals_p60_l60"] = df["ixg_p60_l60"] * 0.95
    df["goals_p60_l120"] = df["goals_p60_l60"]
    df["assists_p60_l60"] = rng.uniform(0.5, 2.2, size=n_rows)
    df["assists_p60_l120"] = df["assists_p60_l60"]
    df["points_p60_l60"] = df["goals_p60_l60"] + df["assists_p60_l60"]
    df["points_p60_l120"] = df["points_p60_l60"]
    df["fin_ratio_l120"] = rng.uniform(0.8, 1.25, size=n_rows)
    df["pp_att_g_l30"] = rng.uniform(0.0, 1.8, size=n_rows)
    df["n_prior"] = rng.integers(10, 80, size=n_rows).astype(float)
    df["is_d"] = (rng.random(n_rows) < 0.32).astype(float)
    df["is_c"] = (rng.random(n_rows) < 0.35).astype(float)
    df["is_home"] = rng.integers(0, 2, size=n_rows).astype(float)
    df["rest_days"] = rng.integers(1, 5, size=n_rows).astype(float)
    df["t_xgf_l10"] = rng.uniform(2.3, 3.6, size=n_rows)
    df["t_xgf_l30"] = df["t_xgf_l10"]
    df["t_goal_l30"] = df["t_xgf_l30"]
    df["t_ppf_l30"] = rng.uniform(2.0, 5.0, size=n_rows)
    df["o_xga_l10"] = rng.uniform(2.3, 3.6, size=n_rows)
    df["o_xga_l30"] = df["o_xga_l10"]
    df["o_goala_l30"] = df["o_xga_l30"]
    df["o_ppa_l30"] = rng.uniform(2.0, 5.0, size=n_rows)
    df["opp_g_gsax100_l60"] = rng.normal(0.0, 1.5, size=n_rows)
    df["opp_g_sv_l60"] = rng.uniform(0.89, 0.92, size=n_rows)
    df["opp_g_xga_shot_l30"] = rng.uniform(0.07, 0.11, size=n_rows)
    df["lg_goal_l30"] = 2.95
    df["lg_xgf_l30"] = 2.95

    # Heterogeneous true mean and true covariate-dependent overdispersion
    mu_g = np.clip((df["toi_ewm"] / 60.0) * df["ixg_p60_l20"] * (df["o_xga_l30"] / 2.95), 0.05, 1.2)
    alpha_g = np.where(df["is_d"] > 0.5, 0.35, 0.05) + 0.25 * (df["pp_att_g_l30"] > 1.0)
    # Sample NegBin via Gamma-Poisson mixture
    g_rate = rng.gamma(shape=1.0 / alpha_g, scale=alpha_g * mu_g)
    df["goals"] = rng.poisson(g_rate)

    mu_a = np.clip((df["toi_ewm"] / 60.0) * df["assists_p60_l60"], 0.08, 1.5)
    alpha_a = 0.10 + 0.20 * (df["is_home"] > 0.5)
    a_rate = rng.gamma(shape=1.0 / alpha_a, scale=alpha_a * mu_a)
    df["assists"] = rng.poisson(a_rate)
    df["points"] = df["goals"] + df["assists"]

    # Ensure every SKATER_FEATURES column exists
    for col in SKATER_FEATURES:
        if col not in df.columns:
            df[col] = 0.0
    return df


def main() -> int:
    t0 = time.perf_counter()
    print("WALK-FORWARD SANITY CHECK ONLY — NOT THE PROVING SHOT")
    data_root = os.environ.get("MLB_BASE_DATA")
    mode = "synthetic"
    if data_root and Path(data_root).exists():
        print(f"SANITY (local MLB_BASE_DATA={data_root} detected, using synthetic benchmark for uniform 4-family check)")

    # 1 & 2. NBA Points & Threes via strict pregame feature builder
    box = _make_synthetic_nba_boxscores(seed=42)
    feat_df = build_rolling_training_frame(box, min_prior_games=5)
    targets = box[["game_date", "player_id", "points", "fg3m", "fg3a"]].rename(columns={"game_date": "date"})
    nba_frame = feat_df.merge(targets, on=["date", "player_id"], how="inner").sort_values(["date", "player_id"]).reset_index(drop=True)

    dates = sorted(nba_frame["date"].unique())
    split_d = dates[int(len(dates) * 0.7)]
    tr_nba = nba_frame[nba_frame["date"] < split_d].reset_index(drop=True)
    oof_nba = nba_frame[nba_frame["date"] >= split_d].reset_index(drop=True)

    # Synthetic lines for tail split + U4 blend check
    oof_pts_with_line = oof_nba.copy()
    oof_pts_with_line["line"] = np.round(oof_pts_with_line["min_ewm"] * 0.55) + 0.5
    pts_cmp = compare_points_distributions(tr_nba, oof_pts_with_line, k_max=70, max_rounds=80, disp_rounds=40)
    print(
        f"SANITY ({mode}) NBA Points: negbin_ll={pts_cmp['negbin_ll']:.5f}, "
        f"skewnormal_ll={pts_cmp['skewnormal_ll']:.5f}, conv_ll={pts_cmp['component_conv_ll']:.5f}, "
        f"tail_gain={pts_cmp['tail_ll_gain']:+.5f}, decision={pts_cmp['decision']}"
    )

    threes_model = fit_threes(tr_nba, k_max=16, max_rounds=80, disp_rounds=40)
    oof_pmf_3 = threes_model.pmf(oof_nba, k_max=16)
    oof_ll_3 = pmf_log_likelihood(oof_nba["fg3m"].to_numpy(dtype=int), oof_pmf_3)
    print(
        f"SANITY ({mode}) NBA Threes: chosen={threes_model.family}, "
        f"oof_ll={oof_ll_3:.5f} | {threes_model.meta['reason']}"
    )

    # Synthetic U4 blend & calibration metrics check on NBA points OOF
    rng = np.random.default_rng(123)
    y_over = (oof_pts_with_line["points"].to_numpy() > oof_pts_with_line["line"].to_numpy()).astype(float)
    from mod04d.nba_points import fit as fit_pts

    pts_m = fit_pts(tr_nba, k_max=70, max_rounds=80, disp_rounds=40)
    p_mod = pts_m.p_over(oof_nba, oof_pts_with_line["line"].to_numpy(), k_max=70)
    p_mkt = np.clip(0.5 + 0.3 * (p_mod - 0.5) + rng.normal(0.0, 0.04, size=len(p_mod)), 0.08, 0.92)
    c_opt = fit_blend_c(y_over, p_mod, p_mkt)
    p_srv = blend_probs(p_mod, p_mkt, c_opt)
    print(
        f"SANITY ({mode}) U4 Blend & Calibration (NBA Points OOF): c={c_opt:.2f}, "
        f"ll={log_loss(y_over, p_srv):.5f}, brier={brier_score(y_over, p_srv):.5f}, "
        f"ece10={ece(y_over, p_srv, 10):.5f}, cal_slope={calibration_slope(y_over, p_srv):.3f}"
    )

    # 3. NHL Goals & Points covariate-dispersion vs fixed-alpha OOF LL
    nhl_df = _make_synthetic_nhl_skater(seed=7)
    nhl_dates = sorted(nhl_df["date"].unique())
    nhl_split = nhl_dates[int(len(nhl_dates) * 0.7)]
    tr_nhl = nhl_df[nhl_df["date"] < nhl_split].reset_index(drop=True)
    oof_nhl = nhl_df[nhl_df["date"] >= nhl_split].reset_index(drop=True)

    bundle = train_bundle({"skater": tr_nhl}, stats=("goals", "points"), max_rounds=100, disp_rounds=50)
    for stat, kmax in (("goals", 10), ("points", 12)):
        mu_oof, alpha_oof = predict(bundle, oof_nhl, stat=stat)
        pmf_disp = predict_pmf(bundle, oof_nhl, stat=stat, k_max=kmax)
        alpha_fixed = bundle["models"][stat]["meta"]["nb_alpha_global"]
        pmf_fixed = negbin_pmf(mu_oof, alpha_fixed, k_max=kmax)
        y_stat = oof_nhl[stat].to_numpy(dtype=int)
        ll_disp = pmf_log_likelihood(y_stat, pmf_disp)
        ll_fixed = pmf_log_likelihood(y_stat, pmf_fixed)
        print(
            f"SANITY ({mode}) NHL {stat.capitalize()}: disp_oof_ll={ll_disp:.5f} vs "
            f"fixed_alpha_oof_ll={ll_fixed:.5f} (alpha std={np.std(alpha_oof):.4f}, "
            f"engine={bundle['manifest']['engines'][stat]})"
        )

    elapsed = time.perf_counter() - t0
    print(f"SANITY completed in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
