"""tests/test_combo_taker_window2.py -- Verification for Window 2 Portfolio Architecture Package (RES-12 / RUL-20261119).

Tests all 13 requirements specified in WINDOW2_BUILD_PACKET.md.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

import pytest

from ev.combo_taker_scanner import (
    ComboCandidate,
    ComboLeg,
    ComboLiabilityGuard,
    ComboNoSignal,
    evaluate_combo,
)
from ev.combo_taker_window2 import (
    FamilyTrustBook,
    LegStressInputs,
    RoiCalibrator,
    Window2Admission,
    Window2Config,
    Window2Decision,
    Window2Fire,
    fit_calibrator,
    rotten_leg_v2,
)

NOW = 1_800_000_000.0


# -----------------------------------------------------------------------------
# Fixtures / helpers matching tests/test_tail_and_combo_taker.py style
# -----------------------------------------------------------------------------

def _leg(**kw) -> ComboLeg:
    base = dict(
        ticker="T1",
        venue="kalshi",
        player_norm=None,
        game_id="g1",
        sport="nfl",
        stat="rec_yds",
        line=59.5,
        direction=1,
        p_lambda=0.50,
        routing="OVER_ELIGIBLE",
        q_implied=0.50,
        is_hot_scratch=False,
        game_start_ts=NOW + 7200.0,
        team=None,
    )
    base.update(kw)
    return ComboLeg(**base)


def _candidate(legs: List[ComboLeg], **kw) -> ComboCandidate:
    base = dict(
        combo_id="c1",
        venue="kalshi",
        legs=legs,
        bid_yes=0.45,
        age_since_creation_sec=3600.0,
        is_rfq_live=False,
        now_ts=NOW,
        stake=None,
    )
    base.update(kw)
    return ComboCandidate(**base)


def _firing_candidate(combo_id: str, game_a: str = "g1", game_b: str = "g2", **kw) -> ComboCandidate:
    legs = [
        _leg(ticker=f"{combo_id}-a", game_id=game_a),
        _leg(ticker=f"{combo_id}-b", game_id=game_b),
    ]
    return _candidate(legs, combo_id=combo_id, **kw)


def _fire(
    candidate: ComboCandidate,
    stress: Sequence[Optional[LegStressInputs]] = (),
    day: str = "2026-11-19",
) -> Window2Fire:
    sig = evaluate_combo(candidate)
    return Window2Fire(candidate=candidate, signal=sig, stress=tuple(stress), day=day)


def _trusted_book() -> FamilyTrustBook:
    book = FamilyTrustBook(Window2Config())
    for fam in ("NBA_PROP", "NHL_PROP", "NFL_PROP", "GAME_LINE"):
        for d in range(8):
            for _ in range(15):
                book.add_fire(fam, f"day_{d}", 0.05)
    return book


# -----------------------------------------------------------------------------
# Required 13 Tests
# -----------------------------------------------------------------------------

def test_tier_b_reservation_caps_at_40_percent_before_release() -> None:
    """1. Many Tier B fires, one game, mins_to_tip > 60:

    assert tier-B collateral <= 0.40*150 game cap and <= 0.40*600 pool;
    then the same fires with game_start within 60 min admit strictly more collateral.
    """
    cfg = Window2Config()
    # Unprimed trust book -> GAME_LINE is ACCRUING -> all fires are Tier B
    book = FamilyTrustBook(cfg)

    # 20 firing candidates on the same game "g1" with mins_to_tip > 60 (120 min)
    fires_far: List[Window2Fire] = []
    for i in range(20):
        legs = [
            _leg(ticker=f"f{i}-a", game_id="g1", player_norm=None, stat=f"stat_a_{i}", game_start_ts=NOW + 7200.0),
            _leg(ticker=f"f{i}-b", game_id="g1", player_norm=None, stat=f"stat_b_{i}", game_start_ts=NOW + 7200.0),
        ]
        cand = _candidate(legs, combo_id=f"c_far_{i}", now_ts=NOW)
        fires_far.append(_fire(cand))

    engine_far = Window2Admission(cfg=cfg, trust_book=book)
    decisions_far = engine_far.run_day(fires_far, "day1")

    admitted_collateral_far = sum(d.collateral for d in decisions_far if d.admitted)
    assert admitted_collateral_far <= 0.40 * cfg.game_cap_dollars + 1e-9
    assert admitted_collateral_far <= 0.40 * cfg.daily_pool + 1e-9
    assert any("TIER_B_RESERVATION" in d.refusals for d in decisions_far)

    # Same fires with game_start within 60 min (mins_to_tip = 45 min)
    fires_near: List[Window2Fire] = []
    for i in range(20):
        legs = [
            _leg(ticker=f"f{i}-a", game_id="g1", player_norm=None, stat=f"stat_a_{i}", game_start_ts=NOW + 2700.0),
            _leg(ticker=f"f{i}-b", game_id="g1", player_norm=None, stat=f"stat_b_{i}", game_start_ts=NOW + 2700.0),
        ]
        cand = _candidate(legs, combo_id=f"c_near_{i}", now_ts=NOW)
        fires_near.append(_fire(cand))

    engine_near = Window2Admission(cfg=cfg, trust_book=book)
    decisions_near = engine_near.run_day(fires_near, "day1")

    admitted_collateral_near = sum(d.collateral for d in decisions_near if d.admitted)
    assert admitted_collateral_near > admitted_collateral_far


def test_shared_leg_cap_stops_pileup_at_35() -> None:
    """2. >=10 fires sharing one (game, player, stat) leg:

    assert total admitted collateral charged to that leg <= 35.0 and at least one refusal names SHARED_LEG_CAP.
    """
    cfg = Window2Config()
    book = _trusted_book()  # Tier A eligible

    # Create 12 fires sharing leg (g1, star_player, rec_yds), with unique 2nd legs in unique games
    fires: List[Window2Fire] = []
    for i in range(12):
        shared_leg = _leg(ticker="shared_l1", game_id="g1", player_norm="star_player", sport="nfl", stat="rec_yds")
        other_leg = _leg(ticker=f"other_{i}", game_id=f"game_unique_{i}", player_norm=f"p_{i}", sport="nfl", stat="rush_yds")
        cand = _candidate([shared_leg, other_leg], combo_id=f"share_{i}")
        fires.append(_fire(cand))

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day(fires, "day1")

    shared_key = ("g1", "star_player", "rec_yds")
    admitted_decisions = [d for d in decisions if d.admitted]
    total_charged_collateral = sum(d.collateral for d in admitted_decisions)

    assert total_charged_collateral <= 35.0 + 1e-9
    assert any("SHARED_LEG_CAP" in d.refusals for d in decisions)


def test_rotten_leg_v2_shadow_never_blocks() -> None:
    """3. A fire whose stress inputs make V2 flag True (huge p_max on one leg)

    is still admitted when caps allow; decision.rotten_leg_v2 is True and admitted is True.
    """
    cfg = Window2Config()
    book = _trusted_book()
    cand = _firing_candidate("c_stress", "g1", "g2")
    # Massive p_max = 0.99 causes single-leg edge collapse under stress
    stress = (LegStressInputs(p_max=0.99), None)
    fire = _fire(cand, stress=stress)

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day([fire], "day1")

    assert len(decisions) == 1
    d = decisions[0]
    assert d.rotten_leg_v2 is True
    assert "EDGE_COLLAPSE" in d.rotten_leg_v2_conditions
    assert d.admitted is True


def test_rotten_leg_v2_condition_b() -> None:
    """4. Claimed roi > 1.50 and bid_yes/p_true_yes > 2.50 -> flag with TAIL_RATIO_ANOMALY."""
    cfg = Window2Config()
    cand = _firing_candidate("c_cond_b", bid_yes=0.80)
    sig = ComboNoSignal(
        combo_id="c_cond_b",
        venue="kalshi",
        ask_no=0.20,
        p_true_yes=0.10,
        p_cons_yes=0.12,
        roi_no_taker=1.60,
        has_rotten_leg=False,
        is_cannibalistic=False,
        fires=True,
        reasons=("QUALIFIED_NO_TAKER",),
    )
    res = rotten_leg_v2(cand, sig, (), cfg)
    assert res.flag is True
    assert "TAIL_RATIO_ANOMALY" in res.conditions


def test_calibration_clamps_at_plus_50_percent() -> None:
    """5. Calibrator fit on rows with high realized ROI; predict(roi_claimed=150.0) <= 0.50."""
    cfg = Window2Config()
    rows = [
        {"roi_claimed": 0.05, "ask_no": 0.50, "pnl_per_contract": 0.40, "family": "NBA_PROP"},
        {"roi_claimed": 0.10, "ask_no": 0.50, "pnl_per_contract": 0.45, "family": "NBA_PROP"},
        {"roi_claimed": 0.50, "ask_no": 0.50, "pnl_per_contract": 0.50, "family": "NBA_PROP"},
        {"roi_claimed": 1.00, "ask_no": 0.50, "pnl_per_contract": 0.50, "family": "NBA_PROP"},
    ]
    cal = fit_calibrator(rows, cfg)
    pred = cal.predict(150.0, "NBA_PROP")
    assert pred <= 0.50


def test_calibration_shrinks_explosion_rows() -> None:
    """6. Fit on synthetic rows where tiny-ask rows realized poorly;

    predict for a huge claimed ROI is far below the raw claim and below predict for
    a moderate clean claim's ratio (assert predict(50.0) < 5.0).
    """
    cfg = Window2Config()
    rows = [
        # Normal rows realize well
        {"roi_claimed": 0.05, "ask_no": 0.50, "pnl_per_contract": 0.10, "family": "NBA_PROP"},
        {"roi_claimed": 0.10, "ask_no": 0.50, "pnl_per_contract": 0.12, "family": "NBA_PROP"},
        {"roi_claimed": 0.20, "ask_no": 0.40, "pnl_per_contract": 0.15, "family": "NBA_PROP"},
        {"roi_claimed": 0.30, "ask_no": 0.40, "pnl_per_contract": 0.15, "family": "NBA_PROP"},
        # Tiny-ask rows claim enormous ROI but realize poorly
        {"roi_claimed": 30.0, "ask_no": 0.02, "pnl_per_contract": -0.01, "family": "NBA_PROP"},
        {"roi_claimed": 50.0, "ask_no": 0.02, "pnl_per_contract": -0.02, "family": "NBA_PROP"},
        {"roi_claimed": 80.0, "ask_no": 0.01, "pnl_per_contract": -0.02, "family": "NBA_PROP"},
        {"roi_claimed": 120.0, "ask_no": 0.01, "pnl_per_contract": -0.02, "family": "NBA_PROP"},
    ]
    cal = fit_calibrator(rows, cfg)
    pred_huge = cal.predict(50.0, "NBA_PROP")
    assert pred_huge < 5.0
    assert pred_huge <= cfg.output_clamp


def test_circuit_breaker_excludes_family() -> None:
    """7. Trust book with a family at fires>=50, days>=5, mean<=-0.020 ->

    its fires refused with FAMILY_CIRCUIT_BROKEN, contracts 0.
    """
    cfg = Window2Config()
    book = FamilyTrustBook(cfg)
    for d in range(5):
        for _ in range(10):
            book.add_fire("NFL_PROP", f"day_{d}", -0.05)

    assert book.regime("NFL_PROP") == "CIRCUIT_BROKEN"

    cand = _firing_candidate("cb_fire", "g1", "g2")
    cand.legs[0].player_norm = "p1"
    cand.legs[0].sport = "nfl"
    cand.legs[1].player_norm = "p2"
    cand.legs[1].sport = "nfl"
    fire = _fire(cand)

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day([fire], "day1")

    assert len(decisions) == 1
    d = decisions[0]
    assert d.admitted is False
    assert "FAMILY_CIRCUIT_BROKEN" in d.refusals
    assert d.contracts == 0
    assert d.collateral == 0.0


def test_below_floor_family_deranked_and_tier_b_only() -> None:
    """8. ACCRUING family -> tau 0.75 in decisions, tier 'B' even with roi_cal >= 0.12."""
    cfg = Window2Config()
    book = FamilyTrustBook(cfg)
    book.add_fire("NFL_PROP", "day_1", 0.05)
    assert book.regime("NFL_PROP") == "ACCRUING"

    cand = _firing_candidate("accruing_fire", "g1", "g2")
    cand.legs[0].player_norm = "p1"
    cand.legs[0].sport = "nfl"
    cand.legs[1].player_norm = "p2"
    cand.legs[1].sport = "nfl"
    fire = _fire(cand)

    # Calibrator that produces high roi_cal >= 0.12
    rows = [
        {"roi_claimed": 0.20, "ask_no": 0.50, "pnl_per_contract": 0.15, "family": "NFL_PROP"},
        {"roi_claimed": 0.25, "ask_no": 0.50, "pnl_per_contract": 0.15, "family": "NFL_PROP"},
        {"roi_claimed": 0.30, "ask_no": 0.50, "pnl_per_contract": 0.20, "family": "NFL_PROP"},
        {"roi_claimed": 0.35, "ask_no": 0.50, "pnl_per_contract": 0.20, "family": "NFL_PROP"},
    ]
    cal = fit_calibrator(rows, cfg)
    engine = Window2Admission(cfg=cfg, calibrator=cal, trust_book=book)
    decisions = engine.run_day([fire], "day1")

    assert len(decisions) == 1
    d = decisions[0]
    assert d.tau_fam == pytest.approx(0.75)
    assert d.roi_cal >= 0.12
    assert d.tier == "B"


def test_composite_discount_second_identical_leg() -> None:
    """9. Two fires in one 60s batch sharing a leg;

    the second decision's n_shared_leg >= 1 and its discount factor == 1/(1+0.50*1+0.15*1)
    (within 1e-9) if it shares the game too.
    """
    cfg = Window2Config()
    book = _trusted_book()

    # Fire 1 and Fire 2 in same batch (now_ts=NOW)
    # Fire 1 has slightly higher claimed ROI (bid_yes=0.46 -> lower ask_no) so it admits first
    leg_shared = _leg(ticker="s1", game_id="g1", player_norm="p1", sport="nfl", stat="rec_yds")
    leg_diff_1 = _leg(ticker="d1", game_id="g2", player_norm="p2", sport="nfl", stat="rush_yds")
    cand1 = _candidate([leg_shared, leg_diff_1], combo_id="c1", bid_yes=0.46, now_ts=NOW)

    leg_diff_2 = _leg(ticker="d2", game_id="g3", player_norm="p3", sport="nfl", stat="pass_yds")
    cand2 = _candidate([leg_shared, leg_diff_2], combo_id="c2", bid_yes=0.45, now_ts=NOW)

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day([_fire(cand1), _fire(cand2)], "day1")

    assert len(decisions) == 2
    d1, d2 = decisions[0], decisions[1]
    assert d1.combo_id == "c1"
    assert d1.admitted is True
    assert d2.combo_id == "c2"
    assert d2.n_shared_leg >= 1
    assert d2.n_game >= 1
    expected_discount = 1.0 / (1.0 + 0.50 * 1 + 0.15 * 1)
    assert math.isclose(d2.discount, expected_discount, rel_tol=1e-9)


def test_game_leg_count_cap() -> None:
    """10. Fires whose legs would push one game past 15 admitted combo-legs -> refusal names GAME_LEG_COUNT."""
    cfg = Window2Config()
    book = _trusted_book()

    # Create 9 combos, each with 2 legs in game "g1" (total 18 legs)
    fires: List[Window2Fire] = []
    for i in range(9):
        legs = [
            _leg(ticker=f"g1_l{i}_a", game_id="g1", player_norm=f"p_{i}_a", sport="nfl", stat="rec_yds"),
            _leg(ticker=f"g1_l{i}_b", game_id="g1", player_norm=f"p_{i}_b", sport="nfl", stat="rush_yds"),
        ]
        cand = _candidate(legs, combo_id=f"leg_cap_c{i}")
        fires.append(_fire(cand))

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day(fires, "day1")

    # 7 combos admitted = 14 legs. 8th combo would push to 16 legs > 15 -> refused GAME_LEG_COUNT
    admitted = [d for d in decisions if d.admitted]
    assert len(admitted) == 7
    refused = [d for d in decisions if not d.admitted]
    assert len(refused) >= 1
    assert any("GAME_LEG_COUNT" in d.refusals for d in refused)


def test_daily_pool_cap() -> None:
    """11. Enough fires across games -> cumulative admitted collateral <= 600 and a refusal names DAILY_POOL."""
    cfg = Window2Config()
    book = _trusted_book()

    # Create 70 Tier A fires across 35 distinct games (2 fires per game) so game caps don't bind
    fires: List[Window2Fire] = []
    for i in range(70):
        game_id = f"slate_game_{i // 2}"
        legs = [
            _leg(ticker=f"dp_{i}_a", game_id=game_id, player_norm=f"ply_{i}_a", sport="nfl", stat="rec_yds"),
            _leg(ticker=f"dp_{i}_b", game_id=game_id, player_norm=f"ply_{i}_b", sport="nfl", stat="rush_yds"),
        ]
        cand = _candidate(legs, combo_id=f"dp_cand_{i}")
        fires.append(_fire(cand))

    engine = Window2Admission(cfg=cfg, trust_book=book)
    decisions = engine.run_day(fires, "day1")

    total_admitted_collateral = sum(d.collateral for d in decisions if d.admitted)
    assert total_admitted_collateral <= 600.0 + 1e-9
    assert any("DAILY_POOL" in d.refusals for d in decisions)


def test_window1_shadow_logged_and_legacy_defaults_untouched() -> None:
    """12. Decisions carry window1_admitted as bool; ComboLiabilityGuard() defaults still 75/150/15 after a run_day."""
    cfg = Window2Config()
    cand = _firing_candidate("c_w1_check", "g1", "g2")
    fire = _fire(cand)

    engine = Window2Admission(cfg=cfg)
    decisions = engine.run_day([fire], "day1")

    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d.window1_admitted, bool)

    # ComboLiabilityGuard defaults remain 75 / 150 / 15
    fresh_guard = ComboLiabilityGuard()
    assert fresh_guard.max_exposure_per_player_dollars == pytest.approx(75.00)
    assert fresh_guard.max_exposure_per_game_dollars == pytest.approx(150.00)
    assert fresh_guard.max_per_combo_liability_dollars == pytest.approx(15.00)


def test_roi_denominator_includes_fee() -> None:
    """13. Construct a firing candidate with ask_no=0.50 (bid_yes=0.50); fee=0.0175;

    assert signal.roi_no_taker == net_cons_edge/(0.50+0.0175) recomputed from
    signal.p_cons_yes, within 1e-9; and assert it differs from net_cons_edge/0.50.
    """
    cand = _firing_candidate("fee_cand", "g1", "g2", bid_yes=0.50)
    sig = evaluate_combo(cand)

    assert sig.ask_no == pytest.approx(0.50)
    expected_fee = 0.07 * 0.50 * (1.0 - 0.50)  # 0.0175
    assert expected_fee == pytest.approx(0.0175)

    net_cons_edge = (1.0 - sig.p_cons_yes) - 0.50 - expected_fee
    correct_roi = net_cons_edge / (0.50 + expected_fee)
    legacy_roi = net_cons_edge / 0.50

    assert math.isclose(sig.roi_no_taker, correct_roi, rel_tol=1e-9)
    assert not math.isclose(sig.roi_no_taker, legacy_roi, rel_tol=1e-5)
