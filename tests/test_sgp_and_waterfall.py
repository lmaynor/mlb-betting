"""tests/test_sgp_and_waterfall.py -- Module 10 Verification Suite for Batch 3 (RES-12c).

Covers:
  1. Novig 4-State Waterfall (TAKER, MAKER_WIDE_STINK_BID, WITNESS_SINK_A, ABSTAIN).
  2. SGP curve-fit recovery & proxy evaluation (Module 10 Test 4).
  3. Futures sleeve allocation, entry/exit gates, and n-way Power devig.
  4. Discovery cell classification and synthetic end-to-end screen pipeline.
  5. 28-day rolling live decay circuit breaker and logit-intercept centering.
  6. News sniper stale quote candidate detection.
"""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timezone, timedelta
import numpy as np
import pytest

from ev.futures_sleeve import (
    FuturesBoardRow,
    FuturesPosition,
    FuturesSnapshot,
    bank_futures_board,
    devig_n_way_power,
    evaluate_futures_entry,
    futures_ev_net,
    sleeve_allocation,
    take_profit_exit_price,
)
from ev.news_sniper import (
    AvailabilityTransition,
    QuoteSnapshot,
    StaleQuoteCandidate,
    find_stale_quote_candidates,
)
from ev.novig.map import (
    NovigLeg,
    STAT_BY_TYPE_BY_SPORT,
    map_prop_market_for_sport,
)
from ev.novig.waterfall import (
    WaterfallDecision,
    evaluate_waterfall,
    floor_to_005_tick,
)
from ev.sgp_book_pricer import (
    copula_joint,
    fit_book_sgp_params,
    predict_book_sgp_odds,
)
from ev.sgp_proxy_builder import (
    build_sgp_proxy_candidates,
)
from mlb_core.odds.utils import american_to_decimal
from mlb_core.risk.gates import (
    DecayBreakerDecision,
    apply_centering,
    logit_intercept_centering,
    rolling_decay_status,
)
from scripts.discovery_screen_res12 import (
    DiscoveryClassification,
    classify_cell,
    evaluate_cell_pipeline,
    generate_synthetic_data,
    run_discovery_screen,
)


# ==============================================================================
# 1. Novig 4-State Waterfall Tests
# ==============================================================================

def test_waterfall_state_1_taker_clears():
    """State 1 TAKER fires when P_fair_floor - ask >= 0.025 at $0 fee with L1 ask >= $25."""
    dec = evaluate_waterfall(
        p_cons=0.55,
        p_blend=0.54,
        p_model=0.54,
        p_novig_ask=0.510,  # edge = 0.540 - 0.510 = 0.030 >= 0.025
        p_novig_bid=0.450,
        n_books=5,
        disp=0.03,
        fee_charged="WHEN_LIVE",
        l1_ask_dollars=50.0,
        minutes_to_start=120.0,
    )
    assert dec.state == "TAKER"
    assert dec.reason_code == "TAKER_EDGE_CLEARED"
    assert dec.cancel_resting is True
    assert dec.p_bid is None
    assert dec.notional == 10.0  # capped by default per_bet_cap $10


def test_waterfall_state_2_maker_wide_stink_bid_q0():
    """State 2 MAKER_WIDE_STINK_BID fires at Q=0 when yes_spread >= 0.08."""
    # p_fair_floor = min(0.50, 0.50) = 0.50
    # yes_spread = 0.55 - 0.40 = 0.15 >= 0.08
    # delta_0 = 0.025 (count prop), disp = 0.02 -> delta = max(0.035, 0.025 + 0.01) = 0.035
    # p_raw_bid = 0.50 - 0.035 = 0.465 -> floored to 0.465
    # p_novig_bid + 0.010 = 0.400 + 0.010 = 0.410
    # p_bid = min(0.465, 0.410) = 0.410 >= p_novig_bid (0.400) -> Q=0 satisfied!
    dec = evaluate_waterfall(
        p_cons=0.50,
        p_blend=0.50,
        p_model=0.51,
        p_novig_ask=0.55,
        p_novig_bid=0.40,
        n_books=5,
        disp=0.02,
        stat_kind="count",
        fee_charged="WHEN_LIVE",
        l1_ask_dollars=10.0,  # does not clear Taker $25 min
        minutes_to_start=60.0,
    )
    assert dec.state == "MAKER_WIDE_STINK_BID"
    assert dec.reason_code == "MAKER_WIDE_STINK_BID_POSTED"
    assert dec.p_bid == 0.410
    assert dec.notional == 5.0
    assert dec.cancel_resting is False


def test_waterfall_state_2_maker_behind_book_bid_no_bid():
    """When computed P_bid < P_novig_bid, State 2 returns no-bid rationale (Q=0 rule)."""
    # p_fair_floor = 0.45
    # delta = 0.035 -> p_bid = 0.415
    # If p_novig_bid is 0.440, 0.415 < 0.440 -> violates Q=0!
    dec = evaluate_waterfall(
        p_cons=0.45,
        p_blend=0.45,
        p_model=0.46,
        p_novig_ask=0.55,
        p_novig_bid=0.44,
        n_books=5,
        disp=0.02,
        stat_kind="count",
        fee_charged="WHEN_LIVE",
        l1_ask_dollars=10.0,
        minutes_to_start=60.0,
    )
    assert dec.state == "MAKER_WIDE_STINK_BID"
    assert dec.reason_code == "MAKER_BEHIND_BOOK_BID"
    assert dec.p_bid is None
    assert dec.notional == 0.0


def test_waterfall_state_2_hard_cancel_at_t_minus_5m():
    """Resting quote must carry cancel_resting=True inside T-5m."""
    dec = evaluate_waterfall(
        p_cons=0.50,
        p_blend=0.50,
        p_model=0.51,
        p_novig_ask=0.55,
        p_novig_bid=0.40,
        n_books=5,
        disp=0.02,
        fee_charged="WHEN_LIVE",
        l1_ask_dollars=10.0,
        minutes_to_start=4.0,  # inside T-5m
    )
    assert dec.state == "MAKER_WIDE_STINK_BID"
    assert dec.reason_code == "MAKER_T_MINUS_5M_CANCEL"
    assert dec.cancel_resting is True
    assert dec.p_bid is None


def test_waterfall_state_3_witness_sink_a_on_tight_spread():
    """Spread < 0.08 routes to WITNESS_SINK_A sharp reference only."""
    dec = evaluate_waterfall(
        p_cons=0.50,
        p_blend=0.50,
        p_model=0.51,
        p_novig_ask=0.53,
        p_novig_bid=0.47,  # yes_spread = 0.06 < 0.08
        n_books=5,
        disp=0.02,
        fee_charged="WHEN_LIVE",
        l1_ask_dollars=100.0,
        minutes_to_start=60.0,
    )
    assert dec.state == "WITNESS_SINK_A"
    assert dec.reason_code == "WITNESS_SINK_A_TIGHT_SPREAD"
    assert dec.p_bid is None
    assert dec.cancel_resting is True


def test_waterfall_state_4_abstain_cases():
    """State 4 ABSTAIN evaluates first on insufficient books, dispersion, divergence, or blackout."""
    # Case A: n_books < 4
    dec_books = evaluate_waterfall(
        p_cons=0.50, p_blend=0.50, p_model=0.50, p_novig_ask=0.45, p_novig_bid=0.40,
        n_books=3, disp=0.02,
    )
    assert dec_books.state == "ABSTAIN"
    assert dec_books.reason_code == "ABSTAIN_N_BOOKS_LT_4"

    # Case B: disp > 0.06
    dec_disp = evaluate_waterfall(
        p_cons=0.50, p_blend=0.50, p_model=0.50, p_novig_ask=0.45, p_novig_bid=0.40,
        n_books=5, disp=0.075,
    )
    assert dec_disp.state == "ABSTAIN"
    assert dec_disp.reason_code == "ABSTAIN_DISP_GT_0_06"

    # Case C: |P_model - P_cons| > 0.15
    dec_div = evaluate_waterfall(
        p_cons=0.50, p_blend=0.50, p_model=0.68, p_novig_ask=0.45, p_novig_bid=0.40,
        n_books=5, disp=0.02,
    )
    assert dec_div.state == "ABSTAIN"
    assert dec_div.reason_code == "ABSTAIN_MODEL_CONS_DIVERGENCE"

    # Case D: Injury blackout
    dec_blackout = evaluate_waterfall(
        p_cons=0.50, p_blend=0.50, p_model=0.50, p_novig_ask=0.45, p_novig_bid=0.40,
        n_books=5, disp=0.02, is_injury_blackout=True,
    )
    assert dec_blackout.state == "ABSTAIN"
    assert dec_blackout.reason_code == "ABSTAIN_INJURY_BLACKOUT"


# ==============================================================================
# 2. SGP Curve-Fit Recovery & Proxy Upgrade Tests (Module 10 Test 4)
# ==============================================================================

def test_sgp_curve_fit_recovery():
    """Verify sgp_book_pricer recovers (rho_hat, h_hat) within 0.01 on synthetic FanDuel quotes."""
    true_rho, true_hold = 0.10, 0.14
    book, arch = "fanduel", "nba_reb_ast_10_10"

    raw_pairs = [
        (+260, +300), (+280, +320), (+300, +350), (+320, +400), (+350, +420),
        (+240, +280), (+270, +310), (+290, +330), (+310, +360), (+330, +390),
        (+250, +290), (+340, +410), (+360, +430), (+380, +450), (+400, +480),
    ]

    quotes = []
    for l1, l2 in raw_pairs:
        q1 = 100.0 / (l1 + 100.0)
        q2 = 100.0 / (l2 + 100.0)
        cj = copula_joint([q1, q2], true_rho)
        p_sgp = min(0.99, (1.0 + true_hold) * cj)
        dec_sgp = 1.0 / p_sgp
        quotes.append({
            "book": book,
            "archetype": arch,
            "leg1_odds": l1,
            "leg2_odds": l2,
            "sgp_odds": round((dec_sgp - 1.0) * 100.0),
            "sgp_dec": dec_sgp,
        })

    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        rejections = {}
        fitted = fit_book_sgp_params(quotes, out_path=tmp.name, rejections=rejections)
        key = f"{book}|{arch}"
        assert key in fitted, rejections
        res = fitted[key]
        assert abs(res["rho_book"] - true_rho) < 0.01
        assert abs(res["hold_book"] - true_hold) < 0.01


def test_sgp_reb_ast_proxy_flagged_over_triple_double():
    """Verify the 10+ Reb + 10+ Ast proxy (+1850) is flagged over standalone Triple-Double (+1350)."""
    # 10+ Reb + 10+ Ast (+1850) has decimal odds 19.50 vs Triple-Double (+1350) decimal odds 14.50.
    # The synthetic proxy offers +34.5% higher payout than the direct novelty market.
    proxy_odds_am = 1850
    proxy_dec = american_to_decimal(proxy_odds_am)
    direct_odds_am = 1350
    direct_dec = american_to_decimal(direct_odds_am)

    payout_premium_pct = ((proxy_dec - direct_dec) / direct_dec) * 100.0
    assert proxy_dec > direct_dec
    assert payout_premium_pct > 30.0  # +34.48% higher payout

    # Verify via build_sgp_proxy_candidates direct market comparison
    def bk(over, under):
        return [
            {"book": "fanduel", "over": over, "under": under},
            {"book": "draftkings", "over": over + 10, "under": under},
            {"book": "betmgm", "over": over - 5, "under": under},
            {"book": "caesars", "over": over, "under": under},
        ]

    rows = [
        {
            "game_key": "nba_20261118_dal_den",
            "commence": "2026-11-18T23:00:00Z",
            "away": "DAL",
            "home": "DEN",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "stat": "points",
            "line": 9.5,
            "books": bk(-300, 230),
        },
        {
            "game_key": "nba_20261118_dal_den",
            "commence": "2026-11-18T23:00:00Z",
            "away": "DAL",
            "home": "DEN",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "stat": "rebounds",
            "line": 9.5,
            "books": bk(+260, -320),
        },
        {
            "game_key": "nba_20261118_dal_den",
            "commence": "2026-11-18T23:00:00Z",
            "away": "DAL",
            "home": "DEN",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "stat": "assists",
            "line": 9.5,
            "books": bk(+300, -380),
        },
        {
            "game_key": "nba_20261118_dal_den",
            "commence": "2026-11-18T23:00:00Z",
            "away": "DAL",
            "home": "DEN",
            "player": "Nikola Jokic",
            "player_norm": "nikola jokic",
            "stat": "triple_double",
            "line": 0.5,
            "books": [{"book": "fanduel", "over": 1350, "under": None}, {"book": "draftkings", "over": 1300, "under": None}],
        },
    ]

    cands = build_sgp_proxy_candidates(rows, min_ev_pct=-100.0, sport="nba")
    td_proxies = [c for c in cands if c.direct_market_label == "Triple-Double"]
    assert len(td_proxies) >= 1
    top_proxy = td_proxies[0]
    assert top_proxy.direct_best_american == 1350
    assert top_proxy.est_sgp_american > 1350
    assert top_proxy.vs_direct_pct is not None and top_proxy.vs_direct_pct > 0.0


# ==============================================================================
# 3. Futures Sleeve Tests (Module 8)
# ==============================================================================

def test_futures_sleeve_allocation():
    """Sleeve allocation splits total bankroll into Sleeve A (80-85%) and B (15-20%)."""
    a_dol, b_dol = sleeve_allocation(10000.0, sleeve_a_share=0.825)
    assert a_dol == 8250.0
    assert b_dol == 1750.0
    assert a_dol + b_dol == 10000.0

    # Outside band raises ValueError
    with pytest.raises(ValueError):
        sleeve_allocation(1000.0, sleeve_a_share=0.79)
    with pytest.raises(ValueError):
        sleeve_allocation(1000.0, sleeve_a_share=0.86)


def test_futures_entry_gate():
    """Futures entry gate fires iff |P_sim - q_market| >= 0.080 and EV_net >= +12%."""
    # Case 1: Dislocation 0.09 >= 0.08, EV = 0.49/0.40 - 1 = 22.5% >= 12% -> True
    assert evaluate_futures_entry(p_sim=0.49, q_market=0.40) is True

    # Case 2: Dislocation 0.07 < 0.08, EV = 0.47/0.40 - 1 = 17.5% -> False
    assert evaluate_futures_entry(p_sim=0.47, q_market=0.40) is False

    # Case 3: Dislocation 0.085 >= 0.08, EV = 0.44/0.40 - 1 = 10% < 12% -> False
    assert evaluate_futures_entry(p_sim=0.44, q_market=0.40) is False


def test_futures_exit_price_math():
    """P_exit = q_entry + 0.75 * (P_sim - q_entry). E.g. q=0.40, P_sim=0.56 -> 0.52."""
    q_entry = 0.40
    p_sim = 0.56
    p_exit = take_profit_exit_price(q_entry, p_sim)
    assert p_exit == 0.52


def test_futures_board_banking_n_way_power():
    """bank_futures_board devigs multi-way market quotes via n-way Power devig."""
    snapshots = [
        {"venue": "kalshi", "market": "KXNBADIV", "outcome": "BOS", "q": 0.55},
        {"venue": "kalshi", "market": "KXNBADIV", "outcome": "NYK", "q": 0.35},
        {"venue": "kalshi", "market": "KXNBADIV", "outcome": "PHI", "q": 0.20},
    ]
    board = bank_futures_board(snapshots)
    assert len(board) == 3
    fair_sum = sum(row.p_fair_power for row in board)
    assert math.isclose(fair_sum, 1.0, abs_tol=1e-5)
    # Power devig shades favorite down less than longshot
    assert board[0].p_fair_power < 0.55


# ==============================================================================
# 4. Standing Discovery Screen Tests (Module 9)
# ==============================================================================

def test_classify_cell_promotions_and_clamp():
    """classify_cell routes passing cells to MODEL_CONFLUENCE_ACTIVE, and clamps failing cells."""
    # Passing cell
    passing_metrics = {
        "c_stat": 0.32,
        "t_stat": 2.6,
        "delta_ll": -0.0035,
        "ece": 0.012,
        "delta_roi_model": 0.028,
    }
    res_pass = classify_cell(passing_metrics, p4_roi=0.01)
    assert res_pass.status == "MODEL_CONFLUENCE_ACTIVE"
    assert res_pass.effective_c_stat == 0.32

    # Failing c_stat but strong consensus outlier (P4 ROI = 3.0% >= 2.5%)
    failing_metrics = {
        "c_stat": 0.12,
        "t_stat": 1.1,
        "delta_ll": 0.001,
        "ece": 0.022,
        "delta_roi_model": 0.005,
    }
    res_p4 = classify_cell(failing_metrics, p4_roi=0.030)
    assert res_p4.status == "CONSENSUS_OUTLIER_ONLY"
    assert res_p4.effective_c_stat == 0.0

    # Failing everything
    res_inact = classify_cell(failing_metrics, p4_roi=0.015)
    assert res_inact.status == "INACTIVE"
    assert res_inact.effective_c_stat == 0.0


def test_discovery_screen_synthetic_run_end_to_end():
    """Verify discovery_screen --synthetic mode runs end-to-end on synthetic data."""
    results = run_discovery_screen(
        synthetic=True,
        min_rows=100,
        min_dates=10,
    )
    assert len(results) >= 1
    # Check that output fields are present
    first = results[0]
    for key in ("league", "stat", "status", "c_stat", "effective_c_stat", "delta_ll", "ece"):
        assert key in first


# ==============================================================================
# 5. 28-Day Live Decay Circuit Breaker Tests (Module 9)
# ==============================================================================

def test_decay_circuit_breaker_actions():
    """Verify rolling decay decisions: center on high ECE, clamp on persistent Delta LL, ok on healthy."""
    c_stat = 0.35

    # High ECE -> center
    dec_center = rolling_decay_status(ece_28d=0.020, delta_ll_28d=0.001, c_stat=c_stat)
    assert dec_center.action == "center"
    assert dec_center.effective_c_stat == c_stat

    # Persistent positive Delta LL > 0.002 after centering -> clamp to 0.0
    dec_clamp = rolling_decay_status(
        ece_28d=0.012, delta_ll_28d=0.003, c_stat=c_stat, centered=True
    )
    assert dec_clamp.action == "clamp"
    assert dec_clamp.effective_c_stat == 0.0

    # Healthy metrics -> ok
    dec_ok = rolling_decay_status(ece_28d=0.010, delta_ll_28d=-0.002, c_stat=c_stat)
    assert dec_ok.action == "ok"
    assert dec_ok.effective_c_stat == c_stat


def test_logit_intercept_centering_math():
    """Verify logit intercept shift restores calibrated mean."""
    # Ground truth rate 0.40, uncalibrated model predicting 0.55
    rng = np.random.default_rng(123)
    p_preds = rng.uniform(0.50, 0.60, size=200).tolist()
    y_trues = (rng.uniform(size=200) < 0.40).astype(float).tolist()
    pairs = list(zip(p_preds, y_trues))

    shift = logit_intercept_centering(pairs)
    assert shift < 0.0  # Must shift downwards because model overpredicted

    # Apply centering
    centered_probs = [apply_centering(p, shift) for p in p_preds]
    mean_centered = np.mean(centered_probs)
    mean_actual = np.mean(y_trues)
    assert abs(mean_centered - mean_actual) < 0.02


# ==============================================================================
# 6. Event-Triggered Availability Sniper Library Tests (Module 7.3)
# ==============================================================================

def test_news_sniper_finds_stale_quotes():
    """Verify find_stale_quote_candidates flags quotes predating injury news in [0, 10m] window."""
    news_time = datetime(2026, 11, 18, 18, 0, 0, tzinfo=timezone.utc)
    ev = AvailabilityTransition(
        player="CeeDee Lamb",
        sport="nfl",
        prev_status="QUESTIONABLE",
        new_status="OUT",
        news_ts=news_time,
        source="official_inactives",
        game_key="nfl_20261118_dal_gb",
    )

    q_stale = QuoteSnapshot(
        venue="novig",
        game_key="nfl_20261118_dal_gb",
        player_norm="ceedee lamb",
        stat="rec_yds",
        line=75.5,
        side="OVER",
        price=0.52,
        quote_ts=news_time - timedelta(minutes=4),  # 4m before news -> STALE!
    )
    q_fresh = QuoteSnapshot(
        venue="novig",
        game_key="nfl_20261118_dal_gb",
        player_norm="ceedee lamb",
        stat="rec_yds",
        line=75.5,
        side="OVER",
        price=0.10,
        quote_ts=news_time + timedelta(seconds=30),  # post-news -> not stale
    )

    current_eval_time = news_time + timedelta(minutes=5)
    candidates = find_stale_quote_candidates(
        events=[ev],
        quotes=[q_stale, q_fresh],
        window_minutes=10.0,
        current_ts=current_eval_time,
        recompute=lambda e, q: 0.005,  # Player OUT -> probability collapses to ~0
    )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.quote.venue == "novig"
    assert c.stale_seconds == 240.0
    assert c.p_lambda_recomputed == 0.005


# ==============================================================================
# 7. Novig Map Expansion & Depth Persistence Tests (Module 7.1)
# ==============================================================================

def test_map_prop_market_for_sport_multi_league():
    """Verify sport-parameterized mapper correctly parses NBA, NHL, MLB, NFL."""
    events = {
        "ev_nba": ("nba:2026-11-18:lal@gsw", datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)),
        "ev_nhl": ("nhl:2026-11-18:bos@tor", datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)),
        "ev_mlb": ("mlb:2026-11-18:lad@nyy", datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)),
    }

    # NBA Points
    m_nba = {
        "marketId": "m_nba_1",
        "eventId": "ev_nba",
        "marketType": "POINTS",
        "description": "Stephen Curry 28.5 POINTS",
        "strike": "28.5",
        "status": "OPEN",
        "voids": "FMV",
        "outcomes": [
            {"outcomeId": "o1", "name": "Over 28.5", "ask_size": 150.0, "bid_size": 80.0},
            {"outcomeId": "o2", "name": "Under 28.5"},
        ],
        "quote_ts": 1792330200000,
    }
    leg_nba = map_prop_market_for_sport(m_nba, events, "nba")
    assert isinstance(leg_nba, NovigLeg)
    assert leg_nba.stat == "points"
    assert leg_nba.player == "Stephen Curry"
    assert leg_nba.ask_size == 150.0
    assert leg_nba.bid_size == 80.0
    assert leg_nba.quote_ts is not None

    # NHL SOG
    m_nhl = {
        "marketId": "m_nhl_1",
        "eventId": "ev_nhl",
        "marketType": "SHOTS_ON_GOAL",
        "description": "Auston Matthews 3.5 SHOTS_ON_GOAL",
        "strike": "3.5",
        "status": "OPEN",
        "voids": "FMV",
        "outcomes": [
            {"outcomeId": "o1", "name": "Over 3.5"},
            {"outcomeId": "o2", "name": "Under 3.5"},
        ],
    }
    leg_nhl = map_prop_market_for_sport(m_nhl, events, "nhl")
    assert isinstance(leg_nhl, NovigLeg)
    assert leg_nhl.stat == "sog"
    assert leg_nhl.player == "Auston Matthews"

    # MLB Game-level market (NRFI)
    m_mlb = {
        "marketId": "m_mlb_1",
        "eventId": "ev_mlb",
        "marketType": "NRFI",
        "description": "LAD @ NYY NRFI",
        "strike": "0.5",
        "status": "OPEN",
        "voids": "FMV",
        "outcomes": [
            {"outcomeId": "o1", "name": "Over 0.5"},
            {"outcomeId": "o2", "name": "Under 0.5"},
        ],
    }
    leg_mlb = map_prop_market_for_sport(m_mlb, events, "mlb")
    assert isinstance(leg_mlb, NovigLeg)
    assert leg_mlb.stat == "nrfi"

