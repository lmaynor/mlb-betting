"""Tests for mod04.hurdle_nb — PMF sanity, GH quadrature vs brute-force, and U4 shift."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.stats import nbinom, norm

from mod04.hurdle_nb import (
    hurdle_nb_cdf,
    hurdle_nb_pmf,
    p_over,
    plain_nb_pmf,
    shift_lambda_for_p_over,
)


def test_pmf_sums_to_one_and_monotone_p_over():
    pmf_h = hurdle_nb_pmf(pi=0.18, lambda_rate=0.04, alpha=0.25, mu_min=30.0, sigma_min=4.5, k_max=20)
    pmf_p = plain_nb_pmf(lambda_rate=0.04, alpha=0.25, mu_min=30.0, sigma_min=4.5, k_max=20)
    pmf_0 = hurdle_nb_pmf(pi=0.0, lambda_rate=0.04, alpha=0.25, mu_min=30.0, sigma_min=4.5, k_max=20)

    assert np.all(pmf_h >= 0.0)
    assert np.isclose(pmf_h.sum(), 1.0, atol=1e-9)
    assert np.isclose(pmf_p.sum(), 1.0, atol=1e-9)
    assert np.allclose(pmf_p, pmf_0, atol=1e-12)

    lines = [0.5, 1.0, 1.5, 2.5, 3.5]
    probs = [p_over(L, 0.18, 0.04, 0.25, 30.0, 4.5) for L in lines]
    assert all(probs[i] >= probs[i + 1] for i in range(len(probs) - 1))
    assert 0.0 < probs[0] < 1.0

    # Zero minutes -> deterministic 0 count
    pmf_out = hurdle_nb_pmf(pi=0.2, lambda_rate=0.04, alpha=0.25, mu_min=0.0, sigma_min=0.0)
    assert np.isclose(pmf_out[0], 1.0)
    assert p_over(0.5, 0.2, 0.04, 0.25, 0.0, 0.0) == 0.0


def test_quadrature_agrees_with_dense_brute_force_integration():
    pi, lam, alpha, mu_min, sigma_min = 0.12, 0.045, 0.30, 32.0, 5.0
    pmf_gh = hurdle_nb_pmf(pi, lam, alpha, mu_min, sigma_min, k_max=15, n_quad=5)

    # Brute-force 2000-point grid integration over M ~ max(0, N(mu_min, sigma_min^2))
    z_grid = np.linspace(-6.0, 6.0, 2001)
    dz = z_grid[1] - z_grid[0]
    w_grid = norm.pdf(z_grid) * dz
    w_grid /= w_grid.sum()
    m_grid = np.maximum(0.0, mu_min + sigma_min * z_grid)

    r = 1.0 / alpha
    ks = np.arange(0, 16)
    bf_pmf = np.zeros(16)
    for m, w in zip(m_grid, w_grid):
        mu = lam * m
        if mu <= 0:
            nb_k = np.eye(1, 16)[0]
        else:
            nb_k = nbinom.pmf(ks, r, r / (r + mu))
        bf_pmf += w * ((1.0 - pi) * nb_k + np.eye(1, 16)[0] * pi)

    assert np.max(np.abs(pmf_gh - bf_pmf)) < 2e-3
    p_gh = p_over(1.5, pi, lam, alpha, mu_min, sigma_min)
    p_bf = 1.0 - float(bf_pmf[:2].sum())
    assert abs(p_gh - p_bf) < 2e-3
    assert math.isclose(hurdle_nb_cdf(1.5, pi, lam, alpha, mu_min, sigma_min), 1.0 - p_gh, abs_tol=1e-9)


def test_u4_location_shift_preserves_pi_and_alpha():
    pi, lam0, alpha, mu_min, sigma_min = 0.15, 0.035, 0.28, 30.0, 4.5
    line = 0.5
    target_p = 0.58
    lam1 = shift_lambda_for_p_over(line, target_p, pi, lam0, alpha, mu_min, sigma_min)
    got_p = p_over(line, pi, lam1, alpha, mu_min, sigma_min)
    assert abs(got_p - target_p) < 1e-7
    assert lam1 > lam0  # initial lambda gave lower p_over than 0.58


def test_shadow_scorer_and_rescore_window(tmp_path: Path):
    """End-to-end smoke test of train_pipeline + score_board + --rescore pre-lock gate."""
    import json
    import pandas as pd
    from tests.test_mod04_feature_store import _write_fixture_dataset
    from mod04.score_clustera import score_board
    from mod04.train_clustera_nba import train_pipeline
    import mod04.train_clustera_nba as tr_mod

    data_dir = tmp_path / "data"
    _write_fixture_dataset(data_dir, include_future_box=True, include_future_games=True)
    # Add an October month of games so monthly walk-forward has Oct (train) -> Nov (OOF)
    pb_p = data_dir / "NBA/player_boxscores_master.csv"
    gm_p = data_dir / "NBA/games_master.csv"
    pb_df = pd.read_csv(pb_p)
    gm_df = pd.read_csv(gm_p)
    oct_pb, oct_gm = [], []
    for k, od in enumerate(["2026-10-10", "2026-10-14", "2026-10-18", "2026-10-22", "2026-10-26"]):
        g_sub = gm_df.iloc[[0]].copy()
        g_sub["game_id"] = f"g_oct_{k}"
        g_sub["date"] = od
        oct_gm.append(g_sub)
        p_sub = pb_df[pb_df["game_id"] == "g_0"].copy()
        p_sub["game_id"] = f"g_oct_{k}"
        p_sub["date"] = od
        oct_pb.append(p_sub)
    pd.concat([*oct_pb, pb_df], ignore_index=True).to_csv(pb_p, index=False)
    pd.concat([*oct_gm, gm_df], ignore_index=True).to_csv(gm_p, index=False)

    orig_min = tr_mod.MIN_TRAIN_ROWS
    tr_mod.MIN_TRAIN_ROWS = 4
    try:
        bundle = train_pipeline(
            data_dir=data_dir,
            out_dir=tmp_path / "models",
            min_date="2026-10-01",
            test_from="2026-11-01",
            version="v_smoke",
            max_rounds=10,
        )
    finally:
        tr_mod.MIN_TRAIN_ROWS = orig_min

    assert set(bundle["manifest"]["stats"]) == {"steals", "blocks"}
    assert "ll_hurdle_nb" in bundle["manifest"]["oof_metrics"]["blocks"]
    assert bundle["manifest"]["oof_metrics"]["blocks"]["shipped_variant"] in {"hurdle_nb", "plain_nb"}

    board = {
        "date": "2026-11-20",
        "rows": [
            {
                "player": "Alice Guard",
                "stat": "steals",
                "line": 0.5,
                "consensus_line": 0.5,
                "side": "over",
                "p_cons_w_over": 0.60,
                "p_over_blend": 0.61,
                "p_blend_side": 0.61,
                "game_key": "nba:2026-11-20:NY@BOS",
                "commence": "2026-11-20T23:30:00+00:00",  # locks in 30m from 23:00Z
                "team": "BOS",
                "opp": "NY",
                "engine": "hier_v1",
            },
            {
                "player": "Evan Guard",
                "stat": "blocks",
                "line": 0.5,
                "consensus_line": 0.5,
                "side": "under",
                "p_cons_w_over": 0.35,
                "p_blend_side": 0.65,
                "game_key": "nba:2026-11-20:NY@BOS",
                "commence": "2026-11-20T22:00:00+00:00",  # already tipped before 23:00Z!
                "team": "NY",
                "opp": "BOS",
                "engine": "hier_v1",
            },
        ],
    }
    scored = score_board(board, tmp_path / "models", data_dir, rescore=False, now_utc="2026-11-20T22:30:00Z")
    assert len(scored) == 2
    assert all(r["rescore_of"] is None for r in scored)
    assert scored[0]["engine"] == "mod04_steals_shadow_v1"
    assert scored[1]["engine"] == "mod04_blocks_shadow_v1"
    assert abs(scored[1]["p_board_served"] - 0.35) < 1e-6

    existing = tmp_path / "shadow.jsonl"
    existing.write_text("\n".join(json.dumps(r) for r in scored) + "\n")
    rescored = score_board(
        board,
        tmp_path / "models",
        data_dir,
        rescore=True,
        now_utc="2026-11-20T23:00:00Z",
        existing_jsonl=existing,
    )
    # Only Alice Guard (23:30Z tip = 30m away <= 75m) is rescored; Evan Guard (22:00Z tip) has passed!
    assert len(rescored) == 1
    assert rescored[0]["player"] == "Alice Guard"
    assert rescored[0]["rescore_of"] == "2026-11-20T22:30:00+00:00"
    assert rescored[0]["mu_min"] == 0.0  # Alice is Out in 13:00 ET pre-cutoff report!
