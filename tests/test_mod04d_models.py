from __future__ import annotations

import numpy as np
import pandas as pd

from mod04d.distributions import negbin_pmf, pmf_log_likelihood
from mod04d.features import build_rolling_training_frame
from mod04d.nba_points import compare_points_distributions, fit as fit_points
from mod04d.nba_threes import fit_threes
from mod04d.nhl_scoring import SKATER_FEATURES, load_bundle, predict, save_bundle, train_bundle


def _synthetic_nba_frame(seed: int = 19, n_players: int = 20, n_dates: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-11-01", periods=n_dates, freq="D")
    teams = ["BOS", "NYK", "DEN", "OKC"]
    rows = []
    p_min = rng.uniform(18.0, 35.0, size=n_players)
    p_fga = rng.uniform(0.35, 0.60, size=n_players)
    p_fta = rng.uniform(0.06, 0.18, size=n_players)
    p_3sh = rng.uniform(0.25, 0.50, size=n_players)
    p_3p = rng.uniform(0.31, 0.41, size=n_players)

    for d_i, dt in enumerate(dates):
        for pid in range(n_players):
            mins = float(np.clip(rng.normal(p_min[pid], 4.0), 8.0, 42.0))
            fga = int(rng.poisson(max(1.0, mins * p_fga[pid])))
            fg3a = int(rng.binomial(fga, p_3sh[pid]))
            fg2a = fga - fg3a
            fta = int(rng.poisson(max(0.2, mins * p_fta[pid])))
            fg3m = int(rng.binomial(fg3a, p_3p[pid]))
            fg2m = int(rng.binomial(fg2a, 0.52))
            ftm = int(rng.binomial(fta, 0.80))
            rows.append(
                {
                    "game_date": dt.strftime("%Y-%m-%d"),
                    "game_id": 1000 + d_i,
                    "player_id": pid,
                    "team": teams[pid % len(teams)],
                    "minutes": mins,
                    "fga": fga,
                    "fgm": fg2m + fg3m,
                    "fg2a": fg2a,
                    "fg2m": fg2m,
                    "fg3a": fg3a,
                    "fg3m": fg3m,
                    "fta": fta,
                    "ftm": ftm,
                    "points": ftm + 2 * fg2m + 3 * fg3m,
                    "oreb": 1,
                    "tov": 2,
                    "starter": 1 if p_min[pid] >= 25.0 else 0,
                }
            )
    box = pd.DataFrame(rows)
    feats = build_rolling_training_frame(box, min_prior_games=5)
    targets = box[["game_date", "player_id", "points", "fg3m", "fg3a"]].rename(columns={"game_date": "date"})
    return feats.merge(targets, on=["date", "player_id"], how="inner").sort_values(["date", "player_id"]).reset_index(drop=True)


def _synthetic_nhl_hetero(seed: int = 23, n_rows: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-10-15", periods=24, freq="D")
    df = pd.DataFrame(index=range(n_rows))
    df["date"] = rng.choice([d.strftime("%Y-%m-%d") for d in dates], size=n_rows)
    df["game_id"] = np.arange(1, n_rows + 1)
    df["player_id"] = rng.integers(1, 30, size=n_rows)
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)

    for col in SKATER_FEATURES:
        df[col] = rng.uniform(0.2, 1.2, size=n_rows)
    df["toi_ewm"] = rng.uniform(10.0, 22.0, size=n_rows)
    df["toi_l10"] = df["toi_ewm"]
    df["ixg_p60_l20"] = rng.uniform(0.3, 1.5, size=n_rows)
    df["points_p60_l60"] = rng.uniform(1.0, 3.2, size=n_rows)
    df["is_d"] = (np.arange(n_rows) % 2 == 0).astype(float)
    df["n_prior"] = 25.0

    # Strongly heterogeneous variance across is_d so dispersion GBDT sees genuine signal
    mu_g = np.clip((df["toi_ewm"] / 60.0) * df["ixg_p60_l20"], 0.1, 1.2)
    alpha_g = np.where(df["is_d"] > 0.5, 0.80, 0.02)
    rates_g = rng.gamma(shape=1.0 / alpha_g, scale=alpha_g * mu_g)
    df["goals"] = rng.poisson(rates_g)

    mu_p = np.clip((df["toi_ewm"] / 60.0) * df["points_p60_l60"], 0.2, 2.0)
    alpha_p = np.where(df["is_d"] > 0.5, 0.65, 0.03)
    rates_p = rng.gamma(shape=1.0 / alpha_p, scale=alpha_p * mu_p)
    df["points"] = rng.poisson(rates_p)
    return df


def test_nba_points_and_threes_models() -> None:
    frame = _synthetic_nba_frame()
    dates = sorted(frame["date"].unique())
    split = dates[int(len(dates) * 0.7)]
    tr = frame[frame["date"] < split].reset_index(drop=True)
    oof = frame[frame["date"] >= split].reset_index(drop=True)

    m_pts = fit_points(tr, k_max=70, max_rounds=60, disp_rounds=30)
    pmf = m_pts.pmf(oof, k_max=70)
    assert pmf.shape == (len(oof), 71)
    assert np.allclose(pmf.sum(axis=-1), 1.0, atol=1e-6)

    y_oof = oof["points"].to_numpy(dtype=int)
    model_ll = pmf_log_likelihood(y_oof, pmf)
    const_mu = np.full(len(oof), float(tr["points"].mean()))
    const_ll = pmf_log_likelihood(y_oof, negbin_pmf(const_mu, 0.08, k_max=70))
    assert model_ll > const_ll

    cmp = compare_points_distributions(tr, oof, k_max=70, max_rounds=50, disp_rounds=25)
    assert cmp["decision"] in {"negbin", "skewnormal"}
    assert np.isfinite(cmp["negbin_ll"]) and np.isfinite(cmp["skewnormal_ll"])
    assert np.isfinite(cmp["negbin_tail_ll"]) and np.isfinite(cmp["skewnormal_tail_ll"])

    m_3 = fit_threes(tr, k_max=16, max_rounds=50, disp_rounds=25)
    assert m_3.family in {"negbin", "betabinom_negbin"}
    assert isinstance(m_3.meta.get("reason"), str) and len(m_3.meta["reason"]) > 0
    pmf3 = m_3.pmf(oof, k_max=16)
    assert np.allclose(pmf3.sum(axis=-1), 1.0, atol=1e-6)


def test_nhl_bundle_round_trip_and_heterogeneous_dispersion() -> None:
    df = _synthetic_nhl_hetero()
    bundle = train_bundle({"skater": df}, stats=("goals", "points"), max_rounds=60, disp_rounds=40)

    store: dict[str, bytes] = {}
    ver = save_bundle(bundle, lambda k, b: store.__setitem__(k, b))
    loaded = load_bundle(lambda k: store[k], version=ver)

    for stat in ("goals", "points"):
        assert loaded["manifest"]["engines"][stat] == f"mod04_nhl_{stat}_shadow_v1"
        mu_orig, alpha_orig = predict(bundle, df.head(40), stat=stat)
        mu_load, alpha_load = predict(loaded, df.head(40), stat=stat)
        assert np.allclose(mu_orig, mu_load, rtol=1e-6, atol=1e-6)
        assert np.allclose(alpha_orig, alpha_load, rtol=1e-6, atol=1e-6)
        # Dispersion alpha(x) must vary across rows (not constant)
        assert float(np.std(alpha_load)) > 1e-4
