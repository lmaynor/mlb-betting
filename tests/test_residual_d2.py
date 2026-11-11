"""Argon 2026-11-10, Pack 4 direction D2 + data-methods D4: universal market-anchored residual
training and calibration plumbing.

Covers:
  * the de-vigged consensus log-odds is a FIXED OFFSET in the residual learners (coefficient 1.0,
    never learned): a change in the market moves the prediction exactly 1:1 in log-odds space;
  * the winner's-curse concave shrinkage, exact form
        edge_shrunk = edge_blend * exp(-max(0, |gap_raw| - 0.08) / 0.04)
    (identity at |gap| <= 0.08, exponential decay above);
  * ECE computation correctness on a hand-computable fixture, and a synthetic overconfident model
    whose pre-blend isotonic calibration reaches the D4 target ECE <= 0.015 on validation;
  * the Official Card caps in the picks path (official EV in [3.5%, 10%] inclusive on the shrunk
    blended EV, >= 4 books, sharp agreement required), each cap rejecting and accepting;
  * per-(sport, stat) opt-in: residual training refuses a stat that has not opted in, and pre-blend
    calibration is a passthrough until a calibrator is registered (serving paths unchanged).
"""
import math

import numpy as np
import pytest

from mlb_core.risk import calibration as C
from mlb_core.risk import residual as R


@pytest.fixture(autouse=True)
def _clean_residual_state():
    R.clear_opt_ins()
    C.clear_pre_blend_calibrators()
    yield
    R.clear_opt_ins()
    C.clear_pre_blend_calibrators()


def _logit(p):
    return np.log(np.asarray(p, float) / (1 - np.asarray(p, float)))


# ------------------------------------------------------------------------------------------------- fixed offset
def _residual_dataset(n=8000, seed=3):
    rng = np.random.default_rng(seed)
    pm = rng.uniform(0.2, 0.8, n)
    X = rng.normal(size=(n, 2))
    z = _logit(pm) + 0.9 * X[:, 0] - 0.6 * X[:, 1]
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(float)
    return X, y, pm


def test_offset_is_fixed_not_learned_logit():
    X, y, pm = _residual_dataset()
    m = R.fit_offset_logit(X, y, pm, feature_names=["a", "b"], l2=5.0)
    assert m.offset_coef == 1.0
    # moving ONLY the market moves the prediction exactly 1:1 in log-odds: the offset enters verbatim
    p1 = m.predict(X, pm)
    p2 = m.predict(X, np.full(len(X), 0.5))
    shift = (_logit(p2) - _logit(p1)) - (0.0 - _logit(pm))
    assert np.abs(shift).max() < 1e-9
    # the learned part is the residual only: it recovers the planted residual slopes (standardized X)
    assert m.coef[0] == pytest.approx(0.9, abs=0.15) and m.coef[1] == pytest.approx(-0.6, abs=0.15)
    # the artifact contract pins the offset: a dict without offset_coef == 1.0 is refused
    d = m.to_dict()
    assert d["offset"] == "logit(p_market)" and d["offset_coef"] == 1.0
    assert np.abs(R.OffsetLogit.from_dict(d).predict(X, pm) - p1).max() < 1e-12
    d["offset_coef"] = 0.7
    with pytest.raises(ValueError):
        R.OffsetLogit.from_dict(d)


def test_offset_logit_with_no_signal_stays_on_the_market():
    rng = np.random.default_rng(5)
    n = 6000
    pm = rng.uniform(0.2, 0.8, n)
    X = rng.normal(size=(n, 2))                                   # pure noise features
    y = (rng.uniform(size=n) < pm).astype(float)                  # outcomes ARE the market
    m = R.fit_offset_logit(X, y, pm, l2=1e5)                      # heavily penalized residual
    assert np.abs(m.predict(X, pm) - pm).max() < 0.02


def test_offset_gbdt_uses_the_market_as_base_margin():
    X, y, pm = _residual_dataset(n=4000, seed=9)
    g = R.fit_offset_gbdt(X, y, pm, feature_names=["a", "b"], rounds=60)
    assert g.offset_coef == 1.0
    pred = g.predict(X, pm)
    # the trees learned a real residual: predictions beat the market's log loss on the training rows
    ll_model = float(np.mean(-(y * np.log(np.clip(pred, 1e-9, 1 - 1e-9)) +
                               (1 - y) * np.log(np.clip(1 - pred, 1e-9, 1)))))
    ll_mkt = float(np.mean(-(y * np.log(pm) + (1 - y) * np.log(1 - pm))))
    assert ll_model < ll_mkt
    # residual margin is the trees alone (base_margin 0): predict == sigmoid(offset + residual)
    assert np.abs(pred - R.sigmoid(R.devig_offset(pm) + g.residual_margin(X))).max() < 1e-6


def test_residual_training_is_opt_in_per_sport_stat():
    X, y, pm = _residual_dataset(n=500)
    with pytest.raises(ValueError):
        R.fit_residual_model(X, y, pm, "nba", "rebounds")         # not opted in: refused
    assert not R.residual_enabled("nba", "rebounds")
    R.opt_in("nba", "rebounds")
    assert R.residual_enabled("NBA", "Rebounds")                  # case-insensitive, per (sport, stat)
    assert not R.residual_enabled("nba", "threes")                # a different stat is NOT opted in
    m = R.fit_residual_model(X, y, pm, "nba", "rebounds", feature_names=["a", "b"])
    assert isinstance(m, R.OffsetLogit)


# ------------------------------------------------------------------------------------------------- shrinkage (exact D4 form)
@pytest.mark.parametrize("gap,factor", [
    (0.0, 1.0), (0.05, 1.0), (0.08, 1.0),                          # at / below the knee: NO shrinkage
    (0.12, math.exp(-1.0)),                                        # (0.12 - 0.08) / 0.04 = 1
    (0.16, math.exp(-2.0)),
    (0.28, math.exp(-5.0)),
    (-0.12, math.exp(-1.0)),                                       # |gap|: symmetric in the gap
])
def test_concave_shrink_edge_exact_formula(gap, factor):
    assert C.concave_shrink_edge(0.10, gap) == pytest.approx(0.10 * factor, rel=1e-12)
    assert C.concave_shrink_edge(-0.10, gap) == pytest.approx(-0.10 * factor, rel=1e-12)   # edge sign preserved


def test_concave_shrink_edge_edges():
    assert C.WINNERS_CURSE_KNEE == 0.08 and C.WINNERS_CURSE_TAU == 0.04
    assert C.concave_shrink_edge(0.07, None) == pytest.approx(0.07)      # no gap measured: no shrinkage
    assert C.concave_shrink_edge(float("nan"), 0.2) == 0.0
    xs = [i / 100 for i in range(0, 41)]
    ys = [C.concave_shrink_edge(0.10, x) for x in xs]
    assert all(b <= a for a, b in zip(ys, ys[1:]))                        # monotone non-increasing in |gap|


# ------------------------------------------------------------------------------------------------- ECE + pre-blend calibration
def test_ece_hand_computed_fixture():
    # 2 equal-count bins: [0.1, 0.2 | 0.8, 0.9] repeated; bin means pred 0.15 / 0.85, outcomes 0 / 1
    y = np.array([0, 0, 1, 1] * 25, dtype=float)
    p = np.array([0.1, 0.2, 0.8, 0.9] * 25, dtype=float)
    assert C.expected_calibration_error(y, p, n_bins=2) == pytest.approx(0.5 * 0.15 + 0.5 * 0.15)
    # a perfectly calibrated fixture has ECE exactly 0
    y2 = np.array([0, 1] * 50, dtype=float)
    p2 = np.full(100, 0.5)
    assert C.expected_calibration_error(y2, p2, n_bins=10) == pytest.approx(0.0)
    # too few rows to bin honestly -> NaN, never a fabricated pass
    assert math.isnan(C.expected_calibration_error([0, 1], [0.4, 0.6]))


def _overconfident(n, rng):
    p_raw = rng.uniform(0.05, 0.95, n)
    p_true = 1 / (1 + np.exp(-0.55 * _logit(p_raw)))              # the raw model is overconfident
    return (rng.uniform(size=n) < p_true).astype(float), p_raw


def test_pre_blend_isotonic_calibration_reaches_ece_target_on_validation():
    rng = np.random.default_rng(11)
    y_cal, p_cal = _overconfident(20000, rng)
    y_val, p_val = _overconfident(20000, rng)
    cal = C.fit_pre_blend_calibrator(y_cal, p_cal)
    rep = C.calibration_report(y_val, p_val, cal)
    assert rep["ece_before"] > C.ECE_TARGET                        # the raw model really is miscalibrated
    assert rep["ece_after"] < rep["ece_before"]
    assert rep["ece_after"] <= C.ECE_TARGET and rep["meets_target"] is True   # measured, not assumed


def test_pre_blend_calibration_passthrough_until_registered_and_applies_in_the_blend():
    from ev import daily_projections as DP
    base = DP.blend_p_over(0.70, 0.50, sport="nba", stat="rebounds")
    base_other = DP.blend_p_over(0.70, 0.50, sport="nba", stat="threes")
    p_cal, applied = C.pre_blend_calibrate(0.70, "nba", "rebounds")
    assert (p_cal, applied) == (0.70, False)                       # nothing registered: passthrough
    # an identity-ish calibrator (fitted on data where p was already the truth) changes the blend path
    rng = np.random.default_rng(2)
    y = (rng.uniform(size=4000) < 0.3).astype(float)
    C.register_pre_blend_calibrator("nba", "rebounds", C.fit_pre_blend_calibrator(y, np.full(4000, 0.3)))
    p_cal, applied = C.pre_blend_calibrate(0.70, "NBA", "Rebounds")
    assert applied is True and p_cal == pytest.approx(0.3, abs=0.02)
    moved = DP.blend_p_over(0.70, 0.50, sport="nba", stat="rebounds")
    assert moved != base and moved < base                          # calibrated model prob feeds the blend
    other = DP.blend_p_over(0.70, 0.50, sport="nba", stat="threes")
    assert other == base_other                                     # other stats untouched by the registration


# ------------------------------------------------------------------------------------------------- Official Card caps (picks path)
from ev import model_picks as MP
from tests.test_model_picks import row


def test_official_caps_ev_band_inclusive():
    assert MP.qualifies(row(blend_ev=0.035)) == "official"         # floor, inclusive
    assert MP.qualifies(row(blend_ev=0.034)) == "tracked"
    assert MP.qualifies(row(blend_ev=0.10, market_ev=0.095)) == "official"   # ceiling, inclusive (card gate needs market_ev <= 0.10 too)
    assert MP.qualifies(row(blend_ev=0.101, market_ev=0.095)) == "tracked"   # above the D4 ceiling: adverse-selection trap
    caps = MP.official_caps(row(blend_ev=0.07))
    assert caps["passes"] and caps["official_ev"] == pytest.approx(0.07)     # gap 0.05 <= 0.08: unshrunk


def test_official_caps_use_the_shrunk_ev():
    # raw gap 0.20: shrink factor exp(-(0.20-0.08)/0.04) = exp(-3); a 7% blend EV shrinks below the floor
    r = row(blend_ev=0.07, p_model_side=0.73, p_mkt_side=0.53)
    assert MP.official_ev(r) == pytest.approx(0.07 * math.exp(-3.0))
    assert MP.qualifies(r) == "tracked"
    # the board's own stamp wins when present
    assert MP.official_ev(row(blend_ev_shrunk=0.05)) == pytest.approx(0.05)
    assert MP.qualifies(row(blend_ev=0.20, blend_ev_shrunk=0.05, market_ev=0.09)) == "official"


def test_official_caps_books_floor():
    assert MP.qualifies(row(n_books=4)) == "official"
    assert MP.qualifies(row(n_books=3)) == "tracked"
    assert MP.official_caps(row(n_books=3))["books_ok"] is False


def test_official_caps_sharp_agreement_required():
    assert MP.qualifies(row(sharp_agrees=False)) == "tracked"                       # sharp disagreement
    assert MP.qualifies(row(sharp_agrees=False, n_sharp=2)) == "tracked"
    assert MP.qualifies(row(sharp_agrees=True, n_sharp=2)) == "official"             # sharp agreement present
    assert MP.qualifies(row(n_sharp=2)) == "tracked"                                 # a sharp book quotes but no agreement recorded
    assert MP.official_caps(row(sharp_agrees=True, n_sharp=1))["sharp_ok"] is True
    assert MP.official_caps(row(n_sharp=2))["sharp_ok"] is False
