"""Offline tests for nfl.models (distribution maths, training, persistence, walk-forward plumbing). Synthetic data."""
import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from nfl.features import props_features as F
from nfl.models import props as P
from nfl.models import walkforward as W

NB = {"family": "negbin", "alpha": 0.25}
NRM = {"family": "normal", "s0": float(np.log(20.0)), "sp": 0.4}
HG = {"family": "hgamma", "a": -2.0, "b": -0.6, "log_k": float(np.log(1.4)), "kb": 0.0}


# ---------------------------------------------------------------------------------------------- p_over maths
@pytest.mark.parametrize("params", [NB, HG, NRM, {"family": "negbin", "alpha": 0.0}])
@pytest.mark.parametrize("line", [0.5, 1.5, 3.5, 24.5, 3.0, 25.0, 0.0, 1.0])
@pytest.mark.parametrize("mu", [0.3, 2.0, 18.0, 70.0])
def test_probabilities_sum_to_one(params, line, mu):
    po, pu, pp = P.p_over("receptions" if params["family"] == "negbin" else "rec_yds", mu, line, params)
    assert po + pu + pp == pytest.approx(1.0, abs=1e-9)
    assert min(po, pu, pp) >= 0.0


@pytest.mark.parametrize("params", [NB, HG, NRM])
def test_half_point_lines_never_push_integer_lines_do(params):
    stat = "receptions" if params["family"] == "negbin" else "rec_yds"
    assert P.p_over(stat, 5.0, 4.5, params)[2] == 0.0
    po, pu, pp = P.p_over(stat, 5.0, 5.0, params)
    assert pp > 0.01
    # integer line push == pmf at that integer == difference of the half-point lines either side
    over_lo, under_lo, _ = P.p_over(stat, 5.0, 4.5, params)
    over_hi, under_hi, _ = P.p_over(stat, 5.0, 5.5, params)
    assert pp == pytest.approx(over_lo - over_hi, abs=1e-9)
    assert po == pytest.approx(over_hi, abs=1e-9) and pu == pytest.approx(under_lo, abs=1e-9)


@pytest.mark.parametrize("params", [NB, HG, NRM])
def test_monotone_in_line_and_in_mu(params):
    stat = "receptions" if params["family"] == "negbin" else "rec_yds"
    lines = np.arange(0.5, 40.5, 1.0)
    po, pu, pp = P.p_over(stat, 12.0, lines, params)
    assert np.all(np.diff(po) <= 1e-12) and np.all(np.diff(pu) >= -1e-12)
    mus = np.linspace(1.0, 60.0, 30)
    po_mu = P.p_over(stat, mus, 12.5, params)[0]
    assert np.all(np.diff(po_mu) >= -1e-9)


def test_anytime_td_is_one_minus_zero_mass():
    mu, alpha = 0.55, 0.05
    po, pu, pp = P.p_over("anytime_td", mu, 0.5, {"family": "negbin", "alpha": alpha})
    n = 1 / alpha
    p0 = sps.nbinom.pmf(0, n, n / (n + mu))
    assert pu == pytest.approx(p0) and po == pytest.approx(1 - p0) and pp == 0.0


def test_alpha_zero_is_poisson():
    k = np.arange(0, 8)
    np.testing.assert_allclose(P.negbin_cdf(k, 2.3, 0.0), sps.poisson.cdf(k, 2.3))


def test_hgamma_mean_matches_mu():
    mu = 55.0
    ks = np.arange(0, 600)
    cdf = P.hgamma_cdf(ks, np.full(len(ks), mu), HG)
    pmf = np.diff(np.concatenate([[0.0], cdf]))
    assert pmf.sum() == pytest.approx(1.0, abs=1e-6)
    assert float((ks * pmf).sum()) == pytest.approx(mu, rel=0.05)


def test_hgamma_shape_slope_is_recovered():
    rng = np.random.default_rng(3)
    mu = rng.uniform(8.0, 120.0, 8000)
    true = {"a": -2.5, "b": -0.4, "log_k": float(np.log(3.0)), "kb": 0.6}
    pi, shape, scale = P._hg_parts(mu, true)
    pos = rng.gamma(shape, scale)
    y = np.where(rng.random(len(mu)) < pi, 0, np.maximum(np.round(pos), 1))
    fit = P.fit_hgamma(mu, y)
    assert fit["kb"] == pytest.approx(0.6, abs=0.2)


def test_select_family_prefers_the_true_family():
    rng = np.random.default_rng(5)
    mu = rng.uniform(150, 300, 4000)
    y = np.clip(np.round(rng.normal(mu, 0.3 * mu ** 0.8)), 0, None)           # symmetric -> normal should win
    params, fits, scores = P.select_family(("hgamma", "normal", "negbin"), mu, y)
    assert params["family"] == "normal" and set(fits) == {"hgamma", "normal", "negbin"}
    mu2 = rng.uniform(5, 90, 4000)
    k = 1.2
    y2 = np.round(rng.gamma(k, mu2 / k))                                      # right-skewed, CV const -> not normal
    assert P.select_family(("hgamma", "normal", "negbin"), mu2, y2)[0]["family"] != "normal"


def test_normal_fit_recovers_sd():
    rng = np.random.default_rng(6)
    mu = rng.uniform(100, 300, 5000)
    y = np.round(rng.normal(mu, 60.0))
    fit = P.fit_normal(mu, y)
    assert np.exp(fit["s0"]) * (200.0 / 100.0) ** fit["sp"] == pytest.approx(60.0, rel=0.1)


def test_unknown_stat_and_family_raise():
    with pytest.raises(ValueError):
        P.p_over("nope", 1.0, 0.5, NB)
    with pytest.raises(ValueError):
        P.dist_cdf("nope", 1, 1.0, {})


def test_vectorised_matches_scalar():
    mus = np.array([1.0, 4.0, 9.0])
    lines = np.array([0.5, 4.5, 8.0])
    po, pu, pp = P.p_over("receptions", mus, lines, NB)
    for i in range(3):
        s = P.p_over("receptions", float(mus[i]), float(lines[i]), NB)
        assert (po[i], pu[i], pp[i]) == pytest.approx(s)


# ---------------------------------------------------------------------------------------------- dispersion fits
def test_negbin_alpha_recovered_on_simulated_data():
    rng = np.random.default_rng(0)
    mu = rng.uniform(1.0, 8.0, 6000)
    alpha = 0.3
    n = 1 / alpha
    y = rng.negative_binomial(n, n / (n + mu))
    fit = P.fit_negbin_alpha(mu, y)
    assert fit["alpha"] == pytest.approx(alpha, abs=0.06)


def test_hgamma_fit_recovers_zero_mass_and_shape():
    rng = np.random.default_rng(1)
    mu = rng.uniform(5.0, 90.0, 6000)
    pi = P._sigmoid(HG["a"] + HG["b"] * np.log(mu))
    k = float(np.exp(HG["log_k"]))
    pos = rng.gamma(k, (mu / (1 - pi)) / k)
    y = np.where(rng.random(len(mu)) < pi, 0, np.maximum(np.round(pos), 1))
    fit = P.fit_hgamma(mu, y)
    assert fit["family"] == "hgamma"
    assert np.exp(fit["log_k"]) == pytest.approx(k, rel=0.2)
    assert abs(fit["kb"]) < 0.25                           # truth is a constant shape
    # fitted zero-mass at mu=30 close to the truth
    est = P._sigmoid(fit["a"] + fit["b"] * np.log(30.0))
    tru = P._sigmoid(HG["a"] + HG["b"] * np.log(30.0))
    assert est == pytest.approx(tru, abs=0.03)


# ---------------------------------------------------------------------------------------------- training
def synth_features(seed=0, seasons=(2019, 2020, 2021, 2022, 2023), per_season=900, stat="receptions"):
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        n = per_season
        usage = rng.uniform(2.5, 10.0, n)
        env = rng.normal(0, 1, n)
        mu = 0.55 * usage * np.exp(0.08 * env)
        alpha = 0.15
        y = rng.negative_binomial(1 / alpha, (1 / alpha) / ((1 / alpha) + mu))
        rows.append(pd.DataFrame({
            "player_id": [f"p{i}" for i in range(n)], "player_name": "x", "position": "WR", "pos_grp": "WR",
            "team": "AAA", "opponent": "BBB", "season": s, "week": rng.integers(1, 18, n), "game_id": [f"{s}_{i}" for i in range(n)],
            "gameday": "2020-01-01", "is_upcoming": False,
            "tgt_ewm": usage + rng.normal(0, 0.4, n),
            "rec_l5": 0.55 * usage + rng.normal(0, 0.8, n), "rec_szn": 0.55 * usage + rng.normal(0, 0.9, n),
            "rec_ewm": 0.55 * usage + rng.normal(0, 0.7, n), "env": env, "noise": rng.normal(size=n),
            "y_receptions": y.astype(float)}))
    df = pd.concat(rows, ignore_index=True)
    for c in F.Y_COLS:
        if c not in df:
            df[c] = 0.0
    for c in F.ELIG_COLS:
        df[c] = 1
    for c in ("rec_l5", "rec_szn", "rec_ewm"):
        df[c] = df[c].clip(lower=0.05)
    return df


def test_train_predict_save_load_roundtrip(tmp_path, monkeypatch):
    df = synth_features()
    model = P.train_stat(df, "receptions")
    assert model.meta["version"] == P.VERSION and model.meta["dispersion"]["family"] == "negbin"
    assert 0.02 < model.meta["dispersion"]["alpha"] < 0.5
    assert set(model.features) == set(F.feature_columns(df))
    assert set(model.meta["feature_means"]) == set(model.features)
    assert model.meta["train_seasons"] == [2019, 2020, 2021, 2022, 2023]
    mu = P.predict_mu(model, df.head(200))
    assert (mu > 0).all()
    # learned the signal: correlation with the true usage-driven mean
    assert np.corrcoef(mu, df.head(200)["tgt_ewm"])[0, 1] > 0.8

    monkeypatch.setenv("NFL_MODEL_DIR", str(tmp_path))
    d = P.save_model(model)
    assert d == str(tmp_path)
    again = P.load_model("receptions")
    np.testing.assert_allclose(P.predict_mu(again, df.head(200)), mu, rtol=1e-5)
    assert again.meta["dispersion"] == model.meta["dispersion"]
    out = P.predict_prob(again, df.head(5), 3.5)
    np.testing.assert_allclose(out[["p_over", "p_under", "p_push"]].sum(axis=1), 1.0)


def test_yards_model_trains_with_auto_family_and_exports_alternatives():
    rng = np.random.default_rng(2)
    df = synth_features(per_season=500, seasons=(2019, 2020, 2021, 2022))
    mu = 15.0 * df["tgt_ewm"] * np.exp(rng.normal(0, 0.1, len(df)))
    df["y_rec_yds"] = np.round(rng.gamma(1.5, mu.to_numpy() / 1.5))
    df["rec_yds_l5"] = df["rec_yds_szn"] = df["rec_yds_ewm"] = mu
    model = P.train_stat(df, "rec_yds")
    assert model.dispersion["family"] in {"hgamma", "normal", "negbin"}
    assert set(model.meta["alt_dispersions"]) == {"hgamma", "normal", "negbin"}
    assert model.meta["objective"] == "reg:tweedie"
    po, pu, pp = P.p_over("rec_yds", P.predict_mu(model, df.head(50)), 45.5, model.dispersion)
    np.testing.assert_allclose(po + pu + pp, 1.0)


def test_predict_fills_absent_feature_columns_with_means():
    df = synth_features(per_season=500, seasons=(2019, 2020, 2021))
    model = P.train_stat(df, "receptions")
    mu = P.predict_mu(model, df.drop(columns=["env"]).head(20))
    assert len(mu) == 20 and np.isfinite(mu).all()
    with pytest.raises(KeyError):
        P.predict_mu(model, df.drop(columns=["env"]).head(20), fill_missing_cols=False)


def test_training_excludes_upcoming_unlabelled_and_ineligible_rows():
    df = synth_features(per_season=400, seasons=(2019, 2020, 2021))
    df.loc[df.index[:50], "is_upcoming"] = True
    df.loc[df.index[50:100], "y_receptions"] = np.nan
    df.loc[df.index[100:150], "elig_receptions"] = 0
    tr = P.training_rows(df, "receptions")
    assert len(tr) == len(df) - 150


def test_dispersion_is_fitted_on_out_of_sample_residuals_only():
    df = synth_features(per_season=600, seasons=(2019, 2020, 2021, 2022))
    spec = P.SPECS["receptions"]
    feats = F.feature_columns(df)
    oof = P.oof_predictions(df, spec, feats, rounds=60, n_folds=2)
    assert set(oof["season"]) == {2021, 2022}          # earliest season never scored (nothing to train on)
    model = P.train_stat(df, "receptions")
    assert model.meta["n_oof"] > 0


# ---------------------------------------------------------------------------------------------- walk-forward
def test_proxy_line_is_nearest_half_point_and_atd_fixed():
    np.testing.assert_allclose(W.proxy_line([0.2, 3.2, 3.8, 4.1, 6.5], "receptions"), [0.5, 3.5, 3.5, 4.5, 6.5])
    np.testing.assert_allclose(W.proxy_line([0.1, 0.9], "anytime_td"), [0.5, 0.5])


def test_eval_season_trains_only_on_earlier_seasons_and_reports_metrics():
    df = synth_features(per_season=700, seasons=(2019, 2020, 2021, 2022))
    res, model = W.eval_season(df, "receptions", 2022)
    assert max(model.meta["train_seasons"]) == 2021
    assert (res["season"] == 2022).all()
    s = W.summarize(res, "receptions")
    for k in ("mae_model", "mae_l5", "pdev_model", "ls_model", "brier_model", "brier_l5", "ece_model",
              "t_abs_vs_l5", "t_ls_vs_l5"):
        assert k in s and np.isfinite(s[k])
    assert (res["line"] % 1 == 0.5).all()
    # the model sees real signal in this synthetic world, so it should not be worse than a noisy baseline on MAE
    assert s["mae_model"] < s["mae_l5"] * 1.02


def test_report_renders_and_carries_the_no_lines_disclaimer():
    df = synth_features(per_season=700, seasons=(2019, 2020, 2021, 2022))
    res, _ = W.eval_season(df, "receptions", 2022)
    out = W.render_report({"receptions": {2022: W.summarize(res, "receptions")}},
                          {"partial_season": None, "pooled": {"receptions": res}, "pooled_seasons": "2022-2022"})
    assert "NO HISTORICAL NFL PROP LINES" in out and "receptions" in out and "Pooled" in out
    out.encode("ascii")                                   # ASCII-only convention


def test_volume_count_specs_reuse_the_count_path():
    spec_cols = {"pass_attempts": "y_pass_attempts", "completions": "y_completions", "rush_attempts": "y_rush_attempts"}
    for stat, target in spec_cols.items():
        sp = P.SPECS[stat]
        assert sp.target == target and sp.kind == "count" and sp.objective == "count:poisson" and sp.family == "negbin"
        assert sp.elig in F.ELIG_COLS and target in F.Y_COLS
        po, pu, pp = P.p_over(stat, 33.0, 32.5, {"family": "negbin", "alpha": 0.05})
        assert po + pu + pp == pytest.approx(1.0) and pp == 0.0
    assert set(P.DEFAULT_STATS) <= set(P.SPECS) and not set(spec_cols) & set(P.DEFAULT_STATS)
