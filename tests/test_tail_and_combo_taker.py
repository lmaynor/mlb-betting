"""tests/test_tail_and_combo_taker.py -- Verification for Module 4 & Module 5 (RES-12b).

Covers Argon spec Module 10, test 3:
  1. Luka Doncic 50+ pts at +680 (implied YES 0.1282 / NO ask 0.8718) against a
     4.0% Power-devigged fair triggers TAIL_NO_LAY, with Kalshi fee drag
     0.07 * 0.8718 * 0.1282 and ROI ~ +9.2%; the Novig variant carries zero fee
     drag and ROI ~ +10.1%.
  2. TAIL_YES_SMASH fires only for OVER_ELIGIBLE routing; a ROUTE_TO_NO_TAKER
     row never produces it.
  3. Combo scanner Blade 1: a 4-leg combo with 3 fair legs + 1 rotten leg is
     flagged has_rotten_leg, both via the leg's routing and via single-leg
     overpricing >= 0.050.
  4. Blade 2: a cannibalistic game-total Under + QB pass_yds Over pair resolves
     a negative signed rho (-0.34 from the correlation table) and
     P_copula < 0.85 * P_indep.
  5. Blade 3 fire gates: ROI_no_taker >= 0.030 and per-contract net edge
     >= 0.025 are required; a hot scratch or a start < 15 minutes out blocks
     firing.
  6. Blade 4 liability guard: caps $75/player, $150/game, $15/combo -- five
     $15 takes against one player are admitted, the sixth is refused.
"""

from __future__ import annotations

import math

import pytest

from ev.capability_mask import CapabilityDecision
from ev.combo_taker_scanner import (
    ComboCandidate,
    ComboLeg,
    ComboLiabilityGuard,
    evaluate_combo,
    scan_combos,
)
from ev.kalshi_rfq_pricer import LegFairValue, PricerConfig, pair_rho_true
from ev.tail_taker_layer import TailQuote, scan_two_sided_tails, tail_fee_drag

NOW = 1_800_000_000.0


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

def _decision(routing: str, p_over: float) -> CapabilityDecision:
    return CapabilityDecision(
        p_over_masked=p_over,
        p_under_masked=1.0 - p_over,
        routing=routing,
        reason_code="TEST",
    )


def _luka_quote(venue: str) -> TailQuote:
    """Luka Doncic 50+ pts at +680: implied YES 0.1282, NO ask 0.8718, fair 4.0%."""
    return TailQuote(
        sport="nba",
        stat="points",
        line=49.5,
        venue=venue,
        book=venue,
        p_model_over=0.1282,
        p_blend_over=None,
        decision=_decision("ROUTE_TO_NO_TAKER", 0.04),
        # Three two-sided reference books on the exact rung; their Power-devigged
        # (method="log") fairs sit above 4.0%, so the conservative NO-lay anchor
        # min(consensus, masked model) is the 4.0% masked fair.
        ref_quotes=[
            ("fanduel", 0.070, 0.950),
            ("hardrock", 0.075, 0.945),
            ("bet365", 0.065, 0.955),
        ],
        ask_no=0.8718,
    )


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
        game_start_ts=NOW + 3600.0,
        team=None,
    )
    base.update(kw)
    return ComboLeg(**base)


def _candidate(legs, **kw) -> ComboCandidate:
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


def _firing_candidate(combo_id: str, game_a: str, game_b: str, **kw) -> ComboCandidate:
    """Two fair 50% legs in different games at bid_yes 0.45: clears Blade 3."""
    legs = [
        _leg(ticker=f"{combo_id}-a", game_id=game_a),
        _leg(ticker=f"{combo_id}-b", game_id=game_b),
    ]
    return _candidate(legs, combo_id=combo_id, **kw)


# --------------------------------------------------------------------------
# Module 4 -- Tail Taker
# --------------------------------------------------------------------------

def test_luka_tail_no_lay_kalshi_fee_and_roi() -> None:
    signals = scan_two_sided_tails([_luka_quote("kalshi")])
    assert len(signals) == 1
    sig = signals[0]
    assert sig.mode == "TAIL_NO_LAY"
    assert sig.p_tail_fair_over == pytest.approx(0.04)
    expected_fee = 0.07 * 0.8718 * 0.1282
    assert sig.fee_drag == pytest.approx(expected_fee)
    assert sig.fee_drag == pytest.approx(tail_fee_drag("kalshi", 0.8718))
    assert 0.085 <= sig.roi_or_net_ev <= 0.105
    assert sig.roi_or_net_ev == pytest.approx(0.0922, abs=0.005)


def test_luka_tail_no_lay_novig_zero_fee() -> None:
    signals = scan_two_sided_tails([_luka_quote("novig")])
    assert len(signals) == 1
    sig = signals[0]
    assert sig.mode == "TAIL_NO_LAY"
    assert sig.fee_drag == 0.0
    assert 0.085 <= sig.roi_or_net_ev <= 0.115
    assert sig.roi_or_net_ev == pytest.approx(0.1012, abs=0.005)


def test_tail_yes_smash_fires_for_over_eligible() -> None:
    quote = TailQuote(
        sport="nba",
        stat="points",
        line=49.5,
        venue="novig",
        book="novig",
        p_model_over=0.09,
        p_blend_over=0.08,
        decision=_decision("OVER_ELIGIBLE", 0.08),
        ref_quotes=[
            ("fanduel", 0.095, 0.925),
            ("hardrock", 0.098, 0.922),
            ("bet365", 0.092, 0.928),
        ],
        d_over=21.0,  # +2000
        q_yes=1.0 / 21.0,
    )
    signals = scan_two_sided_tails([quote])
    assert [s.mode for s in signals] == ["TAIL_YES_SMASH"]


def test_tail_yes_smash_never_fires_for_no_taker_routing() -> None:
    quote = TailQuote(
        sport="nba",
        stat="points",
        line=49.5,
        venue="novig",
        book="novig",
        p_model_over=0.09,
        p_blend_over=0.08,
        decision=_decision("ROUTE_TO_NO_TAKER", 0.04),
        ref_quotes=[
            ("fanduel", 0.095, 0.925),
            ("hardrock", 0.098, 0.922),
            ("bet365", 0.092, 0.928),
        ],
        d_over=21.0,
        q_yes=1.0 / 21.0,
    )
    signals = scan_two_sided_tails([quote])
    assert all(s.mode != "TAIL_YES_SMASH" for s in signals)
    assert signals == []


# --------------------------------------------------------------------------
# Module 5 -- Combo scanner, Blade 1 (rotten leg)
# --------------------------------------------------------------------------

def _four_leg_combo(rotten_leg: ComboLeg) -> ComboCandidate:
    legs = [
        _leg(ticker="L1", game_id="g1"),
        _leg(ticker="L2", game_id="g2"),
        _leg(ticker="L3", game_id="g3"),
        rotten_leg,
    ]
    return _candidate(legs)


def test_blade1_rotten_leg_via_routing() -> None:
    rotten = _leg(ticker="L4", game_id="g4", routing="ROUTE_TO_NO_TAKER")
    sig = evaluate_combo(_four_leg_combo(rotten))
    assert sig.has_rotten_leg is True


def test_blade1_rotten_leg_via_overpricing() -> None:
    # q_implied - p_lambda = 0.56 - 0.50 = 0.06 >= 0.050
    rotten = _leg(ticker="L4", game_id="g4", q_implied=0.56)
    sig = evaluate_combo(_four_leg_combo(rotten))
    assert sig.has_rotten_leg is True


def test_blade1_no_rotten_leg_when_all_fair() -> None:
    fair = _leg(ticker="L4", game_id="g4")
    sig = evaluate_combo(_four_leg_combo(fair))
    assert sig.has_rotten_leg is False


# --------------------------------------------------------------------------
# Module 5 -- Blade 2 (cannibalistic / negative correlation)
# --------------------------------------------------------------------------

def _stub(leg: ComboLeg) -> LegFairValue:
    return LegFairValue(
        ticker=leg.ticker,
        sport=leg.sport,
        game_key=leg.game_id,
        market_class="player_prop" if leg.player_norm else "game_line",
        player_norm=leg.player_norm,
        stat=leg.stat,
        strike=leg.line,
        p_cons_yes=leg.p_lambda,
        n_books=3,
        disp=0.01,
        p_model_yes=leg.p_lambda,
        pooled=False,
        updated_ts=0.0,
        team=leg.team,
    )


def test_blade2_negative_rho_and_cannibalistic() -> None:
    total_leg = _leg(
        ticker="TOT", game_id="nfl:g1", stat="total", line=41.5,
        direction=-1, player_norm=None,
    )
    qb_leg = _leg(
        ticker="QB", game_id="nfl:g1", stat="pass_yds", line=275.5,
        direction=1, player_norm="qb_one",
    )
    rho = pair_rho_true(
        _stub(total_leg), total_leg.direction,
        _stub(qb_leg), qb_leg.direction,
        PricerConfig(),
    )
    assert rho == pytest.approx(-0.34)

    sig = evaluate_combo(_candidate([total_leg, qb_leg]))
    p_indep = total_leg.p_lambda * qb_leg.p_lambda
    assert sig.is_cannibalistic is True
    assert sig.p_true_yes < 0.85 * p_indep


# --------------------------------------------------------------------------
# Module 5 -- Blade 3 (fire gates)
# --------------------------------------------------------------------------

def test_blade3_fires_when_gates_clear() -> None:
    sig = evaluate_combo(_firing_candidate("ok", "g1", "g2"))
    assert sig.fires is True
    assert sig.roi_no_taker >= 0.030


def test_blade3_roi_and_edge_gates_block_bad_price() -> None:
    # bid_yes 0.24 -> ask_no 0.76 against a ~0.258 conservative joint fair:
    # both the ROI gate and the per-contract edge gate fail.
    cand = _firing_candidate("bad", "g1", "g2", bid_yes=0.24)
    sig = evaluate_combo(cand)
    assert sig.fires is False
    assert sig.roi_no_taker < 0.030
    assert "ROI_BELOW_0.030" in sig.reasons
    assert "EDGE_BELOW_0.025" in sig.reasons


def test_blade3_hot_scratch_blocks_firing() -> None:
    cand = _firing_candidate("hot", "g1", "g2")
    cand.legs[0].is_hot_scratch = True
    sig = evaluate_combo(cand)
    assert sig.fires is False
    assert "HOT_SCRATCH" in sig.reasons


def test_blade3_start_too_close_blocks_firing() -> None:
    cand = _firing_candidate("soon", "g1", "g2")
    cand.legs[1].game_start_ts = NOW + 600.0  # 10 minutes out (< 15)
    sig = evaluate_combo(cand)
    assert sig.fires is False
    assert "GAME_TOO_CLOSE" in sig.reasons


# --------------------------------------------------------------------------
# Module 5 -- Blade 4 (liability guard)
# --------------------------------------------------------------------------

def test_blade4_caps_and_player_limit() -> None:
    guard = ComboLiabilityGuard()
    assert guard.max_exposure_per_player_dollars == 75.00
    assert guard.max_exposure_per_game_dollars == 150.00
    assert guard.max_per_combo_liability_dollars == 15.00

    candidates = []
    for i in range(6):
        legs = [
            _leg(ticker=f"s{i}-a", game_id=f"sg{i}a", player_norm="star_player"),
            _leg(ticker=f"s{i}-b", game_id=f"sg{i}b", player_norm=None, stat="total"),
        ]
        candidates.append(
            _candidate(legs, combo_id=f"s{i}", stake=15.0)
        )

    admitted = scan_combos(candidates, guard=guard)
    assert len(admitted) == 5
    assert guard.player_exposure["star_player"] == pytest.approx(75.0)
    assert len(guard.admitted_combos) == 5


def test_blade4_per_combo_liability_capped_at_15() -> None:
    guard = ComboLiabilityGuard()
    cand = _candidate([_leg()], stake=40.0)
    assert guard.get_liability(cand) == pytest.approx(15.0)
    assert math.isclose(guard.max_per_combo_liability_dollars, 15.0)
