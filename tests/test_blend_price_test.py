"""mlb_core.odds.model_vs_market.blend_price_test: walk-forward blend, scores and price rules on synthetic frames."""
import numpy as np
import pandas as pd
import pytest

from mlb_core.odds import model_vs_market as MVM


def _world(n_per_season=4000, seasons=(2022, 2023, 2024), signal=0.4, seed=0):
    """Truth: logit P(over) = logit(p_mkt) + signal * s, where the model sees s (+ noise) and the market does not."""
    rng = np.random.default_rng(seed)
    rows = []
    for sea in seasons:
        pm = np.clip(rng.normal(0.5, 0.05, n_per_season), 0.3, 0.7)
        s = rng.normal(0, 1, n_per_season)
        lm = np.log(pm / (1 - pm))
        truth = 1 / (1 + np.exp(-(lm + signal * s)))
        y = (rng.random(n_per_season) < truth).astype(float)
        lmod = lm + (s * 0.5 + rng.normal(0, 0.5, n_per_season))
        pmod = 1 / (1 + np.exp(-lmod))
        date = pd.to_datetime(f"{sea}-11-01") + pd.to_timedelta(rng.integers(0, 120, n_per_season), unit="D")
        rows.append(pd.DataFrame({"season": sea, "over": y, "p_market": pm, "p_model": pmod,
                                  "game_date": date.strftime("%Y-%m-%d"), "game_id": np.arange(n_per_season) + sea * 10**5,
                                  "best_over_odds": -108.0, "best_under_odds": -108.0}))
    return pd.concat(rows, ignore_index=True)


def test_first_season_is_never_scored_and_fit_uses_only_earlier_seasons():
    r = MVM.blend_price_test(_world(), n_boot=50)
    assert sorted(r["fits"]) == [2023, 2024]
    assert r["fits"][2023]["n_train"] == 4000 and r["fits"][2024]["n_train"] == 8000
    assert r["n"] == 8000


def test_real_signal_gives_positive_c_and_a_better_brier_than_the_market():
    r = MVM.blend_price_test(_world(signal=0.6, n_per_season=8000), n_boot=100)
    assert all(v["c"] > 0.1 for v in r["fits"].values())
    d = r["scores"]["d_brier_blend_vs_market"]
    assert d["mean"] < 0 and d["ci_hi"] < 0.0005
    assert r["scores"]["blend"]["brier"] < r["scores"]["market"]["brier"]


def test_no_signal_gives_c_near_zero_and_blend_is_not_better_than_market():
    r = MVM.blend_price_test(_world(signal=0.0, n_per_season=8000, seed=3), n_boot=100)
    assert all(abs(v["c"]) < 0.15 for v in r["fits"].values())
    assert r["scores"]["d_brier_blend_vs_market"]["mean"] > -0.0005


def test_blend_is_clamped_to_the_market_and_price_rules_are_reported():
    f = _world(n_per_season=1500)
    r = MVM.blend_price_test(f, thresholds=(0.03, 0.05), max_shift=0.0001, n_boot=50)
    # with a ~zero shift budget the blend IS the market: same Brier, same bets under the 'market' and 'blend' rules
    assert r["scores"]["blend"]["brier"] == pytest.approx(r["scores"]["market"]["brier"], abs=1e-4)
    assert r["price"]["blend"][0.05]["n_bets"] == pytest.approx(r["price"]["market"][0.05]["n_bets"], abs=5)
    assert set(r["price"]) == {"blend", "market", "model"} and set(r["price"]["model"]) == {0.03, 0.05}
    assert "rule=blend" in MVM.render_price_test("t", r)


def test_roi_arithmetic_on_a_hand_checkable_frame():
    # one season to fit on (>= min_train via a low min_train), one to score; every row's over wins at +100, under -100
    train = pd.DataFrame({"season": 1, "over": [1.0, 0.0] * 3, "p_market": 0.5, "p_model": 0.5,
                          "game_date": "2020-01-01", "game_id": range(6), "best_over_odds": 100.0, "best_under_odds": 100.0})
    test = pd.DataFrame({"season": 2, "over": [1.0, 1.0, 0.0, 1.0], "p_market": 0.5, "p_model": 0.7,
                         "game_date": ["2021-01-01", "2021-01-01", "2021-01-02", "2021-01-02"], "game_id": range(10, 14),
                         "best_over_odds": 100.0, "best_under_odds": -150.0})
    r = MVM.blend_price_test(pd.concat([train, test], ignore_index=True), thresholds=(0.0,), min_train=3, n_boot=20)
    m = r["price"]["model"][0.0]                      # model 0.7 vs implied 0.5 at +100 -> take OVER on every row
    assert m["n_bets"] == 4 and m["roi"] == pytest.approx((1 + 1 - 1 + 1) / 4)


def test_missing_columns_and_empty_input():
    with pytest.raises(ValueError):
        MVM.blend_price_test(pd.DataFrame({"over": [1.0]}))
    assert MVM.blend_price_test(_world(n_per_season=100), min_train=10**6)["n"] == 0
    assert "not run" in MVM.render_price_test("x", {"n": 0})
