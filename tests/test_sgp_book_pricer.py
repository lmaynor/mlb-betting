"""
Unit tests for ev.sgp_book_pricer.
"""

from __future__ import annotations

import math
import tempfile
import numpy as np
import pytest

from ev.sgp_book_pricer import (
    SGP_ARCHETYPES,
    build_sgp_deeplink,
    copula_joint,
    fit_book_sgp_params,
    predict_book_sgp_odds,
)


def test_copula_bivariate_properties():
    p1, p2 = 0.25, 0.30

    # Independence (rho = 0)
    p_indep = copula_joint([p1, p2], rho_or_corr=0.0)
    assert math.isclose(p_indep, p1 * p2, rel_tol=1e-7, abs_tol=1e-7)

    # Positive correlation (rho > 0)
    rho = 0.22
    p_pos = copula_joint([p1, p2], rho_or_corr=rho)
    assert p_pos > (p1 * p2)
    assert p_pos <= min(p1, p2)

    # Negative correlation (rho < 0)
    rho_neg = -0.15
    p_neg = copula_joint([p1, p2], rho_or_corr=rho_neg)
    assert p_neg < (p1 * p2)
    assert p_neg >= max(0.0, p1 + p2 - 1.0)


def test_copula_trivariate_properties():
    p1, p2, p3 = 0.20, 0.30, 0.40
    p_prod = p1 * p2 * p3

    # Independence (rho = 0)
    p_indep = copula_joint([p1, p2, p3], rho_or_corr=0.0)
    assert math.isclose(p_indep, p_prod, rel_tol=1e-7, abs_tol=1e-7)

    # Positive correlation (scalar rho > 0)
    rho = 0.18
    p_pos = copula_joint([p1, p2, p3], rho_or_corr=rho)
    assert p_pos > p_prod
    assert p_pos <= min(p1, p2, p3)

    # Correlation matrix input
    R = np.array([
        [1.0, 0.20, 0.15],
        [0.20, 1.0, 0.18],
        [0.15, 0.18, 1.0],
    ])
    p_mat = copula_joint([p1, p2, p3], rho_or_corr=R)
    assert p_mat > p_prod
    assert p_mat <= min(p1, p2, p3)


def test_fit_book_sgp_params_recovery():
    """
    Generate synthetic FanDuel 10+Reb / 10+Ast quotes with known
    (rho_book = 0.10, hold_book = 0.14) and verify curve_fit recovers
    (hat_rho, hat_hold) within 0.01 and predicts decimal odds within 1%.
    """
    true_rho = 0.10
    true_hold = 0.14
    book = "fanduel"
    arch = "nba_reb_ast_10_10"

    # 15 distinct leg odds pairs (American format)
    raw_odds_pairs = [
        (+260, +300), (+280, +320), (+300, +350), (+320, +400), (+350, +420),
        (+240, +280), (+270, +310), (+290, +330), (+310, +360), (+330, +390),
        (+250, +290), (+340, +410), (+360, +430), (+380, +450), (+400, +480),
    ]

    quotes = []
    for l1, l2 in raw_odds_pairs:
        # compute exact theoretical quote under (true_rho, true_hold)
        q1 = 100.0 / (l1 + 100.0)
        q2 = 100.0 / (l2 + 100.0)
        cj = copula_joint([q1, q2], true_rho)
        p_sgp = min(0.99, (1.0 + true_hold) * cj)
        dec_sgp = 1.0 / p_sgp
        # Convert to American
        am_sgp = round((dec_sgp - 1.0) * 100.0)
        quotes.append({
            "book": book,
            "archetype": arch,
            "leg1_odds": l1,
            "leg2_odds": l2,
            "sgp_odds": am_sgp,
            "sgp_dec": dec_sgp,
        })

    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        fitted = fit_book_sgp_params(quotes, out_path=tmp.name)
        key = f"{book}|{arch}"
        assert key in fitted

        res = fitted[key]
        hat_rho = res["rho_book"]
        hat_hold = res["hold_book"]

        # Recovered parameters within 0.01
        assert abs(hat_rho - true_rho) < 0.01
        assert abs(hat_hold - true_hold) < 0.01

        # Test prediction accuracy on an unobserved pair
        test_l1, test_l2 = +320, +410
        pred = predict_book_sgp_odds(book, arch, [test_l1, test_l2], custom_params=fitted)

        q1_test = 100.0 / (test_l1 + 100.0)
        q2_test = 100.0 / (test_l2 + 100.0)
        true_p = (1.0 + true_hold) * copula_joint([q1_test, q2_test], true_rho)
        true_dec = 1.0 / true_p

        # Prediction within 1% of true decimal odds
        pct_err = abs(pred.decimal - true_dec) / true_dec
        assert pct_err < 0.01


def test_build_sgp_deeplink():
    # DraftKings multi-outcome deep link
    dk_legs = [
        {"sid": "12345", "stat": "rebounds"},
        {"sid": "67890", "stat": "assists"},
    ]
    dk_url = build_sgp_deeplink("draftkings", "event_999", dk_legs)
    assert dk_url == "https://sportsbook.draftkings.com/event/event_999?outcomes=12345,67890"

    # DraftKings fallback without sids
    dk_fallback = build_sgp_deeplink("draftkings", "event_999", [])
    assert dk_fallback == "https://sportsbook.draftkings.com/event/event_999"

    # FanDuel multi-outcome deep link
    fd_legs = [
        {"marketId": "1.1001", "selectionId": "2001"},
        {"marketId": "1.1002", "selectionId": "2002"},
    ]
    fd_url = build_sgp_deeplink("fanduel", "event_888", fd_legs)
    assert fd_url == "https://sportsbook.fanduel.com/addToBetslip?marketId[0]=1.1001&selectionId[0]=2001&marketId[1]=1.1002&selectionId[1]=2002"

    # FanDuel fallback without selectionIds
    fd_fallback = build_sgp_deeplink("fanduel", "event_888", [])
    assert fd_fallback == "https://sportsbook.fanduel.com/event/event_888"


def test_predict_book_sgp_odds_structure():
    pred = predict_book_sgp_odds("draftkings", "nba_pts_reb_vs_pr", [-110, -115])
    assert pred.book == "draftkings"
    assert pred.archetype == "nba_pts_reb_vs_pr"
    assert pred.decimal > 1.0
    assert pred.american != 0
    assert 0.0 < pred.implied_prob < 1.0


def test_build_sgp_proxy_candidates():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    # Simulated board rows for an NBA player
    nba_rows = [
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "rebounds",
            "line": 9.5,
            "curve_fair_over": 0.25,
            "books": [
                {"book": "fanduel", "over": 320, "under": -450},
                {"book": "draftkings", "over": 310, "under": -430},
            ],
        },
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "assists",
            "line": 9.5,
            "curve_fair_over": 0.22,
            "books": [
                {"book": "fanduel", "over": 410, "under": -600},
                {"book": "draftkings", "over": 400, "under": -580},
            ],
        },
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "triple_double",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": 1300, "under": None},
                {"book": "draftkings", "over": 1250, "under": None},
            ],
        },
    ]

    cands = build_sgp_proxy_candidates(nba_rows, min_ev_pct=-50.0)
    assert len(cands) >= 1
    top = cands[0]
    assert top.player == "Josh Hart"
    assert top.archetype == "nba_reb_ast_10_10"
    assert top.est_sgp_american > 1000
    assert top.direct_best_american == 1300
    assert top.vs_direct_pct is not None
    assert top.vs_direct_pct > 0.0  # Synthetic SGP beats direct market +1300
