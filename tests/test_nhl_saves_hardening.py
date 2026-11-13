"""NHL saves hardening (Argon Props D1 / Pack 4 D1 / Data D2): 28-day log-odds centering, rolling residual
calibration, the dual-confirmed starter gate (fail-closed ESPN stub), the 14-day regime gate, the >= 4-book
median 3.0pp price gate, and the board / officials wiring in ev.daily_projections + ev.model_picks."""
import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

from nhl.props import saves_hardening as SH


def _sig(z):
    return 1 / (1 + math.exp(-z))


def _rows(start: str, n_days: int, per_day: int, p_mkt=0.50, resid=0.40, over_rate=0.45, seed=0):
    """Settled history rows: constant log-odds residual, wins drawn at `over_rate`."""
    rng = np.random.default_rng(seed)
    out = []
    d0 = date.fromisoformat(start)
    for k in range(n_days):
        day = (d0 + timedelta(days=k)).isoformat()
        for _ in range(per_day):
            out.append({"date": day, "p_model": _sig(SH.logit(p_mkt) + resid), "p_mkt": p_mkt,
                        "won_over": float(rng.uniform() < over_rate)})
    return out


AS_OF = "2026-02-01"


# -------------------------------------------------------------------------------------------------- centering
def test_centering_subtracts_the_trailing_28d_mean_residual():
    rows = _rows("2026-01-04", 28, 3, resid=0.40)                       # exactly the 28 days before AS_OF
    res = SH.trailing_residual(rows, AS_OF)
    assert res.applied and res.n == 84 and res.residual_mean == pytest.approx(0.40, abs=1e-9)
    # a model prob carrying exactly that bias is centered back onto the market
    assert SH.center_probability(_sig(0.40), res.residual_mean) == pytest.approx(0.50, abs=1e-9)
    # and in general the centered logit is the raw logit minus the residual
    p = 0.63
    assert SH.logit(SH.center_probability(p, 0.25)) == pytest.approx(SH.logit(p) - 0.25, abs=1e-9)


def test_centering_ignores_same_day_future_and_old_rows():
    base = _rows("2026-01-04", 28, 3, resid=0.40)
    poisoned = base + _rows(AS_OF, 10, 5, resid=9.0) + _rows("2025-11-01", 10, 5, resid=-9.0)
    assert SH.trailing_residual(poisoned, AS_OF).residual_mean == pytest.approx(0.40, abs=1e-9)


def test_centering_not_applied_below_min_n():
    res = SH.trailing_residual(_rows("2026-01-20", 5, 2, resid=0.40), AS_OF)   # n=10 < 30
    assert not res.applied and res.residual_mean == 0.0 and res.n == 10
    state = SH.build_state(_rows("2026-01-20", 5, 2, resid=0.40), AS_OF)
    h = state.harden(0.62, 0.50)
    assert h["p_centered"] == pytest.approx(0.62) and h["p_final"] == pytest.approx(0.62)


def test_walk_forward_centered_never_sees_its_own_day_or_later():
    rows = _rows("2025-10-01", 60, 4, resid=0.30, seed=3)
    df = SH.history_frame(rows)
    base = SH.walk_forward_centered(df)
    garbled = df.copy()
    after_mask = garbled["date"] > "2025-11-10"                        # future INPUTS are garbled ...
    garbled.loc[after_mask, "p_model"] = 0.99
    garbled.loc[after_mask, "p_mkt"] = 0.01
    garbled.loc[garbled["date"] >= "2025-11-10", "won_over"] = 1.0      # ... and labels on/after the day
    after = SH.walk_forward_centered(garbled)
    same = df["date"] == "2025-11-10"
    np.testing.assert_allclose(after[same.to_numpy()], base[same.to_numpy()], rtol=0, atol=0)


# ---------------------------------------------------------------------------------------------------- regime
def test_regime_gate_trips_above_050_and_not_at_or_below():
    hot = _rows("2026-01-25", 7, 10, over_rate=1.0, seed=1)             # 70 rows in the 14d window, all overs
    for r in hot:
        r["won_over"] = 1.0
    res = SH.regime_gate(hot, AS_OF)
    assert res.blocked and res.over_rate == pytest.approx(1.0) and res.n == 70

    half = _rows("2026-01-25", 7, 10, seed=1)
    for i, r in enumerate(half):
        r["won_over"] = float(i % 2 == 0)                               # exactly 0.50: does NOT trip (> 0.50)
    res = SH.regime_gate(half, AS_OF)
    assert not res.blocked and res.over_rate == pytest.approx(0.50)

    cold = _rows("2026-01-25", 7, 10, seed=1)
    for r in cold:
        r["won_over"] = 0.0
    assert not SH.regime_gate(cold, AS_OF).blocked


def test_regime_gate_ignores_rows_outside_the_14d_window_and_thin_windows_do_not_block():
    old = _rows("2025-12-01", 10, 10, seed=1)
    for r in old:
        r["won_over"] = 1.0
    res = SH.regime_gate(old, AS_OF)                                    # all overs, but a month old
    assert not res.blocked and res.n == 0 and res.over_rate is None
    thin = _rows("2026-01-30", 2, 3, seed=1)
    for r in thin:
        r["won_over"] = 1.0
    res = SH.regime_gate(thin, AS_OF)                                   # n=6 < REGIME_MIN_N: unknown, not blocked
    assert not res.blocked and res.over_rate is None and res.n == 6


# ---------------------------------------------------------------------------------------------------- starter
class _FakeSecond:
    name = "fake"

    def __init__(self, confirmed=True, scratched=False):
        self._c, self._s = confirmed, scratched

    def check(self, *, team, goalie, date):
        return SH.SecondSourceResult(source=self.name, confirmed=self._c, scratched=self._s,
                                     status="confirmed" if self._c else "unconfirmed")


def test_dual_gate_passes_only_on_rotowire_and_second_source_confirmation():
    rw = {"TOR": "Joseph Woll"}
    ok = SH.dual_confirmed_starter(team="TOR", goalie="Joseph Woll", date=AS_OF,
                                   rotowire_confirmed=rw, second_source=_FakeSecond(True))
    assert ok.official_ok and ok.reason == "dual_confirmed"
    # RotoWire writes an initialled first name on some pages: still the same goalie
    ok2 = SH.dual_confirmed_starter(team="TOR", goalie="J. Woll", date=AS_OF,
                                    rotowire_confirmed=rw, second_source=_FakeSecond(True))
    assert ok2.official_ok


def test_dual_gate_fails_closed_with_the_espn_stub():
    stub = SH.default_second_source()
    assert isinstance(stub, SH.EspnAvailabilityStub)
    res = stub.check(team="TOR", goalie="Joseph Woll", date=AS_OF)
    assert res.confirmed is False and res.status == "unconfirmed_stub"    # the stub confirms NOBODY
    gate = SH.dual_confirmed_starter(team="TOR", goalie="Joseph Woll", date=AS_OF,
                                     rotowire_confirmed={"TOR": "Joseph Woll"}, second_source=stub)
    assert not gate.official_ok and "second_source_unconfirmed" in gate.reason


def test_dual_gate_blocks_on_scratch_missing_rotowire_and_wrong_goalie():
    rw = {"TOR": "Joseph Woll"}
    assert not SH.dual_confirmed_starter(team="TOR", goalie="Joseph Woll", date=AS_OF,
                                         rotowire_confirmed=rw,
                                         second_source=_FakeSecond(True, scratched=True)).official_ok
    assert not SH.dual_confirmed_starter(team="TOR", goalie="Joseph Woll", date=AS_OF,
                                         rotowire_confirmed={}, second_source=_FakeSecond(True)).official_ok
    assert not SH.dual_confirmed_starter(team="TOR", goalie="Anthony Stolarz", date=AS_OF,
                                         rotowire_confirmed=rw, second_source=_FakeSecond(True)).official_ok


# ------------------------------------------------------------------------------------------------------ price
def test_price_gate_needs_4_books_and_3pp_over_the_median():
    assert SH.price_gate(0.55, 0.52, 4)[0] is True                      # exactly +3.0pp at exactly 4 books
    assert SH.price_gate(0.549, 0.52, 6)[0] is False                    # +2.9pp is short
    assert SH.price_gate(0.60, 0.52, 3)[0] is False                     # a 3-book median is not a >= 4-book median
    assert SH.price_gate(None, 0.52, 6)[0] is False
    assert SH.price_gate(0.60, None, 6)[0] is False


def test_officials_gate_combines_all_three_and_missing_inputs_fail():
    starter = SH.dual_confirmed_starter(team="TOR", goalie="Joseph Woll", date=AS_OF,
                                        rotowire_confirmed={"TOR": "Joseph Woll"},
                                        second_source=_FakeSecond(True))
    regime_ok = SH.RegimeResult(False, 0.44, 60)
    regime_hot = SH.RegimeResult(True, 0.58, 60)
    base = dict(p_model_side=0.56, p_median_side=0.52, n_books=5, starter=starter, regime=regime_ok)
    assert SH.officials_gate(**base).ok
    assert not SH.officials_gate(**{**base, "regime": regime_hot}).ok
    assert not SH.officials_gate(**{**base, "starter": None}).ok
    assert not SH.officials_gate(**{**base, "n_books": 2}).ok


STAMPS_OK = {"saves_starter_dual_confirmed": True, "saves_regime_blocked": False,
             "saves_median_edge": 0.041, "saves_median_n_books": 6,
             "saves_starter_gate_reason": "dual_confirmed", "saves_regime_over_rate": 0.44}


def test_officials_gate_from_row_legacy_unstamped_and_stamped():
    assert SH.officials_gate_from_row({"sport": "nhl", "stat": "saves"}) is None      # pre-hardening board
    assert SH.officials_gate_from_row(dict(STAMPS_OK)).ok
    for bad in ({"saves_starter_dual_confirmed": False}, {"saves_regime_blocked": True},
                {"saves_median_edge": 0.02}, {"saves_median_n_books": 3}):
        assert not SH.officials_gate_from_row({**STAMPS_OK, **bad}).ok, bad
    partial = dict(STAMPS_OK)
    del partial["saves_median_edge"]                                    # any stamp present -> fail-closed
    assert not SH.officials_gate_from_row(partial).ok


# ------------------------------------------------------------------------------------------------ calibration
def _calibration_history(seed=7):
    rng = np.random.default_rng(seed)
    out = []
    d0 = date(2025, 10, 20)
    for k in range(100):
        day = (d0 + timedelta(days=k)).isoformat()
        for _ in range(6):
            pm = float(rng.uniform(0.35, 0.65))
            pe = float(np.clip(pm + rng.normal(0, 0.07), 0.05, 0.95))
            true = _sig(SH.logit(pm) + 0.35 * (SH.logit(pe) - SH.logit(pm)))
            out.append({"date": day, "p_model": pe, "p_mkt": pm, "won_over": float(rng.uniform() < true)})
    return out


def test_calibration_fit_is_market_anchored_and_clamped():
    rows = _calibration_history()
    fit = SH.fit_residual_calibration(rows, "2026-01-28")
    assert fit is not None and fit.n >= SH.CALIB_MIN_N and fit.c > 0    # the planted signal is recovered
    for pm, pk in ((0.9, 0.5), (0.1, 0.5), (0.99, 0.9), (0.01, 0.1)):
        got = fit.apply(pm, pk)
        assert abs(got - pk) <= SH.MAX_CALIB_SHIFT + 1e-9               # never further than the band from market
        assert 0 < got < 1


def test_calibration_plumbing_through_the_state_pipeline():
    rows = _calibration_history()
    state = SH.build_state(rows, "2026-01-28")
    assert state.calibration is not None and state.centering.applied
    h = state.harden(0.70, 0.52)
    assert h["calibrated"] is True and h["center_n"] > 0
    assert abs(h["p_final"] - 0.52) <= SH.MAX_CALIB_SHIFT + 1e-9
    assert SH.logit(h["p_centered"]) == pytest.approx(SH.logit(0.70) - h["center_residual"], abs=1e-9)
    # no market price: calibration cannot run, the centered probability is final
    h2 = state.harden(0.70, None)
    assert h2["calibrated"] is False and h2["p_final"] == h2["p_centered"]
    # thin history: no fit, identity pipeline
    thin = SH.build_state(rows[:40], "2026-01-28")
    assert thin.calibration is None and thin.harden(0.70, 0.52)["p_final"] == pytest.approx(0.70)


# ------------------------------------------------------------------------------------------------- promotion
def test_promotion_check_is_the_preregistered_rule():
    assert SH.promotion_check(n_settled=399, ll_gain=0.01, roi=0.05, as_of="2026-11-01")["verdict"] == "pending"
    assert SH.promotion_check(n_settled=400, ll_gain=0.0020, roi=0.01, as_of="2026-11-01")["promote"] is True
    assert SH.promotion_check(n_settled=400, ll_gain=0.0019, roi=0.01, as_of="2026-11-01")["verdict"] == "display_only"
    assert SH.promotion_check(n_settled=400, ll_gain=0.01, roi=0.0, as_of="2026-11-01")["verdict"] == "display_only"
    assert SH.promotion_check(n_settled=12, ll_gain=0.01, roi=0.05, as_of="2026-12-21")["judged"] is True


# ---------------------------------------------------------------------------------------------------- history IO
def test_settled_history_parsing_and_loader():
    recs = [{"stat": "saves", "game_key": "nhl:2026-01-05:BOS@TOR", "won_over": 1.0, "void": False,
             "last": {"p_model": 0.44, "p_mkt": 0.50}},
            {"stat": "goals", "game_key": "nhl:2026-01-05:BOS@TOR", "won_over": 1.0, "void": False,
             "last": {"p_model": 0.44, "p_mkt": 0.50}},
            {"stat": "saves", "game_key": "nhl:2026-01-05:BOS@TOR", "won_over": None, "void": True,
             "last": {"p_model": 0.44, "p_mkt": 0.50}}]
    rows = SH.settled_history_from_records(recs)
    assert rows == [{"date": "2026-01-05", "p_model": 0.44, "p_mkt": 0.50, "won_over": 1.0}]

    from datetime import datetime, timezone
    now = datetime(2026, 1, 6, 17, 0, tzinfo=timezone.utc)
    payload = ("\n".join(json.dumps(r) for r in recs) + "\n").encode()
    store = {SH.SETTLED_KEY.format(day="2026-01-05"): payload}
    got = SH.load_settled_saves_history(now, days=3, read=store.get)
    assert got == rows
    assert SH.load_settled_saves_history(now, days=3, read=lambda k: None) == []


# ------------------------------------------------------------------------------------------------ board wiring
def test_board_stamps_trained_saves_and_leaves_the_probability_alone_without_history():
    from tests.test_nhl_props_hierarchical import AS_OF as BOARD_AS_OF, make_logs, row_for
    from mlb_core.data import cockpit_form as cf
    logs = make_logs()
    for r in logs.idx["goalie"].get(cf.norm_name("Gus Goalie"), []):
        r["player_id"] = 900
    logs.set_trained({(900, BOARD_AS_OF, "saves"): (26.4, 0.04, "v1")})
    row = row_for(logs, "saves", "Gus Goalie", line=25.5)
    assert row["nhl_model"] == "trained_v1" and 0 < row["p_over_model"] < 1
    # every gate stamp is present; the stub means dual confirmation is False (fail-closed, by design)
    assert row["saves_starter_dual_confirmed"] is False
    assert row["saves_starter_second_status"] == "unconfirmed_stub"
    assert row["saves_regime_blocked"] is False and row["saves_center_n"] == 0
    assert row["saves_median_n_books"] == row["n_books"]


def test_model_picks_qualifies_consults_the_hardening_stamps():
    from ev import model_picks as MP
    from tests.test_model_picks import row
    base = dict(sport="nhl", stat="saves", nhl_model="trained_v1")
    assert MP.qualifies(row(**base)) == "official"                       # no stamps: legacy board, fail-open
    assert MP.qualifies(row(**base, **STAMPS_OK)) == "official"          # every gate clears
    for bad in ({"saves_starter_dual_confirmed": False}, {"saves_regime_blocked": True},
                {"saves_median_edge": 0.029}, {"saves_median_n_books": 3}):
        assert MP.qualifies(row(**base, **{**STAMPS_OK, **bad})) == "tracked", bad
    pick = MP.to_pick(row(**base, **STAMPS_OK))
    assert pick["saves_hardening"]["gate_ok"] is True
    assert pick["saves_hardening"]["saves_median_edge"] == pytest.approx(0.041)
