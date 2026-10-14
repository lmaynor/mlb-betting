"""nba.props.dist + train: distribution math, artifact contract, walk-forward split discipline."""
import json

import numpy as np
import pandas as pd
import pytest

from nba.props import config as C
from nba.props import dist as Dd
from nba.props import train as T


# ----------------------------------------------------------------------------- distributions
@pytest.mark.parametrize("family", Dd.FAMILIES)
def test_cdf_is_a_cdf_and_pmf_sums_to_one(family):
    mu, var = 12.0, 40.0
    ks = np.arange(0, 120)
    F = Dd.cdf(ks, mu, var, family)
    assert np.all(np.diff(F) >= -1e-12) and F[-1] == pytest.approx(1.0, abs=1e-6) and F[0] >= 0
    pm = np.exp(Dd.logpmf(ks, mu, var, family))
    assert pm.sum() == pytest.approx(1.0, abs=1e-4)
    assert (pm * ks).sum() == pytest.approx(mu, rel=0.02)


@pytest.mark.parametrize("family", Dd.FAMILIES)
def test_half_point_lines_have_no_push_and_probabilities_complement(family):
    po = Dd.p_over(np.array([10.5, 12.5]), 12.0, 40.0, family)
    assert np.all(Dd.p_push(np.array([10.5, 12.5]), 12.0, 40.0, family) == 0)
    assert po[0] > po[1]
    # P(over 10.5) == P(Y >= 11) == 1 - F(10)
    assert po[0] == pytest.approx(1 - Dd.cdf(10, 12.0, 40.0, family))
    pw = Dd.p_over(12.0, 12.0, 40.0, family) + Dd.p_push(12.0, 12.0, 40.0, family) + Dd.cdf(11, 12.0, 40.0, family)
    assert pw == pytest.approx(1.0)


def test_nbinom_matches_the_repo_negbin_convention():
    """Var = mu + alpha*mu^2 and P(over) identical to mlb.analysis.gen_preds.p_over (so MLB and NBA read the same)."""
    from mlb.analysis.gen_preds import p_over as repo_p_over
    mu, alpha = 5.0, 0.3
    var = mu + alpha * mu ** 2
    for line in (3.5, 5.5, 8.5):
        assert Dd.p_over(line, mu, var, "nbinom") == pytest.approx(repo_p_over(line, mu, alpha), abs=1e-9)


def test_underdispersed_variance_is_clipped_to_poisson_not_crashing():
    po = Dd.p_over(1.5, 1.2, 0.5, "nbinom")                  # var < mean
    from scipy.stats import poisson
    assert po == pytest.approx(1 - poisson.cdf(1, 1.2), abs=1e-3)


def test_crps_prefers_the_true_distribution():
    rng = np.random.default_rng(0)
    mu, alpha = 9.0, 0.25
    n = 1 / alpha
    y = rng.negative_binomial(n, n / (n + mu), size=4000).astype(float)
    var = mu + alpha * mu ** 2
    good = Dd.crps(y, np.full(len(y), mu), np.full(len(y), var), "nbinom", 60).mean()
    shifted = Dd.crps(y, np.full(len(y), mu + 3), np.full(len(y), var), "nbinom", 60).mean()
    narrow = Dd.crps(y, np.full(len(y), mu), np.full(len(y), mu * 1.05), "nbinom", 60).mean()
    assert good < shifted and good < narrow


def test_pit_recalibration_removes_a_systematic_shape_error():
    """Truth is a symmetric (rounded normal) count; the NegBin family is too right-skewed and under-states P(over) at the
    centre. A PIT map fitted on one sample fixes it on a fresh sample, and is ~identity when the family is already right."""
    rng = np.random.default_rng(5)
    mu, sd = 20.0, 5.0
    var = sd ** 2
    y_fit = np.round(rng.normal(mu, sd, 40000)).clip(0)
    y_test = np.round(rng.normal(mu, sd, 40000)).clip(0)
    mus, vs = np.full(40000, mu), np.full(40000, var)
    tab = Dd.fit_pit_table(y_fit, mus, vs, "nbinom")
    line = 19.5
    truth = float((y_test > line).mean())
    raw = float(Dd.p_over(line, mu, var, "nbinom"))
    cal = float(Dd.p_over_cal(line, mu, var, "nbinom", tab))
    assert abs(cal - truth) < abs(raw - truth) and abs(cal - truth) < 0.01
    # cal CDF is a proper CDF
    F = Dd.cdf_cal(np.arange(0, 60), mu, var, "nbinom", tab)
    assert np.all(np.diff(F) >= -1e-9) and F[0] >= 0 and F[-1] == pytest.approx(1.0, abs=1e-3)
    # already-calibrated forecast -> map is ~identity
    n = 1 / 0.25
    y_nb = rng.negative_binomial(n, n / (n + 9.0), 40000).astype(float)
    tab2 = Dd.fit_pit_table(y_nb, np.full(40000, 9.0), np.full(40000, 9.0 + 0.25 * 81), "nbinom")
    grid = np.linspace(0.05, 0.95, 19)
    assert np.allclose(Dd.recal(grid, tab2), grid, atol=0.02)
    assert np.array_equal(Dd.recal(grid, None), grid)


def test_phi_table_recovers_dispersion_from_out_of_sample_residuals():
    rng = np.random.default_rng(1)
    mu = rng.uniform(2, 20, size=60000)
    alpha = 0.2
    n = 1 / alpha
    y = rng.negative_binomial(n, n / (n + mu)).astype(float)
    tab = Dd.fit_phi_table(mu, y)
    est = Dd.phi_from_table(np.array([3.0, 10.0, 18.0]), tab)
    true = 1 + alpha * np.array([3.0, 10.0, 18.0])
    assert np.allclose(est, true, rtol=0.12)
    assert np.all(Dd.var_from_table(np.array([0.5]), tab, phi_floor=1.0) >= 0.5)


# ----------------------------------------------------------------------------- artifacts
def _toy(n=900, seed=2):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"min_ewm8": rng.uniform(8, 38, n), "points_ewm8": rng.uniform(3, 25, n),
                      "is_home": rng.integers(0, 2, n).astype(float), "noise": rng.normal(size=n),
                      "missing_sometimes": np.where(rng.random(n) < 0.2, np.nan, rng.normal(size=n))})
    mu = 0.55 * X["points_ewm8"] + 0.05 * X["min_ewm8"]
    y = rng.poisson(mu).astype(float)
    return X, y


def test_stat_model_roundtrip_and_meta_contract(tmp_path):
    X, y = _toy()
    tr, va = slice(0, 600), slice(600, 900)
    m = T.fit_stat(X.iloc[tr], y[tr], X.iloc[va], y[va], "points", max_rounds=40, info={"trained_through_season": 2025})
    files = m.save(tmp_path)
    assert set(files) == {"booster", "meta", "var"} and all((tmp_path / f.split("/")[-1]).exists() for f in files.values())
    names = C.artifact_names("points")
    assert names["booster"] == "xgb_nba_props_points_v1.json" and names["meta"] == "model_meta_nba_props_points_v1.json"
    meta = json.loads((tmp_path / names["meta"]).read_text())
    for key in ("version", "features", "feature_means", "best_iteration", "family", "phi_table", "log_only"):
        assert key in meta, key                                          # CONTEXT s5 model-artifact contract
    assert meta["features"] == list(X.columns) and meta["best_iteration"] == m.n_rounds and meta["log_only"] is True
    assert meta["pit_table"] and len(meta["pit_table"]["x"]) == len(meta["pit_table"]["y"])
    m2 = T.StatModel.load("points", tmp_path)
    assert m2.pit_table == m.pit_table
    mu0, v0 = m.predict(X.iloc[va])
    assert np.allclose(m.p_over(20.5, mu0, v0), m2.p_over(20.5, mu0, v0))
    mu1, v1 = m.predict(X.iloc[va])
    mu2, v2 = m2.predict(X.iloc[va])
    assert np.allclose(mu1, mu2) and np.allclose(v1, v2)
    assert np.all(mu1 > 0) and np.all(v1 >= mu1 * T.PHI_FLOOR[m.family] - 1e-9)
    # scoring tolerates NaN features and column reordering (reindexed to the stored feature list)
    shuffled = X.iloc[va][list(reversed(X.columns))]
    assert np.allclose(m.predict_mu(shuffled), mu1)


def test_fetch_models_round_trips_through_storage_and_fails_loudly_when_missing(tmp_path, monkeypatch):
    """score.py --models-gcs: artifacts read back through mlb_core.storage (local mode here); a missing file raises."""
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    base = tmp_path / "base"
    monkeypatch.setenv("MLB_BASE_DATA", str(base))
    X, y = _toy()
    m = T.fit_stat(X.iloc[:600], y[:600], X.iloc[600:], y[600:], "points", max_rounds=30)
    stash = tmp_path / "stash"
    m.save(stash)
    target = base / "Models" / "NBA" / "props"
    target.mkdir(parents=True)
    for f in stash.glob("*.json"):
        (target / f.name).write_bytes(f.read_bytes())
    got = T.fetch_models(tmp_path / "dl", stats=["points"])
    m2 = T.StatModel.load("points", got)
    assert np.allclose(m.predict_mu(X.iloc[600:]), m2.predict_mu(X.iloc[600:]))
    with pytest.raises(Exception):
        T.fetch_models(tmp_path / "dl2", stats=["rebounds"])              # nothing uploaded for rebounds


def test_train_final_writes_local_files_only(tmp_path, monkeypatch):
    """GCS upload is behind an explicit flag: train_final itself must never touch mlb_core.storage."""
    from mlb_core import storage

    def boom(*a, **k):
        raise AssertionError("training attempted a GCS write")

    monkeypatch.setattr(storage, "upload_model", boom)
    monkeypatch.setattr(storage, "write_bytes", boom)
    X, y = _toy()
    meta = pd.DataFrame({"season_year": np.r_[np.full(300, 2023), np.full(300, 2024), np.full(300, 2025)],
                         "points": y})
    monkeypatch.setattr(T, "MAX_ROUNDS", 30)
    written = T.train_final(X, meta, tmp_path, stats=["points"], last_season=2025, progress=lambda *_: None)
    assert (tmp_path / "xgb_nba_props_points_v1.json").exists() and list(written) == ["points"]


def test_walk_forward_never_trains_on_the_test_season_or_the_one_before_it_unvalidated(monkeypatch):
    """For test season T: train on <= T-2, validate on T-1, predict T -- recorded from the calls themselves."""
    seen = []
    X, y = _toy(n=1200)
    meta = pd.DataFrame({"season_year": np.repeat([2021, 2022, 2023, 2024], 300), "points": y, "rebounds": y,
                         "assists": y, "threes": y, "pra": y})

    real = T.fit_stat

    def spy(Xtr, ytr, Xva, yva, stat, **kw):
        seen.append((len(Xtr), len(Xva)))
        kw["max_rounds"] = 25
        return real(Xtr, ytr, Xva, yva, stat, **kw)

    monkeypatch.setattr(T, "fit_stat", spy)
    out = T.walk_forward(X, meta, stats=["points"], test_seasons=(2023, 2024), progress=lambda *_: None)
    assert seen == [(300, 300), (600, 300)]                              # T=2023: train 2021 | val 2022; T=2024: 2021-22 | 2023
    for season in (2023, 2024):
        rows = out[out["season"] == season]["row"].to_numpy()
        assert set(meta["season_year"].to_numpy()[rows]) == {season}


def test_no_lightgbm_dependency():
    import pathlib
    req = (pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text().lower()
    assert "lightgbm" not in req
