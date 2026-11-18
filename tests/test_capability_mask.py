"""tests/test_capability_mask.py -- Verification for Module 1 & Module 3.

Covers:
  1. Andre Drummond (q95 = 18.0 pts) on 49.5 (50+ pts) alt rung clamped to
     p_over_masked <= 0.005 and routed to ROUTE_TO_NO_TAKER (CAPABILITY_CEILING_ZERO).
  2. NBA high-3PAr wing (fg3a_per_fga = 0.60, vacated_usage_share = 0.06, spread = -3.0)
     on 30+ pts routes to OVER_ELIGIBLE, while in a spread = -15.5 blowout routes
     to ROUTE_TO_NO_TAKER.
  3. NFL secondary high-aDOT WR (target_share = 0.16, adot = 11.2, gap = +9.5)
     routes to OVER_ELIGIBLE on 80+ rec_yds, while low-aDOT TE (adot = 5.1)
     routes to ROUTE_TO_NO_TAKER.
  4. Winner's-curse damper blocks pop_delta > 0.120, requires >= 2 peer books (or 1 sharp)
     at least 1.5pp closer to p_model, and applies concave 50% damping between 8.0pp and 12.0pp.
  5. Module 3 Power-devig routing helper ladder_devig_fair in ev/props.py.
"""

from __future__ import annotations

import pytest

from ev.capability_mask import (
    CapabilityDecision,
    apply_capability_mask,
    apply_winners_curse_damper,
    check_empirical_ceiling,
    evaluate_archetype_gate,
)
from ev.props import LADDER_DEVIG_BOOKS, ladder_devig_fair
from mlb_core.odds.utils import devig_two_way


def test_drummond_capability_ceiling_clamp() -> None:
    """Verify Andre Drummond (q95 = 18.0 pts) on 49.5 line is clamped to p_over <= 0.005."""
    # 50 games of history with points between 6 and 22 (q95 ≈ 18.0, max_obs ≈ 22.0)
    drummond_hist = [10.0, 12.0, 14.0, 18.0, 8.0, 16.0, 20.0, 22.0, 11.0, 15.0] * 5
    drummond_mins = [28.0, 30.0, 26.0, 29.0, 24.0, 28.0, 31.0, 27.0, 25.0, 29.0] * 5

    line = 49.5  # 50+ points alt rung
    minutes_proj = 28.0
    p_model_raw = 0.025  # Raw model incorrectly predicting 2.5%

    # Direct check_empirical_ceiling
    dec = check_empirical_ceiling(
        line=line,
        history_values=drummond_hist,
        minutes_proj=minutes_proj,
        minutes_hist=drummond_mins,
        p_over_raw=p_model_raw,
    )
    assert dec.routing == "ROUTE_TO_NO_TAKER"
    assert dec.reason_code == "CAPABILITY_CEILING_ZERO"
    assert dec.p_over_masked <= 0.005
    assert dec.p_under_masked == pytest.approx(1.0 - dec.p_over_masked)

    # Full apply_capability_mask pipeline
    full_dec = apply_capability_mask(
        p_model=p_model_raw,
        q_book=0.015,
        sport="nba",
        stat="points",
        line=line,
        main_line=12.5,
        history_values=drummond_hist,
        minutes_proj=minutes_proj,
        minutes_hist=drummond_mins,
    )
    assert full_dec.routing == "ROUTE_TO_NO_TAKER"
    assert full_dec.reason_code == "CAPABILITY_CEILING_ZERO"
    assert full_dec.p_over_masked <= 0.005


def test_nba_high_3par_wing_vs_blowout() -> None:
    """Verify high-3PAr wing is OVER_ELIGIBLE at spread -3.0 vs ROUTE_TO_NO_TAKER at spread -15.5."""
    line = 29.5  # 30+ pts alt ladder
    main_line = 21.5

    # 1. Competitive game (spread = -3.0) -> OVER_ELIGIBLE
    features_tight = {
        "fg3a_per_fga": 0.60,
        "vacated_usage_share": 0.06,
        "spread": -3.0,
    }
    res_tight = evaluate_archetype_gate("nba", "points", line, main_line, features_tight)
    assert res_tight == "OVER_ELIGIBLE"
    assert res_tight.routing == "OVER_ELIGIBLE"

    # Full pipeline with tight game
    dec_tight = apply_capability_mask(
        p_model=0.42,
        q_book=0.38,
        sport="nba",
        stat="points",
        line=line,
        main_line=main_line,
        features=features_tight,
    )
    assert dec_tight.routing == "OVER_ELIGIBLE"

    # 2. Blowout script (spread = -15.5) -> ROUTE_TO_NO_TAKER
    features_blowout = {
        "fg3a_per_fga": 0.60,
        "vacated_usage_share": 0.06,
        "spread": -15.5,
    }
    res_blowout = evaluate_archetype_gate("nba", "points", line, main_line, features_blowout)
    assert res_blowout == "ROUTE_TO_NO_TAKER"
    assert res_blowout.routing == "ROUTE_TO_NO_TAKER"
    assert res_blowout.reason_code == "NBA_BLOWOUT_SCRIPT"

    dec_blowout = apply_capability_mask(
        p_model=0.42,
        q_book=0.38,
        sport="nba",
        stat="points",
        line=line,
        main_line=main_line,
        features=features_blowout,
    )
    assert dec_blowout.routing == "ROUTE_TO_NO_TAKER"
    assert dec_blowout.reason_code == "NBA_BLOWOUT_SCRIPT"


def test_nfl_high_adot_wr_vs_low_adot_te() -> None:
    """Verify secondary high-aDOT WR is OVER_ELIGIBLE on 80+ rec yds vs low-aDOT TE ROUTE_TO_NO_TAKER."""
    line = 79.5  # 80+ receiving yards
    main_line = 52.5

    # 1. High-aDOT secondary WR
    features_wr = {
        "target_share": 0.16,
        "adot": 11.2,
        "gap": 9.5,
    }
    res_wr = evaluate_archetype_gate("nfl", "rec_yds", line, main_line, features_wr)
    assert res_wr == "OVER_ELIGIBLE"
    assert res_wr.routing == "OVER_ELIGIBLE"

    dec_wr = apply_capability_mask(
        p_model=0.30,
        q_book=0.25,
        sport="nfl",
        stat="rec_yds",
        line=line,
        main_line=main_line,
        features=features_wr,
    )
    assert dec_wr.routing == "OVER_ELIGIBLE"

    # 2. Low-aDOT TE
    features_te = {
        "target_share": 0.18,
        "adot": 5.1,
        "gap": 2.0,
    }
    res_te = evaluate_archetype_gate("nfl", "rec_yds", line, main_line, features_te)
    assert res_te == "ROUTE_TO_NO_TAKER"
    assert res_te.routing == "ROUTE_TO_NO_TAKER"
    assert res_te.reason_code == "NFL_LOW_ADOT_HIGH_RUNG"

    dec_te = apply_capability_mask(
        p_model=0.30,
        q_book=0.25,
        sport="nfl",
        stat="rec_yds",
        line=line,
        main_line=main_line,
        features=features_te,
    )
    assert dec_te.routing == "ROUTE_TO_NO_TAKER"
    assert dec_te.reason_code == "NFL_LOW_ADOT_HIGH_RUNG"


def test_winners_curse_damper_and_peer_gate() -> None:
    """Verify winner's-curse damper rules (Hard Veto 1, Hard Veto 2, Concave Damper)."""
    # 1. Hard Veto 1: pop_delta > 0.120
    q_book = 0.40
    p_model_wild = 0.53  # pop_delta = 0.130 > 0.120
    damper_veto1 = apply_winners_curse_damper(
        p_model=p_model_wild,
        q_book=q_book,
        p_peer_med=0.48,
        n_peer_books=3,
    )
    assert not damper_veto1.passed
    assert damper_veto1.p_effective == q_book
    assert damper_veto1.reason_code == "DAMPER_VETO_UPPER_DIVERGENCE"

    # 2. Hard Veto 2: Peer corroboration fails when peer median < target book + 1.5pp
    p_model = 0.48  # pop_delta = 0.080
    p_peer_stale = 0.41  # peer median is only +1.0pp above book (needs >= +1.5pp)
    damper_veto2a = apply_winners_curse_damper(
        p_model=p_model,
        q_book=q_book,
        p_peer_med=p_peer_stale,
        n_peer_books=3,
    )
    assert not damper_veto2a.passed
    assert damper_veto2a.reason_code == "DAMPER_VETO_PEER_CORROBORATION"

    # 3. Hard Veto 2: Peer corroboration fails when n_peer_books < 2 and no sharp
    p_peer_good = 0.44  # +4.0pp above book, within 4.0pp of model
    damper_veto2b = apply_winners_curse_damper(
        p_model=p_model,
        q_book=q_book,
        p_peer_med=p_peer_good,
        n_peer_books=1,
        sharp_books=["draftkings"],  # soft book, not in sharp exchanges
    )
    assert not damper_veto2b.passed
    assert damper_veto2b.reason_code == "DAMPER_VETO_PEER_CORROBORATION"

    # Sharp exchange override: 1 book is enough if it's Pinnacle or Novig
    damper_sharp = apply_winners_curse_damper(
        p_model=p_model,
        q_book=q_book,
        p_peer_med=p_peer_good,
        n_peer_books=1,
        sharp_books=["pinnacle"],
    )
    assert damper_sharp.passed
    assert damper_sharp.reason_code == "DAMPER_PASSED"

    # 4. Concave damper: for 0.080 < pop_delta <= 0.120
    # pop_delta = 0.100 (p_model = 0.500, q_book = 0.400)
    # p_effective = q_book + 0.080 + 0.50 * (0.100 - 0.080) = 0.400 + 0.080 + 0.010 = 0.490
    damper_concave = apply_winners_curse_damper(
        p_model=0.500,
        q_book=0.400,
        p_peer_med=0.450,
        n_peer_books=3,
    )
    assert damper_concave.passed
    assert damper_concave.p_effective == pytest.approx(0.490, abs=1e-5)


def test_ladder_devig_fair_routing() -> None:
    """Verify Module 3 ladder_devig_fair Power-devig routing."""
    # Skewed ladder line: raw book implied probabilities
    # e.g. Over +250 (q=0.2857), Under -350 (q=0.7778)
    q_over = 0.2857
    q_under = 0.7778
    prop_over, prop_under = devig_two_way(q_over, q_under, method="proportional")
    assert prop_over <= 0.35  # Skewed ladder condition met

    # 1. Eligible book (fanduel) and eligible stat (pts) -> Power devig (method="log")
    fair_over, fair_under = ladder_devig_fair(q_over, q_under, book="fanduel", stat="pts")
    log_over, log_under = devig_two_way(q_over, q_under, method="log")
    assert fair_over == pytest.approx(log_over, abs=1e-6)
    assert fair_under == pytest.approx(log_under, abs=1e-6)
    assert fair_over != pytest.approx(prop_over, abs=1e-4)  # Differs from proportional

    # 2. Ineligible book (bovada) -> stays proportional
    bov_over, bov_under = ladder_devig_fair(q_over, q_under, book="bovada", stat="pts")
    assert bov_over == pytest.approx(prop_over, abs=1e-6)

    # 3. Ineligible stat (home_runs) -> stays proportional
    hr_over, hr_under = ladder_devig_fair(q_over, q_under, book="fanduel", stat="home_runs")
    assert hr_over == pytest.approx(prop_over, abs=1e-6)

    # 4. Un-skewed line (e.g. 50/50 main line) -> stays proportional
    q_50 = 0.5238  # -110 / -110
    fair_main_over, _ = ladder_devig_fair(q_50, q_50, book="fanduel", stat="pts")
    assert fair_main_over == pytest.approx(0.50, abs=1e-4)
