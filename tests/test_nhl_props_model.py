"""NHL SOG: NegBin helpers, market analytics (consensus / Sink A / cluster stats / blend test), artifact contract."""
import json

import numpy as np
import pandas as pd
import pytest

from nhl.props import features as F
from nhl.props import market, negbin, train
from tests.test_nhl_props_features import synth_gamelog


# ------------------------------------------------------------------------------------------------ negbin
def test_vectorised_p_over_matches_the_repos_canonical_scalar():
    from mlb.analysis.gen_preds import p_over
    for line in (0.5, 1.5, 2.5, 3.5):
        for mu in (0.7, 1.6, 2.9):
            for a in (0.0, 0.09, 0.3):
                assert negbin.p_over_vec(line, mu, a) == pytest.approx(p_over(line, mu, a))
    arr = negbin.p_over_vec(np.array([1.5, 2.5]), np.array([1.6, 1.6]), np.array([0.0, 0.1]))
    assert arr[0] == pytest.approx(p_over(1.5, 1.6, 0.0)) and arr[1] == pytest.approx(p_over(2.5, 1.6, 0.1))
    assert negbin.p_over_vec(1.5, 0.0, 0.1) == 0.5                          # degenerate mu convention


def test_fit_alpha_recovers_overdispersion():
    rng = np.random.default_rng(1)
    mu = rng.uniform(0.8, 3.0, 20000)
    n = 1 / 0.2
    y = rng.negative_binomial(n, n / (n + mu))
    assert negbin.fit_alpha(y, mu) == pytest.approx(0.2, abs=0.05)
    yp = rng.poisson(mu)
    assert negbin.fit_alpha(yp, mu) <= 0.03


# ------------------------------------------------------------------------------------------------ market
def _pairs():
    """One prop, 5 books; book 'soft' prices the over at +125 (fair ~ -120/+100 elsewhere)."""
    rows = []
    for b, o, u in (("a", -120, 100), ("b", -118, -102), ("c", -122, 102), ("d", -121, 101), ("soft", 125, -160)):
        rows.append({"event_id": "e", "pkey": "id1", "line": 2.5, "bookmaker": b, "over": o, "under": u, "game_id": 1,
                     "status": "ok", "push": False, "won_over": 1.0})
    from mlb_core.odds.utils import american_to_implied_prob, devig_two_way
    df = pd.DataFrame(rows)
    df["fair_over"] = [devig_two_way(american_to_implied_prob(o), american_to_implied_prob(u), "proportional")[0]
                       for o, u in zip(df.over, df.under)]
    return df


def test_leave_self_out_consensus_excludes_the_quote_itself():
    p = market.add_consensus(_pairs())
    soft = p[p.bookmaker == "soft"].iloc[0]
    others = p[p.bookmaker != "soft"]["fair_over"]
    assert soft["loo_n"] == 4 and soft["loo_fair"] == pytest.approx(others.median())
    a = p[p.bookmaker == "a"].iloc[0]
    assert a["loo_n"] == 4 and a["cons_n"] == 5


def test_sink_a_flags_only_the_soft_over_and_settles_flat_stake():
    p = market.add_consensus(_pairs())
    b = market.sink_a_bets(p, min_others=3, min_ev=0.03, max_ev=None)
    assert list(b["bookmaker"]) == ["soft"] and list(b["side"]) == ["over"]
    assert b["ret"].iloc[0] == pytest.approx(1.25)                          # won at +125
    capped = market.sink_a_bets(p, min_others=3, min_ev=0.03, max_ev=0.15)
    assert len(capped) == 0                                                  # the repo EV cap removes a too-good-to-be-true quote


def test_roi_stats_clusters_correlated_bets():
    ret = pd.Series([1.0, 1.0, 1.0, 1.0, -1.0, -1.0])
    indep = market.roi_stats(ret, pd.Series(range(6)))
    one_game = market.roi_stats(ret, pd.Series([1, 1, 1, 1, 2, 2]))
    assert indep["roi"] == one_game["roi"] == pytest.approx(1 / 3)
    assert one_game["se"] > indep["se"] and one_game["n_clusters"] == 2    # same bets, fewer independent clusters -> wider SE


def test_side_returns_skip_voids_and_pushes():
    p = _pairs().head(3).copy()
    p.loc[p.index[0], "status"] = "void"
    p.loc[p.index[1], "push"] = True
    r = market.side_returns(p, "over")
    assert np.isnan(r.iloc[0]) and np.isnan(r.iloc[1]) and not np.isnan(r.iloc[2])


def test_blend_regression_finds_no_signal_when_model_is_noise_and_finds_it_when_real():
    rng = np.random.default_rng(3)
    n = 6000
    true_p = rng.uniform(0.35, 0.65, n)
    y = (rng.uniform(size=n) < true_p).astype(float)
    mkt = np.clip(true_p + rng.normal(0, 0.03, n), 0.05, 0.95)              # market ~ truth
    noise = np.clip(mkt + rng.normal(0, 0.05, n), 0.05, 0.95)               # "model" = market + pure noise
    cl = np.arange(n) // 6
    r0 = market.blend_regression(y, mkt, noise, cl)
    assert abs(r0["t_c"]) < 3 and r0["b"] == pytest.approx(1.0, abs=0.3)
    informed = np.clip(true_p + rng.normal(0, 0.01, n), 0.05, 0.95)         # model that knows the truth better than the market
    r1 = market.blend_regression(y, mkt, informed, cl)
    assert r1["c"] > 0.5 and r1["t_c"] > 3


def test_logloss_and_brier_basics():
    assert market.logloss([1, 0], [0.9, 0.1]) == pytest.approx(-np.log(0.9))
    assert market.brier([1, 0], [0.9, 0.1]) == pytest.approx(0.01)


# ------------------------------------------------------------------------------------------------ artifact contract
@pytest.fixture(scope="module")
def artifact():
    gl = synth_gamelog(n_days=300, seed=5)
    feat = F.build_features(gl)
    return train.fit(feat, cutoff=None), feat


def test_artifact_contract_and_roundtrip(tmp_path, artifact):
    art, feat = artifact
    meta = art["meta"]
    for k in ("version", "features", "feature_means", "best_iteration", "nb_alpha", "auc_oos"):      # CONTEXT s5 contract
        assert k in meta
    assert meta["features"] == F.FEATURES and set(meta["feature_means"]) == set(F.FEATURES)
    assert 0.0 <= meta["nb_alpha"] <= 0.6 and meta["best_iteration"] >= 1
    bp, mp = train.save(art, str(tmp_path))
    assert bp.name == "xgb_nhl_sog_v1.json" and mp.name == "model_meta_nhl_sog_v1.json"
    loaded = train.load(str(tmp_path))
    rows = feat.dropna(subset=["sog"]).tail(50)
    assert np.allclose(train.predict_mu(art, rows), train.predict_mu(loaded, rows), atol=1e-5)
    assert json.loads(mp.read_text())["features"] == F.FEATURES


def test_training_uses_only_rows_before_the_cutoff_and_nan_features_fall_back_to_means(artifact):
    art, feat = artifact
    cut = sorted(feat["date"].unique())[100]
    tr = train.training_frame(feat, cut)
    assert tr["date"].max() < cut and (tr["n_prior"] >= train.MIN_PRIOR).all()
    art2 = train.fit(feat, cutoff=cut)
    assert art2["meta"]["train_end"] < cut
    row = feat.dropna(subset=["sog"]).tail(1).copy()
    blank = row.copy()
    blank[F.FEATURES] = np.nan
    means = pd.DataFrame([art2["meta"]["feature_means"]])
    assert train.predict_mu(art2, blank)[0] == pytest.approx(train.predict_mu(art2, means)[0], abs=1e-5)


def test_refuses_to_fit_on_a_tiny_sample(artifact):
    _, feat = artifact
    with pytest.raises(RuntimeError):
        train.fit(feat.head(100), cutoff=None)


def test_gcs_upload_is_never_implicit(monkeypatch, tmp_path, artifact):
    called = []
    monkeypatch.setattr(train, "upload_gcs", lambda d: called.append(d))
    monkeypatch.setattr(train, "load_gamelog", lambda s, db=None: synth_gamelog(n_days=300, seed=5))
    assert train.main(["--source", "db", "--out-dir", str(tmp_path)]) == 0
    assert called == [] and (tmp_path / "xgb_nhl_sog_v1.json").exists()
    assert train.main(["--source", "db", "--out-dir", str(tmp_path), "--gcs"]) == 0
    assert called == [str(tmp_path)]
