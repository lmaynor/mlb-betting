"""Tests for MOD-04 Cluster (e) E2 hierarchical game-script opportunity model."""
import numpy as np
import pandas as pd
import pytest

from nfl.mod04e import model as M


def _synthetic_training_frame(n: int = 120, seed: int = 7):
    rng = np.random.default_rng(seed)
    target_share = rng.uniform(0.08, 0.30, size=n)
    carry_share = rng.uniform(0.05, 0.45, size=n)
    snap_share = np.clip(0.4 + 1.5 * target_share + rng.normal(0, 0.05, n), 0.25, 0.98)
    team_plays = rng.normal(63.0, 3.0, size=n)
    team_pass_rate = rng.normal(0.58, 0.04, size=n)
    team_proe = team_pass_rate - 0.57
    team_spread = rng.normal(0.0, 5.0, size=n)
    total_line = rng.normal(46.0, 4.0, size=n)
    imp_tot = total_line / 2.0 + team_spread / 2.0
    depth = np.where(target_share > 0.20, 1.0, 2.0)

    df = pd.DataFrame({
        "player_id": [f"P{i % 20}" for i in range(n)],
        "position": ["WR"] * n,
        "depth_chart_role": depth,
        "n_prior_games": rng.integers(3, 20, size=n).astype(float),
        "team_plays_ewm": team_plays,
        "team_pass_rate_ewm": team_pass_rate,
        "team_proe_ewm": team_proe,
        "team_spread": team_spread,
        "total_line": total_line,
        "implied_team_total": imp_tot,
        "snap_share_ewm": snap_share,
        "target_share_ewm": target_share,
        "carry_share_ewm": carry_share,
        "pass_attempts_ewm": rng.normal(33.0, 3.0, size=n),
        "catch_rate_ewm": rng.uniform(0.58, 0.72, size=n),
        "ypt_ewm": rng.uniform(6.5, 10.5, size=n),
        "ypc_ewm": rng.uniform(3.5, 5.2, size=n),
        "comp_pct_ewm": rng.uniform(0.60, 0.70, size=n),
        "int_rate_ewm": rng.uniform(0.015, 0.035, size=n),
        "rz_carries_ewm": rng.uniform(0.0, 1.5, size=n),
        "rz_targets_ewm": rng.uniform(0.2, 2.0, size=n),
        "rz_rush_td_rate_ewm": rng.uniform(0.08, 0.20, size=n),
        "rz_rec_td_rate_ewm": rng.uniform(0.10, 0.25, size=n),
        "explosive_td_rate_ewm": rng.uniform(0.01, 0.03, size=n),
        "vacated_target_share": np.zeros(n),
        "vacated_carry_share": np.zeros(n),
    })

    exp_rec = team_plays * team_pass_rate * target_share * df["catch_rate_ewm"].to_numpy()
    y_rec = rng.poisson(np.clip(exp_rec, 0.2, 15.0)).astype(float)
    y_rec_yds = np.round(y_rec * rng.gamma(2.5, 4.5, size=n)).astype(float)
    y_rush_att = rng.poisson(np.clip(team_plays * (1 - team_pass_rate) * carry_share, 0.2, 25.0)).astype(float)
    y_rush_yds = np.round(y_rush_att * rng.gamma(2.0, 2.1, size=n)).astype(float)
    y_pass_att = rng.poisson(34.0, size=n).astype(float)
    y_pass_cmp = rng.binomial(y_pass_att.astype(int), 0.65).astype(float)
    y_int = rng.poisson(0.8, size=n).astype(float)
    y_td_cnt = rng.poisson(0.45, size=n).astype(float)

    labels = pd.DataFrame({
        "receptions": y_rec,
        "rec_yds": y_rec_yds,
        "rush_attempts": y_rush_att,
        "rush_yds": y_rush_yds,
        "pass_attempts": y_pass_att,
        "pass_completions": y_pass_cmp,
        "interceptions": y_int,
        "anytime_td": (y_td_cnt >= 1).astype(float),
        "td_count": y_td_cnt,
        "gradeable": True,
    })
    return df, labels


def test_pmfs_sum_to_one_and_rungs_coherent_across_all_families():
    df, labels = _synthetic_training_frame(n=90)
    row = df.iloc[0].to_dict()

    for fam in M.ALL_FAMILIES:
        mdl = M.fit(df, labels, fam)
        assert mdl.engine_id == M.ENGINE_ID == "mod04_nfl_gamescript_shadow_v1"
        pmf = mdl.pmf(row)
        assert np.all(np.isfinite(pmf)) and np.all(pmf >= 0.0)
        assert abs(float(pmf.sum()) - 1.0) < 1e-9, (fam, pmf.sum())

        # Rung coherence: p_over monotonically non-increasing in line
        lines = [0.5, 1.5, 2.5, 4.5, 10.5, 25.5] if fam not in ("anytime_td", "interceptions") else [0.5, 1.5, 2.5]
        probs = [mdl.p_over(row, ln) for ln in lines]
        assert all(probs[i] >= probs[i + 1] - 1e-12 for i in range(len(probs) - 1)), (fam, probs)

        # Bound p_over / p_at_least after pmf(row) match two-arg calls
        assert mdl.p_over(lines[0]) == pytest.approx(probs[0], abs=1e-12)
        assert mdl.p_at_least(1) == pytest.approx(1.0 - pmf[0], abs=1e-12)


def test_anytime_td_p_at_least_one_equals_one_minus_p0_and_2plus_coherent():
    df, labels = _synthetic_training_frame(n=90)
    mdl = M.fit(df, labels, "anytime_td")
    row = df.iloc[0].to_dict()
    pmf = mdl.pmf(row)

    assert mdl.p_at_least(row, 1) == pytest.approx(1.0 - pmf[0], abs=1e-12)
    assert mdl.p_over(row, 0.5) == pytest.approx(1.0 - pmf[0], abs=1e-12)
    assert mdl.p_at_least(row, 2) == pytest.approx(float(pmf[2:].sum()), abs=1e-12)
    assert mdl.p_at_least(row, 1) >= mdl.p_at_least(row, 2)


def test_synthetic_fit_recovers_higher_target_share_ordering():
    df, labels = _synthetic_training_frame(n=150)
    mdl = M.fit(df, labels, "receptions")

    base = df.iloc[0].to_dict()
    low_row = {**base, "target_share_ewm": 0.08, "snap_share_ewm": 0.50, "depth_chart_role": 2.0}
    high_row = {**base, "target_share_ewm": 0.28, "snap_share_ewm": 0.90, "depth_chart_role": 1.0}

    low_pmf = mdl.pmf(low_row)
    high_pmf = mdl.pmf(high_row)
    low_mean = float(np.sum(np.arange(len(low_pmf)) * low_pmf))
    high_mean = float(np.sum(np.arange(len(high_pmf)) * high_pmf))
    assert high_mean > low_mean
    assert mdl.p_over(high_row, 3.5) > mdl.p_over(low_row, 3.5)


def test_fit_refuses_banned_columns():
    df, labels = _synthetic_training_frame(n=60)
    for col in ("targets_post", "actual_receptions", "outcome_status", "prop_line", "prop_odds_over"):
        bad = df.copy()
        bad[col] = 1.0
        with pytest.raises(ValueError):
            M.fit(bad, labels, "receptions")
